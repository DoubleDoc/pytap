"""Tests for device naming, the inverter name, module/string editing and
the node mapping status."""

from datetime import datetime
from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap import async_migrate_entry
from custom_components.pytap.const import (
    CONF_INVERTER,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_PEAK_POWER,
    CONF_MODULE_STRING,
    CONF_MODULES,
    DEFAULT_PORT,
    DOMAIN,
    MAPPING_COMPLETE,
    MAPPING_PARTIAL,
    MAPPING_WAITING,
)
from custom_components.pytap.coordinator import PyTapDataUpdateCoordinator
from custom_components.pytap.naming import (
    optimizer_device_name,
    string_device_name,
    string_label,
    total_device_name,
)
from custom_components.pytap.pytap.core.events import (
    InfrastructureEvent,
    PowerReportEvent,
)
from custom_components.pytap.sensor import PyTapSensor, async_setup_entry

MODULES = [
    {
        CONF_MODULE_STRING: "2",
        CONF_MODULE_NAME: "P01",
        CONF_MODULE_BARCODE: "A-1234567B",
        CONF_MODULE_PEAK_POWER: 455,
    },
    {
        CONF_MODULE_STRING: "2",
        CONF_MODULE_NAME: "P02",
        CONF_MODULE_BARCODE: "C-2345678D",
        CONF_MODULE_PEAK_POWER: 455,
    },
]


# ---- naming ----


def test_string_label() -> None:
    """Numeric strings get an S prefix; other labels are kept."""
    assert string_label("2") == "S2"
    assert string_label(" 3 ") == "S3"
    assert string_label("East roof") == "East roof"
    assert string_label("A") == "A"
    assert string_label("") == ""


def test_device_names_with_inverter() -> None:
    """Multi-gateway installs put the inverter first."""
    assert optimizer_device_name("Master", "P01") == "Tigo Master P01"
    assert string_device_name("Master", "2") == "Tigo Master String 2"
    assert total_device_name("Master") == "Tigo Master Total"


def test_device_names_without_inverter() -> None:
    """Single-gateway installs leave the inverter out."""
    assert optimizer_device_name("", "P01") == "Tigo P01"
    assert string_device_name("", "East") == "Tigo String East"
    assert total_device_name("") == "Tigo Total"


async def test_sensor_device_names_use_inverter(hass: HomeAssistant) -> None:
    """Optimizer, string and total devices are named from the hierarchy."""
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.data = {
        CONF_HOST: "192.168.1.100",
        CONF_PORT: DEFAULT_PORT,
        CONF_INVERTER: "Master",
        CONF_MODULES: MODULES,
    }
    coordinator = MagicMock(spec=PyTapDataUpdateCoordinator)
    coordinator.data = {"nodes": {}}
    from tests.conftest import with_modules

    with_modules(coordinator, MODULES)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = coordinator

    entities = []
    await async_setup_entry(hass, entry, lambda e: entities.extend(e))

    names = {e.device_info["name"] for e in entities}
    assert names == {
        "Tigo Master P01",
        "Tigo Master P02",
        "Tigo Master String 2",
        "Tigo Master Total",
    }
    optimizer = next(e for e in entities if isinstance(e, PyTapSensor))
    # New optimizer entities get position + barcode entity IDs.
    assert {e.entity_id for e in entities if isinstance(e, PyTapSensor)} >= {
        "sensor.tigo_master_p01_a_1234567b_power",
        "sensor.tigo_master_p02_c_2345678d_daily_energy",
    }
    # No attribute that changes with every reading (it would force a state
    # row per reading even when the value is unchanged).
    optimizer.coordinator.data = {"nodes": {optimizer._barcode: {
        "gateway_id": 1, "last_update": "2026-11-17T12:00:00"}}}
    assert "last_update" not in (optimizer.extra_state_attributes or {})


# ---- migration ----


