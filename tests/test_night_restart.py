"""Reading health across a Home Assistant restart at night.

Quiet time used to count from the restart, so after a night restart every
optimizer held its last values for NIGHT_QUIET_SECONDS before reading as
asleep. At night the last report time saved before the restart now carries
over; by day it doesn't (the restart counts as when daylight began).
"""

from datetime import timedelta
import time
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.pytap.const import (
    NIGHT_QUIET_SECONDS,
    READING_ASLEEP,
    READING_OK,
    VALIDITY_ASLEEP,
)
from tests.test_reading_health import A, C, _coordinator, _sensor


def _saved(coordinator, **minutes_ago) -> None:
    """Saved last-report times, as loaded from the Store."""
    now = dt_util.now()
    for key, minutes in minutes_ago.items():
        coordinator._saved_report_times[{"a": A, "c": C}[key]] = now - timedelta(
            minutes=minutes
        )


def _restart(coordinator, *, sun_low: bool) -> None:
    coordinator._sun_low = sun_low
    coordinator._day_since = None if sun_low else time.monotonic()
    coordinator._resume_report_times()


async def test_night_restart_reads_asleep_at_once(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _saved(coordinator, a=120, c=5)
    _restart(coordinator, sun_low=True)

    # Quiet for two hours: asleep immediately, live readings 0.
    assert coordinator.reading_state(A) == READING_ASLEEP
    assert coordinator.data_validity(A) == VALIDITY_ASLEEP
    assert _sensor(hass, coordinator, "power").native_value == 0.0
    # Quiet for five minutes (dusk): not asleep until 10 minutes have passed.
    assert coordinator.reading_state(C) == READING_OK
    coordinator._last_report[C] -= NIGHT_QUIET_SECONDS  # time passes
    assert coordinator.reading_state(C) == READING_ASLEEP


async def test_day_restart_is_unchanged(hass: HomeAssistant) -> None:
    """By day the saved times aren't used: no 'asleep' blip until reports arrive."""
    coordinator = _coordinator(hass)
    _saved(coordinator, a=30, c=30)
    _restart(coordinator, sun_low=False)
    assert A not in coordinator._last_report
    assert coordinator.reading_state(A) == READING_OK


async def test_a_report_after_start_wins(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    coordinator._last_report[A] = time.monotonic()  # already reported
    _saved(coordinator, a=120)
    _restart(coordinator, sun_low=True)
    assert coordinator.reading_state(A) == READING_OK


async def test_a_clock_that_went_backwards_is_ignored(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _saved(coordinator, a=-60)  # "saved" in the future
    _restart(coordinator, sun_low=True)
    assert A not in coordinator._last_report


async def test_saved_times_are_loaded_from_snapshots(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    reported = dt_util.now() - timedelta(hours=2)
    coordinator._store = MagicMock()

    async def _load():
        return {
            "node_snapshots": {
                A: {"power": 0.0, "last_update": reported.isoformat()},
                C: {"power": 0.0, "last_update": "not a time"},
            }
        }

    coordinator._store.async_load = _load
    await coordinator._async_load_coordinator_state()
    assert coordinator._saved_report_times == {A: reported}


async def test_start_works_out_the_sun_before_resuming(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _saved(coordinator, a=120)

    def night() -> None:
        coordinator._sun_low = True
        coordinator._day_since = None

    with (
        patch.object(coordinator, "_async_load_coordinator_state", return_value=None),
        patch.object(coordinator, "_update_sun", side_effect=night),
        patch.object(coordinator, "_async_listen", MagicMock()),
    ):
        await coordinator.async_start_listener()
    try:
        assert coordinator.reading_state(A) == READING_ASLEEP
    finally:
        coordinator._cancel_lifecycle_timers()


async def test_estimates_never_start_before_pytap_did(hass: HomeAssistant) -> None:
    """A resumed report time mustn't make an estimate cover the downtime."""
    coordinator = _coordinator(hass)
    now = time.monotonic()
    coordinator._started_mono = now - 30
    coordinator._last_report[A] = now - 900  # resumed: quiet since before the restart
    coordinator._share[A] = 1.0
    with patch.object(coordinator, "_is_fresh", side_effect=lambda b, _now: b != A):
        coordinator._estimate_enabled = True
        coordinator._sun_low = False
        coordinator._day_since = now - 3600
        nodes = coordinator._state["nodes"]
        nodes[A] = {**coordinator.data["nodes"][A]}
        nodes[C] = {**coordinator.data["nodes"][C], "power": 200.0}
        coordinator._update_estimates(now)
    assert A in coordinator._est_cursor  # an estimate did start
    estimated_today, _ = coordinator.estimated_energy(A)
    # The 30 s since start at about 200 W (1.7 Wh), not 180 s of backfill (10 Wh).
    assert 0 < estimated_today <= 200.0 * 31 / 3600
