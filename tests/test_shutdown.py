"""PyTap stops its listener and saves its state when Home Assistant stops.

Home Assistant doesn't unload config entries at shutdown, so stopping only
on unload left the listener blocked and the last changes unsaved.
"""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap.const import DOMAIN
from custom_components.pytap.coordinator import PyTapDataUpdateCoordinator


async def test_listener_stops_when_home_assistant_stops(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.100", CONF_PORT: 502, "modules": []},
        version=6,
    )
    entry.add_to_hass(hass)
    with (
        patch.object(PyTapDataUpdateCoordinator, "async_start_listener", AsyncMock()),
        patch.object(
            PyTapDataUpdateCoordinator, "async_stop_listener", AsyncMock()
        ) as stop,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        stop.assert_not_awaited()

        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
        stop.assert_awaited_once()
