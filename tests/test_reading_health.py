"""Tests for reading health (asleep / silent / offline), log prefixes,
connection-outage logging and the RS-485 error-rate warning."""

from datetime import datetime
import logging
import time
from unittest.mock import MagicMock, patch

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
import pytest

from custom_components.pytap import coordinator as coord_mod
from custom_components.pytap.const import (
    BUS_CONVERTER_UNREACHABLE,
    BUS_ERROR_WINDOW_SECONDS,
    BUS_NORMAL,
    BUS_QUIET,
    CONF_INVERTER,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_PEAK_POWER,
    CONF_MODULE_STRING,
    CONF_MODULES,
    DEFAULT_PORT,
    LINK_DOWN_SECONDS,
    NIGHT_QUIET_SECONDS,
    READING_ASLEEP,
    READING_OFFLINE,
    READING_OK,
    READING_SILENT,
    SILENT_UNAVAILABLE_SECONDS,
)
from custom_components.pytap.coordinator import PyTapDataUpdateCoordinator
from custom_components.pytap.pytap.core.events import PowerReportEvent
from custom_components.pytap.sensor import (
    INSTALLATION_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    PyTapAggregateSensor,
    PyTapBusStatusSensor,
    PyTapDataValiditySensor,
    PyTapSensor,
)

A = "A-1234567B"
C = "C-2345678D"
MODULES = [
    {
        CONF_MODULE_STRING: "2",
        CONF_MODULE_NAME: "P01",
        CONF_MODULE_BARCODE: A,
        CONF_MODULE_PEAK_POWER: 400,
    },
    {
        CONF_MODULE_STRING: "2",
        CONF_MODULE_NAME: "P02",
        CONF_MODULE_BARCODE: C,
        CONF_MODULE_PEAK_POWER: 400,
    },
]
LOGGER = "custom_components.pytap.coordinator"


def _node(barcode: str, power: float) -> dict:
    return {
        "barcode": barcode,
        "name": barcode,
        "peak_power": 400,
        "power": power,
        "performance": power / 4,
        "voltage_in": 30.0,
        "voltage_out": 29.0,
        "current_in": power / 30,
        "current_out": power / 29,
        "temperature": 21.0,
        "dc_dc_duty_cycle": 0.9,
        "rssi": -60,
        "daily_energy_wh": 500.0,
        "total_energy_wh": 9000.0,
        "readings_today": 100,
        "daily_reset_date": "",
    }


def _coordinator(hass: HomeAssistant, inverter: str = "Master"):
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.data = {
        CONF_HOST: "192.168.1.100",
        CONF_PORT: DEFAULT_PORT,
        CONF_INVERTER: inverter,
        CONF_MODULES: MODULES,
    }
    coordinator = PyTapDataUpdateCoordinator(hass, entry)
    coordinator.data["nodes"] = {A: _node(A, 200.0), C: _node(C, 180.0)}
    return coordinator


def _set(coordinator, *, sun_low: bool, day_for: float | None = None, **ago):
    """Set the sun state and how long ago each module last reported."""
    now = time.monotonic()
    coordinator._sun_low = sun_low
    coordinator._day_since = None if sun_low else now - (day_for or 0)
    for barcode, seconds in ago.items():
        coordinator._last_report[{"a": A, "c": C}[barcode]] = now - seconds


def _sensor(hass, coordinator, key: str, barcode: str = A) -> PyTapSensor:
    description = next(d for d in SENSOR_DESCRIPTIONS if d.key == key)
    module = next(m for m in MODULES if m[CONF_MODULE_BARCODE] == barcode)
    sensor = PyTapSensor(coordinator, description, module, MagicMock(), "Master")
    sensor.hass = hass
    sensor.async_write_ha_state = MagicMock()
    sensor._handle_coordinator_update()
    return sensor


def _total(hass, coordinator, key: str) -> PyTapAggregateSensor:
    description = next(d for d in INSTALLATION_SENSOR_DESCRIPTIONS if d.key == key)
    sensor = PyTapAggregateSensor(coordinator, description, [A, C], MagicMock(), "u")
    sensor.hass = hass
    sensor.async_write_ha_state = MagicMock()
    sensor._handle_coordinator_update()
    return sensor


# ---- reading_state ----


