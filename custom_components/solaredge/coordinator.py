"""Provides the data update coordinators for SolarEdge."""

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import TYPE_CHECKING, Any, override

from aiohttp import ClientResponseError

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util, snakecase

from .api import SolarEdge
from .const import (
    DETAILS_UPDATE_DELAY,
    DOMAIN,
    ENERGY_DETAILS_DELAY,
    INVENTORY_UPDATE_DELAY,
    LOGGER,
    OVERVIEW_UPDATE_DELAY,
    POWER_FLOW_UPDATE_DELAY,
    STORAGE_DATA_UPDATE_DELAY,
)

if TYPE_CHECKING:
    from .types import SolarEdgeConfigEntry


class SolarEdgeDataService(ABC):
    """Get and update the latest data."""

    coordinator: DataUpdateCoordinator[None]

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SolarEdgeConfigEntry,
        api: SolarEdge,
        site_id: str,
    ) -> None:
        """Initialize the data object."""
        self.api = api
        self.site_id = site_id

        self.data: dict[str, Any] = {}
        self.attributes: dict[str, Any] = {}

        self.hass = hass
        self.config_entry = config_entry

    @callback
    def async_setup(self) -> None:
        """Coordinator creation."""
        self.coordinator = DataUpdateCoordinator(
            self.hass,
            LOGGER,
            config_entry=self.config_entry,
            name=str(self),
            update_method=self.async_update_data,
            update_interval=self.update_interval,
        )

    @property
    @abstractmethod
    def update_interval(self) -> timedelta:
        """Update interval."""

    @abstractmethod
    async def async_update_data(self) -> None:
        """Update data."""


class SolarEdgeOverviewDataService(SolarEdgeDataService):
    """Get and update the latest overview data."""

    @property
    @override
    def update_interval(self) -> timedelta:
        """Update interval."""
        return OVERVIEW_UPDATE_DELAY

    @override
    async def async_update_data(self) -> None:
        """Update the data from the SolarEdge Monitoring API."""
        try:
            data = await self.api.get_overview(self.site_id)
            overview = data["overview"]
        except KeyError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="missing_data",
                translation_placeholders={"service": "overview"},
            ) from ex
        except ClientResponseError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_request_failed",
                translation_placeholders={
                    "service": "overview",
                    "status": str(ex.status),
                },
            ) from None

        self.data = {}

        energy_keys = ["lifeTimeData", "lastYearData", "lastMonthData", "lastDayData"]
        for key, value in overview.items():
            if key in energy_keys:
                data = value["energy"]
            elif key == "currentPower":
                data = value["power"]
            else:
                data = value
            self.data[key] = data

        # Sanity check the energy values. SolarEdge API sometimes
        # reports "lifetimedata" of zero,
        # while values for last Year, Month and Day energy are still OK.
        # See https://github.com/home-assistant/core/issues/59285 .
        if set(energy_keys).issubset(self.data.keys()):
            for index, key in enumerate(energy_keys, start=1):
                # All coming values in list should be larger than the current value.
                if any(self.data[k] > self.data[key] for k in energy_keys[index:]):
                    LOGGER.warning(
                        "Ignoring invalid energy value %s for %s", self.data[key], key
                    )
                    self.data.pop(key)

        LOGGER.debug("Updated SolarEdge overview: %s", self.data)


class SolarEdgeDetailsDataService(SolarEdgeDataService):
    """Get and update the latest details data."""

    @property
    @override
    def update_interval(self) -> timedelta:
        """Update interval."""
        return DETAILS_UPDATE_DELAY

    @override
    async def async_update_data(self) -> None:
        """Update the data from the SolarEdge Monitoring API."""

        try:
            data = await self.api.get_details(self.site_id)
            details = data["details"]
        except KeyError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="missing_data",
                translation_placeholders={"service": "details"},
            ) from ex
        except ClientResponseError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_request_failed",
                translation_placeholders={
                    "service": "details",
                    "status": str(ex.status),
                },
            ) from None

        self.data = {}
        self.attributes = {}

        for key, value in details.items():
            key = snakecase(key)

            if key == "primary_module":
                for module_key, module_value in value.items():
                    self.attributes[snakecase(module_key)] = module_value
            elif key in [
                "peak_power",
                "type",
                "name",
                "last_update_time",
                "installation_date",
            ]:
                self.attributes[key] = value
            elif key == "status":
                self.data["status"] = value

        LOGGER.debug(
            "Updated SolarEdge details: %s, %s",
            self.data.get("status"),
            self.attributes,
        )


