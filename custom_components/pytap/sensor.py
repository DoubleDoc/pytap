"""Sensor platform for PyTap integration.

Creates sensor entities for each user-configured Tigo optimizer module.
Entities are keyed by barcode (stable identifier) and created deterministically
from the configured module list — no auto-discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo
import logging
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    BUS_NORMAL,
    BUS_STATES,
    CONF_INVERTER,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_PEAK_POWER,
    CONF_MODULE_STRING,
    CONF_STRING_INVERTERS,
    DEFAULT_PEAK_POWER,
    DOMAIN,
    MAPPING_COMPLETE,
    MAPPING_PARTIAL,
    MAPPING_WAITING,
    READING_ASLEEP,
    READING_OFFLINE,
    READING_SILENT,
    STALE_UNAVAILABLE,
    STALE_ZERO,
    VALIDITY_REPORTING,
    VALIDITY_STATES,
)
from .coordinator import PyTapConfigEntry, PyTapDataUpdateCoordinator
from .naming import (
    cca_total_device_name,
    has_inverter_totals,
    inverter_total_object_id,
    optimizer_device_name,
    optimizer_object_id,
    string_device_name,
    string_inverter,
    total_device_name,
)

_LOGGER = logging.getLogger(__name__)

# Sensor keys that describe what the optimizer is doing right now. They go
# unavailable when the optimizer is silent by day or the converter is
# offline. Energy and readings-today sensors are never affected.
LIVE_KEYS = frozenset(
    {
        "performance",
        "power",
        "voltage_in",
        "voltage_out",
        "current_in",
        "current_out",
        "temperature",
        "dc_dc_duty_cycle",
        "rssi",
    }
)
# Live keys that read 0 while the optimizer is asleep (low sun, quiet).
# Temperature and RSSI keep their last value.
ZERO_WHEN_ASLEEP_KEYS = frozenset(
    {
        "performance",
        "power",
        "voltage_in",
        "voltage_out",
        "current_in",
        "current_out",
        "dc_dc_duty_cycle",
    }
)
# Aggregate keys that follow the live state of their optimizers.
AGGREGATE_LIVE_KEYS = frozenset({"performance", "power"})


def _coerce_restored_state_value(raw_state: str, sensor_key: str) -> int | float | None:
    """Convert a restored state string to a native numeric sensor value."""
    if raw_state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "None", "none", ""):
        return None

    try:
        numeric_value = float(raw_state)
    except (TypeError, ValueError):
        return None

    if sensor_key == "readings_today":
        return int(numeric_value)

    return numeric_value


@dataclass(frozen=True, kw_only=True)
class PyTapSensorEntityDescription(SensorEntityDescription):
    """Describes a PyTap sensor entity."""

    value_key: str


@dataclass(frozen=True, kw_only=True)
class PyTapAggregateSensorDescription(SensorEntityDescription):
    """Describes a PyTap aggregate sensor entity."""

    value_key: str


# Precision of the state written to Home Assistant, by sensor key. Live values
# are averages over the write interval, so their extra decimals are averaging
# noise; it changes the value on nearly every write and so causes a database
# row each time. Rounding to about 0.3% (power, voltage) and 1 °C keeps what
# matters; currents keep 0.01 A so small differences between panels in a
# string still show. Idea and measurements: seaosair (upstream PR #25).
STATE_PRECISION: dict[str, int] = {
    "performance": 1,
    "power": 0,
    "voltage_in": 1,
    "voltage_out": 1,
    "current_in": 2,
    "current_out": 2,
    "temperature": 0,
    "dc_dc_duty_cycle": 0,
    "rssi": 0,
    "daily_energy": 2,
    "total_energy": 2,
}


def _round_state(key: str, value: Any) -> Any:
    """Round a sensor value to its state precision."""
    digits = STATE_PRECISION.get(key)
    if digits is None or not isinstance(value, (int, float)) or isinstance(
        value, bool
    ):
        return value
    return round(float(value), digits)


SENSOR_DESCRIPTIONS: tuple[PyTapSensorEntityDescription, ...] = (
    PyTapSensorEntityDescription(
        key="performance",
        translation_key="performance",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_key="performance",
    ),
    PyTapSensorEntityDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="power",
    ),
    PyTapSensorEntityDescription(
        key="voltage_in",
        translation_key="voltage_in",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="voltage_in",
    ),
    PyTapSensorEntityDescription(
        key="voltage_out",
        translation_key="voltage_out",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="voltage_out",
    ),
    PyTapSensorEntityDescription(
        key="current_in",
        translation_key="current_in",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="current_in",
    ),
    PyTapSensorEntityDescription(
        key="current_out",
        translation_key="current_out",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="current_out",
    ),
    PyTapSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="temperature",
    ),
    PyTapSensorEntityDescription(
        key="dc_dc_duty_cycle",
        translation_key="dc_dc_duty_cycle",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        value_key="dc_dc_duty_cycle",
    ),
    PyTapSensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="rssi",
    ),
    PyTapSensorEntityDescription(
        key="daily_energy",
        translation_key="daily_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_key="daily_energy_wh",
    ),
    PyTapSensorEntityDescription(
        key="total_energy",
        translation_key="total_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_key="total_energy_wh",
    ),
    PyTapSensorEntityDescription(
        key="readings_today",
        translation_key="readings_today",
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_key="readings_today",
    ),
)


STRING_SENSOR_DESCRIPTIONS: tuple[PyTapAggregateSensorDescription, ...] = (
    PyTapAggregateSensorDescription(
        key="performance",
        translation_key="string_performance",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_key="performance",
    ),
    PyTapAggregateSensorDescription(
        key="power",
        translation_key="string_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="power",
    ),
    PyTapAggregateSensorDescription(
        key="daily_energy",
        translation_key="string_daily_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_key="daily_energy_wh",
    ),
    PyTapAggregateSensorDescription(
        key="total_energy",
        translation_key="string_total_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_key="total_energy_wh",
    ),
)


INSTALLATION_SENSOR_DESCRIPTIONS: tuple[PyTapAggregateSensorDescription, ...] = (
    PyTapAggregateSensorDescription(
        key="performance",
        translation_key="installation_performance",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_key="performance",
    ),
    PyTapAggregateSensorDescription(
        key="power",
        translation_key="installation_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="power",
    ),
    PyTapAggregateSensorDescription(
        key="daily_energy",
        translation_key="installation_daily_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_key="daily_energy_wh",
    ),
    PyTapAggregateSensorDescription(
        key="total_energy",
        translation_key="installation_total_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_key="total_energy_wh",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PyTapConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PyTap sensors from a config entry.

    Each module gets the 12 measurement sensors plus a Data validity sensor,
    and strings and the gateway get totals. With discovery on, optimizers
    heard on the bus are added as they appear (adapted from Tim Laing's
    upstream PR azebro/pytap#37).
    """
    coordinator = entry.runtime_data
    inverter: str = entry.data.get(CONF_INVERTER, "") or ""
    string_inverters: dict[str, str] = dict(entry.data.get(CONF_STRING_INVERTERS, {}))

    def inverter_of(string_name: str) -> str:
        return string_inverter(string_inverters, string_name, inverter)

    known_barcodes: set[str] = set()
    string_sensors: dict[str, list[PyTapAggregateSensor]] = {}
    total_sensors: list[PyTapAggregateSensor] = []
    inverter_sensors: dict[str, list[PyTapAggregateSensor]] = {}
    split_by_inverter = has_inverter_totals(string_inverters, inverter)
    total_device_info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_installation")},
        name=(
            cca_total_device_name(inverter)
            if split_by_inverter
            else total_device_name(inverter)
        ),
        manufacturer="Tigo Energy",
        model="Installation Aggregate",
    )

    @callback
    def add_new_entities() -> None:
        """Add entities for modules not seen yet (runs on HA's loop)."""
        if len(known_barcodes) == coordinator.module_count:
            return
        entities: list[SensorEntity] = []
        string_to_barcodes: dict[str, list[str]] = {}
        all_barcodes: list[str] = []

        for module_config in coordinator.modules:
            barcode = module_config.get(CONF_MODULE_BARCODE, "")
            if not barcode:
                continue
            all_barcodes.append(barcode)
            if string_name := module_config.get(CONF_MODULE_STRING, ""):
                string_to_barcodes.setdefault(string_name, []).append(barcode)
            if barcode in known_barcodes:
                continue
            known_barcodes.add(barcode)
            for description in SENSOR_DESCRIPTIONS:
                entities.append(
                    PyTapSensor(
                        coordinator=coordinator,
                        description=description,
                        module_config=module_config,
                        entry=entry,
                        inverter=inverter_of(module_config.get(CONF_MODULE_STRING, "")),
                    )
                )
            entities.append(
                PyTapDataValiditySensor(
                    coordinator=coordinator,
                    module_config=module_config,
                    inverter=inverter_of(module_config.get(CONF_MODULE_STRING, "")),
                )
            )

        aggregates: list[SensorEntity] = []
        for string_name, barcodes in string_to_barcodes.items():
            if string_name not in string_sensors:
                device_info = DeviceInfo(
                    identifiers={(DOMAIN, f"{entry.entry_id}_string_{string_name}")},
                    name=string_device_name(inverter_of(string_name), string_name),
                    manufacturer="Tigo Energy",
                    model="String Aggregate",
                )
                string_sensors[string_name] = [
                    PyTapAggregateSensor(
                        coordinator=coordinator,
                        description=description,
                        barcodes=barcodes,
                        device_info=device_info,
                        unique_id=(
                            f"{DOMAIN}_{entry.entry_id}_string_{string_name}_{description.key}"
                        ),
                    )
                    for description in STRING_SENSOR_DESCRIPTIONS
                ]
                aggregates.extend(string_sensors[string_name])
            for sensor in string_sensors[string_name]:
                sensor.set_barcodes(barcodes)

        if all_barcodes and not total_sensors:
            total_sensors.extend(
                PyTapAggregateSensor(
                    coordinator=coordinator,
                    description=description,
                    barcodes=all_barcodes,
                    device_info=total_device_info,
                    unique_id=f"{DOMAIN}_{entry.entry_id}_installation_{description.key}",
                )
                for description in INSTALLATION_SENSOR_DESCRIPTIONS
            )
            aggregates.extend(total_sensors)
            aggregates.append(
                PyTapNodeMappingSensor(
                    coordinator=coordinator,
                    device_info=total_device_info,
                    unique_id=f"{DOMAIN}_{entry.entry_id}_node_mapping",
                )
            )
            aggregates.append(
                PyTapBusStatusSensor(
                    coordinator=coordinator,
                    device_info=total_device_info,
                    unique_id=f"{DOMAIN}_{entry.entry_id}_bus_status",
                )
            )
        for sensor in total_sensors:
            sensor.set_barcodes(all_barcodes)

        # A total per inverter named on a string (one CCA, several inverters).
        inverter_to_barcodes = (
            _inverter_members(string_to_barcodes, string_inverters, inverter)
            if split_by_inverter
            else {}
        )
        for inv, barcodes in inverter_to_barcodes.items():
            if inv not in inverter_sensors:
                device_info = DeviceInfo(
                    identifiers={(DOMAIN, f"{entry.entry_id}_inverter_{inv}")},
                    name=total_device_name(inv),
                    manufacturer="Tigo Energy",
                    model="Inverter Aggregate",
                )
                inverter_sensors[inv] = []
                for description in INSTALLATION_SENSOR_DESCRIPTIONS:
                    sensor = PyTapAggregateSensor(
                        coordinator=coordinator,
                        description=description,
                        barcodes=barcodes,
                        device_info=device_info,
                        unique_id=(
                            f"{DOMAIN}_{entry.entry_id}_inverter_{inv}_{description.key}"
                        ),
                    )
                    # Own IDs, so they never clash with the entry total's.
                    sensor.entity_id = (
                        f"sensor.{inverter_total_object_id(inv)}_{description.key}"
                    )
                    inverter_sensors[inv].append(sensor)
                aggregates.extend(inverter_sensors[inv])
            for sensor in inverter_sensors[inv]:
                sensor.set_barcodes(barcodes)

        # String and total devices first: optimizer devices connect via them.
        if aggregates or entities:
            async_add_entities([*aggregates, *entities])
        _sync_performance_entities(
            hass, entry.entry_id, coordinator, string_to_barcodes, all_barcodes,
            inverter_to_barcodes,
        )

    # Registered first, so totals learn new members before their own update.
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))
    add_new_entities()
    if coordinator.auto_discovery:
        async_add_entities([PyTapDiscoverySensor(coordinator, entry, total_device_info)])


