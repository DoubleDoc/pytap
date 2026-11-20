"""Lifecycle robustness, adapted from Tim Laing's upstream PR azebro/pytap#33.

Covers: a saved state that can't be read is never overwritten, removing an
entry deletes its saved state, a stop interrupts a blocked socket read, a
listener that won't stop is parked and setup waits for it, invalid saved
connection settings fail setup clearly, and diagnostics hide the title.
"""

import asyncio
import socket
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap import coordinator as coordinator_module
from custom_components.pytap.const import CONF_MODULES, DOMAIN
from custom_components.pytap.coordinator import (
    PyTapDataUpdateCoordinator,
    coordinator_store,
    parked_coordinators,
)
from custom_components.pytap.diagnostics import async_get_config_entry_diagnostics
from custom_components.pytap.pytap.core.source import TcpSource

MODULES = [{"string": "A", "name": "P01", "barcode": "A-1234567B", "peak_power": 400}]


def _mock_entry(entry_id: str = "lifecycle_entry", **data):
    entry = MagicMock()
    entry.data = {CONF_HOST: "192.168.1.100", CONF_PORT: 502, CONF_MODULES: MODULES, **data}
    entry.entry_id = entry_id
    entry.options = {}
    return entry


async def test_unreadable_saved_state_is_never_overwritten(hass: HomeAssistant) -> None:
    coordinator = PyTapDataUpdateCoordinator(hass, _mock_entry())
    coordinator._store.async_load = AsyncMock(side_effect=ValueError("corrupt"))
    coordinator._store.async_save = AsyncMock()

    await coordinator._async_load_coordinator_state()
    coordinator._unsaved_changes = True
    await coordinator._async_save_coordinator_state()

    coordinator._store.async_save.assert_not_called()


async def test_remove_entry_deletes_saved_state(hass: HomeAssistant) -> None:
    from custom_components.pytap import async_remove_entry

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "h", CONF_PORT: 502})
    store = coordinator_store(hass, entry.entry_id)
    await store.async_save({"x": 1})
    assert await store.async_load() == {"x": 1}

    await async_remove_entry(hass, entry)

    assert await coordinator_store(hass, entry.entry_id).async_load() is None


@pytest.mark.enable_socket
def test_interrupt_wakes_a_blocked_read() -> None:
    """A real socket read blocked in another thread returns promptly."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    source = TcpSource("127.0.0.1", port)
    source.connect()
    conn, _ = server.accept()
    source._socket.settimeout(30)  # much longer than the test waits
    done = threading.Event()

    def reader() -> None:
        try:
            source.read(4096)
        except OSError:
            pass
        done.set()

    thread = threading.Thread(target=reader)
    thread.start()
    time.sleep(0.2)
    started = time.monotonic()
    source.interrupt()
    assert done.wait(5)
    assert time.monotonic() - started < 5
    thread.join()
    source.close()
    conn.close()
    server.close()


async def test_stop_that_times_out_parks_and_setup_waits(hass: HomeAssistant) -> None:
    """A listener that won't stop parks its coordinator; setup raises NotReady."""
    from custom_components.pytap import _async_stop_or_park

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.168.1.100", CONF_PORT: 502, CONF_MODULES: MODULES},
        version=6,
    )
    entry.add_to_hass(hass)
    coordinator = PyTapDataUpdateCoordinator(hass, _mock_entry(entry.entry_id))
    stuck: asyncio.Future = hass.loop.create_future()
    coordinator._worker_future = stuck

    with patch.object(coordinator_module, "LISTENER_STOP_TIMEOUT", 0.2):
        assert await _async_stop_or_park(hass, entry.entry_id, coordinator) is False
    assert parked_coordinators(hass)[entry.entry_id] is coordinator

    with patch.object(coordinator_module, "LISTENER_STOP_TIMEOUT", 0.2):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY

    # Once the worker exits, the parked coordinator cleans itself up.
    # (The real listener wrapper runs the exit callbacks when the thread ends.)
    stuck.set_result(None)
    callbacks, coordinator._exit_callbacks = coordinator._exit_callbacks, []
    for exit_callback in callbacks:
        exit_callback()
    await hass.async_block_till_done()
    assert entry.entry_id not in parked_coordinators(hass)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_invalid_saved_settings_fail_setup_clearly(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.168.1.100", CONF_PORT: 0, CONF_MODULES: MODULES},
        version=6,
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.reason is not None and "port" in entry.reason


async def test_diagnostics_hide_the_entry_title(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="PyTap (192.168.1.100)",
        data={CONF_HOST: "192.168.1.100", CONF_PORT: 502, CONF_MODULES: MODULES},
    )
    coordinator = MagicMock()
    coordinator.get_diagnostics_data.return_value = {}
    coordinator.data = {"nodes": {}}
    entry.runtime_data = coordinator

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert "192.168.1.100" not in str(result)
