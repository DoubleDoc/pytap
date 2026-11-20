"""Device naming and entity IDs for PyTap.

Device names are built from up to four levels, leaving out any that are not
set. They're for display only and follow every edit:

* inverter: the string's own inverter if set (one CCA serving several
  inverters), otherwise the entry's inverter name (usually one PyTap entry
  per CCA). Most installations leave it blank.
* string: the module's string group (numbers are shown as ``S2``).
* module: the module's own name, usually its position (``P01``); a
  discovered optimizer that isn't named yet shows its barcode.

Examples::

    inverter="Master", string="2", module="P01" -> "Tigo Master S2 P01"
    inverter="",       string="East", module="P01" -> "Tigo East P01"

Optimizer entity IDs are built from the barcode alone, which never changes:
``sensor.tigo_4_5db467z_power``. Moving a module to another string or
inverter, or renaming it, never changes them. Entity IDs that already exist
are never changed.
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


def optimizer_device_name(
    inverter: str, string_name: str, module_name: str, barcode: str = ""
) -> str:
    """Name of an optimizer device, e.g. ``Tigo Master S2 P01``."""
    return _join(PREFIX, inverter, string_label(string_name), module_name.strip() or barcode)


def optimizer_object_id(barcode: str) -> str:
    """Entity ID base of an optimizer: ``tigo_4_5db467z``."""
    return slugify(_join(PREFIX, barcode))


def string_device_name(inverter: str, string_name: str) -> str:
    """Name of a string aggregate device, e.g. ``Tigo Master String 2``."""
    return _join(PREFIX, inverter, "String", string_name)


def total_device_name(inverter: str) -> str:
    """Name of a total device, e.g. ``Tigo Master Total``."""
    return _join(PREFIX, inverter, "Total")


def string_inverter(string_inverters: dict[str, str], string_name: str, default: str) -> str:
    """The inverter a string feeds: its own if set, else the entry's."""
    return (string_inverters.get(string_name) or "").strip() or default


def has_inverter_totals(string_inverters: dict[str, str], default: str) -> bool:
    """Whether strings feed more than the entry's own inverter.

    Then every inverter gets its own Total device and the entry's Total
    becomes the CCA total, whatever the entry's inverter name is.
    """
    return any(
        (inv or "").strip() and inv.strip() != default for inv in string_inverters.values()
    )


def cca_total_device_name(inverter: str) -> str:
    """The entry's total once inverters have their own: ``Tigo Master CCA Total``."""
    return _join(PREFIX, inverter, "CCA Total")


def inverter_total_object_id(inverter: str) -> str:
    """Entity ID base of an inverter's total: ``tigo_inverter_master``."""
    return slugify(_join(PREFIX, "Inverter", inverter))
