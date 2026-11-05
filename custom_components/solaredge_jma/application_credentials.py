"""Application credentials platform for SolarEdge (JMA).

SolarEdge Connect uses the OAuth 2.0 authorization code flow, but its token
endpoint expects a JSON body that always contains the client id and secret
(also when refreshing). Home Assistant's default implementation sends form
data, so the token request is overridden here.
"""

from typing import Any, override

from aiohttp import ClientError

from homeassistant.components.application_credentials import (
    AuthImplementation,
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow, oauth2
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DEVELOPER_CONSOLE_URL,
    LOGGER,
    OAUTH2_ACCESS_DURATION,
    OAUTH2_AUTHORIZE_URL,
    OAUTH2_SCOPES,
    OAUTH2_TOKEN_URL,
)


class SolarEdgeOAuth2Implementation(AuthImplementation):
    """SolarEdge Connect OAuth 2.0 implementation."""

    @property
    @override
    def extra_authorize_data(self) -> dict[str, Any]:
        """Extra data appended to the authorize URL."""
        return {
            "scope": " ".join(OAUTH2_SCOPES),
            "access_duration": str(OAUTH2_ACCESS_DURATION),
        }

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
    return {
        "developer_url": DEVELOPER_CONSOLE_URL,
        "redirect_url": config_entry_oauth2_flow.MY_AUTH_CALLBACK_PATH,
        "scopes": ", ".join(OAUTH2_SCOPES),
    }