def test_reporting_is_ok_day_and_night(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _set(coordinator, sun_low=False, day_for=3600, a=5)
    assert coordinator.reading_state(A) == READING_OK
    _set(coordinator, sun_low=True, a=5)
    assert coordinator.reading_state(A) == READING_OK


def test_quiet_at_low_sun_is_asleep(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _set(coordinator, sun_low=True, a=NIGHT_QUIET_SECONDS - 30)
    assert coordinator.reading_state(A) == READING_OK
    _set(coordinator, sun_low=True, a=NIGHT_QUIET_SECONDS + 30)
    assert coordinator.reading_state(A) == READING_ASLEEP


def test_daytime_dropout_holds_value_until_threshold(hass: HomeAssistant) -> None:
    """A radio dropout by day keeps the last value for a long time."""
    coordinator = _coordinator(hass)
    _set(coordinator, sun_low=False, day_for=4 * 3600, a=20 * 60)
    assert coordinator.reading_state(A) == READING_OK
    _set(coordinator, sun_low=False, day_for=4 * 3600, a=SILENT_UNAVAILABLE_SECONDS + 1)
    assert coordinator.reading_state(A) == READING_SILENT


def test_morning_stays_asleep_until_first_report(hass: HomeAssistant) -> None:
    """Overnight silence doesn't count against daylight; zeros stay."""
    coordinator = _coordinator(hass)
    _set(coordinator, sun_low=False, day_for=10 * 60, a=14 * 3600)
    assert coordinator.reading_state(A) == READING_ASLEEP
    _set(coordinator, sun_low=False, day_for=SILENT_UNAVAILABLE_SECONDS + 1, a=14 * 3600)
    assert coordinator.reading_state(A) == READING_SILENT


def test_no_bytes_is_offline(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _set(coordinator, sun_low=False, day_for=3600, a=5)
    coordinator._last_data_mono = time.monotonic() - LINK_DOWN_SECONDS - 1
    assert not coordinator.link_up
    assert coordinator.reading_state(A) == READING_OFFLINE
    assert coordinator.bus_status == BUS_CONVERTER_UNREACHABLE
    coordinator._outage_cause = BUS_QUIET
    assert coordinator.bus_status == BUS_QUIET
    assert coordinator.data_validity(A) is None
    validity = PyTapDataValiditySensor(coordinator, MODULES[0], "Master")
    assert not validity.available
    coordinator._last_data_mono = time.monotonic()
    assert coordinator.bus_status == BUS_NORMAL
    assert validity.available


def test_unknown_location_never_counts_as_night(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    with patch.object(coord_mod, "solar_elevation", side_effect=ValueError):
        coordinator._update_sun()
    assert coordinator._sun_low is False
    assert coordinator._day_since is not None


# ---- optimizer sensors ----


def test_asleep_zeroes_production_but_keeps_temperature(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _set(coordinator, sun_low=True, a=NIGHT_QUIET_SECONDS + 1)
    for key in ("power", "voltage_in", "current_out", "performance"):
        sensor = _sensor(hass, coordinator, key)
        assert sensor.available
        assert sensor.native_value == 0.0, key
    assert _sensor(hass, coordinator, "temperature").native_value == 21.0
    assert _sensor(hass, coordinator, "daily_energy").native_value == 500.0


def test_silent_makes_live_readings_unavailable(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _set(coordinator, sun_low=False, day_for=4 * 3600, a=SILENT_UNAVAILABLE_SECONDS + 1)
    assert not _sensor(hass, coordinator, "power").available
    assert not _sensor(hass, coordinator, "temperature").available
    assert _sensor(hass, coordinator, "total_energy").available
    assert _sensor(hass, coordinator, "readings_today").available
    assert coordinator.silent_modules() == ["P01"]


def test_offline_keeps_energy_available(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    coordinator._last_data_mono = time.monotonic() - LINK_DOWN_SECONDS - 1
    assert not _sensor(hass, coordinator, "power").available
    assert _sensor(hass, coordinator, "daily_energy").available


# ---- aggregates ----


def test_total_power_skips_silent_and_counts_asleep_as_zero(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    _set(
        coordinator,
        sun_low=False,
        day_for=4 * 3600,
        a=SILENT_UNAVAILABLE_SECONDS + 1,
        c=5,
    )
    total = _total(hass, coordinator, "power")
    assert total.available
    assert total.native_value == 180.0
    assert total.extra_state_attributes["reporting_count"] == 1
    # Performance only counts the reporting optimizer's peak power.
    assert _total(hass, coordinator, "performance").native_value == 45.0

    _set(coordinator, sun_low=True, a=NIGHT_QUIET_SECONDS + 1, c=NIGHT_QUIET_SECONDS + 1)
    total = _total(hass, coordinator, "power")
    assert total.native_value == 0.0
    # Asleep optimizers aren't reporting.
    assert total.extra_state_attributes["reporting_count"] == 0

    # Quiet for a few minutes by day (holding) isn't reporting either.
    _set(coordinator, sun_low=False, day_for=4 * 3600, a=300, c=5)
    assert _total(hass, coordinator, "power").extra_state_attributes[
        "reporting_count"
    ] == 1


def test_total_power_unavailable_when_all_silent_or_offline(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    long = SILENT_UNAVAILABLE_SECONDS + 1
    _set(coordinator, sun_low=False, day_for=4 * 3600, a=long, c=long)
    assert not _total(hass, coordinator, "power").available
    assert _total(hass, coordinator, "daily_energy").available

    _set(coordinator, sun_low=False, day_for=4 * 3600, a=5, c=5)
    coordinator._last_data_mono = time.monotonic() - LINK_DOWN_SECONDS - 1
    assert not _total(hass, coordinator, "power").available


# ---- health check logging ----


def test_health_check_warns_once_per_silent_optimizer(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    coordinator = _coordinator(hass)
    coordinator.async_update_listeners = MagicMock()
    with patch.object(coordinator, "_update_sun"):
        _set(coordinator, sun_low=False, day_for=4 * 3600, a=5, c=5)
        coordinator._async_health_check()
        _set(
            coordinator,
            sun_low=False,
            day_for=4 * 3600,
            a=SILENT_UNAVAILABLE_SECONDS + 1,
            c=5,
        )
        with caplog.at_level(logging.INFO, logger=LOGGER):
            coordinator._async_health_check()
            coordinator._async_health_check()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "[Master] Optimizer P01 (A-1234567B) has not reported" in (
            warnings[0].getMessage()
        )
        coordinator.async_update_listeners.assert_called()

        caplog.clear()
        _set(coordinator, sun_low=False, day_for=4 * 3600, a=5, c=5)
        with caplog.at_level(logging.INFO, logger=LOGGER):
            coordinator._async_health_check()
        assert "Optimizer P01 (A-1234567B) is reporting again" in caplog.text


def test_health_check_single_warning_when_whole_gateway_silent(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    coordinator = _coordinator(hass)
    coordinator.async_update_listeners = MagicMock()
    long = SILENT_UNAVAILABLE_SECONDS + 1
    with patch.object(coordinator, "_update_sun"):
        _set(coordinator, sun_low=False, day_for=4 * 3600, a=long, c=long)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            coordinator._async_health_check()
    assert len(caplog.records) == 1
    assert "No optimizer has reported" in caplog.text


def test_health_check_logs_link_down_and_back(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    coordinator = _coordinator(hass)
    coordinator.async_update_listeners = MagicMock()
    with patch.object(coordinator, "_update_sun"):
        coordinator._last_data_mono = time.monotonic() - LINK_DOWN_SECONDS - 1
        with caplog.at_level(logging.INFO, logger=LOGGER):
            coordinator._async_health_check()
            coordinator._async_health_check()
            coordinator._last_data_mono = time.monotonic()
            coordinator._async_health_check()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Converter unreachable" in warnings[0].getMessage()
    assert "Live readings are unavailable" in warnings[0].getMessage()
    assert "live readings restored" in caplog.text


# ---- RS-485 error rate ----


def test_bus_error_warning(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    coordinator = _coordinator(hass)
    parser = MagicMock()
    coordinator._parser = parser
    now = 1000.0
    parser.counters = {"frames_received": 0, "crc_errors": 0, "runts": 0, "giants": 0}
    coordinator._check_bus_errors(now)
    parser.counters = {"frames_received": 1000, "crc_errors": 2, "runts": 0, "giants": 0}
    with caplog.at_level(logging.INFO, logger=LOGGER):
        coordinator._check_bus_errors(now + BUS_ERROR_WINDOW_SECONDS)
    assert not caplog.records  # 0.2 % is fine

    parser.counters = {"frames_received": 2000, "crc_errors": 102, "runts": 0, "giants": 0}
    with caplog.at_level(logging.INFO, logger=LOGGER):
        coordinator._check_bus_errors(now + 2 * BUS_ERROR_WINDOW_SECONDS)
    assert "frames (9.1%) failed their checks" in caplog.text

    caplog.clear()
    parser.counters = {"frames_received": 3000, "crc_errors": 102, "runts": 0, "giants": 0}
    with caplog.at_level(logging.INFO, logger=LOGGER):
        coordinator._check_bus_errors(now + 3 * BUS_ERROR_WINDOW_SECONDS)
    assert "back to normal" in caplog.text


# ---- listener outage logging ----


class _FakeSource:
    def __init__(self, reads):
        self._reads = list(reads)

    def read(self, _size):
        item = self._reads.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item()
        return item

    def close(self):
        pass


def _run_listener(coordinator, connect_results, max_sleeps=200):
    """Run _listen with connect() returning/raising the given results."""
    results = list(connect_results)

    def fake_connect(_config):
        if not results:
            coordinator._stop_event.set()
            raise OSError("stopped")
        item = results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > max_sleeps:
            coordinator._stop_event.set()

    with (
        patch("custom_components.pytap.pytap.api.connect", fake_connect),
        patch.object(coord_mod.time, "sleep", fake_sleep),
    ):
        coordinator._listen()
    return sleeps


def test_outage_warns_once_and_backs_off(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    coordinator = _coordinator(hass)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        sleeps = _run_listener(coordinator, [OSError("refused")] * 6, max_sleeps=5000)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "[Master] Connection to the RS-485 converter" in warnings[0].getMessage()
    # Back-off: 5, 10, 20, 40, 60, 60 s (slept in 0.1 s steps)
    assert sum(sleeps) == pytest.approx(5 + 10 + 20 + 40 + 60 + 60, abs=1)


def test_unexpected_error_logs_traceback_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    coordinator = _coordinator(hass)
    sources = [_FakeSource([ValueError("parser bug")]) for _ in range(3)]
    with caplog.at_level(logging.INFO, logger=LOGGER):
        _run_listener(coordinator, sources)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert "Unexpected error" in errors[0].getMessage()


def test_recovery_is_logged(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = _coordinator(hass)
    monkeypatch.setattr(coord_mod, "STABLE_CONNECTION_SECONDS", 0)

    def stop_and_close():
        coordinator._stop_event.set()
        raise OSError("closed")

    source = _FakeSource([b"\x00\x00", stop_and_close])
    with caplog.at_level(logging.INFO, logger=LOGGER):
        _run_listener(coordinator, [OSError("refused"), source])
    assert "Receiving data again" in caplog.text


# ---- pending node table reminder ----


def test_pending_reminder_is_rate_limited(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    coordinator = _coordinator(hass)
    report = PowerReportEvent(
        gateway_id=1,
        node_id=5,
        barcode=None,
        voltage_in=30.0,
        voltage_out=29.0,
        current_in=8.0,
        temperature=40.0,
        dc_dc_duty_cycle=0.9,
        rssi=-60,
        timestamp=datetime.now(),
    )
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(500):
            coordinator._handle_power_report(report)
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1  # first report only; reminders every 10 min


def test_connected_but_no_bytes_is_bus_quiet(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The converter accepts the connection but forwards nothing."""
    coordinator = _coordinator(hass)
    monkeypatch.setattr(coord_mod, "RECONNECT_TIMEOUT", 0.001)

    def quiet():
        time_ns = time.monotonic() + 0.002
        while time.monotonic() < time_ns:
            pass
        return b""

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _run_listener(coordinator, [_FakeSource([quiet, quiet, quiet])])
    assert coordinator._outage_cause == BUS_QUIET
    coordinator._last_data_mono = time.monotonic() - LINK_DOWN_SECONDS - 1
    assert coordinator.bus_status == BUS_QUIET
    with patch.object(coordinator, "_update_sun"), caplog.at_level(
        logging.WARNING, logger=LOGGER
    ):
        coordinator.async_update_listeners = MagicMock()
        coordinator._async_health_check()
    assert "Bus quiet" in caplog.text
    assert "Tigo CCA is powered" in caplog.text


def test_bus_status_sensor(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    sensor = PyTapBusStatusSensor(coordinator, MagicMock(), "u")
    assert sensor.native_value == BUS_NORMAL
    assert sensor.extra_state_attributes == {}
    coordinator._last_data_mono = time.monotonic() - LINK_DOWN_SECONDS - 1
    assert sensor.native_value == BUS_CONVERTER_UNREACHABLE
    assert sensor.extra_state_attributes["last_data"] is not None
    assert "last_data" in sensor._unrecorded_attributes


def test_sun_elevation_from_home_location(hass: HomeAssistant) -> None:
    """Noon and midnight at the configured location give day and night."""
    from datetime import UTC
    from datetime import datetime as dt

    coordinator = _coordinator(hass)
    hass.config.latitude, hass.config.longitude = 40.0, -75.0
    with patch.object(
        coord_mod.dt_util, "utcnow", return_value=dt(2026, 6, 21, 17, 0, tzinfo=UTC)
    ):
        coordinator._update_sun()
    assert coordinator._sun_low is False
    with patch.object(
        coord_mod.dt_util, "utcnow", return_value=dt(2026, 6, 21, 5, 0, tzinfo=UTC)
    ):
        coordinator._update_sun()
    assert coordinator._sun_low is True
