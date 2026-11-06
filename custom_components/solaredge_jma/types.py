"""Typing for the SolarEdge (JMA) integration."""

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .api import SolarEdgeClient
from .coordinator import (
    SolarEdgeMeasurementsCoordinator,
    SolarEdgePowerFlowCoordinator,
    SolarEdgeSiteInfoCoordinator,
)

type SolarEdgeConfigEntry = ConfigEntry[SolarEdgeData]


@dataclass
class SolarEdgeData:
    """Runtime data of a SolarEdge config entry."""

    client: SolarEdgeClient
    site_info: SolarEdgeSiteInfoCoordinator
    measurements: SolarEdgeMeasurementsCoordinator
    power_flow: SolarEdgePowerFlowCoordinator | None
