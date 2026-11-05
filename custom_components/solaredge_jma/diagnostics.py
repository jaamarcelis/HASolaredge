"""Diagnostics support for SolarEdge (JMA)."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .types import SolarEdgeConfigEntry

TO_REDACT = {
    # Credentials
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    # Personal data
    "name",
    "location",
    "address",
    "address2",
    "zip",
    "latitude",
    "longitude",
    # Device identifiers
    "serialNumber",
    "serial",
    "SN",
    "sn",
    "connectedInverterSn",
    "connectedSolaredgeDeviceSN",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SolarEdgeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    measurements = runtime.measurements.data or {}
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "update_interval_minutes": runtime.update_interval.total_seconds() / 60,
        "estimated_credits_per_month": runtime.estimated_credits_per_month,
        "coordinators": {
            "site_info": runtime.site_info.last_update_success,
            "measurements": runtime.measurements.last_update_success,
            "power_flow": (
                runtime.power_flow.last_update_success if runtime.power_flow else None
            ),
        },
        # Parsed values, with battery serial numbers replaced by an index
        "measurements": {
            **{k: v for k, v in measurements.items() if k != "batteries"},
            "batteries": list(measurements.get("batteries", {}).values()),
        },
        "power_flow": runtime.power_flow.data if runtime.power_flow else None,
        # Raw API responses, to verify the parsing against the real API
        "api_responses": _redact_responses(runtime.client.last_responses),
    }


def _redact_responses(responses: dict[str, Any]) -> dict[str, Any]:
    """Redact the raw API responses.

    "name" is only redacted in the site response: elsewhere it may name a metric,
    which is needed to verify the parsing.
    """
    result = async_redact_data(responses, TO_REDACT - {"name"})
    if "site" in result:
        result["site"] = async_redact_data(result["site"], TO_REDACT)
    return result
