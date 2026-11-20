"""String and total energy across membership changes.

Adapted from Tim Laing's upstream PR azebro/pytap#35: aggregates add up each
member's increase since it was last seen, so moving, adding or removing a
module never adds its lifetime energy or looks like a meter reset.
"""

from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.pytap.sensor import (
    STRING_SENSOR_DESCRIPTIONS,
    PyTapAggregateSensor,
    _AggregateEnergyData,
)

A, B, C = "A-1234567B", "B-2345678C", "C-3456789D"


def _coordinator(nodes: dict, estimates: dict | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = {"nodes": nodes}
    coordinator.affects.return_value = True
    coordinator.link_up = True
    coordinator.estimated_energy.side_effect = lambda b: (estimates or {}).get(b, (0.0, 0.0))
    return coordinator


def _sensor(hass, coordinator, key: str, barcodes: list[str]) -> PyTapAggregateSensor:
    description = next(d for d in STRING_SENSOR_DESCRIPTIONS if d.key == key)
    sensor = PyTapAggregateSensor(coordinator, description, barcodes, MagicMock(), "u")
    sensor.hass = hass
    sensor.async_write_ha_state = MagicMock()
    return sensor


def _total(wh_by_barcode: dict) -> dict:
    return {b: {"total_energy_wh": wh, "daily_energy_wh": wh} for b, wh in wh_by_barcode.items()}


def test_starts_from_the_sum_then_adds_increases(hass: HomeAssistant) -> None:
    coordinator = _coordinator(_total({A: 1000.0, B: 2000.0}))
    sensor = _sensor(hass, coordinator, "total_energy", [A, B])
    sensor._handle_coordinator_update()
    assert sensor.native_value == 3000.0

    coordinator.data = {"nodes": _total({A: 1010.0, B: 2005.0})}
    sensor._handle_coordinator_update()
    assert sensor.native_value == 3015.0


def test_a_module_moved_in_does_not_jump(hass: HomeAssistant) -> None:
    coordinator = _coordinator(_total({A: 1000.0}))
    sensor = _sensor(hass, coordinator, "total_energy", [A])
    sensor._handle_coordinator_update()

    # C (50 kWh lifetime) joins the string: only its later production counts.
    sensor._barcodes = [A, C]
    coordinator.data = {"nodes": _total({A: 1000.0, C: 50000.0})}
    sensor._handle_coordinator_update()
    assert sensor.native_value == 1000.0
    coordinator.data = {"nodes": _total({A: 1000.0, C: 50020.0})}
    sensor._handle_coordinator_update()
    assert sensor.native_value == 1020.0


def test_a_module_removed_does_not_drop(hass: HomeAssistant) -> None:
    coordinator = _coordinator(_total({A: 1000.0, B: 2000.0}))
    sensor = _sensor(hass, coordinator, "total_energy", [A, B])
    sensor._handle_coordinator_update()

    sensor._barcodes = [A]
    coordinator.data = {"nodes": _total({A: 1000.0})}
    sensor._handle_coordinator_update()
    assert sensor.native_value == 3000.0


def test_member_reset_only_rebases(hass: HomeAssistant) -> None:
    coordinator = _coordinator(_total({A: 800.0}))
    sensor = _sensor(hass, coordinator, "total_energy", [A])
    sensor._handle_coordinator_update()
    coordinator.data = {"nodes": _total({A: 0.0})}  # e.g. a member's counter restarts
    sensor._handle_coordinator_update()
    coordinator.data = {"nodes": _total({A: 30.0})}
    sensor._handle_coordinator_update()
    assert sensor.native_value == 830.0


def test_estimated_energy_counts_as_it_grows(hass: HomeAssistant) -> None:
    estimates = {A: (0.0, 0.0)}
    coordinator = _coordinator(_total({A: 1000.0}), estimates)
    sensor = _sensor(hass, coordinator, "total_energy", [A])
    sensor._handle_coordinator_update()
    estimates[A] = (12.0, 12.0)
    sensor._handle_coordinator_update()
    assert sensor.native_value == 1012.0


def test_daily_energy_starts_a_new_cycle_at_midnight(hass: HomeAssistant) -> None:
    coordinator = _coordinator(_total({A: 500.0}))
    sensor = _sensor(hass, coordinator, "daily_energy", [A])
    with patch.object(sensor, "_today", return_value="2026-11-19"):
        sensor._handle_coordinator_update()
        assert sensor.native_value == 500.0
    with patch.object(sensor, "_today", return_value="2026-11-20"):
        coordinator.data = {"nodes": _total({A: 0.0})}
        sensor._handle_coordinator_update()
        assert sensor.native_value == 0.0
        coordinator.data = {"nodes": _total({A: 40.0})}
        sensor._handle_coordinator_update()
        assert sensor.native_value == 40.0
        assert sensor.last_reset.date().isoformat() == "2026-11-20"


async def test_restore_round_trip(hass: HomeAssistant) -> None:
    coordinator = _coordinator(_total({A: 1000.0}))
    sensor = _sensor(hass, coordinator, "total_energy", [A])
    sensor._handle_coordinator_update()
    coordinator.data = {"nodes": _total({A: 1100.0})}
    sensor._handle_coordinator_update()
    saved = sensor.extra_restore_state_data.as_dict()

    restored = _sensor(hass, coordinator, "total_energy", [A])
    restored.async_get_last_extra_data = MagicMock(
        return_value=_awaitable(_AggregateEnergyData(**saved))
    )
    await restored._async_restore_energy()
    coordinator.data = {"nodes": _total({A: 1150.0})}
    restored._handle_coordinator_update()
    assert restored.native_value == 1150.0


async def test_upgrade_from_plain_sum_has_no_jump(hass: HomeAssistant) -> None:
    """Earlier versions stored only the summed value: continue from it."""
    coordinator = _coordinator(_total({A: 1000.0, C: 50000.0}))
    sensor = _sensor(hass, coordinator, "total_energy", [A, C])
    sensor.async_get_last_extra_data = MagicMock(return_value=_awaitable(None))
    sensor._async_restored_value = MagicMock(return_value=_awaitable(51000.0))
    await sensor._async_restore_energy()
    sensor._handle_coordinator_update()
    assert sensor.native_value == 51000.0
    coordinator.data = {"nodes": _total({A: 1005.0, C: 50000.0})}
    sensor._handle_coordinator_update()
    assert sensor.native_value == 51005.0


def _awaitable(value):
    async def _coro():
        return value

    return _coro()