async def test_migration_v5_to_v6_adds_blank_inverter(hass: HomeAssistant) -> None:
    """Existing entries get an empty inverter name."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        data={CONF_HOST: "192.168.1.100", CONF_MODULES: MODULES},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 8
    assert entry.data["connection_type"] == "tcp"
    assert entry.data[CONF_INVERTER] == ""
    assert entry.data[CONF_MODULES] == MODULES


# ---- node mapping status ----


def _coordinator(hass: HomeAssistant) -> PyTapDataUpdateCoordinator:
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {}
    entry.data = {CONF_HOST: "192.168.1.100", CONF_MODULES: MODULES}
    return PyTapDataUpdateCoordinator(hass, entry)


def _power_report(node_id: int) -> PowerReportEvent:
    return PowerReportEvent(
        gateway_id=1,
        node_id=node_id,
        barcode=None,
        voltage_in=30.0,
        voltage_out=29.0,
        current_in=8.0,
        temperature=40.0,
        dc_dc_duty_cycle=0.9,
        rssi=-60,
        timestamp=datetime.now(),
    )


def test_mapping_waiting_then_complete(hass: HomeAssistant) -> None:
    """Unresolved reports show as waiting; a node table completes it."""
    coordinator = _coordinator(hass)
    status = coordinator.mapping_status()
    assert status["state"] == MAPPING_WAITING
    assert status["configured"] == 2
    assert status["mapped"] == 0
    assert status["last_node_table"] is None

    coordinator._handle_power_report(_power_report(5))
    coordinator._handle_power_report(_power_report(6))
    coordinator._handle_power_report(_power_report(5))
    assert coordinator.mapping_status()["unidentified_nodes"] == 2

    coordinator._handle_infrastructure(
        InfrastructureEvent(
            gateways={1: {"address": None, "version": None}},
            nodes={
                5: {"address": None, "barcode": "A-1234567B"},
                6: {"address": None, "barcode": "C-2345678D"},
            },
            timestamp=datetime.now(),
        )
    )
    status = coordinator.mapping_status()
    assert status["state"] == MAPPING_COMPLETE
    assert status["mapped"] == 2
    assert status["unmapped_barcodes"] == []
    assert status["unidentified_nodes"] == 0
    assert status["last_node_table"] is not None


def test_mapping_partial(hass: HomeAssistant) -> None:
    """A node table missing a configured barcode reports partial."""
    coordinator = _coordinator(hass)
    coordinator._handle_infrastructure(
        InfrastructureEvent(
            gateways={},
            nodes={5: {"address": None, "barcode": "A-1234567B"}},
            timestamp=datetime.now(),
        )
    )
    status = coordinator.mapping_status()
    assert status["state"] == MAPPING_PARTIAL
    assert status["unmapped_barcodes"] == ["C-2345678D"]


# ---- options flow ----


def _entry(hass: HomeAssistant, inverter: str = "") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=6,
        title="PyTap (192.168.1.100)",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_PORT: DEFAULT_PORT,
            CONF_INVERTER: inverter,
            CONF_MODULES: [dict(m) for m in MODULES],
        },
        unique_id="pytap_naming_test",
    )
    entry.add_to_hass(hass)
    return entry


async def _menu(hass: HomeAssistant, flow_id: str, step: str):
    return await hass.config_entries.options.async_configure(flow_id, {"next_step_id": step})


async def test_options_change_inverter(hass: HomeAssistant) -> None:
    """The inverter name is saved on done, trimmed."""
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    result = await _menu(hass, flow_id, "change_inverter")
    assert result["step_id"] == "change_inverter"
    result = await hass.config_entries.options.async_configure(flow_id, {CONF_INVERTER: "  Master "})
    assert result["step_id"] == "init"
    assert entry.data[CONF_INVERTER] == ""  # not saved until done
    result = await _menu(hass, flow_id, "done")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_INVERTER] == "Master"


async def test_options_edit_module(hass: HomeAssistant) -> None:
    """Editing changes string/name/peak power, keeps the barcode, and
    leaves the entry untouched until done."""
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    await _menu(hass, flow_id, "edit_module")
    result = await hass.config_entries.options.async_configure(flow_id, {"edit_barcode": "C-2345678D"})
    assert result["step_id"] == "edit_module_details"
    result = await hass.config_entries.options.async_configure(
        flow_id,
        {CONF_MODULE_STRING: "3", CONF_MODULE_NAME: "P07", CONF_MODULE_PEAK_POWER: 400},
    )
    assert result["step_id"] == "init"
    assert entry.data[CONF_MODULES][1][CONF_MODULE_NAME] == "P02"

    await _menu(hass, flow_id, "done")
    edited = entry.data[CONF_MODULES][1]
    assert edited == {
        CONF_MODULE_STRING: "3",
        CONF_MODULE_NAME: "P07",
        CONF_MODULE_BARCODE: "C-2345678D",
        CONF_MODULE_PEAK_POWER: 400,
    }


async def test_options_edit_module_requires_name(hass: HomeAssistant) -> None:
    """A blank name is rejected."""
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    await _menu(hass, flow_id, "edit_module")
    await hass.config_entries.options.async_configure(flow_id, {"edit_barcode": "A-1234567B"})
    result = await hass.config_entries.options.async_configure(
        flow_id, {CONF_MODULE_STRING: "2", CONF_MODULE_NAME: "  "}
    )
    assert result["errors"] == {CONF_MODULE_NAME: "missing_name"}


async def test_options_rename_string_keeps_history(hass: HomeAssistant) -> None:
    """Renaming a string moves its aggregate entities and device."""
    entry = _entry(hass)
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_string_2")},
    )
    power = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{entry.entry_id}_string_2_power",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="tigo_string_2_power",
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    await _menu(hass, flow_id, "rename_string")
    result = await hass.config_entries.options.async_configure(
        flow_id, {"old_string": "2", "new_string": "East"}
    )
    # Renaming twice in one session tracks back to the saved name.
    await _menu(hass, flow_id, "rename_string")
    await hass.config_entries.options.async_configure(flow_id, {"old_string": "East", "new_string": "West"})
    await _menu(hass, flow_id, "done")

    assert {m[CONF_MODULE_STRING] for m in entry.data[CONF_MODULES]} == {"West"}
    moved = ent_reg.async_get(power.entity_id)
    assert moved.unique_id == f"{DOMAIN}_{entry.entry_id}_string_West_power"
    assert dev_reg.async_get(device.id).identifiers == {(DOMAIN, f"{entry.entry_id}_string_West")}


async def test_options_rename_string_rejects_existing_name(
    hass: HomeAssistant,
) -> None:
    """A string can't be renamed onto another existing string."""
    entry = _entry(hass)
    modules = [dict(m) for m in MODULES]
    modules[1][CONF_MODULE_STRING] = "3"
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_MODULES: modules})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    await _menu(hass, flow_id, "rename_string")
    result = await hass.config_entries.options.async_configure(
        flow_id, {"old_string": "2", "new_string": "3"}
    )
    assert result["errors"] == {"new_string": "string_exists"}


