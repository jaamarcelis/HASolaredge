"""Data update coordinators for SolarEdge (JMA).

Every successful API call costs one credit, and the Free tier only offers 2,000
credits per month. The coordinators therefore fetch as much as possible per call
and the measurement interval is derived from the configured monthly budget.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
from typing import TYPE_CHECKING, Any, override

from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util, snakecase

from .api import (
    SolarEdgeApiError,
    SolarEdgeAuthError,
    SolarEdgeClient,
    SolarEdgeCreditLimitError,
    SolarEdgeForbiddenError,
    SolarEdgeRateLimitError,
)
from .const import (
    CONF_ADVANCED_POWER_FLOW,
    CONF_CREDIT_BUDGET,
    CONF_UPDATE_INTERVAL,
    CREDIT_BUDGET_USAGE,
    DEFAULT_CREDIT_BUDGET,
    DOMAIN,
    ISSUE_CREDIT_LIMIT,
    ISSUE_POWER_FLOW_TIER,
    LIFETIME_CALLS_PER_DAY,
    LIFETIME_ENERGY_UPDATE_INTERVAL,
    LOGGER,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    MINUTES_PER_MONTH,
    POWER_FLOW_CALLS_PER_DAY,
    POWER_FLOW_UPDATE_INTERVAL,
    SITE_INFO_CALLS_PER_DAY,
    SITE_INFO_UPDATE_INTERVAL,
)
from .parsing import (
    ENERGY_FACTORS,
    POWER_FACTORS,
    Sample,
    daily_values,
    device_list,
    device_serial,
    device_type,
    find_number,
    iter_samples,
    latest_by_serial,
    latest_value,
    lifetime_energy,
    scale,
    sum_all,
    sum_latest,
    unwrap_site,
)

if TYPE_CHECKING:
    from .types import SolarEdgeConfigEntry

METER_ENERGY_METRICS = (
    "productionEnergy",
    "consumptionEnergy",
    "importEnergy",
    "exportEnergy",
)
METER_POWER_METRICS = (
    "productionPower",
    "consumptionPower",
    "importPower",
    "exportPower",
)
STORAGE_METRICS = (
    "chargePower",
    "dischargePower",
    "chargeEnergy",
    "dischargeEnergy",
    "stateOfEnergy",
    "remainingEnergy",
)
INVENTORY_CATEGORIES = {
    "INVERTER": "inverters",
    "METER": "meters",
    "GATEWAY": "gateways",
    "BATTERY": "batteries",
}
QUARTER_HOUR_IN_HOURS = 0.25


@dataclass
class SiteInfo:
    """Site metadata and device inventory."""

    details: dict[str, Any] = field(default_factory=dict)
    devices: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> str | None:
        """Return the activation status of the site."""
        status = self.details.get("activationStatus") or self.details.get("status")
        return str(status) if status is not None else None

    @property
    def name(self) -> str | None:
        """Return the site name."""
        name = self.details.get("name")
        return str(name) if name else None

    def devices_of_type(self, kind: str) -> list[dict[str, Any]]:
        """Return the devices of a type (INVERTER, METER, ...)."""
        return [device for device in self.devices if device_type(device) == kind]

    @property
    def has_meters(self) -> bool:
        """Return True if the site has meters."""
        return bool(self.devices_of_type("METER"))

    @property
    def battery_serials(self) -> list[str]:
        """Return the serial numbers of the batteries."""
        return [
            serial
            for device in self.devices_of_type("BATTERY")
            if (serial := device_serial(device))
        ]


def calls_per_measurement_cycle(site_info: SiteInfo) -> int:
    """Return the number of API calls of one measurement update."""
    # energy (today/month/year) + meter telemetry or site power
    calls = 2
    if site_info.battery_serials:
        calls += 1
    return calls


def fixed_calls_per_month(advanced_power_flow: bool) -> int:
    """Return the credits per month used outside of the measurement cycle."""
    per_day = SITE_INFO_CALLS_PER_DAY + LIFETIME_CALLS_PER_DAY
    if advanced_power_flow:
        per_day += POWER_FLOW_CALLS_PER_DAY
    return per_day * 30


def calculate_update_interval(
    options: dict[str, Any], site_info: SiteInfo
) -> timedelta:
    """Return the measurement update interval for the configured budget."""
    if interval := options.get(CONF_UPDATE_INTERVAL):
        return max(MIN_UPDATE_INTERVAL, timedelta(minutes=int(interval)))

    budget = options.get(CONF_CREDIT_BUDGET, DEFAULT_CREDIT_BUDGET)
    available = budget * CREDIT_BUDGET_USAGE - fixed_calls_per_month(
        options.get(CONF_ADVANCED_POWER_FLOW, False)
    )
    if available <= 0:
        return MAX_UPDATE_INTERVAL
    minutes = math.ceil(
        calls_per_measurement_cycle(site_info) * MINUTES_PER_MONTH / available
    )
    return min(
        MAX_UPDATE_INTERVAL, max(MIN_UPDATE_INTERVAL, timedelta(minutes=minutes))
    )


def estimate_credits_per_month(
    options: dict[str, Any], site_info: SiteInfo, interval: timedelta
) -> int:
    """Return the estimated credit usage per month."""
    cycles = MINUTES_PER_MONTH / (interval.total_seconds() / 60)
    return round(
        cycles * calls_per_measurement_cycle(site_info)
        + fixed_calls_per_month(options.get(CONF_ADVANCED_POWER_FLOW, False))
    )


class SolarEdgeCoordinator[DataT](DataUpdateCoordinator[DataT]):
    """Base coordinator with SolarEdge error handling."""

    config_entry: SolarEdgeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SolarEdgeConfigEntry,
        client: SolarEdgeClient,
        site_id: str,
        name: str,
        update_interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {name}",
            update_interval=update_interval,
        )
        self.client = client
        self.site_id = site_id

    async def _async_call[T](self, call: Callable[[], Awaitable[T]]) -> T:
        """Perform an API call and map errors onto coordinator errors."""
        try:
            result = await call()
        except ConfigEntryAuthFailed:
            # Refresh token rejected (OAuth2TokenRequestReauthError is also a
            # ClientError, so it must not end up in the connection error branch)
            raise
        except SolarEdgeAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SolarEdgeCreditLimitError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_CREDIT_LIMIT,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_CREDIT_LIMIT,
            )
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="credit_limit_reached"
            ) from err
        except SolarEdgeRateLimitError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="rate_limited",
                retry_after=err.retry_after,
            ) from err
        except SolarEdgeForbiddenError:
            # Handled by the caller (missing scope or tier)
            raise
        except SolarEdgeApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_request_failed",
                translation_placeholders={"status": str(err.status)},
            ) from err
        except (ClientError, TimeoutError) as err:
            # Includes token refresh errors that are not a reason to reauthenticate
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="cannot_connect"
            ) from err
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_CREDIT_LIMIT)
        return result


class SolarEdgeSiteInfoCoordinator(SolarEdgeCoordinator[SiteInfo]):
    """Fetch the site details and device inventory (2 credits per update)."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SolarEdgeConfigEntry,
        client: SolarEdgeClient,
        site_id: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass, config_entry, client, site_id, "site info", SITE_INFO_UPDATE_INTERVAL
        )

    @override
    async def _async_update_data(self) -> SiteInfo:
        """Fetch the site details and devices."""
        details = unwrap_site(
            await self._async_call(lambda: self.client.get_site(self.site_id))
        )
        try:
            devices = device_list(
                await self._async_call(lambda: self.client.get_devices(self.site_id))
            )
        except SolarEdgeForbiddenError as err:
            # Token without the DEVICE_DATA scope
            LOGGER.warning(
                "Cannot read the devices of site %s (%s); device data is not "
                "available. Grant the DEVICE_DATA scope to enable it",
                self.site_id,
                err.detail,
            )
            devices = []
        return SiteInfo(details=details, devices=devices)

    @property
    def details_attributes(self) -> dict[str, Any]:
        """Return the site details as snake_case attributes."""
        if self.data is None:
            return {}
        attributes: dict[str, Any] = {}
        for key, value in self.data.details.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if not isinstance(sub_value, dict | list):
                        attributes[snakecase(f"{key}_{sub_key}")] = sub_value
            elif not isinstance(value, list):
                attributes[snakecase(key)] = value
        return attributes

    def inventory(self, kind: str) -> list[dict[str, Any]]:
        """Return the devices of an inventory type without nested objects."""
        if self.data is None:
            return []
        return [
            {
                snakecase(key): value
                for key, value in device.items()
                if not isinstance(value, dict | list)
            }
            for device in self.data.devices_of_type(kind)
        ]


