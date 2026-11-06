"""Tests for the SolarEdge (JMA) config flow and OAuth implementation."""

from http import HTTPStatus
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.solaredge_jma.application_credentials import (
    async_get_description_placeholders,
)
from custom_components.solaredge_jma.const import (
    CONF_ADVANCED_POWER_FLOW,
    CONF_CREDIT_BUDGET,
    CONF_DAYLIGHT_ONLY,
    CONF_SUNRISE_ENTITY,
    CONF_SUNSET_ENTITY,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    OAUTH2_AUTHORIZE_URL,
    OAUTH2_TOKEN_URL,
)
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.network import NoURLAvailableError

from .conftest import (
    CLIENT_ID,
    CLIENT_SECRET,
    REDIRECT_URI,
    SITE_ID,
    SITE_RESPONSE,
    SITE_URL,
    mock_api,
)

TOKEN_RESPONSE = {
    "access_token": "new-access-token",
    "token_type": "Bearer",
    "expires_in": 7200,
    "refresh_token": "new-refresh-token",
}


async def _authorize(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    result: dict,
    approved_site_id: str = SITE_ID,
) -> str:
    """Follow the external step; SolarEdge redirects without "state"."""
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    url = urlparse(result["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == OAUTH2_AUTHORIZE_URL
    query = parse_qs(url.query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["scope"] == ["SITE_DATA DEVICE_DATA"]
    assert query["access_duration"] == ["24"]
    state = query["state"][0]

    client = await hass_client_no_auth()
    response = await client.get(
        f"/api/solaredge_jma/oauth/callback?code=abcd&site_id={approved_site_id}"
    )
    assert response.status == HTTPStatus.OK
    return state


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_full_flow(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test site ID, OAuth, site validation and entry creation."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"site_id": "not-a-number"}
    )
    assert result["errors"] == {"site_id": "invalid_site_id"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"site_id": SITE_ID}
    )
    await _authorize(hass, hass_client_no_auth, result)

    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN_RESPONSE)
    mock_api(aioclient_mock)

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["result"].unique_id == SITE_ID
    data = result["data"]
    assert data["site_id"] == SITE_ID
    assert data["auth_implementation"] == DOMAIN
    assert data["token"]["access_token"] == "new-access-token"

    # Token request is JSON with the client credentials
    token_calls = [call for call in aioclient_mock.mock_calls if call[0] == "POST"]
    assert len(token_calls) == 1
    body = token_calls[0][2]
    assert isinstance(body, dict)
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "abcd"
    assert body["client_id"] == CLIENT_ID
    assert body["client_secret"] == CLIENT_SECRET
    assert body["redirect_uri"] == REDIRECT_URI

    # API calls use the bearer token
    site_calls = [
        call
        for call in aioclient_mock.mock_calls
        if call[0] == "GET" and str(call[1]) == SITE_URL
    ]
    assert site_calls[0][3]["Authorization"] == "Bearer new-access-token"


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_site_not_authorized(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the user approved a different site on the consent screen."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"site_id": SITE_ID}
    )
    await _authorize(hass, hass_client_no_auth, result)
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN_RESPONSE)
    aioclient_mock.get(
        SITE_URL,
        status=HTTPStatus.FORBIDDEN,
        json={"title": "Forbidden", "detail": "Site not granted"},
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "site_not_authorized"


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test a site can only be configured once."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"site_id": SITE_ID}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_migrate_v1_entry_via_reauth(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test an API v1 entry (API key) starts reauth and gets an OAuth token."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="solaredge",
        data={"site_id": SITE_ID, "api_key": "old-v1-key"},
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    result = flows[0]
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await _authorize(hass, hass_client_no_auth, result)
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN_RESPONSE)
    mock_api(aioclient_mock)

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert "api_key" not in entry.data
    assert entry.data["token"]["access_token"] == "new-access-token"
    assert entry.data["auth_implementation"] == DOMAIN
    assert entry.unique_id == SITE_ID
    assert entry.state is ConfigEntryState.LOADED


