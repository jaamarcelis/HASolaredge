"""Typing for the SolarEdge Monitoring API."""

from typing import TypedDict

from homeassistant.config_entries import ConfigEntry

from .api import SolarEdge
from .coordinator import SolarEdgeDataService

type SolarEdgeConfigEntry = ConfigEntry[SolarEdgeData]


class SolarEdgeData(TypedDict, total=False):
    """Data for the solaredge integration."""

    api_client: SolarEdge
    data_services: list[SolarEdgeDataService]
