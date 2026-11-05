"""Application credentials platform for SolarEdge (JMA).

SolarEdge Connect uses the OAuth 2.0 authorization code flow, with two deviations
from what Home Assistant's OAuth helpers expect:

- The token endpoint expects a JSON body that always contains the client id and
  secret (also when refreshing), not form data. The token request is overridden.
- The redirect after authorization does not return the "state" parameter (only
  code, site_id and external_id), so Home Assistant's /auth/external/callback
  rejects it. This integration therefore has its own callback view, which resumes
  the SolarEdge config flow that is waiting for the authorization.
"""

from http import HTTPStatus
from typing import Any, override

from aiohttp import ClientError, web

from homeassistant.components.application_credentials import (
    AuthImplementation,
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_entry_oauth2_flow, oauth2
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import KEY_HASS, HomeAssistantView
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util.hass_dict import HassKey

from .const import (
    DEVELOPER_CONSOLE_URL,
    DOMAIN,
    LOGGER,
    OAUTH2_ACCESS_DURATION,
    OAUTH2_AUTHORIZE_URL,
    OAUTH2_CALLBACK_PATH,
    OAUTH2_SCOPES,
    OAUTH2_TOKEN_URL,
)

# Query parameter with the site the user approved on the consent screen
CALLBACK_SITE_ID = "site_id"
# Step of AbstractOAuth2FlowHandler that waits for the external authorization
AUTH_STEP_ID = "auth"

DATA_CALLBACK_VIEW_REGISTERED: HassKey[bool] = HassKey(f"{DOMAIN}_callback_view")

CLOSE_WINDOW_HTML = "<script>window.close()</script>"


@callback
def async_get_local_redirect_uri(hass: HomeAssistant) -> str:
    """Return the OAuth callback URL of this integration.

    Uses the configured external URL, or the internal URL when there is none, so
    the URI is always the same and can be registered at SolarEdge.
    Raises NoURLAvailableError when no URL is configured.
    """
    base_url = get_url(hass, prefer_external=True)
    return f"{base_url}{OAUTH2_CALLBACK_PATH}"


@callback
def async_register_callback_view(hass: HomeAssistant) -> None:
    """Register the OAuth callback view once."""
    if hass.data.get(DATA_CALLBACK_VIEW_REGISTERED):
        return
    hass.http.register_view(SolarEdgeOAuth2CallbackView())
    hass.data[DATA_CALLBACK_VIEW_REGISTERED] = True


class SolarEdgeOAuth2CallbackView(HomeAssistantView):
    """Receive the SolarEdge Connect redirect and resume the config flow."""

    requires_auth = False
    url = OAUTH2_CALLBACK_PATH
    name = f"api:{DOMAIN}:oauth:callback"

    async def get(self, request: web.Request) -> web.Response:
        """Handle the redirect from SolarEdge Connect."""
        hass = request.app[KEY_HASS]
        query = request.query
        if "code" not in query and "error" not in query:
            return web.Response(
                text="Missing code or error parameter",
                status=HTTPStatus.BAD_REQUEST,
            )

        # SolarEdge does not return "state", so resume the flow that is waiting
        # for the authorization. Only one can be in progress at a time.
        flows = [
            flow
            for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            if flow.get("step_id") == AUTH_STEP_ID
        ]
        if len(flows) != 1:
            LOGGER.warning(
                "Received a SolarEdge authorization, but %s setup flows are "
                "waiting for one",
                len(flows),
            )
            return web.Response(
                text=(
                    "No (or more than one) SolarEdge setup is waiting for this "
                    "authorization. Start the setup again in Home Assistant."
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
        flow_id = flows[0]["flow_id"]

        try:
            redirect_uri = async_get_local_redirect_uri(hass)
        except NoURLAvailableError:
            return web.Response(
                text="No Home Assistant URL configured",
                status=HTTPStatus.BAD_REQUEST,
            )

        user_input: dict[str, Any] = {
            # Same shape as the state of Home Assistant's own callback
            "state": {"flow_id": flow_id, "redirect_uri": redirect_uri},
        }
        if "code" in query:
            user_input["code"] = query["code"]
            if site_id := query.get(CALLBACK_SITE_ID):
                user_input[CALLBACK_SITE_ID] = site_id
        else:
            user_input["error"] = query.get("error_description") or query["error"]

        await hass.config_entries.flow.async_configure(
            flow_id=flow_id, user_input=user_input
        )
        LOGGER.debug("Resumed SolarEdge OAuth configuration flow")
        return web.Response(
            headers={"content-type": "text/html"},
            text=CLOSE_WINDOW_HTML,
        )


class SolarEdgeOAuth2Implementation(AuthImplementation):
    """SolarEdge Connect OAuth 2.0 implementation."""

    @property
    @override
    def redirect_uri(self) -> str:
        """Return the callback of this integration."""
        return async_get_local_redirect_uri(self.hass)

    @property
    @override
    def extra_authorize_data(self) -> dict[str, Any]:
        """Extra data appended to the authorize URL."""
        return {
            "scope": " ".join(OAUTH2_SCOPES),
            "access_duration": str(OAUTH2_ACCESS_DURATION),
        }

    @override
    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Generate the authorize URL, making sure the callback view exists."""
        async_register_callback_view(self.hass)
        return await super().async_generate_authorize_url(flow_id)

    @override
    async def _token_request(self, data: dict) -> dict:
        """Request a token with a JSON body containing the client credentials."""
        body = {
            **data,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        session = async_get_clientsession(self.hass)
        LOGGER.debug("Sending %s token request", body.get("grant_type"))
        try:
            async with session.post(self.token_url, json=body) as response:
                if response.status >= 400:
                    LOGGER.debug(
                        "Token request failed (%s): %s",
                        response.status,
                        await response.text(),
                    )
                response.raise_for_status()
                return dict(await response.json(content_type=None))
        except ClientError as err:
            # 4xx (invalid_grant, invalid_client, ...) starts a reauth flow,
            # 429 and 5xx are transient.
            oauth2.raise_mapped_token_error(err, self.service_domain)


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return the authorization server."""
    return AuthorizationServer(
        authorize_url=OAUTH2_AUTHORIZE_URL,
        token_url=OAUTH2_TOKEN_URL,
    )


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> config_entry_oauth2_flow.AbstractOAuth2Implementation:
    """Return the SolarEdge auth implementation."""
    return SolarEdgeOAuth2Implementation(
        hass,
        auth_domain,
        credential,
        AuthorizationServer(
            authorize_url=OAUTH2_AUTHORIZE_URL,
            token_url=OAUTH2_TOKEN_URL,
        ),
    )


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Return description placeholders for the credentials dialog."""
    try:
        redirect_url = async_get_local_redirect_uri(hass)
    except NoURLAvailableError:
        redirect_url = f"<your Home Assistant URL>{OAUTH2_CALLBACK_PATH}"
    return {
        "developer_url": DEVELOPER_CONSOLE_URL,
        "redirect_url": redirect_url,
        "scopes": ", ".join(OAUTH2_SCOPES),
    }