class SolarEdgeInventoryDataService(SolarEdgeDataService):
    """Get and update the latest inventory data."""

    @property
    @override
    def update_interval(self) -> timedelta:
        """Update interval."""
        return INVENTORY_UPDATE_DELAY

    @override
    async def async_update_data(self) -> None:
        """Update the data from the SolarEdge Monitoring API."""
        try:
            data = await self.api.get_inventory(self.site_id)
            inventory = data["Inventory"]
        except KeyError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="missing_data",
                translation_placeholders={"service": "inventory"},
            ) from ex
        except ClientResponseError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_request_failed",
                translation_placeholders={
                    "service": "inventory",
                    "status": str(ex.status),
                },
            ) from None

        self.data = {}
        self.attributes = {}

        for key, value in inventory.items():
            self.data[key] = len(value)
            self.attributes[key] = {key: value}

        LOGGER.debug("Updated SolarEdge inventory: %s, %s", self.data, self.attributes)


class SolarEdgeEnergyDetailsService(SolarEdgeDataService):
    """Get and update the latest power flow data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SolarEdgeConfigEntry,
        api: SolarEdge,
        site_id: str,
    ) -> None:
        """Initialize the power flow data service."""
        super().__init__(hass, config_entry, api, site_id)

        self.unit = None

    @property
    @override
    def update_interval(self) -> timedelta:
        """Update interval."""
        return ENERGY_DETAILS_DELAY

    @override
    async def async_update_data(self) -> None:
        """Update the data from the SolarEdge Monitoring API."""
        try:
            now = dt_util.now()
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            data = await self.api.get_energy_details(
                self.site_id,
                midnight,
                now,
                time_unit="DAY",
            )
            energy_details = data["energyDetails"]
        except KeyError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="missing_data",
                translation_placeholders={"service": "power flow"},
            ) from ex
        except ClientResponseError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_request_failed",
                translation_placeholders={
                    "service": "energy details",
                    "status": str(ex.status),
                },
            ) from None

        if "meters" not in energy_details:
            LOGGER.debug(
                "Missing meters in energy details data. Assuming site does not have any"
            )
            return

        self.data = {}
        self.attributes = {}
        self.unit = energy_details["unit"]

        for meter in energy_details["meters"]:
            if "type" not in meter or "values" not in meter:
                continue
            if meter["type"] not in [
                "Production",
                "SelfConsumption",
                "FeedIn",
                "Purchased",
                "Consumption",
            ]:
                continue
            if len(meter["values"][0]) == 2:
                self.data[meter["type"]] = meter["values"][0]["value"]
                self.attributes[meter["type"]] = {"date": meter["values"][0]["date"]}

        LOGGER.debug(
            "Updated SolarEdge energy details: %s, %s", self.data, self.attributes
        )


class SolarEdgePowerFlowDataService(SolarEdgeDataService):
    """Get and update the latest power flow data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SolarEdgeConfigEntry,
        api: SolarEdge,
        site_id: str,
    ) -> None:
        """Initialize the power flow data service."""
        super().__init__(hass, config_entry, api, site_id)

        self.unit = None

    @property
    @override
    def update_interval(self) -> timedelta:
        """Update interval."""
        return POWER_FLOW_UPDATE_DELAY

    @override
    async def async_update_data(self) -> None:
        """Update the data from the SolarEdge Monitoring API."""
        try:
            data = await self.api.get_current_power_flow(self.site_id)
            power_flow = data["siteCurrentPowerFlow"]
        except KeyError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="missing_data",
                translation_placeholders={"service": "power flow"},
            ) from ex
        except ClientResponseError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_request_failed",
                translation_placeholders={
                    "service": "power flow",
                    "status": str(ex.status),
                },
            ) from None

        if power_flow is None:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="missing_data",
                translation_placeholders={"service": "power flow"},
            )

        power_from = []
        power_to = []

        if "connections" not in power_flow:
            LOGGER.debug(
                "Missing connections in power flow data. Assuming site does not"
                " have any"
            )
            return

        for connection in power_flow["connections"]:
            power_from.append(connection["from"].lower())
            power_to.append(connection["to"].lower())

        self.data = {}
        self.attributes = {}
        self.unit = power_flow["unit"]

        for key, value in power_flow.items():
            if key in ["LOAD", "PV", "GRID", "STORAGE"]:
                self.data[key] = value.get("currentPower")
                self.attributes[key] = {"status": value["status"]}

            if key == "GRID":
                export = key.lower() in power_to
                if self.data[key]:
                    self.data[key] *= -1 if export else 1
                self.data["grid_flow_direction"] = "export" if export else "import"

            if key == "STORAGE":
                charge = key.lower() in power_to
                if self.data[key]:
                    self.data[key] *= -1 if charge else 1
                self.data["storage_flow_direction"] = (
                    "charge" if charge else "discharge"
                )
                self.data["storage_level"] = value["chargeLevel"]

        LOGGER.debug("Updated SolarEdge power flow: %s, %s", self.data, self.attributes)


