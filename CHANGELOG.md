# Changelog

## [Unreleased]

### Changed

- **PV Production (classic) dashboard** — Panel Power tiles match PV Panels: `0 W` with a sleep icon while asleep, the estimate (`~85 W`) while estimated.
- **Flag documentation: second full-day capture** — 304,850 more reports (about 795,000 in all): bit 3 is absent some days, bit 1 again only in the morning, and jstatus bit 7 is mostly set at low current too, as Adam Zebrowski also observed. The capture also confirmed PyTap's live reading counts and daily energy against the raw bus data.
- **README** — The front screenshots show the current tiles (a night view replaces the dusk view and the classic dashboard).

## [1.6.2] - 2026-11-21

### Added

- **Estimated power per optimizer** — While an optimizer is estimated from its string, its Data validity sensor has an `estimated_power` attribute: the estimate the String and Total devices are using. Its own Power sensor still shows only what it reported. The estimate changes at most once a minute, so it adds little to the database.

### Changed

- **Flag documentation from a full-day capture** — 24 hours on both gateways (320,128 reports): status bits 1 and 3 occur only in the morning while idle; jstatus bit 7 latches (each optimizer switches it about 20 times a day) rather than following a current threshold; jstatus levels match conversion only at low current, so they are now documented as open. See [protocol-status-bits.md](docs/protocol-status-bits.md).
- **PV Panels dashboard** — Tiles show watts in every state that has them: `0 W` with a sleep icon while asleep (it said *Asleep*, with no power figure) and `~85 W` while estimated (it said *Est.*). With PyTap 1.6.1 or older, estimated tiles still say *Est.* A night screenshot was added to the dashboard's page.

## [1.6.1] - 2026-11-20

### Changed

