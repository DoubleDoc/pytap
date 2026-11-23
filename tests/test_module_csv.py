"""Importing and exporting module assignments as CSV (fork roadmap item 9)."""

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap.config_flow import modules_to_csv, parse_module_csv
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
from custom_components.pytap.coordinator import barcode_owners

A = "4-5C393EP"
B = "4-745B68W"


def test_parse_only_given_columns() -> None:
    rows, problems = parse_module_csv(
        "Barcode, Name ,string,peak_power,inverter\n"
        f"{A.lower()},P01,2,455,Master\n"
        f"{B},P07,,,\n"
        "\n"
    )
    assert problems == []
    assert rows == [
        {CONF_MODULE_BARCODE: A, CONF_MODULE_NAME: "P01", CONF_MODULE_STRING: "2",
         CONF_MODULE_PEAK_POWER: 455, CONF_INVERTER: "Master"},
        {CONF_MODULE_BARCODE: B, CONF_MODULE_NAME: "P07"},  # empty cells: unchanged
    ]


def test_parse_reports_every_problem_with_its_line() -> None:
    rows, problems = parse_module_csv(
        "barcode,string,peak_power,inverter,colour\n"
        "nonsense,2,455,\n"
        f"{A},2,9999,\n"
        f"{B},3,455,Master\n"
        f"{B},3,455,Master\n"
        "4-5C3962J,3,455,Slave\n"
        "4-5DB480M,,455,Master\n"
    )
    assert rows and len(problems) == 6
    assert problems[0] == "Unknown column(s): colour."
    assert any(p.startswith("Line 2: invalid barcode") for p in problems)
    assert any(p.startswith("Line 3: peak_power") for p in problems)
    assert any("appears more than once" in p for p in problems)
    assert any("two inverters" in p for p in problems)
    assert any("needs the string column" in p for p in problems)
    assert parse_module_csv("name\nP01\n")[1] == [
        "The first line must name the columns, including barcode."
    ]


def test_export_round_trips() -> None:
    modules = [
        {CONF_MODULE_BARCODE: B, CONF_MODULE_NAME: "P07", CONF_MODULE_STRING: "3",
         CONF_MODULE_PEAK_POWER: 455},
        {CONF_MODULE_BARCODE: A, CONF_MODULE_NAME: "P01", CONF_MODULE_STRING: "2",
         CONF_MODULE_PEAK_POWER: 0},
    ]
    text = modules_to_csv(modules, {"2": "Master"})
    assert text.splitlines()[:2] == [
        "barcode,name,string,peak_power,inverter",
        f"{A},P01,2,0,Master",
    ]
    rows, problems = parse_module_csv(text)
    assert problems == [] and {r[CONF_MODULE_BARCODE] for r in rows} == {A, B}


async def test_import_in_options(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=8, entry_id="csv",
        data={
            CONF_HOST: "10.0.0.5", CONF_PORT: 502,
            CONF_MODULES: [{CONF_MODULE_BARCODE: A, CONF_MODULE_NAME: "", CONF_MODULE_STRING:
                            "Discovered", CONF_MODULE_PEAK_POWER: 0}],
        },
    )
    entry.add_to_hass(hass)
    barcode_owners(hass).set_entry("elsewhere", {"4-5DB480M"})
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "import_modules"}
    )
    # A barcode owned by another entry is refused; nothing is applied.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"csv": "barcode,string\n4-5DB480M,2\n"}
    )
    assert result["errors"] == {"base": "csv_invalid"}
    assert "remove it there first" in result["description_placeholders"]["problems"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"csv": f"barcode,name,string,peak_power,inverter\n{A},P01,2,455,Master\n{B},P07,2,455,\n"},
    )
    assert result["step_id"] == "import_confirm"
    assert result["description_placeholders"]["added"] == "1"
    assert result["description_placeholders"]["changed"] == "1"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    modules = {m[CONF_MODULE_BARCODE]: m for m in entry.data[CONF_MODULES]}
    assert modules[A][CONF_MODULE_NAME] == "P01" and modules[A][CONF_MODULE_STRING] == "2"
    assert modules[A][CONF_MODULE_PEAK_POWER] == 455
    assert modules[B][CONF_MODULE_NAME] == "P07"
    assert entry.data[CONF_STRING_INVERTERS] == {"2": "Master"}
