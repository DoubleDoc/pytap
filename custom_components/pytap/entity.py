"""Shared helpers for PyTap entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_INVERTER,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_STRING,
    CONF_STRING_INVERTERS,
    DOMAIN,
    READING_OFFLINE,
    READING_SILENT,
    STALE_UNAVAILABLE,
)
from .naming import optimizer_device_name, optimizer_object_id, string_inverter

if TYPE_CHECKING:
    from .coordinator import PyTapDataUpdateCoordinator


def module_inverter(entry_data: dict[str, Any], module_config: dict[str, Any]) -> str:
    """The inverter a module feeds: its string's own, else the entry's."""
    return string_inverter(
        dict(entry_data.get(CONF_STRING_INVERTERS, {})),
        module_config.get(CONF_MODULE_STRING, ""),
        entry_data.get(CONF_INVERTER, "") or "",
    )


def optimizer_device_info(inverter: str, module_config: dict[str, Any]) -> DeviceInfo:
    """Group every entity of one optimizer under the same device."""
    barcode = module_config[CONF_MODULE_BARCODE]
    return DeviceInfo(
        identifiers={(DOMAIN, barcode)},
        name=optimizer_device_name(
            inverter,
            module_config.get(CONF_MODULE_STRING, ""),
            module_config.get(CONF_MODULE_NAME, ""),
            barcode,
        ),
        manufacturer="Tigo Energy",
        model="TS4",
        serial_number=barcode,
    )


class PyTapFlagEntity(CoordinatorEntity["PyTapDataUpdateCoordinator"]):
    """Base for an optimizer's flag entities (status bits, reading fault).

    They describe the latest report, so they stay visible while a panel is
    reporting or asleep (its last report then shows its idle state). When a
    panel goes silent in daylight they follow the "unavailable" choice of the
    stale-reading settings; there's no meaningful zero for a flag. State is
    written only when it changes, so the recorder doesn't store a row per
    report (adapted from Tim Laing's upstream PR azebro/pytap#39).
    """

    _attr_has_entity_name = True
    # Follow the latest report even when the panel stops reporting.
    _live = True

    def __init__(
        self,
        coordinator: PyTapDataUpdateCoordinator,
        key: str,
        module_config: dict[str, Any],
        inverter: str,
        domain: str,
    ) -> None:
        super().__init__(coordinator)
        self._barcode = module_config[CONF_MODULE_BARCODE]
        self._attr_unique_id = f"{DOMAIN}_{self._barcode}_{key}"
        self.entity_id = f"{domain}.{optimizer_object_id(self._barcode)}_{key}"
        self._attr_device_info = optimizer_device_info(inverter, module_config)
        self._last_written: tuple[Any, ...] | None = None

    def _node(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("nodes", {}).get(self._barcode)

    def _value(self, node: dict[str, Any]) -> Any:
        raise NotImplementedError

    def _current(self) -> Any:
        node = self._node()
        return None if node is None else self._value(node)

    @property
    def available(self) -> bool:
        if self._current() is None:
            return False
        if not self._live:
            return True
        state = self.coordinator.reading_state(self._barcode)
        if state == READING_OFFLINE:
            return False
        if state == READING_SILENT:
            return self.coordinator.silent_action != STALE_UNAVAILABLE
        return True

    def _written(self) -> tuple[Any, ...]:
        return (self.available, self._current())

    @callback
    def _handle_coordinator_update(self) -> None:
        if not self.coordinator.affects((self._barcode,)):
            return
        written = self._written()
        if written == self._last_written:
            return
        self._last_written = written
        self.async_write_ha_state()
