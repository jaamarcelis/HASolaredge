"""Sensors for the SolarEdge (JMA) integration."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import CONF_SITE_ID, DOMAIN
from .coordinator import INVENTORY_CATEGORIES, SolarEdgeSiteInfoCoordinator
from .types import SolarEdgeConfigEntry


class Source(StrEnum):
    """Where the value of a sensor comes from."""

    SITE = "site"
    INVENTORY = "inventory"
    MEASUREMENTS = "measurements"
    # Measurements, or the Advanced power flow when that option is enabled
    POWER = "power"
    STORAGE = "storage"


@dataclass(frozen=True, kw_only=True)
class SolarEdgeSensorEntityDescription(SensorEntityDescription):
    """Sensor entity description for SolarEdge."""

    source: Source = Source.MEASUREMENTS
    value_key: str | None = None


def _energy(key: str, **kwargs: Any) -> SolarEdgeSensorEntityDescription:
    """Return an energy sensor description."""
    return SolarEdgeSensorEntityDescription(
        key=key,
        translation_key=key,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        **kwargs,
    )


def _power(key: str, **kwargs: Any) -> SolarEdgeSensorEntityDescription:
    """Return a power sensor description."""
    return SolarEdgeSensorEntityDescription(
        key=key,
        translation_key=key,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        **kwargs,
    )


SENSOR_TYPES: tuple[SolarEdgeSensorEntityDescription, ...] = (
    _energy("lifetime_energy"),
    _energy("energy_this_year"),
    _energy("energy_this_month"),
    _energy("energy_today"),
    _power("current_power"),
    SolarEdgeSensorEntityDescription(
        key="site_details",
        translation_key="site_details",
        source=Source.SITE,
        entity_registry_enabled_default=False,
    ),
    *(
        SolarEdgeSensorEntityDescription(
            key=category,
            translation_key=category,
            source=Source.INVENTORY,
            entity_registry_enabled_default=False,
        )
        for category in INVENTORY_CATEGORIES.values()
    ),
    _power(
        "power_consumption", source=Source.POWER, entity_registry_enabled_default=False
    ),
    _power("solar_power", source=Source.POWER, entity_registry_enabled_default=False),
    _power("grid_power", source=Source.POWER, entity_registry_enabled_default=False),
    _power("storage_power", source=Source.POWER, entity_registry_enabled_default=False),
    SolarEdgeSensorEntityDescription(
        key="grid_flow_direction",
        translation_key="grid_flow_direction",
        source=Source.POWER,
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.ENUM,
        options=["export", "import"],
    ),
    SolarEdgeSensorEntityDescription(
        key="storage_flow_direction",
        translation_key="storage_flow_direction",
        source=Source.POWER,
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.ENUM,
        options=["charge", "discharge"],
    ),
    SolarEdgeSensorEntityDescription(
        key="storage_level",
        translation_key="storage_level",
        source=Source.POWER,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
    ),
    _energy("purchased_energy", entity_registry_enabled_default=False),
    _energy("production_energy", entity_registry_enabled_default=False),
    _energy("consumption_energy", entity_registry_enabled_default=False),
    _energy("selfconsumption_energy", entity_registry_enabled_default=False),
    _energy("feedin_energy", entity_registry_enabled_default=False),
)

# Only created when the site has batteries
STORAGE_SENSOR_TYPES: tuple[SolarEdgeSensorEntityDescription, ...] = (
    _energy("storage_charge_energy", entity_registry_enabled_default=False),
    _energy("storage_discharge_energy", entity_registry_enabled_default=False),
)

# Created per battery serial number
BATTERY_SENSOR_TYPES: tuple[SolarEdgeSensorEntityDescription, ...] = (
    _energy(
        "battery_charge_energy",
        source=Source.STORAGE,
        value_key="charge_energy",
        entity_registry_enabled_default=False,
    ),
    _energy(
        "battery_discharge_energy",
        source=Source.STORAGE,
        value_key="discharge_energy",
        entity_registry_enabled_default=False,
    ),
    SolarEdgeSensorEntityDescription(
        key="battery_state_of_charge",
        translation_key="battery_state_of_charge",
        source=Source.STORAGE,
        value_key="state_of_charge",
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
    ),
    SolarEdgeSensorEntityDescription(
        key="battery_power",
        source=Source.STORAGE,
        value_key="power",
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarEdgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the SolarEdge sensors."""
    runtime = entry.runtime_data
    site_id: str = entry.data[CONF_SITE_ID]
    power_coordinator = runtime.power_flow or runtime.measurements

    entities: list[SensorEntity] = []
    for description in SENSOR_TYPES:
        if description.source is Source.SITE:
            entities.append(
                SolarEdgeSiteDetailsSensor(runtime.site_info, site_id, description)
            )
        elif description.source is Source.INVENTORY:
            entities.append(
                SolarEdgeInventorySensor(runtime.site_info, site_id, description)
            )
        elif description.source is Source.POWER:
            entities.append(
                SolarEdgeValueSensor(power_coordinator, site_id, description)
            )
        else:
            entities.append(
                SolarEdgeValueSensor(runtime.measurements, site_id, description)
            )

    if serials := (
        runtime.site_info.data.battery_serials if runtime.site_info.data else []
    ):
        entities.extend(
            SolarEdgeValueSensor(runtime.measurements, site_id, description)
            for description in STORAGE_SENSOR_TYPES
        )
        via_device_id = dr.async_get_device_id_by_identifier(
            hass, (DOMAIN, site_id), config_entry_id=entry.entry_id
        )
        entities.extend(
            SolarEdgeBatterySensor(
                runtime.measurements, site_id, description, serial, via_device_id
            )
            for serial in serials
            for description in BATTERY_SENSOR_TYPES
        )

    async_add_entities(entities)


