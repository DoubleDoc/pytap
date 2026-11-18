"""Power report decoding as the Tigo CCA firmware does it.

Per Bobsilvio/tigo_server docs/PROTOCOL-NOTES.md: input current scale by
hardware ID, temperature only when status bit 0x20 is set, RSSI 0 = no
value, and 12-byte reports (no RSSI) accepted.
"""

from datetime import datetime

import pytest

from custom_components.pytap.pytap.core.parser import Parser
from custom_components.pytap.pytap.core.state import PersistentState, SlotClock
from custom_components.pytap.pytap.core.types import (
    LongAddress,
    PowerReport,
    SlotCounter,
    current_scale_for_hw_id,
)

LONG = LongAddress(bytes([0x04, 0xC0, 0x5B, 0x30, 0x00, 0x02, 0xBE, 0x16]))


def _report(status: int = 0x20, rssi: int | None = 0x80, current_raw: int = 500) -> bytes:
    """Vin 40 V, Vout 40 V, duty 128, current_raw, temperature 41.1 °C."""
    data = bytes(
        [
            0x32, 0x01, 0x90,  # Vin 800 x 0.05, Vout 400 x 0.1
            0x80,  # duty cycle
            (current_raw >> 4) & 0xFF, ((current_raw & 0x0F) << 4) | 0x01, 0x9B,  # Iin, temp 0x19B
            0x00,  # jstatus
            (status >> 8) & 0xFF, status & 0xFF,  # status
            0x40, 0x00,  # slot counter
        ]
    )
    return data if rssi is None else data + bytes([rssi])


@pytest.mark.parametrize(
    ("hw_id", "scale"),
    [
        (None, 0.005),
        (0x10, 0.0025),
        (0x23, 0.0025),
        (0x24, 0.005),
        (0x2D, 0.005),
        (0x5F, 0.005),
        (0x60, 0.025),
    ],
)
def test_current_scale_for_hw_id(hw_id, scale) -> None:
    assert current_scale_for_hw_id(hw_id) == scale


def test_temperature_only_with_status_bit() -> None:
    assert PowerReport.from_bytes(_report(status=0x20)).temperature == pytest.approx(41.1)
    assert PowerReport.from_bytes(_report(status=0x00)).temperature is None
    assert PowerReport.from_bytes(_report(status=0xFFDF)).temperature is None


def test_rssi_zero_and_short_report() -> None:
    assert PowerReport.from_bytes(_report(rssi=0x80)).rssi == 0x80
    assert PowerReport.from_bytes(_report(rssi=0)).rssi is None
    short = PowerReport.from_bytes(_report(rssi=None))  # 12 bytes
    assert short.rssi is None
    assert short.voltage_in == pytest.approx(40.0)
    with pytest.raises(ValueError):
        PowerReport.from_bytes(_report(rssi=None)[:11])


def _parser() -> Parser:
    parser = Parser()
    parser._persistent_state.gateway_node_tables[1] = {5: LONG}
    parser._slot_clocks[1] = SlotClock(SlotCounter.from_bytes(b"\x40\x00"), datetime.now())
    return parser


@pytest.mark.parametrize(("hw_hex", "amps"), [(None, 2.5), ("2D", 2.5), ("1F", 1.25), ("61", 12.5)])
def test_version_string_sets_current_scale(hw_hex, amps) -> None:
    parser = _parser()
    if hw_hex is not None:
        parser._handle_string_response(1, 5, f"Mnode Version K8.0120 ({hw_hex})".encode())
        assert parser._persistent_state.node_hw_ids[str(LONG)] == int(hw_hex, 16)
    (event,) = parser._handle_power_report(1, 5, _report(current_raw=500))
    assert event.current_in == pytest.approx(amps)
    # Power = Vin x Iin, as the CCA computes it.
    assert event.power == pytest.approx(40.0 * amps)


def test_other_strings_and_unknown_nodes_ignored() -> None:
    parser = _parser()
    parser._handle_string_response(1, 5, b"Mnode Status OK")
    parser._handle_string_response(1, 9, b"Mnode Version K8.0120 (61)")  # not in node table
    assert parser._persistent_state.node_hw_ids == {}


def test_hw_ids_persist() -> None:
    state = PersistentState()
    state.node_hw_ids[str(LONG)] = 0x61
    assert PersistentState.from_dict(state.to_dict()).node_hw_ids == {str(LONG): 0x61}
    assert PersistentState.from_dict({}).node_hw_ids == {}


def test_coordinator_keeps_last_valid_temperature_and_rssi(hass) -> None:
    """A report without a valid temperature or RSSI keeps the last valid one."""
    from custom_components.pytap.pytap.core.events import PowerReportEvent
    from tests.test_estimation import A, _coordinator

    coordinator = _coordinator(hass)

    def feed(temperature, rssi):
        coordinator._handle_power_report(
            PowerReportEvent(
                gateway_id=1, node_id=1, barcode=A, voltage_in=32.0, voltage_out=30.0,
                current_in=3.0, temperature=temperature, dc_dc_duty_cycle=0.9, rssi=rssi,
                timestamp=datetime.now(),
            )
        )
        return coordinator.data["nodes"][A]

    node = feed(35.0, 120)
    assert (node["temperature"], node["rssi"]) == (35.0, 120)
    node = feed(None, None)
    assert (node["temperature"], node["rssi"]) == (35.0, 120)
    node = feed(36.0, 118)
    assert (node["temperature"], node["rssi"]) == (36.0, 118)
