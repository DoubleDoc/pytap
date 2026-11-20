"""Config flow for PyTap integration.

Implements a menu-driven config flow where users add Tigo optimizer modules
one at a time via individual form fields (string group, name, barcode),
rather than a comma-separated text blob.

Flow: user (host/port) → modules_menu → add_module (repeat) → finish
Options: init (menu) → add_module / remove_module / change_connection /
         change_reporting → done
"""

from __future__ import annotations

import logging
import re
from typing import Any
import uuid

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .connection import InvalidConnectionSettings, normalize_connection
from .const import (
    CONF_ESTIMATE_SILENT,
    CONF_INVERTER,
    CONF_LIVE_REPORTING,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_PEAK_POWER,
    CONF_MODULE_STRING,
    CONF_MODULES,
    CONF_NIGHT_ACTION,
    CONF_SILENT_ACTION,
    CONF_SILENT_MINUTES,
    CONF_WRITE_INTERVAL,
    DEFAULT_ESTIMATE_SILENT,
    DEFAULT_INVERTER,
    DEFAULT_LIVE_REPORTING,
    DEFAULT_NIGHT_ACTION,
    DEFAULT_PEAK_POWER,
    DEFAULT_PORT,
    DEFAULT_SILENT_ACTION,
    DEFAULT_SILENT_MINUTES,
    DEFAULT_WRITE_INTERVAL,
    DOMAIN,
    MAX_SILENT_MINUTES,
    MAX_WRITE_INTERVAL,
    MIN_SILENT_MINUTES,
    MIN_WRITE_INTERVAL,
    NIGHT_ACTIONS,
    SILENT_ACTIONS,
)

_LOGGER = logging.getLogger(__name__)

# Barcode format: X-NNNNNNN[C] where X is hex digit, N is hex, C is alpha
_BARCODE_PATTERN = re.compile(r"^[0-9A-Fa-f]-[0-9A-Fa-f]{1,7}[A-Za-z]$")

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_INVERTER, default=DEFAULT_INVERTER): str,
    }
)

# Sensor keys of the per-string aggregate entities (see sensor.py).
_STRING_SENSOR_KEYS = ("performance", "power", "daily_energy", "total_energy")

ADD_MODULE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MODULE_STRING): str,
        vol.Required(CONF_MODULE_NAME): str,
        vol.Required(CONF_MODULE_BARCODE): str,
        vol.Optional(CONF_MODULE_PEAK_POWER, default=DEFAULT_PEAK_POWER): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=1000)
        ),
    }
)


def validate_barcode(barcode: str) -> None:
    """Validate a single barcode format."""
    if barcode and not _BARCODE_PATTERN.match(barcode):
        raise InvalidBarcodeFormat(
            f"Invalid barcode format: '{barcode}'. "
            "Expected format like A-1234567B (X-NNNNNNNC)."
        )


