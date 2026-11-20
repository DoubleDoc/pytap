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
    SerialPortSelector,
)
import serialx
import voluptuous as vol

from .connection import (
    CONNECTION_KEYS,
    InvalidConnectionSettings,
    connection_title,
    normalize_connection,
    open_source,
    resolved_endpoint_key,
)
from .const import (
    CONF_AUTO_DISCOVERY,
    CONF_BAUD_RATE,
    CONF_CONNECTION_TYPE,
    CONF_ESTIMATE_SILENT,
    CONF_EXCLUDED_BARCODES,
    CONF_INVERTER,
    CONF_LIVE_REPORTING,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_PEAK_POWER,
    CONF_MODULE_STRING,
    CONF_MODULES,
    CONF_NIGHT_ACTION,
    CONF_SERIAL_PORT,
    CONF_SILENT_ACTION,
    CONF_SILENT_MINUTES,
    CONF_WRITE_INTERVAL,
    CONNECTION_SERIAL,
    CONNECTION_TCP,
    DEFAULT_BAUD_RATE,
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
from .coordinator import parked_coordinators
from .naming import optimizer_object_id

_LOGGER = logging.getLogger(__name__)

# Barcode format: X-NNNNNNN[C] where X is hex digit, N is hex, C is alpha
_BARCODE_PATTERN = re.compile(r"^[0-9A-Fa-f]-[0-9A-Fa-f]{1,7}[A-Za-z]$")

def connection_schema(
    connection_type: str,
    current: dict[str, Any] | None = None,
    *,
    with_inverter: bool = False,
) -> vol.Schema:
    """Connection form for setup and options (adapted from azebro/pytap#36)."""
    current = current or {}
    if connection_type == CONNECTION_TCP:
        fields: dict[Any, Any] = {
            vol.Required(CONF_HOST, default=current.get(CONF_HOST, "")): str,
            vol.Optional(CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)): int,
        }
    else:
        fields = {
            vol.Required(
                CONF_SERIAL_PORT, default=current.get(CONF_SERIAL_PORT, "")
            ): SerialPortSelector(),
            vol.Optional(
                CONF_BAUD_RATE, default=current.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE)
            ): int,
        }
    if with_inverter:
        fields[
            vol.Optional(CONF_INVERTER, default=current.get(CONF_INVERTER, DEFAULT_INVERTER))
        ] = str
    return vol.Schema(fields)

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
    """Open and close the configured endpoint once (raises if it can't)."""
    settings = normalize_connection(data)

    def _test_connection() -> None:
        open_source(hass.loop, settings).close()

    await hass.async_add_executor_job(_test_connection)
    return {"title": connection_title(settings)}


async def _same_serial_port(hass: HomeAssistant, first: str, second: str) -> bool:
    """Compare endpoints after resolving symlinks such as /dev/serial/by-id."""
    return await hass.async_add_executor_job(
        lambda: resolved_endpoint_key(first) == resolved_endpoint_key(second)
    )


async def serial_port_claimed(
    hass: HomeAssistant, settings: dict[str, Any], entry_id: str | None = None
) -> bool:
    """Whether another PyTap entry (or a listener still stopping) uses this port."""
    if settings.get(CONF_CONNECTION_TYPE) != CONNECTION_SERIAL:
        return False
    others = [
        entry.data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id != entry_id
    ]
    others.extend(
        coordinator.connection
        for parked_id, coordinator in parked_coordinators(hass).items()
        if parked_id != entry_id
    )
    for other in others:
        if other.get(CONF_CONNECTION_TYPE) == CONNECTION_SERIAL and await _same_serial_port(
            hass, other.get(CONF_SERIAL_PORT, ""), settings[CONF_SERIAL_PORT]
        ):
            return True
    return False