- **Documentation reorganised** — The README is now a short overview with screenshots, a quick start and live credits; the details moved to topic pages under [`docs/`](docs/) (hardware, installation, configuration, entities, readings, capture, troubleshooting, how it works, development, credits). Reviewed throughout: serial and ESPHome connections, discovery, flag entities and the averaged default are described everywhere they apply.
- **Docs reviewed against Tim Laing's documentation** — The design document ([architecture.md](docs/architecture.md)) is rewritten for the current code (listener-owned state, connections, discovery, capture, flags and faults, naming); [implementation.md](docs/implementation.md) becomes the development history, with the fork's phases added. New: hardware safety (keep the receiver passive), serial and ESPHome notes with a link to Tim's ESPHome example, what's in a capture and capture tips, measured capture sizes, and a note that PyTap isn't affiliated with Tigo Energy. Fixed: the connection test shows *Could not connect* and saves on a second submit (it said "only warns").
- **CI tests on Home Assistant 2026.11** — The main suite's test harness pins Home Assistant 2026.10.4, older than PyTap's minimum, so a new job runs harness-free smoke tests (flows, migration, serial, discovery, capture, device linking) on Home Assistant 2026.11.0 itself (adapted from Tim Laing's). Lint also covers `scripts/`.
- **Tim Laing in the license** — About a fifth of the code comes from his pull requests, so the license now carries his copyright line beside Adam Zebrowski's and the fork's.
- **Installation docs cover the Marketplace only** — PyTap 1.5 and later need Home Assistant 2026.11, where the Marketplace replaced HACS, so the HACS instructions for older versions were removed.
- **This fork is the maintainer of record** — The Marketplace now shows DoubleDoc as the maker, and its source and issue links go to this repository instead of upstream. Adam Zebrowski remains credited as PyTap's author, in the README, the credits page and the license.

### Fixed

- **Flag documentation corrected with a dusk capture** — jstatus bit 7 is set in nearly every report above about 0.5 A and rarely below 0.25 A; it is not, as documented, set in every report with current flowing. Its entity is renamed from *Producing* to **High output mode**, Tim Laing's name for it, which this data confirms; its entity ID (`…_producing`) doesn't change. Bit 3 and bit 1 appear only in the morning. The jstatus levels follow the same order at dawn and dusk. Bus traffic is the same day and night. See [protocol-status-bits.md](docs/protocol-status-bits.md).
- **Restart at night reads asleep at once** — After a Home Assistant restart at night, every optimizer held its last values for 10 minutes (Data validity *Holding*) before reading 0 as asleep, because quiet time was counted from the restart. When the sun is low at startup, PyTap now carries each optimizer's last report time over from its saved state, so ones already quiet for 10 minutes read as asleep immediately. Daytime restarts are unchanged, and estimates never count time from before PyTap started. Checked against a copy of a live installation's state at night: all 18 optimizers *Asleep* at 0 W from the first update, where 1.6.0 showed *Holding*.
- **No more `via_device` deprecation warning** — Home Assistant 2026.11 deprecated linking a device to its parent by identifier (it stops working in 2027.8). Optimizer devices now connect via their string's device by registry id (`via_device_id`), registering the string device first.

## [1.6.0] - 2026-11-20

### Added

- **Optimizer flag entities and reading faults** — Each optimizer gets diagnostic entities from its power reports' flag bytes, read bit by bit: **Status** (the byte in hex, bits as attributes), **Low input voltage** (bit 7), **Producing** (jstatus bit 7), and, disabled by default because their meaning is tentative or open, **Idle pass-through** (bit 1), **State field** (bits 3–2) and **jstatus level**. They record a state only when it changes, and follow the stale-reading settings when a panel goes silent. **Reading fault** (problem) flags physically impossible reports (output above input, voltages outside 0–100 V, temperature outside −40…125 °C), which are discarded instead of replacing good values or adding energy. Flags and faults are kept across restarts. Adapted from [Tim Laing](https://github.com/timlaing)'s upstream [PR #39](https://github.com/azebro/pytap/pull/39), with flags as single bits instead of named codes. (#39)
- **Packet capture and offline replay** — Admin-only actions record the raw bus bytes PyTap reads (`pytap.start_capture`, `stop_capture`, `capture_status`, `list_captures`, `export_capture`, `delete_capture`): passive, off unless started, never resumed after a restart, bounded by duration (default 24 h) and storage (default 1 GiB), private files under `/config/pytap_captures/` with checksums, ZIP export. Captures are deleted with the PyTap entry; the README explains how to delete them yourself, and that they're included in backups. `scripts/replay_capture.py` checks and exports a capture offline; the parser accepts a clock so replays use recorded times. Adapted from [Tim Laing](https://github.com/timlaing)'s upstream [PR #38](https://github.com/azebro/pytap/pull/38). (#38)
- **Power report export for research** — `scripts/capture_reports.py` replays a capture (PyTap's own, or a simple timestamped recording) through PyTap's parser and writes every power report to a CSV with its flag bytes decoded bit by bit, with an optional per-optimizer summary of status and jstatus values. The flag decoding moved into the parser library so diagnostics and the script share it.

## [1.5.0] - 2026-11-20

The changes below are ported from [Tim Laing](https://github.com/timlaing)'s upstream pull request series: [PR #32](https://github.com/azebro/pytap/pull/32), [PR #33](https://github.com/azebro/pytap/pull/33), [PR #34](https://github.com/azebro/pytap/pull/34), [PR #35](https://github.com/azebro/pytap/pull/35), [PR #36](https://github.com/azebro/pytap/pull/36) and [PR #37](https://github.com/azebro/pytap/pull/37) so far.

**Requires Home Assistant 2026.11 or later** (for serial connections).

### Fixed

- **String and total energy survive membership changes** — They summed their members' lifetime totals, so moving a module to another string (*Edit a module*), adding one or removing one made a string's or total's energy jump by that module's whole history (recorded as new production) and the old one drop (seen as a meter reset). Each now keeps its own running count of members' increases (measured plus estimated), restored after a restart; existing installations continue from their current value with no jump. The daily sensors reset at local midnight with `last_reset` at the start of their own day. (#35)
- **Saved state that can't be read is never overwritten** — If PyTap couldn't read its saved state, it started empty and its next save replaced the file, losing lifetime energy totals. It now leaves the file alone until the next reload. (#33)
- **Midnight reset is DST-safe** — It used a timer for "seconds until midnight", which fired an hour off on the nights the clocks change; it now uses Home Assistant's local-time scheduling. (#33)
- **Stopping interrupts the connection** — A stop wakes the listener's blocked read at once instead of waiting for its timeout. A listener that doesn't stop within 15 s is set aside and the entry isn't set up again until it has released the connection, instead of the entry getting stuck. A setup that fails part-way stops its listener again. (#33)
- **Removing an entry deletes its saved state.** (#33)
- **Diagnostics hide the entry title**, which contains the converter's address (the host itself was already hidden). (#33)

### Added

- **Module discovery** — Optimizers heard on the bus can be tracked automatically (passive: nothing is sent). On by default for new setups, which can finish without adding modules; existing entries keep manual mode (**Configure → Change module discovery**). A discovered optimizer starts unnamed (device `Tigo [inverter] Discovered <barcode>`), in string *Discovered*, with an unknown rating (0 Wp): no performance figures, and the performance entities it affects are disabled until rated. **Edit a module** works on discovered optimizers too. Removing a module, or deleting its device from the UI, excludes its barcode; empty string devices can be deleted. Each barcode is tracked by one PyTap entry only. A **Module discovery** diagnostic sensor and diagnostics section show its state. (#37)
- **Power report flag bytes in diagnostics** — Each optimizer's latest status (bytes 8–9) and jstatus (byte 7) are kept and shown decoded bit by bit under `flags` in diagnostics: low input voltage (bit 7), the 2-bit state field (bits 3–2), idle pass-through (bit 1, tentative), producing (jstatus bit 7) and the jstatus level. See [docs/protocol-status-bits.md](docs/protocol-status-bits.md) for what each bit means and how sure that is, from a 2026-11-20 capture of 99,600 reports.
- **Serial and ESPHome serial-proxy connections** — Besides an RS-485 to TCP converter, PyTap can read the bus from a serial adapter on the Home Assistant host or from an ESPHome device with `serial_proxy`, picked with Home Assistant's serial-port selector. Setup and **Change connection settings** offer *RS-485 to TCP converter* or *Serial port or ESPHome serial proxy*; switching keeps entities and energy history. Serial is passive (38400 baud default, 8N1, no flow control, DTR/RTS untouched). Ports must be character devices under `/dev` or Home Assistant-managed `esphome-hass://` proxy URLs; a port already used by another PyTap entry is refused; titles, logs and diagnostics never show proxy URLs. Config entries move to version 7 (existing ones marked as TCP). Uses `serialx` through Home Assistant's `usb` integration. (#36)
- **Stale reading behaviour** — **Configure → Change stale reading behaviour** sets what an optimizer's own live sensors show when it stops reporting: at night *Zero* (default) or *Hold last value*; when silent in daylight *Unavailable* (default), *Hold last value* or *Zero*; and after how many minutes a daylight dropout counts as silent (5–240, default 30). Energy sensors and the String and Total devices' rules don't change. Fitted to the fork's sun-aware rules rather than replacing them. (#34)

### Changed

- **Optimizer entity IDs are the barcode** — New optimizer entities are named `sensor.tigo_<barcode>_<measurement>` (`sensor.tigo_4_5db467z_power`), so moving a module to another string or inverter, or renaming it, never changes them. Device names (and so entity names) still read `Tigo [inverter] [string] [module]` and follow every change. Existing entity IDs are not changed.
- **Inverter per string** — For one CCA serving several inverters, **Configure → Set a string's inverter** names the inverter each string is wired to. Strings left unset feed the entry's inverter. Device names follow; once strings feed more than one inverter, each inverter gets its own Total device (power, energy, performance; entity IDs `sensor.tigo_inverter_<name>_…`) and the entry's Total becomes the CCA total (`Tigo [inverter] CCA Total`).
- **Config entry migration from any source** — Entries from upstream (v5), Tim Laing's PR stack (v6 serial, v7 discovery) or earlier fork versions all migrate to v8 by adding only missing settings, so a serial entry stays serial and a discovery choice is kept.
- **Health checks re-render only what changed** — When an optimizer goes quiet or comes back, only its entities and the strings and totals containing it are refreshed, instead of every entity. (#34)
- **Home Assistant only sees published copies of the data** — The listener thread wrote readings straight into the data entities read, so an entity could see a half-updated panel and, in averaged mode, raw readings could show between publishes. The listener now keeps its own state; Home Assistant gets numbered copies, so an older copy queued from the listener can't undo a newer update such as the midnight reset. Silent-optimizer estimates use the latest readings rather than the last published average. (#33)
- **Only the panels that changed are refreshed** — Each publish names the panels it changed; other panels' sensors, and strings and totals that don't include them, skip re-rendering. Health checks still refresh everything. (#33)
- **Save requests are posted once per save cycle** instead of on every reading. (#33)
- **Connection settings are validated** — Setup and *Change connection settings* check the host and port (1–65535). An unreachable converter is reported once; submitting the same settings again continues, for converters that are offline at the time. Invalid saved settings stop setup with a clear message instead of retrying forever, and correcting them reloads the entry. Finishing setup without any modules explains why instead of just showing the menu again. (#33)
- The coordinator is kept in `entry.runtime_data` (Home Assistant's current pattern) instead of `hass.data`. CI lints the tests too. (#33)

- **Invalid slot counters** — An invalid slot counter in a receive-response header no longer crashes the parser (an `IndexError` escaped `Parser.feed` and forced a reconnect). Such a frame never updates the slot clock and keeps its reports only when the packet walk consumes the payload exactly and every packet is well-formed; otherwise the whole frame is dropped. Like any power report, kept reports are still dropped while the gateway has no slot clock yet. Power reports with an invalid slot counter are dropped. New parser counters `invalid_slots`, `invalid_slot_frames_kept`, `invalid_slot_frames_dropped` and `no_slot_clock_drops` record these cases. The "no slot clock" warning is logged once per gateway (per connection), then at debug level, instead of for every discarded report.

## [1.4.11] - 2026-11-18

### Added

- **Brand icon** — PyTap now ships its own icon and logo (`brand/`), so Home Assistant 2026.3+ shows them on the integration and device pages instead of "logo not available". The artwork is original: a solar panel with a radio-signal badge. It isn't Tigo's logo.
- HACS validation now runs the brands check too (it was skipped while there were no brand images).

### Dashboards

- Colouring by power compares each panel with the best panel's live power or 20 W, whichever is higher, so a panel making 1 W at dusk no longer shows full brightness when the best panel also makes 1 W (PV Panels dashboard and the classic Panel Power view).

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