async def validate_connection(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate connection to the Tigo gateway."""
    host = data[CONF_HOST]
    port = data.get(CONF_PORT, DEFAULT_PORT)

    def _test_connection() -> None:
        from .pytap.core.source import TcpSource

        source = TcpSource(host, port)
        try:
            source.connect()
        finally:
            source.close()

    await hass.async_add_executor_job(_test_connection)
    return {"title": f"PyTap ({host})"}


def _modules_description(modules: list[dict[str, Any]]) -> str:
    """Build a human-readable summary of currently added modules."""
    if not modules:
        return "No modules added yet."
    lines = []
    for i, m in enumerate(modules, 1):
        parts = []
        if m.get(CONF_MODULE_STRING):
            parts.append(f"string={m[CONF_MODULE_STRING]}")
        parts.append(m[CONF_MODULE_NAME])
        parts.append(m.get(CONF_MODULE_BARCODE, ""))
        peak_power = m.get(CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER)
        lines.append(f"  {i}. {' / '.join(parts)} ({peak_power}Wp)")
    return f"**Modules ({len(modules)}):**\n" + "\n".join(lines)


class PyTapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PyTap."""

    VERSION = 6

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_data: dict[str, Any] = {}
        self._modules: list[dict[str, str]] = []
        self._unreachable_confirm: tuple[str, int] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PyTapOptionsFlow:
        """Get the options flow for this handler."""
        return PyTapOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle step 1: connection parameters (host/port)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                settings = normalize_connection(user_input)
            except InvalidConnectionSettings as err:
                errors[err.field] = err.error
            else:
                # An unreachable converter is reported once; submitting the
                # same settings again continues (it may simply be offline).
                if await self._async_reachable_or_confirmed(settings, errors):
                    self._user_data = {
                        **user_input,
                        **settings,
                        CONF_INVERTER: user_input.get(CONF_INVERTER, "").strip(),
                    }
                    # Stable unique ID — not tied to mutable connection settings
                    await self.async_set_unique_id(uuid.uuid4().hex)
                    return await self.async_step_modules_menu()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def _async_reachable_or_confirmed(
        self, settings: dict[str, Any], errors: dict[str, str]
    ) -> bool:
        """Test the connection; on failure ask once, then accept the same settings."""
        key = (settings[CONF_HOST], settings[CONF_PORT])
        try:
            await validate_connection(self.hass, settings)
        except Exception:  # noqa: BLE001
            if self._unreachable_confirm == key:
                _LOGGER.warning(
                    "Could not connect to %s:%s — saving anyway", *key
                )
                return True
            self._unreachable_confirm = key
            errors["base"] = "cannot_connect"
            return False
        return True

    async def async_step_modules_menu(
        self, user_input: dict[str, Any] | None = None, error: str = ""
    ) -> ConfigFlowResult:
        """Show the modules menu: add another or finish."""
        return self.async_show_menu(
            step_id="modules_menu",
            menu_options=["add_module", "finish"],
            description_placeholders={
                "modules_list": _modules_description(self._modules),
                "error": error,
            },
        )

    async def async_step_add_module(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle adding a single module (string, name, barcode)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            barcode = user_input.get(CONF_MODULE_BARCODE, "").strip().upper()
            name = user_input.get(CONF_MODULE_NAME, "").strip()
            string_group = user_input.get(CONF_MODULE_STRING, "").strip()

            if not string_group:
                errors[CONF_MODULE_STRING] = "missing_string"
            elif not name:
                errors[CONF_MODULE_NAME] = "missing_name"
            elif not barcode:
                errors[CONF_MODULE_BARCODE] = "missing_barcode"
            else:
                try:
                    validate_barcode(barcode)
                except InvalidBarcodeFormat:
                    errors[CONF_MODULE_BARCODE] = "invalid_barcode"

            # Check for duplicate barcode
            if not errors and any(
                m[CONF_MODULE_BARCODE] == barcode for m in self._modules
            ):
                errors[CONF_MODULE_BARCODE] = "duplicate_barcode"

            if not errors:
                self._modules.append(
                    {
                        CONF_MODULE_STRING: string_group,
                        CONF_MODULE_NAME: name,
                        CONF_MODULE_BARCODE: barcode,
                        CONF_MODULE_PEAK_POWER: user_input.get(
                            CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER
                        ),
                    }
                )
                return await self.async_step_modules_menu()

        return self.async_show_form(
            step_id="add_module",
            data_schema=ADD_MODULE_SCHEMA,
            errors=errors,
            description_placeholders={
                "modules_list": _modules_description(self._modules),
            },
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Finish the config flow — create the entry."""
        if not self._modules:
            # Say why instead of silently showing the menu again.
            return await self.async_step_modules_menu(
                error="**Add at least one module before finishing setup.**"
            )
        # New entries start in averaged mode at DEFAULT_WRITE_INTERVAL: live
        # mode writes every reading to the recorder. Entries created before
        # this keep live mode (DEFAULT_LIVE_REPORTING) until changed.
        data = {
            **self._user_data,
            CONF_MODULES: self._modules,
            CONF_LIVE_REPORTING: False,
            CONF_WRITE_INTERVAL: DEFAULT_WRITE_INTERVAL,
        }
        title = f"PyTap ({self._user_data[CONF_HOST]})"
        return self.async_create_entry(title=title, data=data)


class PyTapOptionsFlow(OptionsFlow):
    """Handle PyTap options — add/remove modules after setup."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._unreachable_confirm: tuple[str, int] | None = None
        # Copy each module: edits must not touch the entry's own data, or
        # saving would see no change and skip the reload.
        self._modules: list[dict[str, str]] = [
            dict(m) for m in config_entry.data.get(CONF_MODULES, [])
        ]
        self._inverter: str = config_entry.data.get(CONF_INVERTER, "") or ""
        self._stale: dict[str, Any] = {
            CONF_NIGHT_ACTION: config_entry.data.get(CONF_NIGHT_ACTION, DEFAULT_NIGHT_ACTION),
            CONF_SILENT_ACTION: config_entry.data.get(
                CONF_SILENT_ACTION, DEFAULT_SILENT_ACTION
            ),
            CONF_SILENT_MINUTES: config_entry.data.get(
                CONF_SILENT_MINUTES, DEFAULT_SILENT_MINUTES
            ),
        }
        # Original string name -> new name, applied to the registries on save.
        self._string_renames: dict[str, str] = {}
        self._edit_barcode: str | None = None
        self._estimate_choice: bool | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options menu: change connection / add / remove / done."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "change_connection",
                "change_reporting",
                "change_inverter",
                "change_stale",
                "add_module",
                "edit_module",
                "rename_string",
                "remove_module",
                "done",
            ],
            description_placeholders={
                "modules_list": _modules_description(self._modules),
            },
        )

    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Save modules and close the options flow."""
        self._migrate_renamed_strings()
        new_data = {
            **self._config_entry.data,
            CONF_MODULES: self._modules,
            CONF_INVERTER: self._inverter,
            **self._stale,
        }
        self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
        return self.async_create_entry(title="", data={})

    def _migrate_renamed_strings(self) -> None:
        """Move string aggregate entities and devices to their new names.

        String aggregates are keyed by string name, so without this a rename
        would create new entities and orphan the old ones with their history.
        """
        if not self._string_renames:
            return
        entry_id = self._config_entry.entry_id
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        for old, new in self._string_renames.items():
            if old == new:
                continue
            for key in _STRING_SENSOR_KEYS:
                old_uid = f"{DOMAIN}_{entry_id}_string_{old}_{key}"
                new_uid = f"{DOMAIN}_{entry_id}_string_{new}_{key}"
                entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, old_uid)
                if entity_id is None:
                    continue
                if ent_reg.async_get_entity_id("sensor", DOMAIN, new_uid):
                    _LOGGER.warning(
                        "Not moving %s to string %s: an entity already exists",
                        entity_id,
                        new,
                    )
                    continue
                ent_reg.async_update_entity(entity_id, new_unique_id=new_uid)
            device = dev_reg.async_get_device(
                identifiers={(DOMAIN, f"{entry_id}_string_{old}")}
            )
            new_ident = (DOMAIN, f"{entry_id}_string_{new}")
            if device is not None and dev_reg.async_get_device(
                identifiers={new_ident}
            ) is None:
                dev_reg.async_update_device(device.id, new_identifiers={new_ident})

    async def async_step_change_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the optional inverter name used in device names."""
        if user_input is not None:
            self._inverter = user_input.get(CONF_INVERTER, "").strip()
            return await self.async_step_init()

        return self.async_show_form(
            step_id="change_inverter",
            data_schema=vol.Schema(
                {vol.Optional(CONF_INVERTER, default=self._inverter): str}
            ),
        )

    async def async_step_change_stale(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """What a quiet optimizer's own live sensors show.

        Adapted from Tim Laing's upstream PR azebro/pytap#34, fitted to the
        fork's sun-aware rules: at night (asleep) and when silent by day.
        """
        if user_input is not None:
            self._stale = {
                CONF_NIGHT_ACTION: user_input[CONF_NIGHT_ACTION],
                CONF_SILENT_ACTION: user_input[CONF_SILENT_ACTION],
                CONF_SILENT_MINUTES: int(user_input[CONF_SILENT_MINUTES]),
            }
            return await self.async_step_init()

        return self.async_show_form(
            step_id="change_stale",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NIGHT_ACTION, default=self._stale[CONF_NIGHT_ACTION]
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(NIGHT_ACTIONS),
                            translation_key=CONF_NIGHT_ACTION,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(
                        CONF_SILENT_ACTION, default=self._stale[CONF_SILENT_ACTION]
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(SILENT_ACTIONS),
                            translation_key=CONF_SILENT_ACTION,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(
                        CONF_SILENT_MINUTES, default=self._stale[CONF_SILENT_MINUTES]
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SILENT_MINUTES, max=MAX_SILENT_MINUTES),
                    ),
                }
            ),
        )

    async def async_step_edit_module(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a module to edit."""
        if not self._modules:
            return await self.async_step_init()

        if user_input is not None:
            self._edit_barcode = user_input.get("edit_barcode")
            return await self.async_step_edit_module_details()

        barcode_options = {
            m[CONF_MODULE_BARCODE]: f"{m[CONF_MODULE_NAME]} ({m[CONF_MODULE_BARCODE]})"
            for m in self._modules
            if m.get(CONF_MODULE_BARCODE)
        }
        return self.async_show_form(
            step_id="edit_module",
            data_schema=vol.Schema(
                {vol.Required("edit_barcode"): vol.In(barcode_options)}
            ),
        )

    async def async_step_edit_module_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit a module's string, name and peak power (barcode stays)."""
        module = next(
            (m for m in self._modules if m[CONF_MODULE_BARCODE] == self._edit_barcode),
            None,
        )
        if module is None:
            return await self.async_step_init()

        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input.get(CONF_MODULE_NAME, "").strip()
            string_group = user_input.get(CONF_MODULE_STRING, "").strip()
            if not string_group:
                errors[CONF_MODULE_STRING] = "missing_string"
            elif not name:
                errors[CONF_MODULE_NAME] = "missing_name"
            if not errors:
                module[CONF_MODULE_STRING] = string_group
                module[CONF_MODULE_NAME] = name
                module[CONF_MODULE_PEAK_POWER] = user_input.get(
                    CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER
                )
                return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MODULE_STRING, default=module.get(CONF_MODULE_STRING, "")
                ): str,
                vol.Required(CONF_MODULE_NAME, default=module[CONF_MODULE_NAME]): str,
                vol.Optional(
                    CONF_MODULE_PEAK_POWER,
                    default=module.get(CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
            }
        )
        return self.async_show_form(
            step_id="edit_module_details",
            data_schema=schema,
            errors=errors,
            description_placeholders={"barcode": module[CONF_MODULE_BARCODE]},
        )

    async def async_step_rename_string(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rename a string for all its modules, keeping the string's history."""
        strings = sorted(
            {m.get(CONF_MODULE_STRING, "") for m in self._modules} - {""}
        )
        if not strings:
            return await self.async_step_init()

        errors: dict[str, str] = {}
        if user_input is not None:
            old = user_input["old_string"]
            new = user_input.get("new_string", "").strip()
            if not new:
                errors["new_string"] = "missing_string"
            elif new != old and new in strings:
                errors["new_string"] = "string_exists"
            if not errors:
                for m in self._modules:
                    if m.get(CONF_MODULE_STRING) == old:
                        m[CONF_MODULE_STRING] = new
                # Track the rename from the string's original (saved) name.
                original = next(
                    (k for k, v in self._string_renames.items() if v == old), old
                )
                self._string_renames[original] = new
                return await self.async_step_init()

        return self.async_show_form(
            step_id="rename_string",
            data_schema=vol.Schema(
                {
                    vol.Required("old_string"): vol.In(strings),
                    vol.Required("new_string"): str,
                }
            ),
            errors=errors,
        )

    async def async_step_change_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to change the Tigo gateway IP address and port."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                settings = normalize_connection(user_input)
            except InvalidConnectionSettings as err:
                errors[err.field] = err.error
                settings = None
            if settings is not None:
                new_host, new_port = settings[CONF_HOST], settings[CONF_PORT]
                key = (new_host, new_port)
                # An unreachable converter is reported once; submitting the
                # same settings again saves them.
                try:
                    await validate_connection(self.hass, settings)
                except Exception:  # noqa: BLE001
                    if self._unreachable_confirm != key:
                        self._unreachable_confirm = key
                        errors["base"] = "cannot_connect"
                    else:
                        _LOGGER.warning(
                            "Could not connect to %s:%s — saving anyway", *key
                        )

            if settings is not None and not errors:
                # Update entry data with new connection details
                new_data = {**self._config_entry.data}
                new_data[CONF_HOST] = new_host
                new_data[CONF_PORT] = new_port
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data=new_data,
                    title=f"PyTap ({new_host})",
                )
                # A loaded entry reloads through its update listener; one that
                # failed setup (e.g. invalid saved settings) has none.
                if self._config_entry.state in (
                    ConfigEntryState.SETUP_ERROR,
                    ConfigEntryState.SETUP_RETRY,
                ):
                    self.hass.config_entries.async_schedule_reload(
                        self._config_entry.entry_id
                    )
                return await self.async_step_init()

        # Pre-fill with current values
        current_host = self._config_entry.data.get(CONF_HOST, "")
        current_port = self._config_entry.data.get(CONF_PORT, DEFAULT_PORT)
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current_host): str,
                vol.Optional(CONF_PORT, default=current_port): int,
            }
        )

        return self.async_show_form(
            step_id="change_connection",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_change_reporting(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Toggle live reporting.

        Live reporting is presented on its own step with just the checkbox.
        Home Assistant's static forms cannot grey out a field reactively, so
        the reporting interval is collected on a separate ``set_interval``
        step that is only reached when live reporting is turned off. When it
        is left on, the interval is irrelevant and never shown.
        """
        if user_input is not None:
            live = user_input.get(CONF_LIVE_REPORTING, DEFAULT_LIVE_REPORTING)
            self._estimate_choice = user_input.get(
                CONF_ESTIMATE_SILENT, DEFAULT_ESTIMATE_SILENT
            )
            if live:
                # Live mode — clear any stored interval and finish.
                new_data = {**self._config_entry.data}
                new_data[CONF_LIVE_REPORTING] = True
                new_data[CONF_ESTIMATE_SILENT] = self._estimate_choice
                new_data.pop(CONF_WRITE_INTERVAL, None)
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                return await self.async_step_init()
            # Interval mode — collect the interval on the next step.
            return await self.async_step_set_interval()

        current_live = self._config_entry.data.get(
            CONF_LIVE_REPORTING, DEFAULT_LIVE_REPORTING
        )
        current_estimate = self._config_entry.data.get(
            CONF_ESTIMATE_SILENT, DEFAULT_ESTIMATE_SILENT
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_LIVE_REPORTING, default=current_live): bool,
                vol.Optional(CONF_ESTIMATE_SILENT, default=current_estimate): bool,
            }
        )

        return self.async_show_form(
            step_id="change_reporting",
            data_schema=schema,
        )

    async def async_step_set_interval(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the reporting interval (only reached when live reporting is off)."""
        if user_input is not None:
            new_data = {**self._config_entry.data}
            new_data[CONF_LIVE_REPORTING] = False
            if self._estimate_choice is not None:
                new_data[CONF_ESTIMATE_SILENT] = self._estimate_choice
            new_data[CONF_WRITE_INTERVAL] = user_input.get(
                CONF_WRITE_INTERVAL, DEFAULT_WRITE_INTERVAL
            )
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            return await self.async_step_init()

        current_write_interval = self._config_entry.data.get(
            CONF_WRITE_INTERVAL, DEFAULT_WRITE_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_WRITE_INTERVAL, default=current_write_interval
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_WRITE_INTERVAL, max=MAX_WRITE_INTERVAL),
                ),
            }
        )

        return self.async_show_form(
            step_id="set_interval",
            data_schema=schema,
        )

    async def async_step_add_module(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a single module in options flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            barcode = user_input.get(CONF_MODULE_BARCODE, "").strip().upper()
            name = user_input.get(CONF_MODULE_NAME, "").strip()
            string_group = user_input.get(CONF_MODULE_STRING, "").strip()

            if not string_group:
                errors[CONF_MODULE_STRING] = "missing_string"
            elif not name:
                errors[CONF_MODULE_NAME] = "missing_name"
            elif not barcode:
                errors[CONF_MODULE_BARCODE] = "missing_barcode"
            else:
                try:
                    validate_barcode(barcode)
                except InvalidBarcodeFormat:
                    errors[CONF_MODULE_BARCODE] = "invalid_barcode"

            if not errors and any(
                m[CONF_MODULE_BARCODE] == barcode for m in self._modules
            ):
                errors[CONF_MODULE_BARCODE] = "duplicate_barcode"

            if not errors:
                self._modules.append(
                    {
                        CONF_MODULE_STRING: string_group,
                        CONF_MODULE_NAME: name,
                        CONF_MODULE_BARCODE: barcode,
                        CONF_MODULE_PEAK_POWER: user_input.get(
                            CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER
                        ),
                    }
                )
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_module",
            data_schema=ADD_MODULE_SCHEMA,
            errors=errors,
            description_placeholders={
                "modules_list": _modules_description(self._modules),
            },
        )

    async def async_step_remove_module(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a module by selecting from a list."""
        if not self._modules:
            return await self.async_step_init()

        if user_input is not None:
            remove_barcode = user_input.get("remove_barcode")
            if remove_barcode:
                self._modules = [
                    m for m in self._modules if m[CONF_MODULE_BARCODE] != remove_barcode
                ]
            return await self.async_step_init()

        # Build selection list from current modules
        barcode_options = {
            m[CONF_MODULE_BARCODE]: f"{m[CONF_MODULE_NAME]} ({m[CONF_MODULE_BARCODE]})"
            for m in self._modules
            if m.get(CONF_MODULE_BARCODE)
        }

        schema = vol.Schema(
            {
                vol.Required("remove_barcode"): vol.In(barcode_options),
            }
        )

        return self.async_show_form(
            step_id="remove_module",
            data_schema=schema,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class InvalidModuleFormat(HomeAssistantError):
    """Error to indicate invalid module format."""


class InvalidBarcodeFormat(HomeAssistantError):
    """Error to indicate invalid barcode format."""
