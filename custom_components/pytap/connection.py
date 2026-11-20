"""Connection settings validation.

Adapted from Tim Laing's upstream PR azebro/pytap#33.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.const import CONF_HOST, CONF_PORT

from .const import DEFAULT_PORT


class InvalidConnectionSettings(ValueError):
    """Invalid settings, with a field and translation key safe for the UI."""

    def __init__(self, field: str, error: str) -> None:
        self.field = field
        self.error = error
        super().__init__(error)


def normalize_connection(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate connection data without opening a connection."""
    host = data.get(CONF_HOST, "")
    if not isinstance(host, str) or not host.strip():
        raise InvalidConnectionSettings(CONF_HOST, "invalid_host")
    port = data.get(CONF_PORT, DEFAULT_PORT)
    if type(port) is not int or not 1 <= port <= 65535:
        raise InvalidConnectionSettings(CONF_PORT, "invalid_port")
    return {CONF_HOST: host.strip(), CONF_PORT: port}
