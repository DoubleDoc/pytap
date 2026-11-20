# Entities and devices

[← PyTap README](../README.md)

## Optimizer sensors

Each Tigo TS4 optimizer PyTap tracks (added by hand or by [discovery](configuration.md#step-2-add-modules)) has these sensors:

| Sensor | Unit | Device Class |
| --- | --- | --- |
| Performance | % | — |
| Power | W | `power` |
| Voltage In | V | `voltage` |
| Voltage Out | V | `voltage` |
| Current In | A | `current` |
| Current Out | A | `current` |
| Temperature | °C | `temperature` |
| DC-DC Duty Cycle | % | — |
| RSSI | dBm | `signal_strength` |
| Daily Energy | Wh | `energy` |
| Total Energy | Wh | `energy` |
| Readings Today | — | — (diagnostic) |
| Data validity | — | `enum` (diagnostic): see [Data validity](readings.md#data-validity-sensor) |

## Status flags and reading faults

Each optimizer also has **flag entities** from its power reports' flag bytes, and a **Reading fault** indicator (all diagnostic; adapted from [Tim Laing](https://github.com/timlaing)'s upstream [PR #39](https://github.com/azebro/pytap/pull/39)). Tigo doesn't document the flag bytes: they're read bit by bit, and the names describe what captured data shows ([details and evidence](protocol-status-bits.md)).

| Entity | Type | Source | Meaning | Enabled |
| --- | --- | --- | --- | --- |
| Status | sensor | status byte | The whole byte, e.g. `0x64`; its bits decoded as attributes | yes |
| Low input voltage | binary | status bit 7 | The optimizer's input voltage is low (around 15 V on average when set) | yes |
| Producing | binary | jstatus bit 7 | Current is flowing through the optimizer | yes |
| Reading fault | binary (problem) | — | The latest report was physically impossible (see below) | yes |
| Idle pass-through | binary | status bit 1 | **Tentative:** an idle state passing input straight through, which only some optimizers enter | no |
| State field | sensor | status bits 3–2 | Always 1 or 2 so far; **meaning open** | no |
| jstatus level | sensor | jstatus bits 0–6 | **Tentative:** 1–4 while producing, following how hard the optimizer converts | no |

Enable the disabled ones on the optimizer's device page if you want to follow them. Flag entities record a state only when their value changes. They show the latest report's flags while the optimizer reports and while it's asleep at night (its last report then shows its idle state); when it goes silent in daylight they become unavailable, unless the stale-reading settings hold or zero the readings, in which case they keep the last flags.

**Reading fault:** a report is discarded as impossible when its output voltage is more than 2 V above its input (TS4 optimizers only step voltage down), a voltage is outside 0–100 V, or a valid temperature is outside −40…125 °C. A discarded report doesn't replace the last good values, add energy, or count as a fresh report; Reading fault turns on (attributes `fault_reasons` and `last_fault_at`) and a warning is logged, and it turns off with the next valid report. In a 99,618-report capture here, no report broke these rules.

## How readings are decoded

Readings are decoded the way the Tigo CCA's own firmware does it:

- **Current In** uses each optimizer's input current scale, which depends on its hardware: 0.0025 A per unit for older units, 0.005 A for most, 0.025 A for "higher rated" units. PyTap learns each optimizer's hardware ID from its Version answer when the CCA asks for it, keeps it across restarts, and assumes 0.005 A until then. **Power** is Voltage In × Current In.
- **Temperature** is updated only from reports that mark the field as a temperature, and **RSSI** only from reports with a usable value (not 0, and not the short 12-byte report, which has none); otherwise the last valid value is kept.

## String, inverter and Total devices

PyTap also creates aggregate devices:

- **Tigo [inverter] String <name>** — `performance`, `power`, `daily_energy`, `total_energy`. Created for every string, even when there is only one, so a string keeps its history when more strings are added later.
- **Tigo [inverter] Total** — `performance`, `power`, `daily_energy`, `total_energy` for everything on the gateway (CCA), plus diagnostic sensors: **Node mapping** (whose `silent_modules` attribute lists silent modules), **Bus status** (see below) and, with discovery on, **Module discovery**.
- **Tigo [inverter] Total per inverter** — only when one CCA serves several inverters and strings are assigned to them (see [Device naming](#device-naming)): the same four sensors per inverter. The gateway's Total is then named *CCA Total*.

On each device page these sensors are simply called **Power**, **Performance**, **Daily energy** and **Total energy**; the device name tells them apart (full names such as *Tigo Master String 2 Power* and *Tigo Master Total Power*). "Total" therefore means two things: the **Total device** (the whole gateway) and the **Total energy** sensor (lifetime energy), which on the Total device is named *Tigo … Total Total energy*. These docs say "the String and Total devices" for the sums, and "Total energy" only for lifetime energy.

## Bus status

Each gateway (each PyTap entry, i.e. each CCA) has a **Bus status** sensor on its Total device. The Tigo CCA polls its optimizers around the clock, so the bus is never quiet in normal operation, even at night. If no data arrives for 5 minutes, PyTap can't tell anything about any optimizer on that gateway, so **every optimizer's live readings and Data validity become unavailable** until data returns. Energy totals are kept.

| State | Meaning | Check |
| --- | --- | --- |
| Normal | Data is arriving from the bus. | — |
| RS-485 converter unreachable | The TCP connection to the converter fails or keeps dropping. | The converter's power, network cable or Wi-Fi, IP address and port. |
| Bus quiet | The converter accepts the connection but has nothing to forward. | That the Tigo CCA is powered and running, and the converter's A/B wiring to the CCA's gateway bus. |

While the status isn't normal, the `last_data` attribute shows when data last arrived.
## Device naming

**Entity IDs are the optimizer's barcode**, which never changes: `sensor.tigo_4_5db467z_power` for barcode `4-5DB467Z`. Moving a module to another string or inverter, renaming it, or changing its position never changes its entity IDs, so dashboards, automations and history keep working through maintenance. The barcode is printed on the optimizer and shown on its device page.

**Device names are for reading** and follow every change. They're built from up to four levels, leaving out any that aren't set:

| Level | Set in | Example |
| --- | --- | --- |
| Inverter | **Inverter name** (per PyTap entry), or a string's own inverter (**Set a string's inverter**) | `Master` |
| String | Each module's **String group** | `2` (numbers are shown as `S2`) or `East` |
| Module | Each module's **Name**, usually its position; a discovered optimizer shows its barcode until named | `P01` |

| Device | One inverter (no inverter name) | Inverters named (`Master`, `Slave`) |
| --- | --- | --- |
| Optimizer | `Tigo S2 P01` | `Tigo Master S2 P01` |
| String total | `Tigo String 2` | `Tigo Master String 2` |
| Total | `Tigo Total` | `Tigo Master Total` |

Entity *names* (what cards and history show, e.g. "Tigo Master S2 P01 Power") come from the device name, so they follow moves too. String and total entity IDs are named after the string or total (`sensor.tigo_master_string_2_power`).

**Which setup you have:**

- **One inverter, one CCA** (most installations): one PyTap entry, inverter name blank.
- **One CCA per inverter:** one PyTap entry per CCA, each with an inverter name (`Master`, `Slave`). Each entry's Total device is that inverter's total.
- **One CCA serving several inverters:** one PyTap entry. Use **Configure → Set a string's inverter** to name the inverter each string is wired to; strings you don't set feed the entry's inverter name. As soon as strings feed more than one inverter, each inverter gets its own Total device (`Tigo Master Total`, entity IDs `sensor.tigo_inverter_master_…`), and the entry's Total becomes the CCA total (`Tigo Master CCA Total`, or `Tigo CCA Total` without an inverter name). String names must be unique within the entry (for example `1`–`4`, or `M1`, `S1`).

**Upgrading:** entity IDs that already exist are never changed; the barcode pattern applies to entities created from now on. Rename older ones in Home Assistant if you want them to match (history and statistics move with the rename).

## Dual-module optimizers

- **TS4-A-O-DUO / TS4-R-O-DUO** connect two panels in series to one optimizer. It has one barcode and sends one set of readings, so it appears as one device whose voltage, power and energy cover both panels. Name it for both positions (for example `P01+P02`) and set its peak power to the two panels combined.
- **TS4-A-F / TS4-A-2F** are rapid-shutdown-only devices that don't report to the TAP, so PyTap can't see them.

## Energy dashboard

Each optimizer, string and gateway total has a **Total energy** sensor (Wh, `total_increasing`). Add the gateway totals under **Settings → Dashboards → Energy → Solar production** to see PyTap's production there. If your inverter also reports production, add only one of them to avoid counting it twice.

The String and Total devices' energy sensors keep their own running count: they add up each member's increase since it was last seen, rather than summing the members' lifetime totals. So moving a module to another string, or adding or removing one, never adds its whole history to a total or looks like a meter reset in long-term statistics; only energy produced afterwards counts. (From [Tim Laing](https://github.com/timlaing)'s upstream [PR #35](https://github.com/azebro/pytap/pull/35).)