def test_optimizer_entity_ids_are_position_plus_barcode() -> None:
    from custom_components.pytap.naming import (
        legacy_optimizer_object_id,
        optimizer_object_id,
    )

    assert optimizer_object_id("Master", "P01", "4-5DB467Z") == "tigo_master_p01_4_5db467z"
    assert optimizer_object_id("", "P01", "4-5DB467Z") == "tigo_p01_4_5db467z"
    assert optimizer_object_id("Master", "", "4-5DB467Z") == "tigo_master_4_5db467z"
    assert legacy_optimizer_object_id("Master", "2", "P01") == "tigo_master_s2_p01"


async def test_migration_v7_to_v8_renames_optimizer_entities(hass: HomeAssistant) -> None:
    """Old string-based IDs move to position + barcode; customised ones stay."""
    from homeassistant.helpers import entity_registry as er

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=7,
        data={CONF_HOST: "h", CONF_MODULES: MODULES, CONF_INVERTER: "Master",
              "connection_type": "tcp"},
    )
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    a, b = MODULES[0][CONF_MODULE_BARCODE], MODULES[1][CONF_MODULE_BARCODE]
    reg.async_get_or_create("sensor", DOMAIN, f"{DOMAIN}_{a}_power", config_entry=entry,
                            suggested_object_id="tigo_master_s2_p01_power")
    reg.async_get_or_create("sensor", DOMAIN, f"{DOMAIN}_{a}_data_validity",
                            config_entry=entry,
                            suggested_object_id="tigo_master_s2_p01_data_validity")
    reg.async_get_or_create("sensor", DOMAIN, f"{DOMAIN}_{b}_power", config_entry=entry,
                            suggested_object_id="my_custom_name")

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 8
    slug_a = a.lower().replace("-", "_")
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{a}_power") == (
        f"sensor.tigo_master_p01_{slug_a}_power"
    )
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{a}_data_validity") == (
        f"sensor.tigo_master_p01_{slug_a}_data_validity"
    )
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{b}_power") == (
        "sensor.my_custom_name"
    )