class SolarEdgeSensorEntity[CoordinatorT: DataUpdateCoordinator[Any]](
    CoordinatorEntity[CoordinatorT], SensorEntity
):
    """Base class for a SolarEdge sensor."""

    _attr_has_entity_name = True

    entity_description: SolarEdgeSensorEntityDescription

    def __init__(
        self,
        coordinator: CoordinatorT,
        site_id: str,
        description: SolarEdgeSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._site_id = site_id
        self._attr_unique_id = f"{site_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, site_id)}, manufacturer="SolarEdge"
        )


class SolarEdgeValueSensor(
    SolarEdgeSensorEntity[DataUpdateCoordinator[dict[str, Any]]]
):
    """Sensor with a value from the measurements or power flow coordinator."""

    @property
    @override
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(
            self.entity_description.value_key or self.entity_description.key
        )


class SolarEdgeSiteDetailsSensor(SolarEdgeSensorEntity[SolarEdgeSiteInfoCoordinator]):
    """Site activation status, with the site details as attributes."""

    def __init__(
        self,
        coordinator: SolarEdgeSiteInfoCoordinator,
        site_id: str,
        description: SolarEdgeSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, site_id, description)
        # Same unique ID as the core integration used
        self._attr_unique_id = site_id

    @property
    @override
    def native_value(self) -> str | None:
        """Return the activation status."""
        return self.coordinator.data.status if self.coordinator.data else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the site details."""
        return self.coordinator.details_attributes


class SolarEdgeInventorySensor(SolarEdgeSensorEntity[SolarEdgeSiteInfoCoordinator]):
    """Number of devices of a type, with the devices as attributes."""

    @property
    def _kind(self) -> str:
        """Return the API device type of this sensor."""
        return next(
            kind
            for kind, category in INVENTORY_CATEGORIES.items()
            if category == self.entity_description.key
        )

    @property
    @override
    def native_value(self) -> int | None:
        """Return the number of devices."""
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.devices_of_type(self._kind))

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the devices."""
        return {self.entity_description.key: self.coordinator.inventory(self._kind)}


class SolarEdgeBatterySensor(SolarEdgeValueSensor):
    """Sensor of a single battery."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        site_id: str,
        description: SolarEdgeSensorEntityDescription,
        serial: str,
        via_device_id: str | None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, site_id, description)
        self._serial = serial
        self._attr_unique_id = f"{site_id}_{serial}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{site_id}_{serial}")},
            manufacturer="SolarEdge",
            name=f"Battery {serial}",
            serial_number=serial,
            via_device_id=via_device_id,
        )

    @property
    @override
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        battery = self.coordinator.data.get("batteries", {}).get(self._serial, {})
        return battery.get(self.entity_description.value_key)
