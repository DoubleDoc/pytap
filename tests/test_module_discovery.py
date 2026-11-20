"""Passive module discovery, fitted to the fork's position + barcode naming.

Adapted from Tim Laing's upstream PR azebro/pytap#37.
"""

from datetime import datetime
from unittest.mock import MagicMock

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap import (
    _async_remove_orphaned_devices,
    async_migrate_entry,
    async_remove_config_entry_device,
)
from custom_components.pytap.const import (
    CONF_AUTO_DISCOVERY,
    CONF_EXCLUDED_BARCODES,
    CONF_INVERTER,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_PEAK_POWER,
    CONF_MODULE_STRING,
    CONF_MODULES,
    DEFAULT_PORT,
    DOMAIN,
)
from custom_components.pytap.coordinator import PyTapDataUpdateCoordinator
from custom_components.pytap.pytap.core.events import (
    InfrastructureEvent,
    PowerReportEvent,
)
from custom_components.pytap.sensor import async_setup_entry as sensor_setup
from tests.test_write_interval import MOCK_MODULES

KNOWN = "A-1234567B"
NEW = "4-5DB467Z"


def _entry(entry_id="disc", *, auto=True, excluded=(), modules=MOCK_MODULES):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.options = {}
    entry.data = {
        CONF_HOST: "192.168.1.100", CONF_PORT: DEFAULT_PORT, CONF_MODULES: modules,
        CONF_INVERTER: "Master", CONF_AUTO_DISCOVERY: auto,
        CONF_EXCLUDED_BARCODES: list(excluded),
    }
    return entry


def _coordinator(hass, **kwargs) -> PyTapDataUpdateCoordinator:
    coordinator = PyTapDataUpdateCoordinator(hass, _entry(**kwargs))
    coordinator._schedule_save = MagicMock()
    coordinator.config_entry = coordinator.config_entry or MagicMock()
    return coordinator


def _hear(coordinator, barcode=NEW, node_id=7, power=120.0) -> None:
    coordinator._handle_infrastructure(InfrastructureEvent(
        gateways={1: {"address": "aa:bb", "version": "1.0"}},
        nodes={node_id: {"address": "11:22", "barcode": barcode}},
        timestamp=datetime.now(),
    ))
    coordinator._process_event(PowerReportEvent(
        gateway_id=1, node_id=node_id, barcode=barcode, voltage_in=30.0,
        voltage_out=30.0, current_in=power / 30.0, temperature=25.0,
        dc_dc_duty_cycle=0.5, rssi=-60, timestamp=datetime.now(),
    ))


async def test_discovered_optimizer_is_tracked_unnamed_and_unrated(hass) -> None:
    coordinator = _coordinator(hass)
    _hear(coordinator)
    module = coordinator.discovered_modules[NEW]
    assert module[CONF_MODULE_NAME] == ""
    assert module[CONF_MODULE_STRING] == "Discovered"
    assert module[CONF_MODULE_PEAK_POWER] == 0
    node = coordinator._state["nodes"][NEW]
    assert node["power"] is not None  # first reading isn't dropped
    assert node["performance"] is None  # no rating, no percentage
    assert coordinator.module_count == 2


async def test_manual_mode_and_exclusions_are_respected(hass) -> None:
    manual = _coordinator(hass, entry_id="m", auto=False)
    _hear(manual)
    assert NEW not in manual.configured_barcodes
    manual.release_barcode_claims()
    excluded = _coordinator(hass, entry_id="x", excluded=[NEW])
    _hear(excluded)
    assert NEW not in excluded.configured_barcodes


async def test_one_entry_per_barcode(hass) -> None:
    first = _coordinator(hass, entry_id="one")
    second = _coordinator(hass, entry_id="two", modules=[])
    _hear(first)
    _hear(second)
    assert NEW in first.configured_barcodes
    assert NEW not in second.configured_barcodes
    first.release_barcode_claims()
    _hear(second, node_id=8)
    assert NEW in second.configured_barcodes


async def test_discovered_modules_survive_a_restart(hass) -> None:
    coordinator = _coordinator(hass)
    _hear(coordinator)
    with coordinator._state_lock:
        saved = coordinator._save_payload()
    coordinator.release_barcode_claims()

    restored = _coordinator(hass, auto=False)  # even with discovery now off
    restored._store = MagicMock()

    async def _load():
        return saved

    restored._store.async_load = _load
    await restored._async_load_coordinator_state()
    assert NEW in restored.configured_barcodes
    assert restored.store_restored


