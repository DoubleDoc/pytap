"""Decode a power report's flag bytes bit by bit.

Meanings come from passive captures and are partly tentative; see
docs/protocol-status-bits.md. Raw values are included so nothing is lost.
Shared by the integration's diagnostics and the offline capture tools.
"""

from __future__ import annotations

from typing import Any


def describe_flags(status: int | None, jstatus: int | None) -> dict[str, Any] | None:
    """The flag bytes of one power report (status = bytes 8-9, jstatus = byte 7)."""
    if status is None and jstatus is None:
        return None
    status = status or 0
    jstatus = jstatus or 0
    return {
        "status": f"0x{status:04X}",
        "status_bits": [bit for bit in range(16) if status >> bit & 1],
        "low_input_voltage": bool(status & 0x80),  # bit 7
        "state_field": (status >> 2) & 0b11,  # bits 3..2, exactly one set so far
        "idle_pass_through": bool(status & 0x02),  # bit 1 (tentative)
        "temperature_valid": bool(status & 0x20),  # bit 5 (CCA firmware)
        "jstatus": f"0x{jstatus:02X}",
        "producing": bool(jstatus & 0x80),  # byte 7 bit 7
        "jstatus_level": jstatus & 0x7F,
    }
