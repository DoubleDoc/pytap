"""Diagnostics support for PyTap."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_SERIAL_PORT
from .coordinator import PyTapConfigEntry

# The entry title contains the host too, e.g. "PyTap (192.168.1.10)".
TO_REDACT = {CONF_HOST, CONF_SERIAL_PORT, "title"}


def describe_flags(status: int | None, jstatus: int | None) -> dict[str, Any] | None:
    """The latest power report's flag bytes, bit by bit.

    Meanings come from passive captures and are partly tentative; see
    docs/protocol-status-bits.md. Raw values are included so nothing is lost.
    """
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


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: PyTapConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    diagnostics_data = coordinator.get_diagnostics_data()

    node_summaries = {
        barcode: {
            "last_update": node_data.get("last_update"),
            "gateway_id": node_data.get("gateway_id"),
            "node_id": node_data.get("node_id"),
            "daily_energy_wh": node_data.get("daily_energy_wh"),
            "total_energy_wh": node_data.get("total_energy_wh"),
            "readings_today": node_data.get("readings_today"),
            "flags": describe_flags(node_data.get("status"), node_data.get("jstatus")),
        }
        for barcode, node_data in coordinator.data.get("nodes", {}).items()
    }

    return {
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "counters": coordinator.data.get("counters", {}),
        "gateways": coordinator.data.get("gateways", {}),
        "discovered_barcodes": coordinator.data.get("discovered_barcodes", []),
        "nodes": node_summaries,
        **async_redact_data(diagnostics_data, TO_REDACT),
    }
