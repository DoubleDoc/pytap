"""Tests for estimating silent optimizers from the rest of their string,
the estimated energy in string/total sensors, the data validity sensor and
the estimate option."""

from datetime import datetime, timedelta
import time
from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap.const import (
    CONF_ESTIMATE_SILENT,
    CONF_INVERTER,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_PEAK_POWER,
    CONF_MODULE_STRING,
    CONF_MODULES,
    DEFAULT_PORT,
    DOMAIN,
    NIGHT_QUIET_SECONDS,
    SILENT_UNAVAILABLE_SECONDS,
    VALIDITY_ASLEEP,
    VALIDITY_ESTIMATED,
    VALIDITY_HOLDING,
    VALIDITY_REPORTING,
    VALIDITY_SILENT,
    VALIDITY_SILENT_ESTIMATED,
)
from custom_components.pytap.coordinator import PyTapDataUpdateCoordinator
from custom_components.pytap.energy import (
    EnergyAccumulator,
    accumulate_energy,
    add_estimated_energy,
)
from custom_components.pytap.pytap.core.events import PowerReportEvent
from custom_components.pytap.sensor import (
    SENSOR_DESCRIPTIONS,
    STRING_SENSOR_DESCRIPTIONS,
    PyTapAggregateSensor,
    PyTapDataValiditySensor,
    PyTapSensor,
)

A, B, C = "A-1234567B", "B-2345678C", "C-3456789D"
MODULES = [
    {
        CONF_MODULE_STRING: "2",
        CONF_MODULE_NAME: name,
        CONF_MODULE_BARCODE: barcode,
        CONF_MODULE_PEAK_POWER: 400,
    }
    for name, barcode in (("P01", A), ("P02", B), ("P03", C))
]


def _coordinator(hass: HomeAssistant, estimate: bool = True):
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.data = {
        CONF_HOST: "192.168.1.100",
        CONF_PORT: DEFAULT_PORT,
        CONF_INVERTER: "Master",
        CONF_MODULES: MODULES,
        CONF_ESTIMATE_SILENT: estimate,
    }
    coordinator = PyTapDataUpdateCoordinator(hass, entry)
    coordinator._schedule_save = MagicMock()
    # Daylight for hours, converter online.
    coordinator._sun_low = False
    coordinator._day_since = time.monotonic() - 4 * 3600
    return coordinator


def _report(coordinator, barcode: str, power: float) -> None:
    """Feed a power report (voltage_out 30 V, so current_out = power / 30)."""
    current_in = power / 32
    coordinator._handle_power_report(
        PowerReportEvent(
            gateway_id=1,
            node_id=1,
            barcode=barcode,
            voltage_in=32.0,
            voltage_out=30.0,
            current_in=current_in,
            temperature=20.0,
            dc_dc_duty_cycle=0.9,
            rssi=-60,
            timestamp=datetime.now(),
        )
    )
    # The listener publishes after each batch of reports (live mode).
    coordinator._publish()


def _quiet(coordinator, barcode: str, seconds: float) -> None:
    coordinator._last_report[barcode] = time.monotonic() - seconds


def _aggregate(hass, coordinator, key: str) -> PyTapAggregateSensor:
    description = next(d for d in STRING_SENSOR_DESCRIPTIONS if d.key == key)
    sensor = PyTapAggregateSensor(coordinator, description, [A, B, C], MagicMock(), "u")
    sensor.hass = hass
    sensor.async_write_ha_state = MagicMock()
    sensor._handle_coordinator_update()
    return sensor


def _learn_shares(coordinator) -> None:
    """P01 is shaded: half the power of the others."""
    _report(coordinator, A, 100.0)
    _report(coordinator, B, 200.0)
    _report(coordinator, C, 200.0)
    coordinator._update_estimates(time.monotonic())


