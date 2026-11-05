"""Tests for setup, coordinators and sensors of SolarEdge (JMA)."""

from http import HTTPStatus

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.solaredge_jma.const import (
    CONF_ADVANCED_POWER_FLOW,
    DOMAIN,
    ISSUE_CREDIT_LIMIT,
    ISSUE_POWER_FLOW_TIER,
    OAUTH2_TOKEN_URL,
)
from custom_components.solaredge_jma.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .conftest import SITE_ID, SITE_URL, mock_api, token_data


@pytest.fixture(autouse=True)
async def setup_time(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Use a fixed local time on 5 November 2026."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to("2026-11-05T11:00:00+00:00")


async def _enable_all_and_reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Enable all disabled entities and reload."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.disabled_by is not None:
            registry.async_update_entity(entity.entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("setup_credentials")
async def test_setup_and_sensors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test setup and the parsed sensor values."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    await _enable_all_and_reload(hass, config_entry)

    registry = er.async_get(hass)

    def value(key: str) -> str:
        unique_id = SITE_ID if key == "site_details" else f"{SITE_ID}_{key}"
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        assert entity_id, key
        sensor_state = hass.states.get(entity_id)
        assert sensor_state, entity_id
        return sensor_state.state

    # Energy (DAY resolution, year to date)
    assert value("energy_today") == "1500.0"
    assert value("energy_this_month") == "7500.0"
    assert value("energy_this_year") == "11500.0"
    assert value("lifetime_energy") == "12500000.0"

    # Meter telemetry (today, quarter hours)
    assert value("production_energy") == "1100.0"
    assert value("consumption_energy") == "950.0"
    assert value("purchased_energy") == "150.0"
    assert value("feedin_energy") == "300.0"
    assert value("selfconsumption_energy") == "800.0"
    assert value("current_power") == "2400.0"
    assert value("solar_power") == "2400.0"
    assert value("power_consumption") == "3000.0"
    assert value("grid_power") == "600.0"
    assert value("grid_flow_direction") == "import"

    # Storage telemetry
    assert value("storage_level") == "45.0"
    assert value("storage_power") == "500.0"
    assert value("storage_flow_direction") == "discharge"
    assert value("storage_charge_energy") == "250.0"
    assert value("storage_discharge_energy") == "125.0"
    assert value("BAT-1_battery_state_of_charge") == "45.0"
    assert value("BAT-1_battery_power") == "-500.0"

    # Site info
    assert value("site_details") == "Active"
    assert value("inverters") == "1"
    assert value("meters") == "1"
    assert value("batteries") == "1"
    assert value("gateways") == "0"

    # Free tier, with a battery: measurements every 80 minutes
    assert config_entry.runtime_data.update_interval.total_seconds() == 80 * 60


@pytest.mark.usefixtures("setup_credentials")
async def test_expired_token_is_refreshed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test an expired access token is refreshed before calling the API."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SITE_ID,
        data={
            "auth_implementation": DOMAIN,
            "token": token_data(expires_at=0),
            "site_id": SITE_ID,
        },
    )
    aioclient_mock.post(
        OAUTH2_TOKEN_URL,
        json={
            "access_token": "refreshed",
            "refresh_token": "refreshed-refresh",
            "expires_in": 7200,
            "token_type": "Bearer",
        },
    )
    mock_api(aioclient_mock)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.data["token"]["access_token"] == "refreshed"
    assert entry.data["token"]["refresh_token"] == "refreshed-refresh"
    get_calls = [call for call in aioclient_mock.mock_calls if call[0] == "GET"]
    assert all(call[3]["Authorization"] == "Bearer refreshed" for call in get_calls)


@pytest.mark.usefixtures("setup_credentials")
async def test_refresh_rejected_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a rejected refresh token starts a reauth flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SITE_ID,
        data={
            "auth_implementation": DOMAIN,
            "token": token_data(expires_at=0),
            "site_id": SITE_ID,
        },
    )
    aioclient_mock.post(
        OAUTH2_TOKEN_URL,
        status=HTTPStatus.BAD_REQUEST,
        json={"error": "invalid_grant", "error_description": "expired"},
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


@pytest.mark.usefixtures("setup_credentials")
async def test_credit_limit_creates_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the monthly credit limit raises a repair issue."""
    aioclient_mock.get(
        SITE_URL,
        status=HTTPStatus.TOO_MANY_REQUESTS,
        json={"message": "Monthly credit limit reached."},
        headers={"x-ratelimit-remaining-minute": "8"},
    )
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_CREDIT_LIMIT)


@pytest.mark.usefixtures("setup_credentials")
async def test_power_flow_tier_insufficient(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test the Advanced power flow on a too low tier raises an issue."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SITE_ID,
        data={
            "auth_implementation": DOMAIN,
            "token": token_data(),
            "site_id": SITE_ID,
        },
        options={CONF_ADVANCED_POWER_FLOW: True},
    )
    mock_api(aioclient_mock)
    aioclient_mock.get(
        f"{SITE_URL}/power-flow",
        status=HTTPStatus.FORBIDDEN,
        json={"title": "Forbidden", "detail": "tier_insufficient"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_POWER_FLOW_TIER)
    assert entry.runtime_data.power_flow is not None
    assert entry.runtime_data.power_flow.update_interval is None


@pytest.mark.usefixtures("setup_credentials")
async def test_diagnostics(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test diagnostics contain the raw responses without secrets."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["entry"]["data"]["token"] == "**REDACTED**"
    assert diagnostics["estimated_credits_per_month"] <= 2000
    responses = diagnostics["api_responses"]
    assert set(responses) >= {
        "site",
        "devices",
        "energy",
        "lifetime",
        "meters_telemetry",
        "storage_telemetry",
    }
    assert responses["site"]["name"] == "**REDACTED**"
    assert responses["site"]["location"] == "**REDACTED**"
    assert responses["devices"]["devices"][0]["serialNumber"] == "**REDACTED**"
    # Device names are kept: they may be needed to verify the parsing
    assert responses["devices"]["devices"][0]["name"] == "Inverter 1"
    assert "BAT-1" not in str(diagnostics)
