"""Constants for the SolarEdge (JMA) integration."""

from datetime import timedelta
import logging
from typing import Final

DOMAIN = "solaredge_jma"

LOGGER = logging.getLogger(__package__)

# Config entry data
CONF_SITE_ID = "site_id"

# Options
CONF_CREDIT_BUDGET: Final = "credit_budget"
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_ADVANCED_POWER_FLOW: Final = "advanced_power_flow"
CONF_DAYLIGHT_ONLY: Final = "daylight_only"
CONF_SUNRISE_ENTITY: Final = "sunrise_entity"
CONF_SUNSET_ENTITY: Final = "sunset_entity"

# SolarEdge Monitoring API v2 / SolarEdge Connect (OAuth 2.0)
API_BASE_URL: Final = "https://monitoringapi.solaredge.com/v2"
OAUTH2_AUTHORIZE_URL: Final = "https://connect.solaredge.com/authorize"
OAUTH2_TOKEN_URL: Final = f"{API_BASE_URL}/oauth2/token"
OAUTH2_SCOPES: Final = ("SITE_DATA", "DEVICE_DATA")
# Maximum grant duration in months
OAUTH2_ACCESS_DURATION: Final = 24
DEVELOPER_CONSOLE_URL: Final = "https://developer.solaredge.com"
# Own OAuth callback: SolarEdge Connect does not return the "state" parameter,
# which Home Assistant's /auth/external/callback requires.
OAUTH2_CALLBACK_PATH: Final = f"/api/{DOMAIN}/oauth/callback"

# Credits (1 credit per successful API call)
DEFAULT_CREDIT_BUDGET: Final = 2000  # Free tier
MIN_CREDIT_BUDGET: Final = 100
# Keep a safety margin so the monthly budget is never fully used
CREDIT_BUDGET_USAGE: Final = 0.9

# Update intervals
MIN_UPDATE_INTERVAL: Final = timedelta(minutes=15)  # API minimum resolution
MAX_UPDATE_INTERVAL: Final = timedelta(hours=24)
SITE_INFO_UPDATE_INTERVAL: Final = timedelta(hours=24)
LIFETIME_ENERGY_UPDATE_INTERVAL: Final = timedelta(hours=6)
POWER_FLOW_UPDATE_INTERVAL: Final = timedelta(minutes=5)
# Added to the daylight duration when the update interval is calculated for the
# "daylight only" option: days get longer during a month and the last update
# after sunset also costs credits.
DAYLIGHT_MARGIN: Final = timedelta(hours=1)

# Credits per day used by calls that are not part of the measurement cycle
SITE_INFO_CALLS_PER_DAY: Final = 2  # site details + device list
LIFETIME_CALLS_PER_DAY: Final = 4
POWER_FLOW_CALLS_PER_DAY: Final = 24 * 60 // 5

# Repair issues
ISSUE_CREDIT_LIMIT: Final = "credit_limit_reached"
ISSUE_POWER_FLOW_TIER: Final = "power_flow_tier"
