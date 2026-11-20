"""Optimizer flag entities and impossible-reading detection.

Adapted from Tim Laing's upstream PR azebro/pytap#39, with the flags exposed
bit by bit (see docs/protocol-status-bits.md).
"""

from datetime import datetime
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.pytap.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    PyTapBinarySensor,
)
from custom_components.pytap.binary_sensor import (
    async_setup_entry as binary_setup,
)
from custom_components.pytap.const import (
    READING_ASLEEP,
    READING_OK,
    READING_SILENT,
)
from custom_components.pytap.coordinator import (
    PyTapDataUpdateCoordinator,
    reading_faults,
)
from custom_components.pytap.pytap.core.events import PowerReportEvent
from custom_components.pytap.sensor import FLAG_SENSOR_DESCRIPTIONS, PyTapFlagSensor
from tests.conftest import with_modules
from tests.test_write_interval import MOCK_MODULES, _make_entry, _make_infra_event

A = "A-1234567B"


def _report(vin=30.0, vout=29.5, temperature=25.0, status=0x64, jstatus=0x83):
    return PowerReportEvent(
        gateway_id=1, node_id=1, barcode=A, voltage_in=vin, voltage_out=vout,
        current_in=3.0, temperature=temperature, dc_dc_duty_cycle=0.9, rssi=-60,
        timestamp=datetime.now(), status=status, jstatus=jstatus,
    )


def test_plausibility_rules() -> None:
    assert reading_faults(_report()) == []
    assert reading_faults(_report(temperature=None)) == []  # temperature not valid
    assert reading_faults(_report(vin=69.7, vout=154.3)) == [
        "voltage_out_out_of_range",
        "voltage_out_above_input",
    ]
    assert reading_faults(_report(vin=30.0, vout=31.9)) == []  # within rounding
    assert reading_faults(_report(vin=-1.0, vout=0.0)) == ["voltage_in_out_of_range"]
    assert reading_faults(_report(temperature=130.0)) == ["temperature_out_of_range"]


def _coordinator(hass) -> PyTapDataUpdateCoordinator:
    coordinator = PyTapDataUpdateCoordinator(hass, _make_entry(hass, live_reporting=True))
    coordinator._schedule_save = MagicMock()
    coordinator._handle_infrastructure(_make_infra_event())
    return coordinator


