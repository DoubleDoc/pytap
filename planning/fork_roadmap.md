# Fork roadmap

The plans of this fork ([DoubleDoc/pytap](https://github.com/DoubleDoc/pytap)), against upstream's [future considerations](future_considerations.md) (items 1–8, February 2026), plus the fork's own items (9 onwards). Last reviewed 2026-11-23, at release 1.6.3.

## Upstream's items

| # | Upstream plan | Status in this fork |
| --- | --- | --- |
| 1 | Per-optimizer daily and lifetime energy | ✅ As planned. Extended: a gap of 2–3 minutes is bridged from the optimizer's own readings either side, and a restart gap of up to 10 minutes is bridged too. |
| 2 | String and installation totals | ✅ Extended: totals per inverter as well, energy totals that survive optimizers moving between strings (Tim Laing's [PR #35](https://github.com/azebro/pytap/pull/35)), and estimates for silent optimizers (see 8b). |
| 3 | Performance (% of peak power) | ✅ As planned. A discovered optimizer has peak power 0 (unknown), and its performance entities stay disabled until a rating is set. |
| 4 | Energy dashboard sensors | ✅ As planned. |
| 5 | Diagnostics download, readings counter | ✅ Extended: bus status, reading health and the power-report flag bytes decoded bit by bit. |
| **6** | **Binary sensors: "will not implement"** (node connectivity from last-update age, gateway online) | **Implemented differently.** For connectivity the fork follows upstream and has no binary sensors: each optimizer has a *Data validity* sensor and each gateway a *Bus status* sensor, both with several states (reporting, holding, estimated, silent, asleep; normal, converter unreachable, bus quiet) instead of on/off. **The fork does have binary sensors for the power reports' status flags** (Low input voltage, jstatus bit 7, Idle pass-through, Reading fault), adapted from Tim Laing's [PR #39](https://github.com/azebro/pytap/pull/39), which upstream declined. They record a state only when it changes. |
| 7 | HACS distribution | Replaced: installed from Home Assistant's built-in Marketplace (HA 2026.11 and later). |
| 8a | Bulk loading of devices | ✅ **Automatic discovery** adds every optimizer heard on the bus (Tim Laing's [PR #37](https://github.com/azebro/pytap/pull/37)), and **importing assignments from CSV** sets names, strings, ratings and inverters in bulk (item 9, done in 1.7.0). |
| 8b | Configurable energy gap threshold (fixed at 120 s) | **Superseded in this fork by estimation.** Once an optimizer is quiet for 2 minutes, its power is estimated from its string neighbours' actual output (its learned share of their average), which follows clouds better than interpolating across a longer gap. The string and installation energy include that estimate; the optimizer's own energy counts only what it reported, by design. The threshold would matter only for an optimizer that can't be estimated (alone in its string, or too few neighbours reporting). Still a reasonable small change for upstream, which has no estimation. |

## 9. Import module assignments from a spreadsheet

**Done in 1.7.0** (Configure → Import modules from CSV / Export modules as CSV), as planned below.

Discovery finds the optimizers, but naming them, putting them in strings, rating them and assigning inverters is still one optimizer at a time under **Configure → Edit a module**. For an installation of dozens of panels, or after rewiring, that's slow and error-prone. Installers usually already have this data in a spreadsheet (the Tigo app's layout, or a commissioning sheet).

**Plan:** an options step, **Import modules**, usable at any time (setup or later), that takes a CSV file, upload or pasted text:

```csv
barcode,name,string,peak_power,inverter
4-000002B,P01,2,455,Master
4-000016P,P01,2,455,Slave
```

- `barcode` required; the other columns optional, and only columns present are changed (so a sheet with just `barcode,name` renames).
- Rows for optimizers already tracked update them; new barcodes are added (whether discovered yet or not); optimizers not in the sheet are left alone (no implicit removal).
- `inverter` sets the string's inverter (`string_inverters`), and must agree for all rows of a string.
- Validation before anything is saved: barcode format, duplicates, unknown or conflicting values, a barcode owned by another PyTap entry. The step shows a preview (added, changed, unchanged, errors) and saves only on confirmation.
- Entity IDs don't change (they use the barcode), so an import never breaks dashboards or history.
- **Export** the current assignments as the same CSV, as a backup and a starting template.

## 10. Offload parsing and processing to an IoT device

**The main direction for the fork.** PyTap's two biggest limitations come from running inside Home Assistant (see [How it works → Future work](../docs/how-it-works.md#future-work)):

- **Load:** Home Assistant receives and parses the whole bus stream (about 10–12 MB an hour per gateway, day and night) and processes every power report, about 9 a second on an 18-optimizer installation in daylight (304,850 on 2026-11-22).
- **Losses:** while Home Assistant restarts, updates or is down, nobody listens. Restarts of up to 10 minutes are bridged; longer gaps lose energy for good.

**Goal:** a small device at the RS-485 bus does the real-time work, around the clock, and Home Assistant gets finished data.

**On the device:**

- receive-only RS-485 (never transmits on the bus), frame parsing, slot clock, power-report decoding with the flag bytes;
- the node table and barcode mapping, kept in its own flash so they survive power cuts and never need relearning;
- per-optimizer energy integration, daily and lifetime counters, readings counts, reading-fault checks;
- averaging and rounding, sent at an interval Home Assistant chooses;
- optionally, raw-bus forwarding or capture on demand, for research like the [status-bit work](../docs/protocol-status-bits.md).

**In Home Assistant (PyTap):** history, dashboards, the Energy dashboard, automations; data validity, sun-aware night handling and estimates across strings and inverters; configuration (names, strings, ratings, inverters, item 9). The existing RS-485-to-TCP and serial paths stay supported for anyone without the device.

**Template:** Bobsilvio's [tigo_server](https://github.com/Bobsilvio/tigo_server), especially its [ESPHome variant](https://github.com/Bobsilvio/tigo_server/blob/main/esphome/README.md): an ESP32 with a receive-only RS-485 module decodes the bus itself, keeps the panel mapping in flash and gives Home Assistant per-panel entities. Its decoding was checked against the CCA firmware; this fork's status-bit findings, energy rules and validation are what PyTap would add.

**Open design questions:**

1. **Device protocol:** ESPHome is a strong candidate: Home Assistant supports it natively, and it handles the connection, updates and logs. Needs research.
2. **Energy authority:** the device's counters become the source of truth, with PyTap reading them, so a Home Assistant outage loses nothing. That needs care with counter resets, device replacement and the migration from PyTap's own counters.
3. **Time and day boundaries:** the device needs reliable time (NTP) for daily counters and midnight resets.
4. **Updates and safety:** OTA updates, and a hardware guarantee that it can't transmit (receive-only transceiver, or the driver-enable line tied off), as with the converter today.
5. **Hardware (the current blocker):** no suitable ready-made device found yet. Concerns: packaging, durability, isolation and protection, power, cost, and the processor and storage (type and capacity). Choose hardware with room to grow: the same device could later become a **CCA replacement**, managing the TAPs itself instead of only listening (see upstream [discussion #27](https://github.com/azebro/pytap/discussions/27)). That would be a separate, much larger step.