async def connection_errors(
    hass: HomeAssistant,
    settings: dict[str, Any],
    flow: Any,
    entry: ConfigEntry | None = None,
) -> dict[str, str]:
    """Check a connection; an unreachable endpoint is reported once.

    Submitting the same settings again accepts them (the converter or proxy
    may simply be offline). ``flow._unreachable_confirm`` remembers them.
    """
    if await serial_port_claimed(hass, settings, entry.entry_id if entry else None):
        return {CONF_SERIAL_PORT: "serial_port_in_use"}
    if (
        entry is not None
        and settings[CONF_CONNECTION_TYPE] == CONNECTION_SERIAL
        and entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_SERIAL
        and await _same_serial_port(
            hass, entry.data.get(CONF_SERIAL_PORT, ""), settings[CONF_SERIAL_PORT]
        )
    ):
        return {}  # the running listener owns this port; a reload applies changes
    key = tuple(sorted(settings.items()))
    try:
        await validate_connection(hass, settings)
    except InvalidConnectionSettings as err:
        return {err.field: err.error}
    except (ValueError, serialx.UnsupportedSetting):
        return {"base": "unsupported_serial_settings"}
    except Exception as err:  # noqa: BLE001
        if flow._unreachable_confirm == key:
            # Backend messages can contain full proxy URLs: log the type only.
            _LOGGER.warning(
                "Could not connect using %s (%s) — saving anyway",
                settings[CONF_CONNECTION_TYPE],
                type(err).__name__,
            )
            return {}
        flow._unreachable_confirm = key
        return {"base": "cannot_connect"}
    return {}


def _modules_description(modules: list[dict[str, Any]]) -> str:
    """Build a human-readable summary of currently added modules."""
    if not modules:
        return "No modules added yet."
    lines = []
    for i, m in enumerate(modules, 1):
        parts = []
        if m.get(CONF_MODULE_STRING):
            parts.append(f"string={m[CONF_MODULE_STRING]}")
        parts.append(m.get(CONF_MODULE_NAME) or "(not named yet)")
        parts.append(m.get(CONF_MODULE_BARCODE, ""))
        peak_power = m.get(CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER)
        rating = f"{peak_power}Wp" if peak_power else "rating unknown"
        lines.append(f"  {i}. {' / '.join(parts)} ({rating})")
    return f"**Modules ({len(modules)}):**\n" + "\n".join(lines)


def _module_label(module: dict[str, Any]) -> str:
    """'P01 (4-5DB467Z)', or '(not named yet) (…)' for a discovered optimizer."""
    return f"{module.get(CONF_MODULE_NAME) or '(not named yet)'} ({module[CONF_MODULE_BARCODE]})"


def _discovered(entry: ConfigEntry) -> dict[str, dict[str, Any]]:
    """Optimizers the running entry discovered (none if it isn't loaded)."""
    if entry.state is ConfigEntryState.LOADED:
        return entry.runtime_data.discovered_modules
    return {}


class PyTapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PyTap."""

    VERSION = 8

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_data: dict[str, Any] = {}
        self._modules: list[dict[str, str]] = []
        self._unreachable_confirm: tuple[str, int] | None = None
        # New setups discover optimizers by default (azebro/pytap#37).
        self._auto_discovery = True

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PyTapOptionsFlow:
        """Get the options flow for this handler."""
        return PyTapOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how PyTap receives the bus traffic."""
        return self.async_show_menu(step_id="user", menu_options=["tcp", "serial"])

    async def async_step_tcp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """An RS-485 to TCP converter."""
        return await self._async_connection_step(CONNECTION_TCP, user_input)

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A local serial adapter or a Home Assistant ESPHome serial proxy."""
        return await self._async_connection_step(CONNECTION_SERIAL, user_input)

    async def _async_connection_step(
        self, connection_type: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                settings = normalize_connection(
                    {**user_input, CONF_CONNECTION_TYPE: connection_type}
                )
            except InvalidConnectionSettings as err:
                errors[err.field] = err.error
            else:
                errors = await connection_errors(self.hass, settings, self)
                if not errors:
                    self._user_data = {
                        **settings,
                        CONF_INVERTER: user_input.get(CONF_INVERTER, "").strip(),
                    }
                    # Stable unique ID — not tied to mutable connection settings
                    await self.async_set_unique_id(uuid.uuid4().hex)
                    return await self.async_step_modules_menu()

        return self.async_show_form(
            step_id=connection_type,
            data_schema=connection_schema(connection_type, user_input, with_inverter=True),
            errors=errors,
        )

    async def async_step_modules_menu(
        self, user_input: dict[str, Any] | None = None, error: str = ""
    ) -> ConfigFlowResult:
        """Show the modules menu: add another or finish."""
        return self.async_show_menu(
            step_id="modules_menu",
            menu_options=["add_module", "discovery", "finish"],
            description_placeholders={
                "modules_list": _modules_description(self._modules),
                "discovery_mode": "Automatic" if self._auto_discovery else "Manual",
                "error": error,
            },
        )

    async def async_step_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose automatic discovery or only the modules added here."""
        if user_input is not None:
            self._auto_discovery = user_input[CONF_AUTO_DISCOVERY]
            return await self.async_step_modules_menu()
        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema(
                {vol.Required(CONF_AUTO_DISCOVERY, default=self._auto_discovery): bool}
            ),
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
        if not self._modules and not self._auto_discovery:
            # Say why instead of silently showing the menu again.
            return await self.async_step_modules_menu(
                error="**Add at least one module, or turn on discovery, before "
                "finishing setup.**"
            )
        # New entries start in averaged mode at DEFAULT_WRITE_INTERVAL: live
        # mode writes every reading to the recorder. Entries created before
        # this keep live mode (DEFAULT_LIVE_REPORTING) until changed.
        data = {
            **self._user_data,
            CONF_MODULES: self._modules,
            CONF_AUTO_DISCOVERY: self._auto_discovery,
            CONF_EXCLUDED_BARCODES: [],
            CONF_LIVE_REPORTING: False,
            CONF_WRITE_INTERVAL: DEFAULT_WRITE_INTERVAL,
        }
        if await serial_port_claimed(self.hass, self._user_data):
            # Another flow may have claimed the port while modules were added.
            connection_type = self._user_data[CONF_CONNECTION_TYPE]
            return self.async_show_form(
                step_id=connection_type,
                data_schema=connection_schema(
                    connection_type, self._user_data, with_inverter=True
                ),
                errors={CONF_SERIAL_PORT: "serial_port_in_use"},
            )
        title = connection_title(self._user_data)
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
        # Discovered optimizers can be edited here too; saving moves them
        # into the module list (azebro/pytap#37).
        known = {m[CONF_MODULE_BARCODE] for m in self._modules}
        self._modules.extend(
            m for b, m in _discovered(config_entry).items() if b not in known
        )
        self._initial: dict[str, dict[str, Any]] = {
            m[CONF_MODULE_BARCODE]: dict(m) for m in self._modules
        }
        self._auto_discovery: bool = config_entry.data.get(CONF_AUTO_DISCOVERY, False)
        self._excluded: set[str] = set(config_entry.data.get(CONF_EXCLUDED_BARCODES, []))
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
                "change_discovery",
                "add_module",
                "edit_module",
                "rename_string",
                "remove_module",
                "done",
            ],
            description_placeholders={
                "modules_list": _modules_description(self._modules),
                "discovery_mode": "Automatic" if self._auto_discovery else "Manual",
            },
        )

    async def async_step_change_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Turn automatic discovery on or off (saved with Done)."""
        if user_input is not None:
            self._auto_discovery = user_input[CONF_AUTO_DISCOVERY]
            return await self.async_step_init()
        return self.async_show_form(
            step_id="change_discovery",
            data_schema=vol.Schema(
                {vol.Required(CONF_AUTO_DISCOVERY, default=self._auto_discovery): bool}
            ),
        )

    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Save modules and close the options flow."""
        # Optimizers discovered while this flow was open are kept; removals
        # are excluded so discovery doesn't add them back.
        selected = {m[CONF_MODULE_BARCODE] for m in self._modules}
        self._excluded |= set(self._initial) - selected
        self._excluded -= selected
        modules = list(self._modules)
        modules.extend(
            m
            for b, m in _discovered(self._config_entry).items()
            if b not in selected | self._excluded
        )
        if not modules and not self._auto_discovery:
            return self.async_show_form(
                step_id="change_discovery",
                data_schema=vol.Schema(
                    {vol.Required(CONF_AUTO_DISCOVERY, default=False): bool}
                ),
                errors={"base": "no_modules"},
            )
        self._migrate_renamed_strings()
        self._rename_newly_named(modules)
        new_data = {
            **self._config_entry.data,
            CONF_MODULES: modules,
            CONF_INVERTER: self._inverter,
            CONF_AUTO_DISCOVERY: self._auto_discovery,
            CONF_EXCLUDED_BARCODES: sorted(self._excluded),
            **self._stale,
        }
        self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
        return self.async_create_entry(title="", data={})

    def _rename_newly_named(self, modules: list[dict[str, Any]]) -> None:
        """Give a discovered optimizer's entity IDs its position once it's named.

        ``sensor.tigo_master_4_5db467z_power`` becomes
        ``sensor.tigo_master_p01_4_5db467z_power``. Only IDs still at the
        automatic pattern are renamed; later renames keep their IDs.
        """
        ent_reg = er.async_get(self.hass)
        old_inverter = self._config_entry.data.get(CONF_INVERTER, "") or ""
        for module in modules:
            barcode = module[CONF_MODULE_BARCODE]
            before = self._initial.get(barcode)
            name = module.get(CONF_MODULE_NAME, "")
            if before is None or before.get(CONF_MODULE_NAME) or not name:
                continue
            old_base = optimizer_object_id(old_inverter, "", barcode)
            new_base = optimizer_object_id(self._inverter, name, barcode)
            prefix = f"{DOMAIN}_{barcode}_"
            for reg_entry in er.async_entries_for_config_entry(
                ent_reg, self._config_entry.entry_id
            ):
                if not (reg_entry.unique_id or "").startswith(prefix):
                    continue
                key = reg_entry.unique_id[len(prefix):]
                if reg_entry.entity_id != f"sensor.{old_base}_{key}":
                    continue
                new_id = f"sensor.{new_base}_{key}"
                if ent_reg.async_get(new_id) is None:
                    ent_reg.async_update_entity(reg_entry.entity_id, new_entity_id=new_id)

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
            m[CONF_MODULE_BARCODE]: _module_label(m)
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
                vol.Required(
                    CONF_MODULE_NAME, default=module.get(CONF_MODULE_NAME, "")
                ): str,
                # 0 = rating unknown (no performance figures).
                vol.Optional(
                    CONF_MODULE_PEAK_POWER,
                    default=module.get(CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
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
        """Choose the transport to use after reloading this entry."""
        return self.async_show_menu(
            step_id="change_connection", menu_options=["change_tcp", "change_serial"]
        )

    async def async_step_change_tcp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the RS-485 to TCP converter's address."""
        return await self._async_connection_step(CONNECTION_TCP, user_input)

    async def async_step_change_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change to (or edit) a serial port or ESPHome serial proxy."""
        return await self._async_connection_step(CONNECTION_SERIAL, user_input)

    async def _async_connection_step(
        self, connection_type: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                settings = normalize_connection(
                    {**user_input, CONF_CONNECTION_TYPE: connection_type}
                )
            except InvalidConnectionSettings as err:
                errors[err.field] = err.error
            else:
                errors = await connection_errors(
                    self.hass, settings, self, self._config_entry
                )
                if not errors:
                    new_data = {
                        key: value
                        for key, value in self._config_entry.data.items()
                        if key not in CONNECTION_KEYS
                    }
                    self.hass.config_entries.async_update_entry(
                        self._config_entry,
                        data={**new_data, **settings},
                        title=connection_title(settings),
                    )
                    # A loaded entry reloads through its update listener; one
                    # that failed setup (e.g. invalid saved settings) has none.
                    if self._config_entry.state in (
                        ConfigEntryState.SETUP_ERROR,
                        ConfigEntryState.SETUP_RETRY,
                    ):
                        self.hass.config_entries.async_schedule_reload(
                            self._config_entry.entry_id
                        )
                    return await self.async_step_init()

        current = user_input if user_input is not None else dict(self._config_entry.data)
        return self.async_show_form(
            step_id=f"change_{connection_type}",
            data_schema=connection_schema(connection_type, current),
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
            m[CONF_MODULE_BARCODE]: _module_label(m)
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
