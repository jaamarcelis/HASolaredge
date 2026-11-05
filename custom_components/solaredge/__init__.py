"""The SolarEdge integration."""

import socket

from aiohttp import ClientError

from homeassistant.const import CONF_API_KEY, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SolarEdge
from .const import CONF_SITE_ID, DATA_API_CLIENT, DOMAIN, LOGGER
from .types import SolarEdgeConfigEntry, SolarEdgeData

PLATFORMS = [Platform.SENSOR]


async def async_migrate_entry(
    hass: HomeAssistant, entry: SolarEdgeConfigEntry
) -> None:
    """Migrate an old config entry."""
    if entry.version == 1 and entry.minor_version < 2:
        # 1.2: web account login (module statistics) is no longer supported,
        # remove the stored credentials.
        data = {
            key: value
            for key, value in entry.data.items()
            if key not in (CONF_USERNAME, CONF_PASSWORD)
        }
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
        LOGGER.debug("Migrated config entry %s to version 1.2", entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: SolarEdgeConfigEntry) -> None:
    """Set up SolarEdge from a config entry."""
    entry.runtime_data = SolarEdgeData()
    site_id = entry.data[CONF_SITE_ID]

    if CONF_API_KEY not in entry.data:
        # Entries that were created with only a web account login
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="api_key_missing",
        )

    session = async_get_clientsession(hass)
    api = SolarEdge(entry.data[CONF_API_KEY], session)

    try:
        response = await api.get_details(site_id)
    except (TimeoutError, ClientError, socket.gaierror) as ex:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_retrieve_details",
        ) from ex

    if "details" not in response:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="missing_details",
        )

    if response["details"].get("status", "").lower() != "active":
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="site_not_active",
        )

    entry.runtime_data[DATA_API_CLIENT] = api

    # Register the site device up front so per-battery devices can resolve it
    # as their via_device when their entities are created.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, site_id)},
        manufacturer="SolarEdge",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(hass: HomeAssistant, entry: SolarEdgeConfigEntry) -> None:
    """Unload SolarEdge config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