async def test_token_refresh_is_json(
    hass: HomeAssistant,
    setup_credentials: None,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the refresh request is JSON and contains the client secret."""
    implementation = await config_entry_oauth2_flow.async_get_implementations(
        hass, DOMAIN
    )
    impl = implementation[DOMAIN]
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN_RESPONSE)

    token = await impl.async_refresh_token(
        {"access_token": "old", "refresh_token": "old-refresh", "expires_in": 0}
    )

    assert token["access_token"] == "new-access-token"
    assert token["refresh_token"] == "new-refresh-token"
    assert "expires_at" in token
    body = aioclient_mock.mock_calls[0][2]
    assert body == {
        "grant_type": "refresh_token",
        "refresh_token": "old-refresh",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }


@pytest.mark.usefixtures("setup_credentials")
async def test_options_flow(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the options flow shows the estimate and reloads the entry."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    # Free tier with a battery: 3 calls per cycle
    assert result["description_placeholders"]["interval"] == "80"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CREDIT_BUDGET: 20000,
            CONF_UPDATE_INTERVAL: 0,
            CONF_ADVANCED_POWER_FLOW: False,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_CREDIT_BUDGET] == 20000
    interval = config_entry.runtime_data.measurements.update_interval
    assert interval.total_seconds() == 15 * 60
    assert SITE_RESPONSE["name"] == config_entry.title


@pytest.mark.usefixtures("setup_credentials")
async def test_options_flow_daylight_only(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the daylight option requires a sunrise and a sunset entity."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    hass.states.async_set("sensor.sun_next_rising", "2026-11-06T06:45:00+00:00")
    hass.states.async_set("sensor.sun_next_setting", "2026-11-06T16:00:00+00:00")
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CREDIT_BUDGET: 2000,
            CONF_UPDATE_INTERVAL: 0,
            CONF_ADVANCED_POWER_FLOW: False,
            CONF_DAYLIGHT_ONLY: True,
            CONF_SUNRISE_ENTITY: "sensor.sun_next_rising",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "daylight_entities_required"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CREDIT_BUDGET: 2000,
            CONF_UPDATE_INTERVAL: 0,
            CONF_ADVANCED_POWER_FLOW: False,
            CONF_DAYLIGHT_ONLY: True,
            CONF_SUNRISE_ENTITY: "sensor.sun_next_rising",
            CONF_SUNSET_ENTITY: "sensor.sun_next_setting",
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_DAYLIGHT_ONLY] is True
    assert config_entry.options[CONF_SUNSET_ENTITY] == "sensor.sun_next_setting"
    # Fewer active hours: updates more often than the 80 minutes for 24 hours
    interval = config_entry.runtime_data.measurements.update_interval
    assert interval.total_seconds() < 80 * 60

    # The estimate in the form follows the daylight hours
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert int(result["description_placeholders"]["interval"]) < 80


@pytest.mark.usefixtures("setup_credentials")
async def test_credentials_dialog_shows_local_callback(hass: HomeAssistant) -> None:
    """Test the credentials dialog shows the redirect URI that is used."""
    placeholders = await async_get_description_placeholders(hass)
    assert placeholders["redirect_url"] == REDIRECT_URI

    hass.config.external_url = None
    hass.config.internal_url = None
    with patch(
        "custom_components.solaredge_jma.application_credentials.get_url",
        side_effect=NoURLAvailableError,
    ):
        placeholders = await async_get_description_placeholders(hass)
    assert placeholders["redirect_url"] == (
        "<your Home Assistant URL>/api/solaredge_jma/oauth/callback"
    )


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_site_mismatch(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the user approved another site than the one entered."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"site_id": SITE_ID}
    )
    await _authorize(hass, hass_client_no_auth, result, approved_site_id="999")
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN_RESPONSE)

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "site_mismatch"
    assert result["description_placeholders"] == {
        "site_id": SITE_ID,
        "approved_site_id": "999",
    }


@pytest.mark.usefixtures("current_request_with_host", "setup_credentials")
async def test_callback_without_waiting_flow(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """Test the callback is rejected when no flow waits for authorization."""
    # Starting a flow up to the external step registers the view
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"site_id": SITE_ID}
    )
    hass.config_entries.flow.async_abort(result["flow_id"])

    client = await hass_client_no_auth()
    response = await client.get("/api/solaredge_jma/oauth/callback?code=abcd")
    assert response.status == HTTPStatus.BAD_REQUEST
    response = await client.get("/api/solaredge_jma/oauth/callback")
    assert response.status == HTTPStatus.BAD_REQUEST
