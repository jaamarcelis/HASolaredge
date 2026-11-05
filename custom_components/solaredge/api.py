"""Client for the SolarEdge Monitoring API.

Based on aiosolaredge 1.0.2 (https://github.com/bdraco/aiosolaredge),
Copyright (c) 2024 J. Nick Koston, MIT License.
"""

from collections.abc import Iterable
from datetime import datetime
import logging
from typing import Any, Literal

import aiohttp
import yarl

_BASE_URL = yarl.URL("https://monitoringapi.solaredge.com")
_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

_LOGGER = logging.getLogger(__name__)

type EnergyMeter = Literal[
    "PRODUCTION", "CONSUMPTION", "SELFCONSUMPTION", "FEEDIN", "PURCHASED"
]


class SolarEdge:
    """SolarEdge Monitoring API client."""

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        timeout: int = 10,
    ) -> None:
        """Initialize the SolarEdge API client."""
        self.api_key = api_key
        self.session = session
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    @staticmethod
    def _get_site_url(site_id: int | str) -> yarl.URL:
        """Get the base URL for a site."""
        return _BASE_URL.joinpath("site", str(site_id))

    async def get_details(self, site_id: int | str) -> dict[str, Any]:
        """Get details of the site."""
        return await self._get_json(self._get_site_url(site_id).joinpath("details"))

    async def get_overview(self, site_id: int | str) -> dict[str, Any]:
        """Get overview of the site."""
        return await self._get_json(self._get_site_url(site_id).joinpath("overview"))

    async def get_inventory(self, site_id: int | str) -> dict[str, Any]:
        """Get inventory of the site."""
        return await self._get_json(self._get_site_url(site_id).joinpath("inventory"))

    async def get_energy_details(
        self,
        site_id: int | str,
        start_time: datetime,
        end_time: datetime,
        meters: Iterable[EnergyMeter] = (),
        time_unit: str = "DAY",
    ) -> dict[str, Any]:
        """Get energy details of the site.

        time_unit is one of QUARTER_OF_AN_HOUR, HOUR, DAY, WEEK, MONTH, YEAR.
        """
        params: dict[str, Any] = {
            "startTime": start_time.strftime(_DATETIME_FORMAT),
            "endTime": end_time.strftime(_DATETIME_FORMAT),
            "timeUnit": time_unit,
        }
        if meters:
            params["meters"] = ",".join(meters)
        return await self._get_json(
            self._get_site_url(site_id).joinpath("energyDetails"), params
        )

    async def get_storage_data(
        self,
        site_id: int | str,
        start_time: datetime,
        end_time: datetime,
        serials: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Get battery storage data of the site (max. one week period)."""
        params: dict[str, Any] = {
            "startTime": start_time.strftime(_DATETIME_FORMAT),
            "endTime": end_time.strftime(_DATETIME_FORMAT),
        }
        if serials:
            params["serials"] = ",".join(serials)
        return await self._get_json(
            self._get_site_url(site_id).joinpath("storageData"), params
        )

    async def get_current_power_flow(self, site_id: int | str) -> dict[str, Any]:
        """Get current power flow of the site."""
        return await self._get_json(
            self._get_site_url(site_id).joinpath("currentPowerFlow")
        )

    async def _get_json(
        self, url: yarl.URL, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Perform a GET request and return the JSON response."""
        _LOGGER.debug("Calling %s with params: %s", url, params)
        async with self.session.get(
            url,
            params={"api_key": self.api_key, **(params or {})},
            timeout=self.timeout,
        ) as response:
            _LOGGER.debug("Response from %s: %s", url, response.status)
            response.raise_for_status()
            json = await response.json()
        _LOGGER.debug("JSON from %s: %s", url, json)
        return json
