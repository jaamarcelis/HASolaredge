"""Tests for the "daylight only" option of SolarEdge (JMA)."""

from datetime import time, timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

from custom_components.solaredge_jma.const import (
    CONF_DAYLIGHT_ONLY,
    CONF_SUNRISE_ENTITY,
    CONF_SUNSET_ENTITY,
    DOMAIN,
)
from custom_components.solaredge_jma.coordinator import (
    SiteInfo,
    calculate_update_interval,
    daylight_hours,
    daylight_window,
    entity_time_of_day,
    estimate_credits_per_month,
    is_daylight,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import SITE_ID, mock_api, token_data

SUNRISE_ENTITY = "input_datetime.sunrise"
SUNSET_ENTITY = "sensor.sun_next_setting"
DAYLIGHT_OPTIONS = {
    CONF_DAYLIGHT_ONLY: True,
    CONF_SUNRISE_ENTITY: SUNRISE_ENTITY,
    CONF_SUNSET_ENTITY: SUNSET_ENTITY,
}
# Tokens stay valid while the tests move the time forward
FAR_FUTURE = 4_000_000_000
BATTERY_SITE = SiteInfo(devices=[{"serialNumber": "BAT-1", "type": "BATTERY"}])


@pytest.fixture(autouse=True)
async def setup_time(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Use a fixed local time: 5 November 2026, 12:00 in Amsterdam."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to("2026-11-05T11:00:00+00:00")


def _set_sun_entities(hass: HomeAssistant) -> None:
    """Sunrise 08:00 (time only helper), sunset 17:00 local (timestamp sensor)."""
    hass.states.async_set(SUNRISE_ENTITY, "08:00:00")
    hass.states.async_set(SUNSET_ENTITY, "2026-11-05T16:00:00+00:00")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (time(7, 59), False),
        (time(8, 0), True),
        (time(12, 0), True),
        (time(17, 0), False),
        (time(23, 0), False),
    ],
)
def test_is_daylight(now: time, expected: bool) -> None:
    """Test the daylight window."""
    assert is_daylight(now, time(8, 0), time(17, 0)) is expected


def test_is_daylight_invalid_window() -> None:
    """Test an invalid window counts as daylight."""
    assert is_daylight(time(3, 0), time(17, 0), time(8, 0))


async def test_entity_time_of_day(hass: HomeAssistant) -> None:
    """Test reading the time of day from different entity types."""
    hass.states.async_set("sensor.sun_next_rising", "2026-11-06T06:45:00+00:00")
    hass.states.async_set("input_datetime.time_only", "07:30:00")
    hass.states.async_set("input_datetime.date_time", "2026-11-05 18:15:00")
    hass.states.async_set("sensor.unavailable", "unavailable")
    hass.states.async_set("sensor.invalid", "not a time")

    # Timestamp in UTC is converted to local time (CET)
    assert entity_time_of_day(hass, "sensor.sun_next_rising") == time(7, 45)
    assert entity_time_of_day(hass, "input_datetime.time_only") == time(7, 30)
    assert entity_time_of_day(hass, "input_datetime.date_time") == time(18, 15)
    assert entity_time_of_day(hass, "sensor.unavailable") is None
    assert entity_time_of_day(hass, "sensor.invalid") is None
    assert entity_time_of_day(hass, "sensor.missing") is None
    assert entity_time_of_day(hass, None) is None


async def test_daylight_window(hass: HomeAssistant) -> None:
    """Test the window is only returned when the option is on and valid."""
    _set_sun_entities(hass)
    assert daylight_window(hass, DAYLIGHT_OPTIONS) == (time(8, 0), time(17, 0))
    assert (
        daylight_window(hass, {**DAYLIGHT_OPTIONS, CONF_DAYLIGHT_ONLY: False}) is None
    )
    hass.states.async_set(SUNSET_ENTITY, "unknown")
    assert daylight_window(hass, DAYLIGHT_OPTIONS) is None


def test_daylight_interval() -> None:
    """Test the saved night credits shorten the interval during the day."""
    # 9 hours of daylight + 1 hour margin
    hours = daylight_hours((time(8, 0), time(17, 0)))
    assert hours == 10
    assert daylight_hours(None) == 24

    full_day = calculate_update_interval({}, BATTERY_SITE)
    daylight = calculate_update_interval({}, BATTERY_SITE, hours)
    assert full_day == timedelta(minutes=80)
    assert daylight == timedelta(minutes=33)
    # Stays within the Free tier budget
    assert estimate_credits_per_month({}, BATTERY_SITE, daylight, hours) <= 2000


@pytest.mark.usefixtures("setup_credentials")
async def test_no_updates_at_night(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test no API calls are made between sunset and sunrise."""
    _set_sun_entities(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SITE_ID,
        data={
            "auth_implementation": DOMAIN,
            "token": token_data(expires_at=FAR_FUTURE),
            "site_id": SITE_ID,
        },
        options=DAYLIGHT_OPTIONS,
    )
    mock_api(aioclient_mock)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    measurements = entry.runtime_data.measurements
    assert measurements.update_interval == timedelta(minutes=33)
    data = measurements.data
    assert data

    async def advance_to(moment: str) -> int:
        calls = aioclient_mock.call_count
        freezer.move_to(moment)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        return aioclient_mock.call_count - calls

    # During the day: updated
    assert await advance_to("2026-11-05T15:30:00+00:00") > 0
    # First update after sunset (17:00 local): one last update
    assert await advance_to("2026-11-05T16:30:00+00:00") > 0
    # Later in the evening and at night: skipped, data is kept
    assert await advance_to("2026-11-05T19:00:00+00:00") == 0
    assert await advance_to("2026-11-06T03:00:00+00:00") == 0
    assert measurements.last_update_success
    assert measurements.data
    # After sunrise: updated again
    assert await advance_to("2026-11-06T07:30:00+00:00") > 0


@pytest.mark.usefixtures("setup_credentials")
async def test_unavailable_entity_keeps_updating(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test updates continue when the sunrise or sunset is not known."""
    hass.states.async_set(SUNRISE_ENTITY, "unavailable")
    hass.states.async_set(SUNSET_ENTITY, "unavailable")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SITE_ID,
        data={
            "auth_implementation": DOMAIN,
            "token": token_data(expires_at=FAR_FUTURE),
            "site_id": SITE_ID,
        },
        options=DAYLIGHT_OPTIONS,
    )
    mock_api(aioclient_mock)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Without a window the full day budget is used
    assert entry.runtime_data.measurements.update_interval == timedelta(minutes=80)

    calls = aioclient_mock.call_count
    freezer.move_to("2026-11-05T23:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count > calls
