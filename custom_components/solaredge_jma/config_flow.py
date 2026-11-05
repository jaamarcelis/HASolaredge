"""Config flow for SolarEdge (JMA): OAuth 2.0 via SolarEdge Connect."""

from collections.abc import Mapping
import logging
from typing import Any, override

from aiohttp import ClientError
import probatio

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import (
    SolarEdgeApiError,
    SolarEdgeAuthError,
    SolarEdgeClient,
    SolarEdgeForbiddenError,
)
from .const import (
    CONF_ADVANCED_POWER_FLOW,
    CONF_CREDIT_BUDGET,
    CONF_SITE_ID,
    CONF_UPDATE_INTERVAL,
    DEFAULT_CREDIT_BUDGET,
    DOMAIN,
    MIN_CREDIT_BUDGET,
    MIN_UPDATE_INTERVAL,
)
from .coordinator import (
    SiteInfo,
    calculate_update_interval,
    estimate_credits_per_month,
)
from .parsing import unwrap_site


class SolarEdgeOAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle SolarEdge OAuth 2.0 authentication."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._site_id: str | None = None

    @property
    @override
    def logger(self) -> logging.Logger:
        """Return the logger."""
        return logging.getLogger(__name__)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return SolarEdgeOptionsFlow()

    def _site_configured(self, site_id: str) -> bool:
        """Return True if the site is already configured."""
        return any(
            str(entry.data.get(CONF_SITE_ID)) == site_id
            for entry in self._async_current_entries(include_ignore=False)
        )

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the site ID, then start the OAuth flow."""
        errors: dict[str, str] = {}
        if user_input is not None:
            site_id = str(user_input[CONF_SITE_ID]).strip()
            if not site_id.isdigit():
                errors[CONF_SITE_ID] = "invalid_site_id"
            else:
                await self.async_set_unique_id(site_id)
                self._abort_if_unique_id_configured()
                if self._site_configured(site_id):
                    return self.async_abort(reason="already_configured")
                self._site_id = site_id
                return await self.async_step_pick_implementation()

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema({probatio.Required(CONF_SITE_ID): str}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication (expired grant or migration from API v1)."""
        self._site_id = str(entry_data[CONF_SITE_ID])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders={CONF_SITE_ID: self._site_id or ""},
            )
        return await self.async_step_pick_implementation()

    @override
    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Verify the authorized site and create or update the entry."""
        site_id = self._site_id
        assert site_id is not None
        access_token: str = data["token"]["access_token"]

        async def token_provider() -> str:
            return access_token

        client = SolarEdgeClient(async_get_clientsession(self.hass), token_provider)
        try:
            site = unwrap_site(await client.get_site(site_id))
        except (SolarEdgeAuthError, SolarEdgeForbiddenError) as err:
            self.logger.warning("Site %s is not authorized: %s", site_id, err)
            return self.async_abort(
                reason="site_not_authorized",
                description_placeholders={CONF_SITE_ID: site_id},
            )
        except (SolarEdgeApiError, ClientError, TimeoutError) as err:
            self.logger.error("Error validating site %s: %s", site_id, err)
            return self.async_abort(reason="cannot_connect")

        status = str(site.get("activationStatus") or site.get("status") or "")
        if status and status.lower() != "active":
            return self.async_abort(reason="site_not_active")

        entry_data = {**data, CONF_SITE_ID: site_id}

        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if str(entry.data.get(CONF_SITE_ID)) != site_id:
                return self.async_abort(reason="wrong_site")
            # Drops the API v1 key of migrated entries
            return self.async_update_reload_and_abort(
                entry,
                unique_id=site_id,
                data={
                    **{k: v for k, v in entry.data.items() if k != CONF_API_KEY},
                    **entry_data,
                },
            )

        return self.async_create_entry(
            title=str(site.get("name") or f"SolarEdge {site_id}"),
            data=entry_data,
        )


class SolarEdgeOptionsFlow(OptionsFlowWithReload):
    """Options: credit budget, update interval and Advanced power flow.

    The entry is reloaded when the options change.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            options = {
                CONF_CREDIT_BUDGET: int(user_input[CONF_CREDIT_BUDGET]),
                CONF_UPDATE_INTERVAL: int(user_input.get(CONF_UPDATE_INTERVAL) or 0),
                CONF_ADVANCED_POWER_FLOW: user_input[CONF_ADVANCED_POWER_FLOW],
            }
            return self.async_create_entry(data=options)

        options = self.config_entry.options
        site_info = SiteInfo()
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is not None and runtime.site_info.data is not None:
            site_info = runtime.site_info.data
        interval = calculate_update_interval(dict(options), site_info)
        credits = estimate_credits_per_month(dict(options), site_info, interval)

        min_minutes = int(MIN_UPDATE_INTERVAL.total_seconds() // 60)
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_CREDIT_BUDGET,
                    default=options.get(CONF_CREDIT_BUDGET, DEFAULT_CREDIT_BUDGET),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_CREDIT_BUDGET,
                        max=10_000_000,
                        step=100,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                probatio.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=options.get(CONF_UPDATE_INTERVAL, 0),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=24 * 60,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                probatio.Required(
                    CONF_ADVANCED_POWER_FLOW,
                    default=options.get(CONF_ADVANCED_POWER_FLOW, False),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "interval": str(int(interval.total_seconds() // 60)),
                "credits": str(credits),
                "min_interval": str(min_minutes),
            },
        )
