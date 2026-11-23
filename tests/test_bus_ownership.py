"""One PyTap entry per optimizer, and one connection per Tigo bus.

From Adam Zebrowski's review of azebro/pytap#37: ownership must cover entries
that aren't loaded (1), a configured optimizer owned elsewhere must not be
tracked twice (3), and saving the options mustn't restore an optimizer deleted
elsewhere (2). Two connections hearing one bus stop the newer one. An
optimizer moved to another bus keeps its lifetime energy.
"""

from datetime import datetime
import time
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap import async_remove_entry
from custom_components.pytap.config_flow import endpoint_in_use
from custom_components.pytap.const import (
    CONF_EXCLUDED_BARCODES,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_PEAK_POWER,
    CONF_MODULE_STRING,
    CONF_MODULES,
    DOMAIN,
)
from custom_components.pytap.coordinator import (
    async_barcode_owners,
    barcode_owners,
    coordinator_store,
)
from custom_components.pytap.energy import EnergyAccumulator
from custom_components.pytap.pytap.core.events import (
    InfrastructureEvent,
    PowerReportEvent,
)
from tests.test_module_discovery import KNOWN, NEW, _coordinator

OTHERS = ["4-000002B", "4-000016P"]


def _module(barcode, string="2", name="P01"):
    return {
        CONF_MODULE_BARCODE: barcode, CONF_MODULE_NAME: name,
        CONF_MODULE_STRING: string, CONF_MODULE_PEAK_POWER: 455,
    }


def _report(coordinator, barcode, node_id) -> None:
    coordinator._handle_infrastructure(InfrastructureEvent(
        gateways={1: {"address": "aa:bb", "version": "1.0"}},
        nodes={node_id: {"address": f"11:{node_id}", "barcode": barcode}},
        timestamp=datetime.now(),
    ))
    coordinator._process_event(PowerReportEvent(
        gateway_id=1, node_id=node_id, barcode=barcode, voltage_in=30.0,
        voltage_out=29.5, current_in=4.0, temperature=25.0,
        dc_dc_duty_cycle=0.9, rssi=-60, timestamp=datetime.now(),
    ))


async def test_unloaded_entry_still_owns_its_optimizers(hass: HomeAssistant) -> None:
    """Bug 1: a disabled entry's optimizers aren't discovered by another."""
    MockConfigEntry(
        domain=DOMAIN, entry_id="disabled", title="Master",
        data={CONF_HOST: "10.0.0.1", CONF_PORT: 502, CONF_MODULES: [_module(NEW)]},
    ).add_to_hass(hass)
    await async_barcode_owners(hass)
    other = _coordinator(hass, entry_id="other", modules=[])
    _report(other, NEW, 7)
    assert NEW not in other.configured_barcodes
    assert other.foreign_barcodes == {NEW: "disabled"}
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "owned_elsewhere_other")
    assert issue is not None and NEW in issue.translation_placeholders["optimizers"]


async def test_configured_elsewhere_is_not_tracked_twice(hass: HomeAssistant) -> None:
    """Bug 3: a configured optimizer owned by another entry gets no entities or energy."""
    first = _coordinator(hass, entry_id="first")
    second = _coordinator(hass, entry_id="second")  # also configures KNOWN
    assert KNOWN in first.configured_barcodes
    assert KNOWN not in second.configured_barcodes
    assert all(m[CONF_MODULE_BARCODE] != KNOWN for m in second.modules)
    _report(second, KNOWN, 3)
    assert KNOWN not in second._energy_state


async def test_two_connections_on_one_bus_stop_the_second(hass: HomeAssistant) -> None:
    owners = barcode_owners(hass)
    first = _coordinator(hass, entry_id="one")
    owners.register("one", first)
    for node_id, barcode in enumerate([NEW, *OTHERS], start=7):
        _report(first, barcode, node_id)
    second = _coordinator(hass, entry_id="two", modules=[])
    second.async_stop_listener = MagicMock()
    for node_id, barcode in enumerate([NEW, *OTHERS], start=7):
        _report(second, barcode, node_id)
    assert second.duplicate_of == "one"
    await hass.async_block_till_done()
    # The stop runs as the entry's background task (a mock entry here).
    second.async_stop_listener.assert_called_once()
    second.config_entry.async_create_background_task.assert_called_once()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "duplicate_bus_two") is not None
    assert not second.configured_barcodes & {NEW, *OTHERS}


