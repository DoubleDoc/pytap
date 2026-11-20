"""Configurable stale-reading behaviour, adapted from Tim Laing's upstream PR
azebro/pytap#34 and fitted to the fork's sun-aware rules."""

import time
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap.const import (
    CONF_MODULES,
    CONF_NIGHT_ACTION,
    CONF_SILENT_ACTION,
    CONF_SILENT_MINUTES,
    DOMAIN,
    NIGHT_QUIET_SECONDS,
    READING_OK,
    READING_SILENT,
)
from tests.test_reading_health import MODULES, A, _coordinator, _sensor, _set, _total

DAY = 4 * 3600


def _with(coordinator, **settings):
    for key, value in settings.items():
        setattr(coordinator, key, value)
    return coordinator


def test_night_hold_keeps_last_values(hass: HomeAssistant) -> None:
    coordinator = _with(_coordinator(hass), night_action="hold")
    _set(coordinator, sun_low=True, a=NIGHT_QUIET_SECONDS + 1)
    power = _sensor(hass, coordinator, "power")
    assert power.available
    assert power.native_value == 200.0
    # Totals still count a sleeping panel as 0 W, not its held 200 W (no
    # phantom night production); the other panel is still awake at 180 W.
    assert _total(hass, coordinator, "power").native_value == 180.0


def test_silent_hold_keeps_panel_available(hass: HomeAssistant) -> None:
    coordinator = _with(_coordinator(hass), silent_action="hold")
    _set(coordinator, sun_low=False, day_for=DAY, a=31 * 60)
    assert coordinator.reading_state(A) == READING_SILENT
    power = _sensor(hass, coordinator, "power")
    assert power.available
    assert power.native_value == 200.0


def test_silent_zero_reads_zero_and_keeps_temperature(hass: HomeAssistant) -> None:
    coordinator = _with(_coordinator(hass), silent_action="zero")
    _set(coordinator, sun_low=False, day_for=DAY, a=31 * 60)
    for key in ("power", "voltage_in", "current_out", "performance"):
        sensor = _sensor(hass, coordinator, key)
        assert sensor.available, key
        assert sensor.native_value == 0.0, key
    assert _sensor(hass, coordinator, "temperature").native_value == 21.0


def test_silent_minutes_sets_when_a_panel_counts_as_silent(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    coordinator._silent_seconds = 10 * 60
    _set(coordinator, sun_low=False, day_for=DAY, a=11 * 60)
    assert coordinator.reading_state(A) == READING_SILENT
    coordinator._silent_seconds = 30 * 60
    assert coordinator.reading_state(A) == READING_OK


def test_settings_are_read_from_the_entry(hass: HomeAssistant) -> None:
    from custom_components.pytap.coordinator import PyTapDataUpdateCoordinator

    entry = MagicMock()
    entry.entry_id = "e"
    entry.options = {}
    entry.data = {
        "host": "192.168.1.100", "port": 502, CONF_MODULES: MODULES,
        CONF_NIGHT_ACTION: "hold", CONF_SILENT_ACTION: "zero", CONF_SILENT_MINUTES: 45,
    }
    coordinator = PyTapDataUpdateCoordinator(hass, entry)
    assert coordinator.night_action == "hold"
    assert coordinator.silent_action == "zero"
    assert coordinator._silent_seconds == 45 * 60


async def test_options_step_saves_settings(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=6,
        data={"host": "192.168.1.100", "port": 502, CONF_MODULES: MODULES},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "change_stale"}
    )
    assert result["step_id"] == "change_stale"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_NIGHT_ACTION: "hold", CONF_SILENT_ACTION: "hold", CONF_SILENT_MINUTES: 20},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert entry.data[CONF_NIGHT_ACTION] == "hold"
    assert entry.data[CONF_SILENT_ACTION] == "hold"
    assert entry.data[CONF_SILENT_MINUTES] == 20


def test_health_check_refreshes_only_changed_panels(hass: HomeAssistant) -> None:
    """A panel going quiet re-renders its own entities and totals, not others'."""
    coordinator = _coordinator(hass)
    coordinator._last_data_mono = time.monotonic()  # bus link up
    _set(coordinator, sun_low=False, day_for=DAY, a=1, c=1)
    coordinator._update_sun = MagicMock()
    coordinator._link_was_up = coordinator.link_up
    coordinator._async_health_check()  # baseline

    seen: list = []
    coordinator.async_update_listeners = lambda: seen.append(coordinator._changed_barcodes)
    _set(coordinator, sun_low=False, day_for=DAY, a=31 * 60, c=1)  # A goes silent
    coordinator._async_health_check()

    assert seen == [frozenset({A})]
