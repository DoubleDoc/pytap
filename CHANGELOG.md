# Changelog

## [1.4.2] - 2026-11-17

### Added

- **Night and dropout handling** — Live readings no longer freeze overnight at the last daytime value (upstream issue #17). Using the sun's position from the Home Assistant location: an optimizer that is quiet for 10 minutes with the sun below 10° reads 0 for power, voltage, current, performance and duty cycle (temperature and RSSI keep their last value). By day a quiet optimizer keeps its last value for 30 minutes, since optimizers can drop off the radio link for a while, then its live readings become unavailable. Energy sensors are never affected.
- **Converter offline** — Live readings become unavailable after 5 minutes without any data from the RS-485 converter.
- **Estimating silent optimizers** — Optimizers in a string carry the same current, so while most of a string reports normally a silent optimizer is almost certainly producing too. String and total sensors now estimate its power from its learned share of the string's average, and add the energy it produced while silent (`estimated_count` and `estimated_energy` attributes). An optimizer's own sensors still show only what it reported. On by default; turn off under **Change reporting settings** to hold the last value instead.
- **Data validity sensor** — Diagnostic enum sensor per optimizer: reporting, holding last value, estimated from string, asleep, silent or converter offline, with the learned share and today's estimated energy as attributes.
- Node mapping sensor attributes `connection` (`up`/`down`) and `silent_modules`; diagnostics section `reading_health`.
- Warnings when one optimizer (or all of them) stops reporting by day, and when more than 2% of RS-485 frames fail their checks (wiring or termination problem).

### Changed

- Log lines start with the inverter name (or the converter address), so two gateways can be told apart.
- Connection failures are logged once per outage as a warning instead of an error every 5 seconds; retries back off from 5 to 60 seconds; recovery is logged once data has flowed for a minute.
- The "waiting for the node table" reminder is logged every 10 minutes instead of every 50 reports.
- String and total power count sleeping optimizers as 0 and silent ones as their estimate (or leave them out when none is possible); `reporting_count` counts only optimizers that are reporting.

### Fixed

- Unexpected errors while processing data were logged as "Connection error" without a traceback; they are now logged once with the traceback.
- Failures to load or save PyTap's stored state now include the reason, and a failed load is a warning instead of a debug message.

## [1.4.1] - 2026-11-17

### Changed

- Setup and connection screens now ask for the RS-485 to TCP converter's address (they said "Tigo gateway", which isn't what PyTap connects to).
- Minimum Home Assistant version is 2024.4 (`hacs.json` said 2023.9; the integration uses `ConfigFlowResult`, added in 2024.4).
- README: hardware setup (RS-485 wiring, Waveshare converter example), installation from the Home Assistant 2026.11 Marketplace or HACS, upgrade notes, energy dashboard, debug logging, removal and known limitations.

### Fixed

- Example dashboards: `Tigo Max Daily Energy` and the Grafana daily-energy panels showed Wh values labelled as kWh. The max sensors in `dashboards/tigo.yaml` now find the panels themselves instead of listing one installation's entity IDs.

## [1.4.0] - 2026-11-17

### Added

- **Inverter name** — Optional per-entry label (set during setup or under **Configure → Change inverter name**) for installations with more than one CCA gateway. Device names are now built as `Tigo [inverter] [string] [module]`, e.g. `Tigo Master S2 P01`, `Tigo Master String 2` and `Tigo Master Total`; numeric strings are shown as `S2`.
- **Node mapping sensor** — Diagnostic sensor on the total device: *waiting for node table*, *partly mapped* or *complete*, with configured/mapped counts, unmapped barcodes, the number of unidentified nodes reporting, and the time of the last node table. Also included in diagnostics.
- **Edit a module** — Change a module's string, name or peak power from the options flow, keeping its barcode and entities.
- **Rename a string** — Renames the string for all its modules and moves the string's aggregate entities and device, so their history is kept.
- README: *First start: what to expect* (why there is no data until the first morning), device naming, and dual-module optimizers.

### Changed

- Device names: optimizers `Tigo TS4 <name>` → `Tigo [inverter] [string] <name>`; the installation device `Tigo Installation` → `Tigo [inverter] Total`. Existing entity IDs are not changed.
- Config entry schema bumped to version 6 (adds `inverter`, default blank, via automatic migration).

### Fixed

- The per-optimizer `last_update` attribute is no longer recorded. It changed with every reading, so the recorder stored a new attributes row for every state write.

## [1.3.0]

### Added

- **Configurable reporting mode** — New "Live reporting" toggle (default on) under **Configure → Change reporting settings**. When disabled, readings are cached and delivered as a time-weighted average every *reporting interval* (5–300 s, default 30), reducing CPU and recorder load on installations with many panels. Energy totals are always integrated from raw readings, so only the instantaneous values are averaged.

### Changed

- Reporting interval default changed from 5 to 30 seconds; allowed range tightened from 1–300 to 5–300 seconds.
- Config entry schema bumped to version 5 (adds `live_reporting`, default `True`, via automatic migration).
- The reporting interval is now collected on its own follow-up screen that appears only when live reporting is turned off, so the interval is never shown (effectively greyed out) while live reporting is enabled.

### Fixed

- Live reporting no longer buffers readings internally, preventing unbounded memory growth over time in the default mode.
- Live reporting now publishes an immutable copy of node data to Home Assistant, avoiding rare races where consumers (sensors, diagnostics) could observe the data changing mid-read.
- Averaged interval mode now flushes buffered readings on schedule during quiet periods (e.g. at dusk), instead of waiting for the next reading or a reconnect.
- The version 5 migration clamps any pre-existing reporting interval below 5 seconds up to the new minimum, so the reporting settings form stays submittable after upgrade.

## [1.2.0] - 2026-03-07

### Changed

- Refactor code structure for improved readability and maintainability

## [1.1.1] - 2026-03-05

### Added

- Proactive midnight reset for daily energy accumulators

### Changed

- Reorder datetime imports for improved clarity
- Refactor code structure for improved readability and maintainability

### Documentation

- Document `sensor.tigo_max_power` and Page 3 (Panel Power) in pv_production dashboard

## [1.1.0] - Previous release

See [v1.1.0 tag](https://github.com/azebro/pytap/releases/tag/v1.1.0).

## [1.0.0] - Initial release

See [v1.0.0 tag](https://github.com/azebro/pytap/releases/tag/v1.0.0).
