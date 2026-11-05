"""Fixtures for the SolarEdge (JMA) tests."""

import time
from typing import Any

import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.solaredge_jma.const import API_BASE_URL, DOMAIN
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"
SITE_ID = "1234567"
SITE_URL = f"{API_BASE_URL}/sites/{SITE_ID}"

SITE_RESPONSE = {
    "siteId": int(SITE_ID),
    "name": "Home",
    "peakPower": 5.6,
    "installationDate": "2022-11-10",
    "location": {"city": "Utrecht", "country": "Netherlands"},
    "activationStatus": "Active",
}

DEVICES_RESPONSE = {
    "devices": [
        {"serialNumber": "INV-1", "type": "INVERTER", "name": "Inverter 1"},
        {"serialNumber": "MTR-1", "type": "METER", "name": "Meter", "parent": None},
        {"serialNumber": "BAT-1", "type": "BATTERY", "name": "Battery 1"},
    ]
}

ENERGY_RESPONSE = {
    "period": {"from": "2026-01-01T00:00:00+01:00", "to": "2026-11-05T12:00:00+01:00"},
    "unit": "Wh",
    "resolution": "DAY",
    "values": [
        {"date": "2026-10-31T00:00:00+01:00", "value": 4000},
        {"date": "2026-11-04T00:00:00+01:00", "value": 6000},
        {"date": "2026-11-05T00:00:00+01:00", "value": 1500},
    ],
}

LIFETIME_RESPONSE = {"energy": 12_500_000, "unit": "Wh"}

METERS_RESPONSE = {
    "meters": [
        {
            "serialNumber": "MTR-1",
            "values": [
                {
                    "date": "2026-11-05T11:30:00+01:00",
                    "productionPower": 2000,
                    "consumptionPower": 800,
                    "importPower": 0,
                    "exportPower": 1200,
                    "productionEnergy": 500,
                    "consumptionEnergy": 200,
                    "importEnergy": 0,
                    "exportEnergy": 300,
                },
                {
                    "date": "2026-11-05T11:45:00+01:00",
                    "productionPower": 2400,
                    "consumptionPower": 3000,
                    "importPower": 600,
                    "exportPower": 0,
                    "productionEnergy": 600,
                    "consumptionEnergy": 750,
                    "importEnergy": 150,
                    "exportEnergy": 0,
                },
            ],
        }
    ]
}

STORAGE_RESPONSE = {
    "batteries": [
        {
            "serialNumber": "BAT-1",
            "values": [
                {
                    "date": "2026-11-05T11:30:00+01:00",
                    "chargePower": 1000,
                    "dischargePower": 0,
                    "chargeEnergy": 250,
                    "dischargeEnergy": 0,
                    "stateOfEnergy": 40,
                },
                {
                    "date": "2026-11-05T11:45:00+01:00",
                    "chargePower": 0,
                    "dischargePower": 500,
                    "chargeEnergy": 0,
                    "dischargeEnergy": 125,
                    "stateOfEnergy": 45,
                },
            ],
        }
    ]
}


@pytest.fixture
async def setup_credentials(hass: HomeAssistant) -> None:
    """Set up the application credentials."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential(CLIENT_ID, CLIENT_SECRET), DOMAIN
    )


def token_data(expires_at: float | None = None) -> dict[str, Any]:
    """Return stored token data."""
    return {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_in": 7200,
        "expires_at": expires_at if expires_at is not None else time.time() + 7200,
    }


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return an OAuth config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        unique_id=SITE_ID,
        data={
            "auth_implementation": DOMAIN,
            "token": token_data(),
            "site_id": SITE_ID,
        },
    )


def mock_api(aioclient_mock: AiohttpClientMocker) -> None:
    """Mock all API endpoints used by the integration."""
    aioclient_mock.get(SITE_URL, json=SITE_RESPONSE)
    aioclient_mock.get(f"{SITE_URL}/devices", json=DEVICES_RESPONSE)
    aioclient_mock.get(f"{SITE_URL}/energy", json=ENERGY_RESPONSE)
    aioclient_mock.get(f"{SITE_URL}/lifetime-energy", json=LIFETIME_RESPONSE)
    aioclient_mock.get(f"{SITE_URL}/meters/telemetry", json=METERS_RESPONSE)
    aioclient_mock.get(f"{SITE_URL}/storage/telemetry", json=STORAGE_RESPONSE)
