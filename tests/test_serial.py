"""Serial and ESPHome serial-proxy connections.

Adapted from Tim Laing's upstream PR azebro/pytap#36.
"""

from unittest.mock import MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap.connection import (
    InvalidConnectionSettings,
    connection_title,
    normalize_connection,
)
from custom_components.pytap.const import (
    CONF_BAUD_RATE,
    CONF_CONNECTION_TYPE,
    CONF_MODULES,
    CONF_SERIAL_PORT,
    CONNECTION_SERIAL,
    DOMAIN,
)
from custom_components.pytap.coordinator import PyTapDataUpdateCoordinator
from custom_components.pytap.diagnostics import async_get_config_entry_diagnostics

PROXY = "esphome-hass://esphome/abc123?port_name=uart1"


def _serial(port: str, baud: int = 38400) -> dict:
    return {CONF_CONNECTION_TYPE: CONNECTION_SERIAL, CONF_SERIAL_PORT: port, CONF_BAUD_RATE: baud}


@pytest.mark.parametrize("port", ["/dev/ttyUSB0", "/dev/serial/by-id/usb-FTDI-if00", PROXY])
def test_valid_serial_endpoints(port: str) -> None:
    assert normalize_connection(_serial(port))[CONF_SERIAL_PORT] == port


@pytest.mark.parametrize(
    "port",
    [
        "/config/secrets.yaml",  # not a device
        "/dev/../config/secrets.yaml",  # normalised away from /dev
        "/dev/fd/0",
        "/dev/stdin",
        "esphome-hass://user:pass@esphome/abc?port_name=x",  # credentials
        "esphome-hass://esphome/abc",  # no port filter
        "http://esphome/abc?port_name=x",
        "",
    ],
)
def test_invalid_serial_endpoints(port: str) -> None:
    with pytest.raises(InvalidConnectionSettings):
        normalize_connection(_serial(port))


def test_invalid_baud_rate() -> None:
    with pytest.raises(InvalidConnectionSettings):
        normalize_connection(_serial("/dev/ttyUSB0", baud=0))


def test_titles_and_log_labels_never_show_a_proxy_url(hass: HomeAssistant) -> None:
    assert connection_title(_serial(PROXY)) == "PyTap (Home Assistant serial proxy)"
    assert connection_title(_serial("/dev/ttyUSB0")) == "PyTap (Serial)"
    entry = MagicMock()
    entry.entry_id = "e"
    entry.options = {}
    entry.data = {**_serial(PROXY), CONF_MODULES: []}
    coordinator = PyTapDataUpdateCoordinator(hass, entry)
    assert coordinator.endpoint_label == "serial proxy"
    assert "esphome-hass" not in coordinator._log.extra["gateway"]


async def test_diagnostics_hide_the_serial_port(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="PyTap (Serial)", data=_serial("/dev/ttyUSB0"))
    coordinator = MagicMock()
    coordinator.get_diagnostics_data.return_value = {}
    coordinator.data = {"nodes": {}}
    entry.runtime_data = coordinator
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert "/dev/ttyUSB0" not in str(result)


async def test_serial_setup_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"tcp", "serial"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "serial"}
    )
    assert result["step_id"] == "serial"
    with patch(
        "custom_components.pytap.config_flow.validate_connection",
        return_value={"title": "x"},
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SERIAL_PORT: PROXY, CONF_BAUD_RATE: 38400}
        )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "modules_menu"


async def test_a_port_used_by_another_entry_is_refused(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, version=7, data=_serial("/dev/ttyUSB0")).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "serial"}
    )
    with (
        patch("custom_components.pytap.config_flow.validate_connection") as validate,
        patch(
            "custom_components.pytap.connection.os.path.realpath", side_effect=lambda p: p
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SERIAL_PORT: "/dev/ttyUSB0", CONF_BAUD_RATE: 38400}
        )
    assert result["errors"] == {CONF_SERIAL_PORT: "serial_port_in_use"}
    validate.assert_not_called()
