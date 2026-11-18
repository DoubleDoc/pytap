"""Sensor platform for PyTap integration.

Creates sensor entities for each user-configured Tigo optimizer module.
Entities are keyed by barcode (stable identifier) and created deterministically
from the configured module list — no auto-discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
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
    CONF_MODULES,
    DEFAULT_PEAK_POWER,
    DOMAIN,
    MAPPING_COMPLETE,
    MAPPING_PARTIAL,
    MAPPING_WAITING,
    READING_ASLEEP,
    READING_OFFLINE,
    READING_SILENT,
    VALIDITY_REPORTING,
    VALIDITY_STATES,
)
from .coordinator import PyTapDataUpdateCoordinator
from .naming import optimizer_device_name, string_device_name, total_device_name

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
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PyTap sensors from a config entry.

    Creates sensor entities deterministically from the configured module list.
    Each configured module gets the 12 measurement sensors plus a Data validity sensor.
    """
    coordinator: PyTapDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    modules: list[dict[str, str]] = entry.data.get(CONF_MODULES, [])
    inverter: str = entry.data.get(CONF_INVERTER, "") or ""

    entities: list[SensorEntity] = []
    string_to_barcodes: dict[str, list[str]] = {}
    all_barcodes: list[str] = []

    for module_config in modules:
        barcode = module_config.get(CONF_MODULE_BARCODE, "")
        string_name = module_config.get(CONF_MODULE_STRING, "")
        if not barcode:
            continue
        all_barcodes.append(barcode)
        if string_name:
            string_to_barcodes.setdefault(string_name, []).append(barcode)

        for description in SENSOR_DESCRIPTIONS:
            entities.append(
                PyTapSensor(
                    coordinator=coordinator,
                    description=description,
                    module_config=module_config,
                    entry=entry,
                    inverter=inverter,
                )
            )
        entities.append(
            PyTapDataValiditySensor(
                coordinator=coordinator,
                module_config=module_config,
                inverter=inverter,
            )
        )

    for string_name, barcodes in string_to_barcodes.items():
        device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_string_{string_name}")},
            name=string_device_name(inverter, string_name),
            manufacturer="Tigo Energy",
            model="String Aggregate",
        )
        for description in STRING_SENSOR_DESCRIPTIONS:
            entities.append(
                PyTapAggregateSensor(
                    coordinator=coordinator,
                    description=description,
                    barcodes=barcodes,
                    device_info=device_info,
                    unique_id=(
                        f"{DOMAIN}_{entry.entry_id}_string_{string_name}_{description.key}"
                    ),
                )
            )

    total_device_info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_installation")},
        name=total_device_name(inverter),
        manufacturer="Tigo Energy",
        model="Installation Aggregate",
    )

    if all_barcodes:
        device_info = total_device_info
        for description in INSTALLATION_SENSOR_DESCRIPTIONS:
            entities.append(
                PyTapAggregateSensor(
                    coordinator=coordinator,
                    description=description,
                    barcodes=all_barcodes,
                    device_info=device_info,
                    unique_id=(
                        f"{DOMAIN}_{entry.entry_id}_installation_{description.key}"
                    ),
                )
            )

        entities.append(
            PyTapNodeMappingSensor(
                coordinator=coordinator,
                device_info=total_device_info,
                unique_id=f"{DOMAIN}_{entry.entry_id}_node_mapping",
            )
        )
        entities.append(
            PyTapBusStatusSensor(
                coordinator=coordinator,
                device_info=total_device_info,
                unique_id=f"{DOMAIN}_{entry.entry_id}_bus_status",
            )
        )

    async_add_entities(entities)


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

        # Device info — groups all sensors for one optimizer under one device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._barcode)},
            name=optimizer_device_name(
                inverter, self._module_string, self._module_name
            ),
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
        if self.entity_description.key in LIVE_KEYS and (
            self.coordinator.reading_state(self._barcode)
            in (READING_SILENT, READING_OFFLINE)
        ):
            return False
        node_data = self.coordinator.data.get("nodes", {}).get(self._barcode)
        return node_data is not None or self._restored_native_value

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        node_data = self.coordinator.data.get("nodes", {}).get(self._barcode)
        if node_data:
            value = node_data.get(self.entity_description.value_key)
            # Convert duty cycle from 0.0-1.0 to percentage
            if self.entity_description.key == "dc_dc_duty_cycle" and value is not None:
                value = round(value * 100, 2)
            self._attr_native_value = value
            self._restored_native_value = False
        else:
            if not self._restored_native_value:
                self._attr_native_value = None
        if (
            self.entity_description.key in ZERO_WHEN_ASLEEP_KEYS
            and self.coordinator.reading_state(self._barcode) == READING_ASLEEP
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


class PyTapAggregateSensor(
    CoordinatorEntity[PyTapDataUpdateCoordinator], RestoreSensor
):
    """Aggregate sensor that sums values across multiple optimizers."""

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
        self._barcodes = barcodes
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info
        self._restored_native_value = False

    async def async_added_to_hass(self) -> None:
        """Restore last known aggregate value from Home Assistant state cache."""
        await super().async_added_to_hass()

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
                self._attr_native_value = round(
                    (total_power / total_peak_power) * 100.0, 2
                )
            else:
                self._attr_native_value = None
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
                # String and total energy include energy estimated while an
                # optimizer was silent; its own sensors never do.
                est_today, est_total = self.coordinator.estimated_energy(barcode)
                if self.entity_description.key == "daily_energy":
                    value = (value or 0.0) + est_today
                elif self.entity_description.key == "total_energy":
                    value = (value or 0.0) + est_total
            if value is not None:
                total = (total or 0.0) + value

        self._attr_native_value = total
        self._restored_native_value = False
        self.async_write_ha_state()

    @property
    def last_reset(self) -> datetime | None:
        """Return last reset for aggregate daily energy."""
        if self.entity_description.key != "daily_energy":
            return None

        timezone = dt_util.UTC
        if self.hass is not None:
            timezone = dt_util.get_time_zone(self.hass.config.time_zone) or dt_util.UTC

        return datetime.combine(dt_util.now(timezone).date(), time.min, tzinfo=timezone)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional aggregate state attributes."""
        nodes = self.coordinator.data.get("nodes", {})
        reporting = [
            barcode
            for barcode in self._barcodes
            if nodes.get(barcode) is not None
            and self.coordinator.reading_state(barcode)
            not in (READING_SILENT, READING_OFFLINE)
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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._barcode)},
            name=optimizer_device_name(
                inverter,
                module_config.get(CONF_MODULE_STRING, ""),
                module_config[CONF_MODULE_NAME],
            ),
            manufacturer="Tigo Energy",
            model="TS4",
            serial_number=self._barcode,
        )

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