async def test_impossible_reading_is_discarded_and_flagged(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    coordinator._process_event(_report())
    node = dict(coordinator._state["nodes"][A])
    last_report = coordinator._last_report[A]
    assert node["reading_fault"] is False

    coordinator._process_event(_report(vin=69.7, vout=154.3))
    faulty = coordinator._state["nodes"][A]
    assert faulty["reading_fault"] is True
    assert faulty["fault_reasons"] == ["voltage_out_out_of_range", "voltage_out_above_input"]
    assert faulty["last_fault_at"]
    # The last good values, energy and freshness are kept.
    for key in ("voltage_in", "voltage_out", "power", "total_energy_wh", "readings_today"):
        assert faulty[key] == node[key], key
    assert coordinator._last_report[A] == last_report

    coordinator._process_event(_report(vin=31.0, vout=30.5))
    cleared = coordinator._state["nodes"][A]
    assert cleared["reading_fault"] is False and cleared["fault_reasons"] == []
    assert cleared["last_fault_at"] == faulty["last_fault_at"]  # when it last happened
    assert cleared["voltage_in"] == 31.0


async def test_flags_and_faults_survive_a_restart(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    coordinator._process_event(_report(status=0xE4, jstatus=0x00))
    coordinator._process_event(_report(vin=69.7, vout=154.3, status=0xE4, jstatus=0x00))
    with coordinator._state_lock:
        snapshot = coordinator._save_payload()["node_snapshots"][A]
    assert snapshot["status"] == 0xE4 and snapshot["jstatus"] == 0x00
    assert snapshot["reading_fault"] is True and snapshot["fault_reasons"]
    restored = coordinator._build_node_payload(A, MOCK_MODULES[0], 1)
    assert restored["reading_fault"] is None  # unknown until a report or restore
    coordinator._merge_snapshot_into_node(restored, snapshot)
    assert restored["status"] == 0xE4 and restored["reading_fault"] is True


def _flag_coordinator(node: dict, state: str = READING_OK, silent_action="unavailable"):
    coordinator = MagicMock()
    coordinator.data = {"nodes": {A: node}}
    coordinator.reading_state.return_value = state
    coordinator.silent_action = silent_action
    coordinator.affects.return_value = True
    return coordinator


def _binary(coordinator, key: str) -> PyTapBinarySensor:
    description = next(d for d in BINARY_SENSOR_DESCRIPTIONS if d.key == key)
    entity = PyTapBinarySensor(coordinator, description, MOCK_MODULES[0], "Master")
    entity.async_write_ha_state = MagicMock()
    return entity


def _sensor(coordinator, key: str) -> PyTapFlagSensor:
    description = next(d for d in FLAG_SENSOR_DESCRIPTIONS if d.key == key)
    entity = PyTapFlagSensor(coordinator, description, MOCK_MODULES[0], "Master")
    entity.async_write_ha_state = MagicMock()
    return entity


@pytest.mark.parametrize(
    ("status", "jstatus", "expected"),
    [
        (0x64, 0x83, {"low_input_voltage": False, "producing": True, "idle_pass_through": False,
                      "status": "0x64", "state_field": 1, "jstatus_level": 3}),
        (0xE4, 0x00, {"low_input_voltage": True, "producing": False, "idle_pass_through": False,
                      "status": "0xE4", "state_field": 1, "jstatus_level": 0}),
        (0x66, 0x00, {"low_input_voltage": False, "producing": False, "idle_pass_through": True,
                      "status": "0x66", "state_field": 1, "jstatus_level": 0}),
        (0x68, 0x82, {"low_input_voltage": False, "producing": True, "idle_pass_through": False,
                      "status": "0x68", "state_field": 2, "jstatus_level": 2}),
    ],
)
def test_flag_values_are_single_bits(status, jstatus, expected) -> None:
    coordinator = _flag_coordinator({"status": status, "jstatus": jstatus})
    for key in ("low_input_voltage", "producing", "idle_pass_through"):
        assert _binary(coordinator, key).is_on is expected[key], key
    for key in ("status", "state_field", "jstatus_level"):
        assert _sensor(coordinator, key).native_value == expected[key], key


def test_status_sensor_attributes_decode_bits() -> None:
    coordinator = _flag_coordinator({"status": 0xE4, "jstatus": 0x00})
    attributes = _sensor(coordinator, "status").extra_state_attributes
    assert attributes["status_bits"] == [2, 5, 6, 7]
    assert attributes["low_input_voltage"] is True


def test_state_is_written_only_when_it_changes() -> None:
    node = {"status": 0x64, "jstatus": 0x83}
    coordinator = _flag_coordinator(node)
    entity = _binary(coordinator, "producing")
    for _ in range(5):
        entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 1
    node["jstatus"] = 0x00
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 2


@pytest.mark.parametrize(
    ("state", "silent_action", "available"),
    [
        (READING_OK, "unavailable", True),
        (READING_ASLEEP, "unavailable", True),  # last report shows the idle state
        (READING_SILENT, "unavailable", False),
        (READING_SILENT, "hold", True),
        (READING_SILENT, "zero", True),
    ],
)
def test_flags_follow_the_stale_reading_settings(state, silent_action, available) -> None:
    node = {"status": 0x64, "jstatus": 0x83, "reading_fault": False}
    coordinator = _flag_coordinator(node, state, silent_action)
    assert _binary(coordinator, "producing").available is available
    assert _sensor(coordinator, "status").available is available
    # The fault indicator always shows the last verdict.
    assert _binary(coordinator, "reading_fault").available is True


def test_no_report_yet_is_unavailable() -> None:
    coordinator = _flag_coordinator({"status": None, "jstatus": None, "reading_fault": None})
    assert _binary(coordinator, "producing").available is False
    assert _binary(coordinator, "reading_fault").available is False


async def test_binary_sensor_platform_uses_barcode_ids(hass: HomeAssistant) -> None:
    entry = MagicMock()
    entry.data = {"inverter": "Master"}
    coordinator = with_modules(MagicMock(spec=PyTapDataUpdateCoordinator), MOCK_MODULES)
    coordinator.data = {"nodes": {}}
    entry.runtime_data = coordinator
    added: list = []
    await binary_setup(hass, entry, added.extend)
    assert {e.entity_id for e in added} == {
        "binary_sensor.tigo_a_1234567b_low_input_voltage",
        "binary_sensor.tigo_a_1234567b_producing",
        "binary_sensor.tigo_a_1234567b_idle_pass_through",
        "binary_sensor.tigo_a_1234567b_reading_fault",
    }
    disabled = {
        e.entity_description.key
        for e in added
        if e.entity_description.entity_registry_enabled_default is False
    }
    assert disabled == {"idle_pass_through"}
    assert {e.device_info["name"] for e in added} == {"Tigo Master A Panel_01"}
