"""Client for the SolarEdge Monitoring API v2."""

from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from http import HTTPStatus
import logging
from typing import Any

import aiohttp

from .const import API_BASE_URL

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Device types that are relevant for this integration
DEVICE_TYPES = ("INVERTER", "BATTERY", "METER", "GATEWAY")

type AccessTokenProvider = Callable[[], Awaitable[str]]


class SolarEdgeApiError(Exception):
    """Generic error returned by the SolarEdge API."""

    def __init__(self, status: int, detail: str) -> None:
        """Initialize the error."""
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class SolarEdgeAuthError(SolarEdgeApiError):
    """Access token missing, invalid, expired or revoked (HTTP 401)."""


class SolarEdgeForbiddenError(SolarEdgeApiError):
    """Access denied: wrong site, missing scope or insufficient tier (HTTP 403)."""

    @property
    def tier_insufficient(self) -> bool:
        """Return True if the endpoint requires a higher subscription tier."""
        return "tier" in self.detail.lower()


class SolarEdgeRateLimitError(SolarEdgeApiError):
    """Calls-per-minute limit exceeded (HTTP 429 with retry-after)."""

    def __init__(self, status: int, detail: str, retry_after: int | None) -> None:
        """Initialize the error."""
        super().__init__(status, detail)
        self.retry_after = retry_after


class SolarEdgeCreditLimitError(SolarEdgeApiError):
    """Monthly credit limit reached (HTTP 429 without retry-after)."""


def _format_datetime(value: datetime) -> str:
    """Format a timezone aware datetime as ISO 8601 (required by the API)."""
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone aware")
    return value.replace(microsecond=0).isoformat()


class SolarEdgeClient:
    """SolarEdge Monitoring API v2 client using an OAuth 2.0 bearer token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token_provider: AccessTokenProvider,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._access_token_provider = access_token_provider
        # Last raw response per endpoint, used for diagnostics
        self.last_responses: dict[str, Any] = {}

    async def get_site(self, site_id: int | str) -> dict[str, Any]:
        """Get site metadata."""
        return await self._get(f"sites/{site_id}", name="site")

    async def get_devices(
        self, site_id: int | str, types: Iterable[str] = DEVICE_TYPES
    ) -> Any:
        """Get the devices of a site."""
        return await self._get(
            f"sites/{site_id}/devices",
            [("types", device_type) for device_type in types],
            name="devices",
        )

    async def get_lifetime_energy(self, site_id: int | str) -> Any:
        """Get the site lifetime production from the onboard counters."""
        return await self._get(f"sites/{site_id}/lifetime-energy", name="lifetime")

    async def get_energy(
        self,
        site_id: int | str,
        start: datetime,
        end: datetime,
        resolution: str = "DAY",
    ) -> Any:
        """Get site energy for a period."""
        return await self._get(
            f"sites/{site_id}/energy",
            self._period(start, end, resolution),
            name="energy",
        )

    async def get_power(
        self,
        site_id: int | str,
        start: datetime,
        end: datetime,
        resolution: str = "QUARTER_HOUR",
    ) -> Any:
        """Get site power for a period."""
        return await self._get(
            f"sites/{site_id}/power",
            self._period(start, end, resolution),
            name="power",
        )

    async def get_meters_telemetry(
        self,
        site_id: int | str,
        start: datetime,
        end: datetime,
        resolution: str = "QUARTER_HOUR",
    ) -> Any:
        """Get telemetry of all meters of a site."""
        return await self._get(
            f"sites/{site_id}/meters/telemetry",
            self._period(start, end, resolution),
            name="meters_telemetry",
        )

    async def get_storage_telemetry(
        self,
        site_id: int | str,
        start: datetime,
        end: datetime,
        resolution: str = "QUARTER_HOUR",
    ) -> Any:
        """Get telemetry of all batteries of a site."""
        return await self._get(
            f"sites/{site_id}/storage/telemetry",
            self._period(start, end, resolution),
            name="storage_telemetry",
        )

    async def activate_power_flow(self, site_id: int | str) -> Any:
        """Activate a live power flow session (Advanced Monitoring API)."""
        return await self._request(
            "POST", f"sites/{site_id}/power-flow", None, name="power_flow_activate"
        )

    async def get_power_flow(self, site_id: int | str) -> Any:
        """Get the power flow of a site (Advanced Monitoring API)."""
        return await self._get(f"sites/{site_id}/power-flow", name="power_flow")

    @staticmethod
    def _period(
        start: datetime, end: datetime, resolution: str
    ) -> list[tuple[str, str]]:
        """Return the query parameters for a measurement period."""
        return [
            ("from", _format_datetime(start)),
            ("to", _format_datetime(end)),
            ("resolution", resolution),
        ]

    async def _get(
        self,
        path: str,
        params: list[tuple[str, str]] | None = None,
        *,
        name: str,
    ) -> Any:
        """Perform a GET request."""
        return await self._request("GET", path, params, name=name)

    async def _request(
        self,
        method: str,
        path: str,
        params: list[tuple[str, str]] | None,
        *,
        name: str,
    ) -> Any:
        """Perform a request and return the decoded JSON response."""
        access_token = await self._access_token_provider()
        url = f"{API_BASE_URL}/{path}"
        _LOGGER.debug("%s %s with params: %s", method, url, params)
        async with self._session.request(
            method,
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=DEFAULT_TIMEOUT,
        ) as response:
            _LOGGER.debug("Response from %s: %s", url, response.status)
            if response.status >= HTTPStatus.BAD_REQUEST:
                raise await self._error_from_response(response)
            if response.status == HTTPStatus.NO_CONTENT:
                data: Any = None
            else:
                data = await response.json(content_type=None)
        _LOGGER.debug("JSON from %s: %s", url, data)
        self.last_responses[name] = data
        return data

    @staticmethod
    async def _error_from_response(
        response: aiohttp.ClientResponse,
    ) -> SolarEdgeApiError:
        """Build the matching error for a failed response."""
        status = response.status
        detail = getattr(response, "reason", None) or ""
        try:
            body = await response.json(content_type=None)
        except aiohttp.ContentTypeError, ValueError:
            body = None
        if isinstance(body, dict):
            # RFC 7807 problem details or API gateway message
            parts = [
                str(body[key])
                for key in ("title", "detail", "message", "error", "error_description")
                if body.get(key)
            ]
            if parts:
                detail = " - ".join(parts)

        if status == HTTPStatus.UNAUTHORIZED:
            return SolarEdgeAuthError(status, detail)
        if status == HTTPStatus.FORBIDDEN:
            return SolarEdgeForbiddenError(status, detail)
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            retry_after = response.headers.get("retry-after")
            remaining = response.headers.get("x-ratelimit-remaining-minute")
            if "credit" in detail.lower() or (
                retry_after is None and remaining not in (None, "0")
            ):
                return SolarEdgeCreditLimitError(status, detail)
            try:
                retry_after_seconds = int(retry_after) if retry_after else None
            except ValueError:
                retry_after_seconds = None
            return SolarEdgeRateLimitError(status, detail, retry_after_seconds)
        return SolarEdgeApiError(status, detail)
