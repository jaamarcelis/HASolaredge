"""The SolarEdge (JMA) integration (SolarEdge Monitoring API v2)."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import config_entry_oauth2_flow, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SolarEdgeClient
from .const import CONF_ADVANCED_POWER_FLOW, CONF_SITE_ID, DOMAIN, LOGGER
from .coordinator import (
    SolarEdgeMeasurementsCoordinator,
    SolarEdgePowerFlowCoordinator,
    SolarEdgeSiteInfoCoordinator,
)
from .types import SolarEdgeConfigEntry, SolarEdgeData

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SolarEdgeConfigEntry) -> None:
    """Set up SolarEdge from a config entry."""
    if "token" not in entry.data:
        # Entry created for the shut down API v1 (API key): authorize with OAuth
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="oauth_required"
        )

    site_id = str(entry.data[CONF_SITE_ID])
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    async def access_token() -> str:
        await oauth_session.async_ensure_token_valid()
        return str(oauth_session.token["access_token"])

    client = SolarEdgeClient(async_get_clientsession(hass), access_token)

    site_info = SolarEdgeSiteInfoCoordinator(hass, entry, client, site_id)
    await site_info.async_config_entry_first_refresh()

    status = site_info.data.status
    if status is not None and status.lower() != "active":
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="site_not_active"
        )

    options = dict(entry.options)
    measurements = SolarEdgeMeasurementsCoordinator(
        hass, entry, client, site_id, site_info
    )
    LOGGER.debug(
        "Site %s: measurements every %s, estimated %s credits per month",
        site_id,
        measurements.update_interval,
        measurements.estimated_credits_per_month,
    )
    await measurements.async_config_entry_first_refresh()

    power_flow = None
    if options.get(CONF_ADVANCED_POWER_FLOW):
        power_flow = SolarEdgePowerFlowCoordinator(hass, entry, client, site_id)
        # Not fatal: the endpoint needs a Business Pro or Enterprise tier
        await power_flow.async_refresh()

    entry.runtime_data = SolarEdgeData(
        client=client,
        site_info=site_info,
        measurements=measurements,
        power_flow=power_flow,
    )

    # Register the site device up front so per-battery devices can resolve it
    # as their via_device when their entities are created.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, site_id)},
        manufacturer="SolarEdge",
        name=site_info.data.name,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(hass: HomeAssistant, entry: SolarEdgeConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
