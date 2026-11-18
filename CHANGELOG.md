# Changelog

## [1.4.10] - 2026-11-18

### Changed

- **Data validity at dusk** — An optimizer that has been quiet for 10 minutes with the sun below 10° now shows *Asleep*, even while others in its string are still awake. It used to show *Estimated* until half its string had gone quiet, although the estimate was about 0 W (the awake optimizers report about 0 W at dusk). Energy is unchanged; by day, estimating works as before.

### Dashboards

- PV Panels dashboard and the classic Panel Power view: tiles redraw every 10 seconds (`update_timer`), so a panel's data validity, energy and readings show current values even while its power is unchanged, such as a panel turning Estimated at 0 W at dusk.
- `dashboards/pv_panels.md`: dusk screenshots showing Estimated (amber) and holding (`1 W*`) tiles, and a note on the refresh timer.

## [1.4.9] - 2026-11-18

### Documentation

- README: midday screenshot of the PV Panels dashboard, a link to tigo_server's ESPHome variant, and a revised Future work example.
- `dashboards/pv_panels.md`: midday screenshots in Power and Energy mode.

## [1.4.8] - 2026-11-18

### Fixed

Decoding now follows how the Tigo CCA firmware decodes power reports, as documented in Bobsilvio's [tigo_server protocol notes](https://github.com/Bobsilvio/tigo_server/blob/main/docs/PROTOCOL-NOTES.md). Readings on installations like the one this fork is tested on (all reports carry a temperature, hardware in the 0.005 A range) don't change.

- **Input current scale by hardware** — Current was always decoded at 0.005 A per unit, so older optimizers read 2× too high and "higher rated" ones 5× too low (and their power with it). PyTap now learns each optimizer's hardware ID from its Version answer, keeps it across restarts, and uses the matching scale (0.0025, 0.005 or 0.025 A), assuming 0.005 A until it is known.
- **Temperature** is taken only from reports whose status word marks the field as a temperature (bit 0x20); other reports keep the last valid temperature.
- **RSSI** of 0 means "no usable value" and no longer replaces the last valid RSSI.
- **12-byte power reports** (no RSSI byte) are decoded instead of dropped.
- **Saving state when Home Assistant stops** — PyTap stopped its listener and saved its state only when its entry was unloaded, which Home Assistant doesn't do at shutdown. So the last few seconds of changes weren't saved at a restart, and Home Assistant logged the listener thread as still running at shutdown. PyTap now stops and saves when Home Assistant stops, after the last reading.

### Testing

- Tests run against Home Assistant 2026.10.4 on Python 3.14 (were 2026.2.2 on 3.13), and CI now also runs the embedded parser's own tests. PyTap was also checked by hand on Home Assistant 2026.11.2, replaying recorded bus traffic.
- Removed `requirements.txt`, an unused copy of Home Assistant 2026.2's pinned dependencies that raised 137 Dependabot alerts.

### Documentation

- README: the fork note and credits for upstream, a Future work section, and softer wording about silent optimizers and live reporting.

## [1.4.7] - 2026-11-18

### Changed

- **Restart gaps are bridged** — PyTap can't hear the bus while Home Assistant is down, so each optimizer's energy for a daytime restart used to be lost. Now, for each optimizer that was reporting when Home Assistant stopped, a gap of up to 10 minutes between its last reading before the restart and its first after is bridged with the average of the two, counted as estimated energy (when **Estimate silent optimizers** is on). Longer outages, such as a host reboot, are still lost; the README's Known limitations now explains this.

### Example dashboards

- **New PV Panels dashboard** (`dashboards/pv_panels_dashboard.yaml`, documented in `dashboards/pv_panels.md`): one view in which every panel tile shows live power (or *Asleep*, *Est.*, *Silent*, *No data*), today's energy and today's readings, with a red-to-green connectivity bar along its bottom edge. A **Colour tiles by [Power | Energy]** switch (helper `input_select.tigo_tile_colour`) picks what the tile colour shows; estimated, silent and no-data panels keep their amber, red and grey colours in both modes. Styling lives in two `button_card_templates`, so each panel is one line. The README now leads with it.
- Classic PV Production dashboard: tile text is centred vertically. button-card kept an empty icon row that pushed the name and value to the bottom of each tile.
- `pv_production.md`: dawn screenshots of all three views; the doc now points to the PV Panels dashboard first.

## [1.4.6] - 2026-11-18

### Fixed

- `reporting_count` on the String and Total devices counted asleep and briefly quiet optimizers as reporting (at night it showed 12 of 12). It now counts only optimizers whose Data validity is *Reporting*: those that reported within the last 2 minutes.

### Example dashboards

