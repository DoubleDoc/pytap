"""Device naming for PyTap.

Names are built from up to three levels, leaving out any that are not set:

* inverter: optional, per config entry (one PyTap instance per CCA gateway,
  usually one per inverter). Most installations have a single instance and
  leave it blank.
* string: the module's string group.
* module: the module's own name, usually its position (``P01``).

Examples::

    inverter="Master", module="P01" -> "Tigo Master P01"
    inverter="",       module="P01" -> "Tigo P01"

Optimizer devices leave the string out: maintenance can move a module to
another string, and the string is shown by the device it connects via (the
string's device) instead. String and total devices keep their names.

Optimizer entity IDs combine the position and the barcode, which never
changes: ``sensor.tigo_master_p01_4_5db467z_power``. They're set when an
entity is first created; existing IDs from the older string-based pattern
(``sensor.tigo_master_s2_p01_power``) are migrated once (config entry v8).
"""

from __future__ import annotations

from homeassistant.util import slugify

PREFIX = "Tigo"


def _join(*parts: str) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


def string_label(string_name: str) -> str:
    """Return the short label for a string inside an optimizer name.

    Numeric strings get an ``S`` prefix so they read as strings rather than
    as panel numbers (``2`` -> ``S2``). Other labels are used as they are.
    """
    string_name = (string_name or "").strip()
    if string_name.isdigit():
        return f"S{string_name}"
    return string_name


def optimizer_device_name(inverter: str, module_name: str, barcode: str = "") -> str:
    """Name of an optimizer device, e.g. ``Tigo Master P01``.

    A discovered optimizer that isn't named yet uses its barcode instead.
    """
    return _join(PREFIX, inverter, module_name.strip() or barcode)


def optimizer_object_id(inverter: str, module_name: str, barcode: str) -> str:
    """Entity ID base of an optimizer: position plus barcode.

    ``tigo_master_p01_4_5db467z``; without a name (e.g. just discovered),
    ``tigo_master_4_5db467z``.
    """
    return slugify(_join(PREFIX, inverter, module_name, barcode))


def legacy_optimizer_object_id(inverter: str, string_name: str, module_name: str) -> str:
    """Entity ID base from before v8: ``tigo_master_s2_p01`` (string, no barcode)."""
    return slugify(_join(PREFIX, inverter, string_label(string_name), module_name))


def string_device_name(inverter: str, string_name: str) -> str:
    """Name of a string aggregate device, e.g. ``Tigo Master String 2``."""
    return _join(PREFIX, inverter, "String", string_name)


def total_device_name(inverter: str) -> str:
    """Name of the instance total device, e.g. ``Tigo Master Total``."""
    return _join(PREFIX, inverter, "Total")