async def test_moved_optimizer_is_not_a_duplicate_bus(hass: HomeAssistant) -> None:
    """Quiet where it was: it's reported as owned elsewhere, nothing stops."""
    owners = barcode_owners(hass)
    first = _coordinator(hass, entry_id="one")
    owners.register("one", first)
    for node_id, barcode in enumerate([NEW, *OTHERS], start=7):
        _report(first, barcode, node_id)
        first._last_report[barcode] = time.monotonic() - 3600  # gone quiet
    second = _coordinator(hass, entry_id="two", modules=[])
    for node_id, barcode in enumerate([NEW, *OTHERS], start=7):
        _report(second, barcode, node_id)
    assert second.duplicate_of is None
    assert set(second.foreign_barcodes) == {NEW, *OTHERS}


async def test_moved_optimizer_keeps_its_lifetime_energy(hass: HomeAssistant) -> None:
    owners = barcode_owners(hass)
    owners.set_entry("old", {NEW})
    second = _coordinator(hass, entry_id="new", modules=[])
    _report(second, NEW, 7)
    assert NEW not in second.configured_barcodes  # still owned by "old"
    # Removed from "old": its energy is released, and "new" continues it.
    owners.set_entry("old", set())
    owners.release_energy(NEW, EnergyAccumulator(total_energy_wh=1234.5), "old")
    _report(second, NEW, 7)
    assert NEW in second.configured_barcodes
    assert second._energy_state[NEW].total_energy_wh >= 1234.5
    assert NEW not in owners.released
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "owned_elsewhere_new") is None


async def test_removed_optimizer_energy_is_released_on_load(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, entry_id="load")

    async def _load():
        return {
            "energy_data": {
                KNOWN: {"total_energy_wh": 10.0},
                NEW: {"total_energy_wh": 987.6, "estimated_total_wh": 1.5},
            }
        }

    coordinator._store.async_load = _load
    await coordinator._async_load_coordinator_state()
    owners = barcode_owners(hass)
    assert owners.released[NEW]["total_energy_wh"] == 987.6
    assert owners.released[NEW]["estimated_total_wh"] == 1.5
    assert NEW not in coordinator._energy_state
    assert KNOWN in coordinator._energy_state  # still configured: kept


async def test_deleted_entry_hands_over_energy_and_barcodes(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="gone",
        data={CONF_HOST: "10.0.0.1", CONF_PORT: 502, CONF_MODULES: [_module(NEW)]},
    )
    entry.add_to_hass(hass)
    await coordinator_store(hass, "gone").async_save(
        {"energy_data": {NEW: {"total_energy_wh": 55.0}}}
    )
    owners = await async_barcode_owners(hass)
    assert owners.owner(NEW) == "gone"
    with patch("custom_components.pytap.async_remove_captures", AsyncMock()):
        await async_remove_entry(hass, entry)
    assert owners.owner(NEW) is None
    assert owners.released[NEW]["total_energy_wh"] == 55.0


async def test_same_converter_twice_is_refused(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN, entry_id="a",
        data={"connection_type": "tcp", CONF_HOST: "127.0.0.1", CONF_PORT: 502},
    ).add_to_hass(hass)
    same = {"connection_type": "tcp", CONF_HOST: "localhost", CONF_PORT: 502}
    assert await endpoint_in_use(hass, same) == CONF_HOST
    assert await endpoint_in_use(hass, {**same, CONF_PORT: 503}) is None
    assert await endpoint_in_use(hass, same, "a") is None  # editing entry "a" itself


async def test_options_done_keeps_a_deletion_made_elsewhere(hass: HomeAssistant) -> None:
    """Bug 2: deleting a device while the options are open isn't undone by Done."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=8, entry_id="opts",
        data={
            CONF_HOST: "10.0.0.9", CONF_PORT: 502,
            CONF_MODULES: [_module(KNOWN), _module(NEW, name="P02")],
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    # Meanwhile, NEW's device is deleted from its page.
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_MODULES: [_module(KNOWN)], CONF_EXCLUDED_BARCODES: [NEW]},
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert [m[CONF_MODULE_BARCODE] for m in entry.data[CONF_MODULES]] == [KNOWN]
    assert NEW in entry.data[CONF_EXCLUDED_BARCODES]
