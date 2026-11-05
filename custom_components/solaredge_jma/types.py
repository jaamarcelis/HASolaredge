"""Typing for the SolarEdge (JMA) integration."""

from dataclasses import dataclass
from datetime import timedelta

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
    update_interval: timedelta
    estimated_credits_per_month: int