- The max sensors in `dashboards/tigo.yaml` are trigger-based and recalculate once a minute. As plain template sensors they recalculated after each panel sensor updated, writing several in-between values to the database per PyTap update.
- **Panel Power** view: tiles are coloured and labelled by each panel's Data validity: green by power while reporting (`312 W`, or `312 W (held)`), amber *Estimated*, red *Silent*, dark *Asleep*, grey *No data*. A silent panel used to look the same as a sleeping one.
- **Panel Connectivity** view: tiles stay dark and show `–` until any panel has reported today, instead of turning red after midnight.
- **PV Panel Layout** view: energy is shown compactly (`838 Wh`, `1.61 kWh`) so it fits portrait tiles.
- `pv_production.md`: screenshots of each view, and D12/D13 corrected to landscape.

## [1.4.5] - 2026-11-17

The two database-write changes below come from [seaosair](https://github.com/seaosair)'s upstream [PR #25](https://github.com/azebro/pytap/pull/25). They found that the `last_update` attribute, not the values, was forcing a write on every reading, showed that excluding it from the recorder doesn't stop the write, and showed that rounding only pays off once that attribute is fixed. They backed each point with measurements from a 23-module installation. We measured the same effects here before adopting them. Thank you, seaosair.

### Changed

- **No per-reading timestamp attribute** — Home Assistant writes a new state row whenever any attribute changes, even one excluded from the recorder. So the per-optimizer `last_update` attribute, which changed with every reading, forced a write on every reading even when the value was unchanged (measured here: a temperature sensor wrote 720 rows in 6 daytime hours while its value changed only 414 times). It is removed. The Data validity sensor shows `last_report` only once the optimizer has stopped reporting, with its share rounded to 0.05, and the Bus status sensor shows `last_data` only while the status isn't normal.
- **States rounded to a sensible precision** — Live values are averages over the reporting interval, so their extra decimals are averaging noise that changes on almost every write. States are now rounded: power 1 W, voltages 0.1 V, currents 0.01 A (kept fine so small differences between panels in a string still show), temperature 1 °C, performance 0.1 %, duty cycle 1 %, RSSI whole numbers, energy 0.01 Wh. Replaying 6 hours of real readings, this cuts an optimizer's rows by about a fifth. Fixed precisions rather than PR #25's per-quantity options.
- **New entries start in averaged mode at 60 seconds** (was live mode). The default reporting interval is now 60 seconds (was 30). Existing entries keep their settings. Energy is integrated from every raw reading in either mode, so the interval doesn't affect energy accuracy. A 10-day backtest of a PV curtailment detector built on the per-panel voltages and currents found the same events at 60 s as at 30 s, each starting at most a minute later.
## [1.4.4] - 2026-11-17

### Fixed

- **Short gaps no longer lose energy** — A 2–3 minute gap in an optimizer's reports that ended before the once-a-minute check started estimating was lost from the String and Total devices' energy. It is now bridged with the average of the optimizer's readings either side, counted as estimated energy (its own energy sensors still show only measured energy). Off when estimating is off.

### Documentation

- The README explains that the String and Total devices' sensors are all named **Power**, **Daily energy**, etc. on their device pages, and that "Total" names both the gateway-wide device and the lifetime **Total energy** sensor; it now says "String and Total devices" instead of "string/total sensors".

## [1.4.3] - 2026-11-17

### Fixed

- Home Assistant 2026.10 warned at startup that `get_astral_location` (used for the sun position) is deprecated and will be removed in 2027.7. PyTap now computes the sun elevation with astral directly, which works on all supported versions.

## [1.4.2] - 2026-11-17

### Added

- **Night and dropout handling** — Live readings no longer freeze overnight at the last daytime value (upstream issue #17). Using the sun's position from the Home Assistant location: an optimizer that is quiet for 10 minutes with the sun below 10° reads 0 for power, voltage, current, performance and duty cycle (temperature and RSSI keep their last value). By day a quiet optimizer keeps its last value for 30 minutes, since optimizers can drop off the radio link for a while, then its live readings become unavailable. Energy sensors are never affected.
- **Bus status sensor** — Per gateway, on the Total device: *normal*, *RS-485 converter unreachable* (the TCP connection fails) or *bus quiet* (connected, but nothing on the bus: CCA off or wiring). After 5 minutes without data every optimizer's live readings become unavailable, and the warning says what to check.
- **Estimating silent optimizers** — Optimizers in a string carry the same current, so while most of a string reports normally a silent optimizer is almost certainly producing too. String and total sensors now estimate its power from its learned share of the string's average, and add the energy it produced while silent (`estimated_count` and `estimated_energy` attributes). An optimizer's own sensors still show only what it reported. On by default; turn off under **Change reporting settings** to hold the last value instead.
- **Data validity sensor** — Diagnostic enum sensor per optimizer: reporting, holding last value, estimated from string, silent but estimated in string, asleep or silent (unavailable while the bus status isn't normal), with the learned share and today's estimated energy as attributes.
- Node mapping sensor attribute `silent_modules`; diagnostics section `reading_health`.
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