class SolarEdgeStorageDataService(SolarEdgeDataService):
    """Get and update the latest storage data."""

    @property
    @override
    def update_interval(self) -> timedelta:
        """Update interval."""
        return STORAGE_DATA_UPDATE_DELAY

    @override
    async def async_update_data(self) -> None:
        """Update the data from the SolarEdge Monitoring API."""
        now = dt_util.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            data = await self.api.get_storage_data(
                self.site_id,
                start_of_day,
                now,
            )
        except ClientResponseError as ex:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_request_failed",
                translation_placeholders={
                    "service": "storage",
                    "status": str(ex.status),
                },
            ) from None
        storage_data = data.get("storageData")
        if storage_data is None:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="data_unavailable",
                translation_placeholders={"service": "storage"},
            )

        batteries = storage_data.get("batteries")
        if batteries is None:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="data_unavailable",
                translation_placeholders={"service": "battery"},
            )

        self.data = {}
        self.attributes = {}

        if not batteries:
            LOGGER.debug("No batteries found in storage data")
            return

        # Aggregate totals across all batteries
        total_charge_energy = 0.0
        total_discharge_energy = 0.0

        for battery in batteries:
            serial = battery.get("serialNumber")
            if not serial:
                LOGGER.debug("Skipping battery without serialNumber")
                continue

            telemetries = battery.get("telemetries", [])

            if not telemetries:
                continue

            latest = telemetries[-1]

            # Per-battery current values
            self.data[f"{serial}_state_of_charge"] = latest.get(
                "batteryPercentageState"
            )
            self.data[f"{serial}_power"] = latest.get("power")

            # Compute daily charge/discharge delta from lifetime counters
            if len(telemetries) >= 2:
                first = telemetries[0]
                charge_energy = latest.get("lifeTimeEnergyCharged", 0.0) - first.get(
                    "lifeTimeEnergyCharged", 0.0
                )
                discharge_energy = latest.get(
                    "lifeTimeEnergyDischarged", 0.0
                ) - first.get("lifeTimeEnergyDischarged", 0.0)
            else:
                charge_energy = 0.0
                discharge_energy = 0.0

            total_charge_energy += charge_energy
            total_discharge_energy += discharge_energy

            self.data[f"{serial}_charge_energy"] = charge_energy
            self.data[f"{serial}_discharge_energy"] = discharge_energy

        self.data["charge_energy"] = total_charge_energy
        self.data["discharge_energy"] = total_discharge_energy

        LOGGER.debug("Updated SolarEdge storage data: %s", self.data)