class SolarEdgeMeasurementsCoordinator(SolarEdgeCoordinator[dict[str, Any]]):
    """Fetch energy, meter and storage measurements within the credit budget."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SolarEdgeConfigEntry,
        client: SolarEdgeClient,
        site_id: str,
        site_info: SolarEdgeSiteInfoCoordinator,
        update_interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass, config_entry, client, site_id, "measurements", update_interval
        )
        self.site_info = site_info
        self._lifetime_energy: float | None = None
        self._lifetime_updated: datetime | None = None
        self._year_supported = True
        self._device_data_available = True

    @override
    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the measurements."""
        now = dt_util.now()
        start_of_day = dt_util.start_of_local_day(now)
        site_info = self.site_info.data or SiteInfo()
        data: dict[str, Any] = {}

        await self._async_update_energy(now, data)
        await self._async_update_lifetime(now, data)

        if site_info.has_meters and self._device_data_available:
            meters = await self._async_device_call("meters", start_of_day, now)
            if meters is not None:
                self._parse_meters(meters, data)
        if not site_info.has_meters or "current_power" not in data:
            power = await self._async_call(
                lambda: self.client.get_power(
                    self.site_id, now - timedelta(hours=1), now
                )
            )
            data["current_power"] = latest_value(power, POWER_FACTORS)

        if site_info.battery_serials and self._device_data_available:
            storage = await self._async_device_call("storage", start_of_day, now)
            if storage is not None:
                self._parse_storage(storage, data)

        LOGGER.debug("Updated SolarEdge measurements: %s", data)
        return data

    async def _async_update_energy(self, now: datetime, data: dict[str, Any]) -> None:
        """Fetch today's, this month's and this year's energy in a single call."""
        start_of_year = dt_util.start_of_local_day(now.replace(month=1, day=1))
        start_of_month = dt_util.start_of_local_day(now.replace(day=1))
        start = start_of_year if self._year_supported else start_of_month
        try:
            energy = await self._async_call(
                lambda: self.client.get_energy(self.site_id, start, now, "DAY")
            )
        except UpdateFailed as err:
            cause = err.__cause__
            if (
                self._year_supported
                and isinstance(cause, SolarEdgeApiError)
                and cause.status == 400
            ):
                # Period too long for this endpoint, fall back to this month
                LOGGER.info(
                    "Energy for the whole year is not available, "
                    "falling back to this month only"
                )
                self._year_supported = False
                await self._async_update_energy(now, data)
                return
            raise

        days = daily_values(energy)
        today = now.date()
        data["energy_today"] = days.get(today, 0.0) if days else None
        data["energy_this_month"] = (
            sum(value for day, value in days.items() if day.month == today.month)
            if days
            else None
        )
        data["energy_this_year"] = (
            sum(days.values()) if days and self._year_supported else None
        )

    async def _async_update_lifetime(self, now: datetime, data: dict[str, Any]) -> None:
        """Fetch the lifetime energy, at most every few hours."""
        if (
            self._lifetime_updated is None
            or now - self._lifetime_updated >= LIFETIME_ENERGY_UPDATE_INTERVAL
        ):
            response = await self._async_call(
                lambda: self.client.get_lifetime_energy(self.site_id)
            )
            self._lifetime_energy = lifetime_energy(response)
            self._lifetime_updated = now
        data["lifetime_energy"] = self._lifetime_energy

    async def _async_device_call(
        self, kind: str, start: datetime, end: datetime
    ) -> list[Sample] | None:
        """Fetch meter or storage telemetry and return the samples."""
        if kind == "meters":
            call = self.client.get_meters_telemetry
            metrics = METER_ENERGY_METRICS + METER_POWER_METRICS
        else:
            call = self.client.get_storage_telemetry
            metrics = STORAGE_METRICS
        try:
            response = await self._async_call(lambda: call(self.site_id, start, end))
        except SolarEdgeForbiddenError as err:
            LOGGER.warning(
                "Cannot read %s telemetry of site %s (%s); device data is not "
                "available. Grant the DEVICE_DATA scope to enable it",
                kind,
                self.site_id,
                err.detail,
            )
            self._device_data_available = False
            return None
        return list(iter_samples(response, metrics))

    @staticmethod
    def _energy_today(
        samples: list[Sample], energy_metric: str, power_metric: str
    ) -> float | None:
        """Return today's energy, integrating quarter-hour power if needed."""
        if (energy := sum_all(samples, energy_metric, ENERGY_FACTORS)) is not None:
            return energy
        if (power := sum_all(samples, power_metric, POWER_FACTORS)) is not None:
            return power * QUARTER_HOUR_IN_HOURS
        return None

    def _parse_meters(self, samples: list[Sample], data: dict[str, Any]) -> None:
        """Derive energy and power values from meter telemetry."""
        production = self._energy_today(samples, "productionEnergy", "productionPower")
        consumption = self._energy_today(
            samples, "consumptionEnergy", "consumptionPower"
        )
        purchased = self._energy_today(samples, "importEnergy", "importPower")
        feedin = self._energy_today(samples, "exportEnergy", "exportPower")
        data["production_energy"] = production
        data["consumption_energy"] = consumption
        data["purchased_energy"] = purchased
        data["feedin_energy"] = feedin
        data["selfconsumption_energy"] = (
            max(production - feedin, 0.0)
            if production is not None and feedin is not None
            else None
        )

        solar = sum_latest(samples, "productionPower", POWER_FACTORS)
        load = sum_latest(samples, "consumptionPower", POWER_FACTORS)
        grid_import = sum_latest(samples, "importPower", POWER_FACTORS)
        grid_export = sum_latest(samples, "exportPower", POWER_FACTORS)
        if solar is not None:
            data["current_power"] = solar
            data["solar_power"] = solar
        data["power_consumption"] = load
        if grid_import is not None or grid_export is not None:
            net = (grid_import or 0.0) - (grid_export or 0.0)
            data["grid_power"] = abs(net)
            data["grid_flow_direction"] = "import" if net >= 0 else "export"

    def _parse_storage(self, samples: list[Sample], data: dict[str, Any]) -> None:
        """Derive aggregate and per-battery values from storage telemetry."""
        latest = latest_by_serial(samples)
        batteries: dict[str, dict[str, Any]] = {}
        for serial in (
            self.site_info.data.battery_serials if self.site_info.data else []
        ):
            battery_samples = [s for s in samples if s.serial == serial]

            def latest_of(metric: str, serial: str = serial) -> float | None:
                sample = latest.get((metric, serial))
                if sample is None:
                    return None
                if metric.endswith("Power"):
                    return scale(sample.value, sample.unit, POWER_FACTORS)
                return sample.value

            charge = latest_of("chargePower")
            discharge = latest_of("dischargePower")
            batteries[serial] = {
                "state_of_charge": latest_of("stateOfEnergy"),
                "charge_energy": self._energy_today(
                    battery_samples, "chargeEnergy", "chargePower"
                ),
                "discharge_energy": self._energy_today(
                    battery_samples, "dischargeEnergy", "dischargePower"
                ),
                "power": (
                    (charge or 0.0) - (discharge or 0.0)
                    if charge is not None or discharge is not None
                    else None
                ),
            }

        # Samples without a serial (site level aggregate) or unknown batteries
        charge_energy = self._energy_today(samples, "chargeEnergy", "chargePower")
        discharge_energy = self._energy_today(
            samples, "dischargeEnergy", "dischargePower"
        )
        levels = [
            sample.value
            for (metric, _serial), sample in latest.items()
            if metric == "stateOfEnergy"
        ]
        charge_power = sum_latest(samples, "chargePower", POWER_FACTORS)
        discharge_power = sum_latest(samples, "dischargePower", POWER_FACTORS)

        data["batteries"] = batteries
        data["storage_charge_energy"] = charge_energy
        data["storage_discharge_energy"] = discharge_energy
        data["storage_level"] = sum(levels) / len(levels) if levels else None
        if charge_power is not None or discharge_power is not None:
            net = (charge_power or 0.0) - (discharge_power or 0.0)
            data["storage_power"] = abs(net)
            data["storage_flow_direction"] = "charge" if net >= 0 else "discharge"


