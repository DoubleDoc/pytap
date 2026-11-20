"""Listener state is published to Home Assistant as numbered copies.

Adapted from Tim Laing's upstream PR azebro/pytap#33: Home Assistant only ever
sees published copies, an older queued snapshot can't undo a newer one,
averaged mode never exposes raw readings, entities skip other panels'
updates, and save requests are posted once per save cycle.
"""

import threading
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.pytap.coordinator import PyTapDataUpdateCoordinator
from custom_components.pytap.sensor import PyTapDataValiditySensor
from tests.test_write_interval import (
    MOCK_MODULES,
    _make_entry,
    _make_infra_event,
    _make_power_event,
)

A = "A-1234567B"


def _coordinator(hass, **kwargs) -> PyTapDataUpdateCoordinator:
    coordinator = PyTapDataUpdateCoordinator(hass, _make_entry(hass, **kwargs))
    coordinator._schedule_save = MagicMock()
    coordinator._handle_infrastructure(_make_infra_event())
    return coordinator


async def test_older_snapshot_cannot_undo_a_newer_one(hass: HomeAssistant) -> None:
    """A snapshot queued before the midnight reset is dropped when it arrives."""
    coordinator = _coordinator(hass, live_reporting=True)
    coordinator._process_event(_make_power_event(power=100.0))
    coordinator._state["nodes"][A]["daily_energy_wh"] = 42.0

    with patch.object(coordinator, "_dispatch_snapshot") as dispatch:
        coordinator._publish()  # built by the listener, still queued
    stale = dispatch.call_args.args

    # Meanwhile the event loop publishes, then the midnight reset patches it.
    coordinator._publish()
    coordinator._publish_patch({A: {"daily_energy_wh": 0.0}})
    assert coordinator.data["nodes"][A]["daily_energy_wh"] == 0.0

    coordinator._async_apply_snapshot(*stale)  # the stale one arrives late
    assert coordinator.data["nodes"][A]["daily_energy_wh"] == 0.0


async def test_averaged_mode_never_shows_raw_readings(hass: HomeAssistant) -> None:
    """Between flushes, Home Assistant keeps the last average."""
    coordinator = _coordinator(hass, live_reporting=False, write_interval=60)
    coordinator._reading_buffers.clear()
    coordinator._process_event(_make_power_event(power=100.0))
    coordinator._process_event(_make_power_event(power=100.0))
    coordinator._ha_update_pending = True
    coordinator._last_ha_update = 0.0
    parser = MagicMock()
    parser.counters = {}
    coordinator._maybe_flush_interval(parser, now=1000.0)
    assert coordinator.data["nodes"][A]["power"] == pytest.approx(100.0, abs=0.01)

    # A raw reading arrives; nothing is published until the next flush.
    coordinator._process_event(_make_power_event(power=900.0))
    assert coordinator._state["nodes"][A]["power"] == pytest.approx(900.0, abs=0.01)
    assert coordinator.data["nodes"][A]["power"] == pytest.approx(100.0, abs=0.01)


async def test_entities_skip_other_panels_updates(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, live_reporting=True)
    coordinator._publish()
    sensor = PyTapDataValiditySensor(coordinator, MOCK_MODULES[0], "Master")
    sensor.async_write_ha_state = MagicMock()

    coordinator._changed_barcodes = frozenset({"Z-9999999Z"})
    sensor._handle_coordinator_update()
    sensor.async_write_ha_state.assert_not_called()

    coordinator._changed_barcodes = frozenset({A})
    sensor._handle_coordinator_update()
    sensor.async_write_ha_state.assert_called_once()

    coordinator._changed_barcodes = None  # health check: everything
    sensor._handle_coordinator_update()
    assert sensor.async_write_ha_state.call_count == 2


async def test_save_requests_are_posted_once_per_cycle(hass: HomeAssistant) -> None:
    coordinator = PyTapDataUpdateCoordinator(hass, _make_entry(hass))
    with patch.object(hass.loop, "call_soon_threadsafe") as post:

        def listener() -> None:
            for _ in range(50):
                coordinator._schedule_save()

        thread = threading.Thread(target=listener)
        thread.start()
        thread.join()
    assert post.call_count == 1
    assert coordinator._unsaved_changes is True


async def test_midnight_reset_keeps_averaged_values(hass: HomeAssistant) -> None:
    """The reset patches the published data, so averages aren't replaced by raw."""
    coordinator = _coordinator(hass, live_reporting=False, write_interval=60)
    coordinator._process_event(_make_power_event(power=100.0))
    coordinator._publish()
    coordinator._async_apply_snapshot(
        coordinator._publish_seq + 1,
        {**coordinator.data, "nodes": {A: {**coordinator.data["nodes"][A], "power": 123.0}}},
        None,
    )
    coordinator._publish_seq += 1
    coordinator._state["nodes"][A]["power"] = 999.0  # raw latest
    coordinator._energy_state[A].daily_reset_date = "2000-01-01"

    coordinator._perform_midnight_reset()

    published = coordinator.data["nodes"][A]
    assert published["daily_energy_wh"] == 0.0
    assert published["power"] == 123.0
