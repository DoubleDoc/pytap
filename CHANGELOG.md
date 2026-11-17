# Changelog

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