POWER_FLOW_ALIASES = {
    "solar_power": ("PV", "solar", "production"),
    "power_consumption": ("LOAD", "consumption", "house"),
    "grid_power": ("GRID",),
    "storage_power": ("STORAGE", "battery"),
}
POWER_KEYS = ("currentPower", "power", "value")


class SolarEdgePowerFlowCoordinator(SolarEdgeCoordinator[dict[str, Any]]):
    """Fetch the live power flow (Advanced Monitoring API, Business Pro tier)."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SolarEdgeConfigEntry,
        client: SolarEdgeClient,
        site_id: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            config_entry,
            client,
            site_id,
            "power flow",
            POWER_FLOW_UPDATE_INTERVAL,
        )
        self.tier_insufficient = False

    @override
    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the power flow."""
        if self.tier_insufficient:
            return {}
        try:
            response = await self._async_call(
                lambda: self.client.get_power_flow(self.site_id)
            )
        except SolarEdgeForbiddenError as err:
            self.tier_insufficient = True
            # Stop polling, the endpoint is not available for this account
            self.update_interval = None
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_POWER_FLOW_TIER,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_POWER_FLOW_TIER,
                translation_placeholders={"detail": err.detail},
            )
            return {}
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_POWER_FLOW_TIER)
        return self.parse_power_flow(response)

    @staticmethod
    def parse_power_flow(response: Any) -> dict[str, Any]:
        """Parse a power flow response into sensor values (W)."""
        data: dict[str, Any] = {}
        flow = response
        if isinstance(response, dict):
            # v1 wrapped the flow in "siteCurrentPowerFlow"
            for value in response.values():
                if isinstance(value, dict) and any(
                    key.upper() in ("PV", "GRID", "LOAD") for key in value
                ):
                    flow = value
                    break
        if not isinstance(flow, dict):
            return data
        unit = flow.get("unit") or flow.get("units")
        factor = POWER_FACTORS.get(str(unit).upper(), 1.0) if unit else 1.0
        upper = {key.upper(): value for key, value in flow.items()}

        for sensor_key, aliases in POWER_FLOW_ALIASES.items():
            for alias in aliases:
                if (element := upper.get(alias.upper())) is not None:
                    value = find_number(element, POWER_KEYS)
                    if value is not None:
                        data[sensor_key] = abs(value) * factor
                        if sensor_key == "grid_power":
                            data["grid_flow_direction"] = (
                                "export" if value < 0 else "import"
                            )
                        elif sensor_key == "storage_power":
                            data["storage_flow_direction"] = (
                                "discharge" if value < 0 else "charge"
                            )
                    if sensor_key == "storage_power":
                        level = find_number(
                            element, ("chargeLevel", "stateOfCharge", "stateOfEnergy")
                        )
                        if level is not None:
                            data["storage_level"] = level
                    break

        # v1 style direction from the connections list
        connections = flow.get("connections")
        if isinstance(connections, list):
            for connection in connections:
                if not isinstance(connection, dict):
                    continue
                source = str(connection.get("from", "")).upper()
                target = str(connection.get("to", "")).upper()
                if "GRID" in (source, target):
                    data["grid_flow_direction"] = (
                        "import" if source == "GRID" else "export"
                    )
                if "STORAGE" in (source, target):
                    data["storage_flow_direction"] = (
                        "discharge" if source == "STORAGE" else "charge"
                    )
        return data