async def test_discovered_entity_ids_use_the_barcode(hass) -> None:
    coordinator = _coordinator(hass)
    _hear(coordinator)
    entry = coordinator.config_entry
    entry.runtime_data = coordinator
    entry.async_on_unload = MagicMock()
    added: list = []
    await sensor_setup(hass, entry, added.extend)
    ids = {e.entity_id for e in added if getattr(e, "_barcode", None) == NEW}
    assert "sensor.tigo_4_5db467z_power" in ids
    assert "sensor.tigo_4_5db467z_data_validity" in ids
    assert any(type(e).__name__ == "PyTapDiscoverySensor" for e in added)
    device_names = {e.device_info["name"] for e in added if getattr(e, "_barcode", None) == NEW}
    assert device_names == {"Tigo Master Discovered 4-5DB467Z"}


async def test_naming_a_discovered_optimizer_keeps_its_entity_ids(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=8, state=config_entries.ConfigEntryState.LOADED,
        data={CONF_HOST: "h", CONF_PORT: 1, CONF_MODULES: [], CONF_INVERTER: "Master",
              CONF_AUTO_DISCOVERY: True, CONF_EXCLUDED_BARCODES: []},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(discovered_modules={NEW: {
        CONF_MODULE_BARCODE: NEW, CONF_MODULE_NAME: "", CONF_MODULE_STRING: "Discovered",
        CONF_MODULE_PEAK_POWER: 0}})
    reg = er.async_get(hass)
    reg.async_get_or_create("sensor", DOMAIN, f"{DOMAIN}_{NEW}_power", config_entry=entry,
                            suggested_object_id="tigo_4_5db467z_power")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "edit_module"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"edit_barcode": NEW})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MODULE_STRING: "2", CONF_MODULE_NAME: "P07", CONF_MODULE_PEAK_POWER: 455})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    assert reg.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{NEW}_power") == (
        "sensor.tigo_4_5db467z_power"
    )
    (module,) = entry.data[CONF_MODULES]
    assert module[CONF_MODULE_NAME] == "P07"
    assert module[CONF_MODULE_STRING] == "2"


async def test_removing_a_module_excludes_it(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=8,
        data={CONF_HOST: "h", CONF_PORT: 1, CONF_MODULES: list(MOCK_MODULES),
              CONF_AUTO_DISCOVERY: True, CONF_EXCLUDED_BARCODES: []},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_module"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"remove_barcode": KNOWN})
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"})
    assert entry.data[CONF_MODULES] == []
    assert entry.data[CONF_EXCLUDED_BARCODES] == [KNOWN]


async def test_manual_entry_cannot_save_with_no_modules(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=8,
        data={CONF_HOST: "h", CONF_PORT: 1, CONF_MODULES: list(MOCK_MODULES),
              CONF_AUTO_DISCOVERY: False},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_module"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"remove_barcode": KNOWN})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"})
    assert result["errors"] == {"base": "no_modules"}


async def test_deleting_an_optimizer_device_excludes_it(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=8,
        data={CONF_HOST: "h", CONF_PORT: 1, CONF_MODULES: list(MOCK_MODULES),
              CONF_AUTO_DISCOVERY: True, CONF_EXCLUDED_BARCODES: []},
    )
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    optimizer = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, KNOWN)})
    total = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_installation")})
    assert not await async_remove_config_entry_device(hass, entry, total)
    assert await async_remove_config_entry_device(hass, entry, optimizer)
    assert entry.data[CONF_MODULES] == []
    assert entry.data[CONF_EXCLUDED_BARCODES] == [KNOWN]


async def test_setup_removes_excluded_and_empty_string_devices(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=8,
        data={CONF_HOST: "h", CONF_PORT: 1, CONF_MODULES: list(MOCK_MODULES),
              CONF_AUTO_DISCOVERY: False, CONF_EXCLUDED_BARCODES: [NEW]},
    )
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    for ident in (KNOWN, NEW, f"{entry.entry_id}_string_A", f"{entry.entry_id}_string_Old"):
        dev_reg.async_get_or_create(config_entry_id=entry.entry_id,
                                    identifiers={(DOMAIN, ident)})
    coordinator = MagicMock(modules=list(MOCK_MODULES), store_restored=True,
                            auto_discovery=False)
    _async_remove_orphaned_devices(hass, entry, coordinator)
    left = {next(iter(d.identifiers))[1]
            for d in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)}
    assert left == {KNOWN, f"{entry.entry_id}_string_A"}


async def test_existing_entries_keep_manual_mode(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=7,
        data={CONF_HOST: "h", CONF_MODULES: MOCK_MODULES, "connection_type": "tcp"},
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.data[CONF_AUTO_DISCOVERY] is False
    assert entry.data[CONF_EXCLUDED_BARCODES] == []