def test_share_is_learned(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    assert coordinator.share(A) == pytest.approx(0.5, abs=0.01)
    assert coordinator.share(B) == pytest.approx(4 / 3, abs=0.01)
    assert coordinator.data_validity(A) == VALIDITY_REPORTING


def test_silent_optimizer_estimated_from_string(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    own_daily = coordinator._state["nodes"][A]["daily_energy_wh"]

    _quiet(coordinator, A, 200)
    _report(coordinator, B, 300.0)
    _report(coordinator, C, 300.0)
    assert coordinator._update_estimates(time.monotonic())

    assert coordinator.estimated_power(A) == pytest.approx(150.0, abs=1)
    assert coordinator.data_validity(A) == VALIDITY_ESTIMATED
    power = _aggregate(hass, coordinator, "power")
    assert power.native_value == pytest.approx(750.0, abs=1)
    assert power.extra_state_attributes["estimated_count"] == 1

    # Energy is added from 180 s back (gap limit), at 150 W: 7.5 Wh.
    est_today, est_total = coordinator.estimated_energy(A)
    assert est_today == pytest.approx(7.5, abs=0.2)
    assert est_total == pytest.approx(est_today)
    # The optimizer's own energy sensor shows only what it reported...
    description = next(d for d in SENSOR_DESCRIPTIONS if d.key == "daily_energy")
    own = PyTapSensor(coordinator, description, MODULES[0], MagicMock(), "Master")
    own.hass = hass
    own.async_write_ha_state = MagicMock()
    own._handle_coordinator_update()
    assert own.native_value == own_daily
    # ...while the string includes the estimate and says how much.
    daily = _aggregate(hass, coordinator, "daily_energy")
    measured = sum(coordinator._state["nodes"][b]["daily_energy_wh"] for b in (A, B, C))
    assert daily.native_value == pytest.approx(measured + est_today, abs=0.01)
    assert daily.extra_state_attributes["estimated_energy"] == pytest.approx(
        est_today, abs=0.1
    )


def test_asleep_at_dusk_even_while_string_estimates(hass: HomeAssistant) -> None:
    """At low sun a quiet optimizer is asleep, not estimated at ~0 W."""
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    coordinator._sun_low = True
    coordinator._day_since = None
    _report(coordinator, B, 0.5)
    _report(coordinator, C, 0.5)

    # Quiet for a few minutes: estimated from the still-awake string.
    _quiet(coordinator, A, 200)
    coordinator._update_estimates(time.monotonic())
    assert coordinator.estimated_power(A) is not None
    assert coordinator.data_validity(A) == VALIDITY_ESTIMATED

    # Quiet past NIGHT_QUIET_SECONDS: asleep, though half its string reports.
    _quiet(coordinator, A, NIGHT_QUIET_SECONDS + 30)
    coordinator._update_estimates(time.monotonic())
    assert coordinator.estimated_power(A) is not None
    assert coordinator.data_validity(A) == VALIDITY_ASLEEP


def test_late_waker_by_day_is_still_estimated(hass: HomeAssistant) -> None:
    """By day, an optimizer not heard from since dawn stays estimated."""
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    coordinator._day_since = time.monotonic() - 15 * 60
    _quiet(coordinator, A, 10 * 3600)
    _report(coordinator, B, 300.0)
    _report(coordinator, C, 300.0)
    coordinator._update_estimates(time.monotonic())
    assert coordinator.data_validity(A) == VALIDITY_ESTIMATED


def test_estimate_follows_string_down(hass: HomeAssistant) -> None:
    """A broken string shows on the others, so the estimate drops too."""
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    _quiet(coordinator, A, 200)
    _report(coordinator, B, 2.0)
    _report(coordinator, C, 2.0)
    coordinator._update_estimates(time.monotonic())
    assert coordinator.estimated_power(A) == pytest.approx(1.0, abs=0.1)


def test_no_estimate_without_enough_reporting_neighbours(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    _quiet(coordinator, A, 200)
    _quiet(coordinator, B, 200)
    _quiet(coordinator, C, 200)
    coordinator._update_estimates(time.monotonic())
    assert coordinator.estimated_power(A) is None
    assert coordinator.data_validity(A) == VALIDITY_HOLDING


def test_estimate_ends_when_optimizer_reports_again(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    _quiet(coordinator, A, 200)
    coordinator._update_estimates(time.monotonic())
    first = coordinator.estimated_energy(A)[0]
    # Pretend the estimate was last added up to 60 s ago.
    coordinator._est_cursor[A] -= 60

    _report(coordinator, A, 100.0)
    assert coordinator.data_validity(A) == VALIDITY_REPORTING
    assert coordinator.estimated_power(A) is None
    coordinator._update_estimates(time.monotonic())
    # The last 60 s at ~100 W were added: about 1.7 Wh more.
    assert coordinator.estimated_energy(A)[0] == pytest.approx(first + 100 / 60, abs=0.2)
    assert A not in coordinator._est_cursor


def test_estimating_off_holds_last_value(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, estimate=False)
    _learn_shares(coordinator)
    _quiet(coordinator, A, SILENT_UNAVAILABLE_SECONDS + 1)
    _report(coordinator, B, 300.0)
    _report(coordinator, C, 300.0)
    coordinator._update_estimates(time.monotonic())
    assert coordinator.estimated_power(A) is None
    assert coordinator.estimated_energy(A) == (0.0, 0.0)
    assert coordinator.data_validity(A) == VALIDITY_SILENT
    # The string holds P01's last reported 100 W.
    assert _aggregate(hass, coordinator, "power").native_value == pytest.approx(700.0, abs=0.1)


def test_estimating_on_silent_without_estimate_is_left_out(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    long = SILENT_UNAVAILABLE_SECONDS + 1
    _quiet(coordinator, A, long)
    _quiet(coordinator, B, long)
    coordinator._update_estimates(time.monotonic())
    # Each needs 1 of its 2 neighbours reporting: both are estimated from C.
    assert coordinator.estimated_power(A) is not None
    _quiet(coordinator, C, long)
    coordinator._update_estimates(time.monotonic())
    assert coordinator.estimated_power(A) is None
    assert not _aggregate(hass, coordinator, "power").available


def test_long_dropout_is_silent_but_still_estimated(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    _quiet(coordinator, A, SILENT_UNAVAILABLE_SECONDS + 1)
    coordinator._update_estimates(time.monotonic())
    assert coordinator.estimated_power(A) is not None
    assert coordinator.data_validity(A) == VALIDITY_SILENT_ESTIMATED


def test_data_validity_sensor(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    _quiet(coordinator, A, 200)
    coordinator._update_estimates(time.monotonic())
    sensor = PyTapDataValiditySensor(coordinator, MODULES[0], "Master")
    assert sensor.unique_id == f"{DOMAIN}_{A}_data_validity"
    assert sensor.device_info["name"] == "Tigo Master S2 P01"
    assert sensor.native_value == VALIDITY_ESTIMATED
    attrs = sensor.extra_state_attributes
    assert attrs["share"] == pytest.approx(0.5, abs=0.01)
    assert attrs["estimated_energy_today"] > 0
    assert attrs["last_report"] is not None
    # While reporting, no last_report: it would change with every reading.
    _report(coordinator, A, 100.0)
    assert sensor.native_value == VALIDITY_REPORTING
    assert "last_report" not in sensor.extra_state_attributes


def test_estimated_energy_resets_daily() -> None:
    acc = EnergyAccumulator(daily_reset_date="2026-11-17")
    add_estimated_energy(acc, 5.0, "2026-11-17")
    add_estimated_energy(acc, 0.0, "2026-11-17")
    assert (acc.estimated_daily_wh, acc.estimated_total_wh) == (5.0, 5.0)
    accumulate_energy(acc, 10.0, datetime(2026, 11, 18, 8, 0))
    assert acc.estimated_daily_wh == 0.0
    assert acc.estimated_total_wh == 5.0


async def test_options_estimate_checkbox(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=6,
        data={
            CONF_HOST: "192.168.1.100",
            CONF_PORT: DEFAULT_PORT,
            CONF_INVERTER: "",
            CONF_MODULES: [dict(m) for m in MODULES],
        },
        unique_id="pytap_estimate_test",
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "change_reporting"}
    )
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys[CONF_ESTIMATE_SILENT].default() is True

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"live_reporting": False, CONF_ESTIMATE_SILENT: False}
    )
    assert result["step_id"] == "set_interval"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"write_interval": 30}
    )
    assert result["type"] is FlowResultType.MENU
    assert entry.data[CONF_ESTIMATE_SILENT] is False
    assert entry.data["write_interval"] == 30


def _gap(coordinator, barcode: str, seconds: float) -> None:
    """Pretend the optimizer's last reading was `seconds` ago."""
    acc = coordinator._energy_state[barcode]
    acc.last_reading_ts = acc.last_reading_ts - timedelta(seconds=seconds)


@pytest.mark.parametrize(
    ("gap", "estimate", "expected_wh"),
    [
        (150, True, 100 * 150 / 3600),  # bridged before estimating began
        (150, False, 0.0),  # estimating off: measured only
        (400, True, 0.0),  # long gap: the string estimate covers it
    ],
)
def test_short_gap_is_bridged(
    hass: HomeAssistant, gap: float, estimate: bool, expected_wh: float
) -> None:
    coordinator = _coordinator(hass, estimate=estimate)
    _learn_shares(coordinator)
    measured = coordinator._state["nodes"][A]["daily_energy_wh"]
    _gap(coordinator, A, gap)
    _report(coordinator, A, 100.0)
    assert coordinator.estimated_energy(A)[0] == pytest.approx(expected_wh, abs=0.05)
    # The optimizer's own energy never includes it.
    assert coordinator._state["nodes"][A]["daily_energy_wh"] == measured


def test_short_gap_not_counted_twice_when_estimating(hass: HomeAssistant) -> None:
    """If the health check already estimated the gap, it isn't bridged too."""
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    _quiet(coordinator, A, 150)
    coordinator._update_estimates(time.monotonic())
    first = coordinator.estimated_energy(A)[0]
    assert first > 0
    _gap(coordinator, A, 150)
    _report(coordinator, A, 100.0)
    assert coordinator.estimated_energy(A)[0] == pytest.approx(first)


def _restart(coordinator, barcode: str, gap: float, quiet_before_stop: float) -> None:
    """Pretend Home Assistant restarted: the optimizer's last reading was `gap`
    seconds ago, `quiet_before_stop` seconds before PyTap last saved."""
    _gap(coordinator, barcode, gap)
    acc = coordinator._energy_state[barcode]
    coordinator._stopped_at = acc.last_reading_ts + timedelta(seconds=quiet_before_stop)
    coordinator._seen_since_start.clear()


@pytest.mark.parametrize(
    ("gap", "quiet_before_stop", "estimate", "expected_wh"),
    [
        (500, 30, True, 100 * 500 / 3600),  # restart gap bridged
        (590, 30, True, 100 * 590 / 3600),  # up to 10 minutes
        (700, 30, True, 0.0),  # longer: dropped
        (500, 30, False, 0.0),  # estimating off: measured only
        (500, 300, True, 0.0),  # already silent (estimated) before the stop
    ],
)
def test_restart_gap_is_bridged(
    hass: HomeAssistant,
    gap: float,
    quiet_before_stop: float,
    estimate: bool,
    expected_wh: float,
) -> None:
    coordinator = _coordinator(hass, estimate=estimate)
    _learn_shares(coordinator)
    measured = coordinator._state["nodes"][A]["daily_energy_wh"]
    _restart(coordinator, A, gap, quiet_before_stop)
    _report(coordinator, A, 100.0)
    assert coordinator.estimated_energy(A)[0] == pytest.approx(expected_wh, abs=0.05)
    assert coordinator._state["nodes"][A]["daily_energy_wh"] == measured


def test_restart_gap_only_for_first_reading_after_start(hass: HomeAssistant) -> None:
    """A long gap later in the run is not a restart gap."""
    coordinator = _coordinator(hass)
    _learn_shares(coordinator)
    _restart(coordinator, A, 60, 30)
    _report(coordinator, A, 100.0)  # first reading after the restart
    before = coordinator.estimated_energy(A)[0]
    acc = coordinator._energy_state[A]
    coordinator._stopped_at = acc.last_reading_ts
    _gap(coordinator, A, 500)
    _report(coordinator, A, 100.0)
    assert coordinator.estimated_energy(A)[0] == pytest.approx(before)


async def test_saved_at_round_trip(hass: HomeAssistant) -> None:
    """The save time is stored and read back as the restart's stop time."""
    coordinator = _coordinator(hass)
    coordinator._store.async_save = AsyncMock()
    await coordinator._async_save_coordinator_state()
    saved = coordinator._store.async_save.call_args[0][0]
    assert "saved_at" in saved

    restarted = _coordinator(hass)
    restarted._store.async_load = AsyncMock(return_value=saved)
    await restarted._async_load_coordinator_state()
    assert restarted._stopped_at == datetime.fromisoformat(saved["saved_at"])
