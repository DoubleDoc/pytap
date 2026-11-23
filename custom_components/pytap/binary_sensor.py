"""Binary sensors for PyTap: an optimizer's flag bits and reading faults.

The flags are single bits of the power report's flag bytes, read bit by bit
(see docs/protocol-status-bits.md). Their names describe what captured data
shows; Tigo doesn't document them, and tentative ones are disabled by
default. Adapted from Tim Laing's upstream PR azebro/pytap#39.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODULE_BARCODE
from .coordinator import PyTapConfigEntry, PyTapDataUpdateCoordinator
from .entity import PyTapFlagEntity, module_inverter


def _bit(field: str, mask: int) -> Callable[[dict[str, Any]], bool | None]:
    def value(node: dict[str, Any]) -> bool | None:
        raw = node.get(field)
        return None if raw is None else bool(raw & mask)

    return value


@dataclass(frozen=True, kw_only=True)
class PyTapBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a PyTap binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool | None]
    # Follows the stale-reading settings once a panel stops reporting.
    live: bool = True


BINARY_SENSOR_DESCRIPTIONS: tuple[PyTapBinarySensorEntityDescription, ...] = (
    PyTapBinarySensorEntityDescription(
        # Status bit 7: input voltage low (reports average ~15 V when set).
        key="low_input_voltage",
        translation_key="low_input_voltage",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_bit("status", 0x80),
    ),
    PyTapBinarySensorEntityDescription(
        # jstatus (byte 7) bit 7, meaning open: never set below 0.1 A, almost
        # always above 3 A, but in between it latches and is often set at low
        # output, so earlier names ("producing", then "high output mode") were
        # misleading. Named after the bit and disabled by default; the key
        # stays "producing" so entity IDs don't change.
        key="producing",
        translation_key="producing",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_bit("jstatus", 0x80),
    ),
    PyTapBinarySensorEntityDescription(
        # Status bit 1, tentative: an idle pass-through state (duty 100 %,
        # output = input, no current) that only some optimizers enter.
        key="idle_pass_through",
        translation_key="idle_pass_through",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_bit("status", 0x02),
    ),
    PyTapBinarySensorEntityDescription(
        key="reading_fault",
        translation_key="reading_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda node: node.get("reading_fault"),
        # Stays as it is until the next valid reading, even after reports stop.
        live=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PyTapConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add binary sensors for configured and discovered optimizers."""
    coordinator = entry.runtime_data
    known_barcodes: set[str] = set()

    @callback
    def add_new_entities() -> None:
        if len(known_barcodes) == coordinator.module_count:
            return
        entities: list[PyTapBinarySensor] = []
        for module_config in coordinator.modules:
            barcode = module_config.get(CONF_MODULE_BARCODE, "")
            if not barcode or barcode in known_barcodes:
                continue
            known_barcodes.add(barcode)
            inverter = module_inverter(dict(entry.data), module_config)
            entities.extend(
                PyTapBinarySensor(coordinator, description, module_config, inverter)
                for description in BINARY_SENSOR_DESCRIPTIONS
            )
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))
    add_new_entities()


class PyTapBinarySensor(PyTapFlagEntity, BinarySensorEntity):
    """One flag bit, or the reading fault indicator, of one optimizer."""

    entity_description: PyTapBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: PyTapDataUpdateCoordinator,
        description: PyTapBinarySensorEntityDescription,
        module_config: dict[str, Any],
        inverter: str,
    ) -> None:
        super().__init__(coordinator, description.key, module_config, inverter, "binary_sensor")
        self.entity_description = description
        self._live = description.live

    def _value(self, node: dict[str, Any]) -> bool | None:
        return self.entity_description.value_fn(node)

    @property
    def is_on(self) -> bool | None:
        return self._current()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "reading_fault" or (node := self._node()) is None:
            return None
        return {
            "fault_reasons": list(node.get("fault_reasons") or []),
            "last_fault_at": node.get("last_fault_at"),
        }

    def _written(self) -> tuple[Any, ...]:
        attributes = self.extra_state_attributes
        return (*super()._written(), tuple(sorted((attributes or {}).items(), key=str)))
