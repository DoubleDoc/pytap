"""One CCA serving several inverters: an optional inverter per string."""

from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap.const import (
    CONF_INVERTER,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_PEAK_POWER,
    CONF_MODULE_STRING,
    CONF_MODULES,
    CONF_STRING_INVERTERS,
    DOMAIN,
)
from custom_components.pytap.coordinator import PyTapDataUpdateCoordinator
from custom_components.pytap.sensor import PyTapAggregateSensor, async_setup_entry
from tests.conftest import with_modules

MODULES = [
    {CONF_MODULE_STRING: "1", CONF_MODULE_NAME: "P01", CONF_MODULE_BARCODE: "A-1111111A",
     CONF_MODULE_PEAK_POWER: 455},
    {CONF_MODULE_STRING: "2", CONF_MODULE_NAME: "P01", CONF_MODULE_BARCODE: "B-2222222B",
     CONF_MODULE_PEAK_POWER: 455},
    {CONF_MODULE_STRING: "3", CONF_MODULE_NAME: "P01", CONF_MODULE_BARCODE: "C-3333333C",
     CONF_MODULE_PEAK_POWER: 455},
]


async def _entities(hass, entry_inverter: str, string_inverters: dict) -> list:
    entry = MagicMock()
    entry.entry_id = "cca"
    entry.data = {CONF_HOST: "h", CONF_PORT: 1, CONF_MODULES: MODULES,
                  CONF_INVERTER: entry_inverter, CONF_STRING_INVERTERS: string_inverters}
    coordinator = with_modules(MagicMock(spec=PyTapDataUpdateCoordinator), MODULES)
    coordinator.data = {"nodes": {}}
    entry.runtime_data = coordinator
    added: list = []
    await async_setup_entry(hass, entry, added.extend)
    return added


async def test_one_cca_two_inverters(hass: HomeAssistant) -> None:
    added = await _entities(hass, "", {"1": "Master", "2": "Slave", "3": "Slave"})
    names = {e.device_info["name"] for e in added}
    assert {"Tigo Master S1 P01", "Tigo Slave S2 P01", "Tigo Slave S3 P01",
            "Tigo Master String 1", "Tigo Slave String 2",
            "Tigo Total", "Tigo Master Total", "Tigo Slave Total"} <= names
    totals = {
        e.unique_id: sorted(e._barcodes) for e in added
        if isinstance(e, PyTapAggregateSensor) and e.unique_id.endswith("_power")
        and "_inverter_" in e.unique_id
    }
    assert totals == {
        f"{DOMAIN}_cca_inverter_Master_power": ["A-1111111A"],
        f"{DOMAIN}_cca_inverter_Slave_power": ["B-2222222B", "C-3333333C"],
    }
    # Entity IDs don't depend on inverter, string or position.
    assert "sensor.tigo_b_2222222b_power" in {e.entity_id for e in added if hasattr(e, "_barcode")}


async def test_one_cca_per_inverter_has_no_extra_totals(hass: HomeAssistant) -> None:
    added = await _entities(hass, "Master", {})
    names = {e.device_info["name"] for e in added}
    assert "Tigo Master S1 P01" in names
    assert not any("_inverter_" in (e.unique_id or "") for e in added)


async def test_options_set_rename_and_clear_a_strings_inverter(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=8, data={
        CONF_HOST: "h", CONF_PORT: 1, CONF_MODULES: MODULES, CONF_INVERTER: ""})
    entry.add_to_hass(hass)

    async def step(flow_id, data):
        return await hass.config_entries.options.async_configure(flow_id, data)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    fid = result["flow_id"]
    await step(fid, {"next_step_id": "string_inverter"})
    await step(fid, {"string": "1", CONF_INVERTER: "Master"})
    await step(fid, {"next_step_id": "string_inverter"})
    await step(fid, {"string": "2", CONF_INVERTER: "Slave"})
    await step(fid, {"next_step_id": "rename_string"})
    await step(fid, {"old_string": "2", "new_string": "4"})
    await step(fid, {"next_step_id": "done"})
    assert entry.data[CONF_STRING_INVERTERS] == {"1": "Master", "4": "Slave"}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    fid = result["flow_id"]
    await step(fid, {"next_step_id": "string_inverter"})
    await step(fid, {"string": "1", CONF_INVERTER: ""})
    await step(fid, {"next_step_id": "done"})
    assert entry.data[CONF_STRING_INVERTERS] == {"4": "Slave"}