def _inverter_members(
    string_to_barcodes: dict[str, list[str]],
    string_inverters: dict[str, str],
    entry_inverter: str,
) -> dict[str, list[str]]:
    """Barcodes per inverter (strings without one feed the entry's inverter).

    Strings with no inverter at all (entry name blank, none set) aren't in
    any inverter's total; the CCA total still counts them.
    """
    members: dict[str, list[str]] = {}
    for string_name, barcodes in string_to_barcodes.items():
        if inv := string_inverter(string_inverters, string_name, entry_inverter):
            members.setdefault(inv, []).extend(barcodes)
    return members


@callback
def _sync_performance_entities(
    hass: HomeAssistant,
    entry_id: str,
    coordinator: PyTapDataUpdateCoordinator,
    string_to_barcodes: dict[str, list[str]],
    all_barcodes: list[str],
    inverter_to_barcodes: dict[str, list[str]] | None = None,
) -> None:
    """Disable performance entities until every panel they cover has a rating.

    Only entities that are enabled, or that PyTap disabled, are changed; a
    user's own choice to disable one is left alone. From azebro/pytap#37.
    """

    def rated(barcodes: list[str]) -> bool:
        return all(coordinator.module_peak_power(barcode) > 0 for barcode in barcodes)

    wanted = {f"{DOMAIN}_{barcode}_performance": rated([barcode]) for barcode in all_barcodes}
    for string_name, barcodes in string_to_barcodes.items():
        wanted[f"{DOMAIN}_{entry_id}_string_{string_name}_performance"] = rated(barcodes)
    if all_barcodes:
        wanted[f"{DOMAIN}_{entry_id}_installation_performance"] = rated(all_barcodes)
    for inv, barcodes in (inverter_to_barcodes or {}).items():
        wanted[f"{DOMAIN}_{entry_id}_inverter_{inv}_performance"] = rated(barcodes)

    registry = er.async_get(hass)
    for unique_id, has_rating in wanted.items():
        if (entity_id := registry.async_get_entity_id("sensor", DOMAIN, unique_id)) is None:
            continue
        disabled_by = registry.entities[entity_id].disabled_by
        if not has_rating and disabled_by is None:
            registry.async_update_entity(
                entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
        elif has_rating and disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(entity_id, disabled_by=None)


class PyTapDiscoverySensor(CoordinatorEntity[PyTapDataUpdateCoordinator], SensorEntity):
    """Shows whether discovery has found any optimizers yet (from azebro/pytap#37)."""

    _attr_has_entity_name = True
    _attr_translation_key = "module_discovery"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["waiting_for_modules", "modules_discovered"]

    def __init__(
        self,
        coordinator: PyTapDataUpdateCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_module_discovery"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str:
        """Waiting until the first optimizer is tracked."""
        return "modules_discovered" if self.coordinator.module_count else "waiting_for_modules"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """How many optimizers are tracked, and how many await naming."""
        return {
            "tracked": self.coordinator.module_count,
            "unnamed": sum(
                1 for m in self.coordinator.discovered_modules.values()
                if not m.get(CONF_MODULE_NAME)
            ),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.affects(()):
            self.async_write_ha_state()


class PyTapSensor(CoordinatorEntity[PyTapDataUpdateCoordinator], RestoreSensor):
    """Representation of a PyTap optimizer sensor.

    Each sensor reads a specific measurement (power, voltage, etc.) for
    a single Tigo optimizer identified by its barcode.
    """

    _attr_has_entity_name = True
    # No per-reading timestamp attribute: any attribute that changes with
    # every reading makes Home Assistant write a new state row even when the
    # value is unchanged. The optimizer's Data validity sensor shows when it
    # last reported once it stops.
    entity_description: PyTapSensorEntityDescription

    def __init__(
        self,
        coordinator: PyTapDataUpdateCoordinator,
        description: PyTapSensorEntityDescription,
        module_config: dict[str, str],
        entry: ConfigEntry,
        inverter: str = "",
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description

        self._barcode = module_config[CONF_MODULE_BARCODE]
        self._module_name = module_config[CONF_MODULE_NAME]
        self._module_string = module_config.get(CONF_MODULE_STRING, "")

        # Unique ID: domain + barcode + sensor key
        self._attr_unique_id = f"{DOMAIN}_{self._barcode}_{description.key}"
        # Entity ID: the barcode (see naming.py); set when first created.
        self.entity_id = f"sensor.{optimizer_object_id(self._barcode)}_{description.key}"

        # Device info — groups all sensors for one optimizer under one device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._barcode)},
            name=optimizer_device_name(
                inverter, self._module_string, self._module_name, self._barcode
            ),
            via_device=(DOMAIN, f"{entry.entry_id}_string_{self._module_string}")
            if self._module_string
            else None,
            manufacturer="Tigo Energy",
            model="TS4",
            serial_number=self._barcode,
        )
        self._restored_native_value = False

    async def async_added_to_hass(self) -> None:
        """Restore last known native value from Home Assistant state cache."""
        await super().async_added_to_hass()
        nodes = self.coordinator.data.get("nodes", {})
        node = nodes.get(self._barcode)
        # Only short-circuit to coordinator data if this sensor has a non-None value.
        if node is not None:
            coordinator_value = node.get(self.entity_description.value_key)
            if coordinator_value is not None:
                self._handle_coordinator_update()
                return

        if restored := await self.async_get_last_sensor_data():
            if restored.native_value is not None:
                self._attr_native_value = restored.native_value
                self._restored_native_value = True
                return

        restored_state = await self.async_get_last_state()
        if restored_state is None:
            return

        native_value = _coerce_restored_state_value(
            restored_state.state,
            self.entity_description.key,
        )
        if native_value is not None:
            self._attr_native_value = native_value
            self._restored_native_value = True

    @property
    def available(self) -> bool:
        """Return True if the sensor has data that can still be trusted."""
        if not self.coordinator.data:
            return False
        if self.entity_description.key in LIVE_KEYS:
            state = self.coordinator.reading_state(self._barcode)
            if state == READING_OFFLINE:
                return False
            if (
                state == READING_SILENT
                and self.coordinator.silent_action == STALE_UNAVAILABLE
            ):
                return False
        node_data = self.coordinator.data.get("nodes", {}).get(self._barcode)
        return node_data is not None or self._restored_native_value

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.affects((self._barcode,)):
            return  # another panel's update
        node_data = self.coordinator.data.get("nodes", {}).get(self._barcode)
        if node_data:
            value = node_data.get(self.entity_description.value_key)
            # Convert duty cycle from 0.0-1.0 to percentage
            if self.entity_description.key == "dc_dc_duty_cycle" and value is not None:
                value = value * 100
            self._attr_native_value = _round_state(self.entity_description.key, value)
            self._restored_native_value = False
        else:
            if not self._restored_native_value:
                self._attr_native_value = None
        if self.entity_description.key in ZERO_WHEN_ASLEEP_KEYS:
            # Asleep (night) and silent (daylight) panels read 0 when their
            # action is "zero"; with "hold" they keep their last values.
            state = self.coordinator.reading_state(self._barcode)
            if (
                state == READING_ASLEEP and self.coordinator.night_action == STALE_ZERO
            ) or (
                state == READING_SILENT and self.coordinator.silent_action == STALE_ZERO
            ):
                self._attr_native_value = 0.0
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        node_data = self.coordinator.data.get("nodes", {}).get(self._barcode)
        if not node_data:
            return None
        attrs: dict[str, Any] = {}
        if self._module_string:
            attrs["string_group"] = self._module_string
        gateway_id = node_data.get("gateway_id")
        if gateway_id is not None:
            attrs["gateway_id"] = gateway_id
        return attrs if attrs else None

    @property
    def last_reset(self) -> datetime | None:
        """Return last reset for daily energy sensor cycles."""
        if self.entity_description.key not in ("daily_energy", "readings_today"):
            return None

        node_data = self.coordinator.data.get("nodes", {}).get(self._barcode)
        if not node_data:
            return None

        reset_date = node_data.get("daily_reset_date")
        if not reset_date:
            return None

        try:
            reset_day = date.fromisoformat(reset_date)
        except ValueError:
            return None

        timezone = dt_util.UTC
        if self.hass is not None:
            timezone = dt_util.get_time_zone(self.hass.config.time_zone)

        return datetime.combine(reset_day, time.min, tzinfo=timezone)


@dataclass
class _AggregateEnergyData(ExtraStoredData):
    """Restorable aggregate energy: own total plus each member's last seen value."""

    native_value: float | None
    native_unit_of_measurement: str | None
    baselines: dict[str, float]
    cycle_date: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "native_value": self.native_value,
            "native_unit_of_measurement": self.native_unit_of_measurement,
            "baselines": dict(self.baselines),
            "cycle_date": self.cycle_date,
        }


class PyTapAggregateSensor(
    CoordinatorEntity[PyTapDataUpdateCoordinator], RestoreSensor
):
    """Aggregate across optimizers.

    Live values sum current member readings. Energy instead adds up each
    member's *increase* since it was last seen (measured plus estimated), in
    the aggregate's own restored counter, so a module moved between strings,
    added or removed never adds its lifetime energy or looks like a meter
    reset in long-term statistics. Adapted from Tim Laing's upstream PR
    azebro/pytap#35.
    """

    _attr_has_entity_name = True
    entity_description: PyTapAggregateSensorDescription

    def __init__(
        self,
        coordinator: PyTapDataUpdateCoordinator,
        description: PyTapAggregateSensorDescription,
        barcodes: list[str],
        device_info: DeviceInfo,
        unique_id: str,
    ) -> None:
        """Initialize aggregate sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._barcodes = list(barcodes)
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info
        self._restored_native_value = False
        self._energy_total: float | None = None
        self._baselines: dict[str, float] = {}
        self._cycle_date: str | None = None


    def set_barcodes(self, barcodes: list[str]) -> None:
        """Update membership when optimizers are discovered (energy doesn't jump)."""
        self._barcodes = list(barcodes)
    @property
    def _is_energy(self) -> bool:
        return self.entity_description.key in ("daily_energy", "total_energy")

    @property
    def _is_daily(self) -> bool:
        return self.entity_description.key == "daily_energy"

    async def async_added_to_hass(self) -> None:
        """Restore last known aggregate value from Home Assistant state cache."""
        await super().async_added_to_hass()

        if self._is_energy:
            await self._async_restore_energy()
            self._handle_coordinator_update()
            return

        nodes = self.coordinator.data.get("nodes", {})
        # Only short-circuit to live data if at least one node already has
        # a non-None value for the aggregate's underlying field. This avoids
        # overwriting a restored value with None from placeholder node dicts.
        value_key = getattr(self.entity_description, "value_key", None)
        if self.entity_description.key == "performance":
            has_fresh_data = any(
                (node := nodes.get(barcode)) is not None
                and node.get("power") is not None
                for barcode in self._barcodes
            )
        elif value_key:
            has_fresh_data = any(
                (node := nodes.get(barcode)) is not None
                and node.get(value_key) is not None
                for barcode in self._barcodes
            )
        else:
            has_fresh_data = False

        if has_fresh_data:
            self._handle_coordinator_update()
            return

        if restored := await self.async_get_last_sensor_data():
            if restored.native_value is not None:
                self._attr_native_value = restored.native_value
                self._restored_native_value = True
                return

        restored_state = await self.async_get_last_state()
        if restored_state is None:
            return

        native_value = _coerce_restored_state_value(
            restored_state.state,
            self.entity_description.key,
        )
        if native_value is not None:
            self._attr_native_value = native_value
            self._restored_native_value = True

    async def _async_restored_value(self) -> float | None:
        if (restored := await self.async_get_last_sensor_data()) is not None:
            if isinstance(restored.native_value, (int, float)):
                return float(restored.native_value)
        if (restored_state := await self.async_get_last_state()) is not None:
            return _coerce_restored_state_value(
                restored_state.state, self.entity_description.key
            )
        return None

    async def _async_restore_energy(self) -> None:
        """Restore the own total and member baselines; upgrade from a plain sum."""
        extra = await self.async_get_last_extra_data()
        data = extra.as_dict() if extra is not None else {}
        baselines = data.get("baselines")
        if isinstance(baselines, dict) and isinstance(
            data.get("native_value"), (int, float)
        ):
            self._energy_total = float(data["native_value"])
            self._baselines = {
                str(barcode): float(value)
                for barcode, value in baselines.items()
                if isinstance(value, (int, float))
            }
            cycle_date = data.get("cycle_date")
            self._cycle_date = cycle_date if isinstance(cycle_date, str) else None
        else:
            # Earlier versions summed the members; continue from that value
            # and treat every member as newly joined (no double counting).
            self._energy_total = await self._async_restored_value()
            if self._is_daily and self._energy_total is not None:
                restored_state = await self.async_get_last_state()
                if restored_state is not None:
                    self._cycle_date = (
                        dt_util.as_local(restored_state.last_updated).date().isoformat()
                    )
        if self._energy_total is not None:
            self._attr_native_value = round(self._energy_total, 2)
            self._restored_native_value = True

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        if not self._is_energy:
            return super().extra_restore_state_data
        return _AggregateEnergyData(
            self._energy_total,
            self.native_unit_of_measurement,
            self._baselines,
            self._cycle_date,
        )

    def _timezone(self) -> tzinfo:
        if self.hass is not None:
            return dt_util.get_time_zone(self.hass.config.time_zone) or dt_util.UTC
        return dt_util.UTC

    def _today(self) -> str:
        return dt_util.now(self._timezone()).date().isoformat()

    def _member_energy(self, barcode: str, node_data: dict[str, Any]) -> float | None:
        """A member's energy as this aggregate counts it: measured plus estimated."""
        value = node_data.get(self.entity_description.value_key)
        # String and total energy include energy estimated while an optimizer
        # was silent; its own sensors never do.
        est_today, est_total = self.coordinator.estimated_energy(barcode)
        estimate = est_today if self._is_daily else est_total
        if value is None and not estimate:
            return None
        return float(value or 0.0) + float(estimate or 0.0)

    def _accumulate_energy(self, nodes: dict[str, Any]) -> None:
        """Add each member's increase since it was last seen."""
        if self._is_daily and self._cycle_date != (today := self._today()):
            self._cycle_date = today
            if self._energy_total is not None:
                self._energy_total = 0.0
        values = {
            barcode: value
            for barcode in self._barcodes
            if (node := nodes.get(barcode)) is not None
            and (value := self._member_energy(barcode, node)) is not None
        }
        if self._energy_total is None:
            # First value ever: start from the members' current totals.
            self._energy_total = sum(values.values()) if values else None
        else:
            for barcode, value in values.items():
                previous = self._baselines.get(barcode)
                # New members and member resets (e.g. midnight) only rebase.
                if previous is not None and value > previous:
                    self._energy_total += value - previous
        # Keep the baselines of members that sent nothing this time.
        self._baselines = {
            barcode: value
            for barcode, value in {**self._baselines, **values}.items()
            if barcode in self._barcodes
        }

    @property
    def available(self) -> bool:
        """Return True when at least one constituent has data.

        Power and performance also need the converter online and at least
        one optimizer that isn't silent.
        """
        if not self.coordinator.data:
            return False
        nodes = self.coordinator.data.get("nodes", {})
        if self.entity_description.key in AGGREGATE_LIVE_KEYS:
            if not self.coordinator.link_up:
                return False
            if all(
                (node := nodes.get(b)) is None or self._live_power(b, node) is None
                for b in self._barcodes
            ) and not self._restored_native_value:
                return False
        return (
            any(nodes.get(barcode) is not None for barcode in self._barcodes)
            or self._restored_native_value
        )

    def _live_power(self, barcode: str, node_data: dict[str, Any]) -> Any:
        """Return the power a constituent contributes to this aggregate.

        An estimate from its string when it is silent and estimating is on;
        0 when asleep; None (left out) when offline, or silent with no
        estimate. With estimating turned off, a silent optimizer's last
        reported value is held instead.
        """
        estimate = self.coordinator.estimated_power(barcode)
        if estimate is not None:
            return estimate
        state = self.coordinator.reading_state(barcode)
        if state == READING_OFFLINE:
            return None
        if state == READING_SILENT:
            if self.coordinator.estimate_enabled:
                return None
            return node_data.get("power")
        if state == READING_ASLEEP:
            return 0.0
        return node_data.get("power")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.affects(self._barcodes):
            return  # none of this string's (or total's) panels changed
        nodes = self.coordinator.data.get("nodes", {})
        has_live_nodes = any(
            nodes.get(barcode) is not None for barcode in self._barcodes
        )

        if not has_live_nodes:
            if not self._restored_native_value:
                self._attr_native_value = None
            self.async_write_ha_state()
            return

        if self.entity_description.key == "performance":
            total_power = 0.0
            total_peak_power = 0.0

            for barcode in self._barcodes:
                node_data = nodes.get(barcode)
                if node_data is None:
                    continue

                power = self._live_power(barcode, node_data)
                peak_power = node_data.get(CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER)
                if power is None:
                    continue

                try:
                    peak_power_value = float(peak_power)
                except (TypeError, ValueError):
                    peak_power_value = float(DEFAULT_PEAK_POWER)

                if peak_power_value <= 0:
                    continue

                total_power += max(power, 0.0)
                total_peak_power += peak_power_value

            if total_peak_power > 0:
                self._attr_native_value = _round_state(
                    "performance", (total_power / total_peak_power) * 100.0
                )
            else:
                self._attr_native_value = None
            self._restored_native_value = False

            self.async_write_ha_state()
            return

        if self._is_energy:
            self._accumulate_energy(nodes)
            if self._energy_total is not None:
                self._attr_native_value = round(self._energy_total, 2)
                self._restored_native_value = False
            self.async_write_ha_state()
            return

        total: float | None = None

        for barcode in self._barcodes:
            node_data = nodes.get(barcode)
            if node_data is None:
                continue
            if self.entity_description.key == "power":
                value = self._live_power(barcode, node_data)
            else:
                value = node_data.get(self.entity_description.value_key)
            if value is not None:
                total = (total or 0.0) + value

        self._attr_native_value = _round_state(self.entity_description.key, total)
        self._restored_native_value = False
        self.async_write_ha_state()

    @property
    def last_reset(self) -> datetime | None:
        """Return last reset for aggregate daily energy (start of its own cycle)."""
        if not self._is_daily:
            return None
        cycle = self._cycle_date or self._today()
        try:
            cycle_day = date.fromisoformat(cycle)
        except ValueError:
            cycle_day = date.fromisoformat(self._today())
        return datetime.combine(cycle_day, time.min, tzinfo=self._timezone())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional aggregate state attributes."""
        nodes = self.coordinator.data.get("nodes", {})
        # Reporting means it has reported recently (Data validity
        # "reporting"): asleep, holding, estimated and silent optimizers
        # don't count.
        reporting = [
            barcode
            for barcode in self._barcodes
            if nodes.get(barcode) is not None
            and self.coordinator.data_validity(barcode) == VALIDITY_REPORTING
        ]
        attrs: dict[str, Any] = {
            "optimizer_count": len(self._barcodes),
            "reporting_count": len(reporting),
        }
        key = self.entity_description.key
        if key in AGGREGATE_LIVE_KEYS:
            attrs["estimated_count"] = sum(
                1
                for b in self._barcodes
                if self.coordinator.estimated_power(b) is not None
            )
        elif key in ("daily_energy", "total_energy"):
            index = 0 if key == "daily_energy" else 1
            attrs["estimated_energy"] = round(
                sum(self.coordinator.estimated_energy(b)[index] for b in self._barcodes),
                1,
            )
        return attrs


class PyTapNodeMappingSensor(
    CoordinatorEntity[PyTapDataUpdateCoordinator], SensorEntity
):
    """Shows whether optimizer readings can be matched to configured modules.

    Readings identify optimizers by node ID; the barcode mapping comes from
    the gateway's node table, which PyTap only sees when the CCA reads it
    (normally when the optimizers wake up in the morning). Until then the
    other sensors stay unknown, and this sensor says why.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "node_mapping"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [MAPPING_WAITING, MAPPING_PARTIAL, MAPPING_COMPLETE]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: PyTapDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id

    @property
    def native_value(self) -> str:
        """Return waiting, partial or complete."""
        return self.coordinator.mapping_status()["state"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return mapping counts and the last node table time."""
        status = self.coordinator.mapping_status()
        status.pop("state")
        return status


class PyTapDataValiditySensor(
    CoordinatorEntity[PyTapDataUpdateCoordinator], SensorEntity
):
    """How trustworthy an optimizer's current readings are.

    reporting: fresh readings. holding: quiet for a couple of minutes, last
    values held. estimated: quiet; the string and total sensors use an
    estimate from the rest of its string. silent_estimated: quiet for a
    long time by day, own live readings unavailable, still estimated in
    the string. asleep: night, live readings 0. silent: quiet for a long
    time by day with no estimate, live readings unavailable. Unavailable
    while the gateway's bus status isn't normal.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "data_validity"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = VALIDITY_STATES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Attributes are kept stable while the optimizer reports, so they don't
    # cause a state write per reading: last_report appears only once it stops
    # reporting, and the share is rounded to 0.05.
    _unrecorded_attributes = frozenset({"last_report", "share"})

    def __init__(
        self,
        coordinator: PyTapDataUpdateCoordinator,
        module_config: dict[str, str],
        inverter: str = "",
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._barcode = module_config[CONF_MODULE_BARCODE]
        self._attr_unique_id = f"{DOMAIN}_{self._barcode}_data_validity"
        self.entity_id = f"sensor.{optimizer_object_id(self._barcode)}_data_validity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._barcode)},
            name=optimizer_device_name(
                inverter,
                module_config.get(CONF_MODULE_STRING, ""),
                module_config[CONF_MODULE_NAME],
                self._barcode,
            ),
            manufacturer="Tigo Energy",
            model="TS4",
            serial_number=self._barcode,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.affects((self._barcode,)):
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Unavailable while the gateway's bus status isn't normal."""
        return self.coordinator.link_up

    @property
    def native_value(self) -> str | None:
        """Return the validity state."""
        return self.coordinator.data_validity(self._barcode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the learned share, estimated energy and, once the optimizer
        has stopped reporting, when it last reported."""
        share = self.coordinator.share(self._barcode)
        attrs: dict[str, Any] = {
            "share": round(round(share * 20) / 20, 2) if share is not None else None,
            "estimated_energy_today": round(
                self.coordinator.estimated_energy(self._barcode)[0], 1
            ),
        }
        if self.native_value not in (None, VALIDITY_REPORTING):
            node = self.coordinator.data.get("nodes", {}).get(self._barcode) or {}
            attrs["last_report"] = node.get("last_update")
        return attrs


class PyTapBusStatusSensor(
    CoordinatorEntity[PyTapDataUpdateCoordinator], SensorEntity
):
    """Whether data is arriving from this gateway's CCA bus.

    normal: bytes are arriving. converter_unreachable: the TCP connection to
    the RS-485 converter fails or drops (converter power or network).
    bus_quiet: connected, but nothing on the bus (CCA off, or the
    converter's wiring to the bus). Not normal after LINK_DOWN_SECONDS
    without data; every optimizer's live readings are then unavailable.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "bus_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = BUS_STATES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"last_data"})

    def __init__(
        self,
        coordinator: PyTapDataUpdateCoordinator,
        device_info: DeviceInfo,
        unique_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id

    @property
    def native_value(self) -> str:
        """Return normal, converter_unreachable or bus_quiet."""
        return self.coordinator.bus_status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when data last arrived, once it has stopped arriving.

        Left out while normal: it would change on every update and make
        Home Assistant write a new state row each time.
        """
        if self.native_value == BUS_NORMAL:
            return {}
        last = self.coordinator.last_data
        return {"last_data": last.isoformat() if last else None}
