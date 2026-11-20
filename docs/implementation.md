# PyTap implementation history

[← PyTap README](../README.md) · [Architecture](architecture.md) · [Changelog](../CHANGELOG.md)

How PyTap was built, phase by phase. For how the code works **now**, see [architecture.md](architecture.md); for every release's changes, see the [changelog](../CHANGELOG.md).

Phases 1–18 are Adam Zebrowski's original development, up to upstream version 1.2.0 (March 2026); they describe the design as it was then. The fork's work follows.

## Upstream development (to 1.2.0)

### Phase 1 — Architecture

Created `docs/architecture.md` capturing the full design: system context, module responsibilities, data flow, threading model, entity model, and configuration schema.

### Phase 2 — Barcode-Driven Design

Updated the architecture to remove auto-discovery in favor of user-configured barcodes, inspired by the [taptap HA add-on](https://github.com/litinoveweedle/hassio-addons).

### Phase 3 — Initial Implementation

Implemented all core files:
- `const.py`, `manifest.json` — Constants and metadata.
- `config_flow.py` — Menu-driven flow (host/port → modules menu → add/remove modules).
- `coordinator.py` — Push-based streaming with barcode filtering.
- `sensor.py` — 12 per-optimizer sensor types plus aggregate sensor platform entities.
- `__init__.py` — Lifecycle management.
- `strings.json`, `translations/en.json` — UI strings.
- Test suite — 14 tests passing.

### Phase 4 — Connection Test Fix

Discovered that the TCP connection test in step 1 blocked the flow when no gateway was available (the common case during development). Changed the connection test to non-blocking: it warns but always proceeds to the modules step.

### Phase 5 — Menu-Driven Config Flow

Refined the config flow to the current menu-driven approach:
- Individual `add_module` form with string/name/barcode fields.
- Menu loop for adding multiple modules.
- Options flow with add/remove/done menu.
- Per-field error reporting (errors shown on the specific field).
- Dropdown-based module removal in options flow.
- All tests rewritten for the new flow pattern (16 tests passing).

### Phase 6 — Documentation

Created this implementation document capturing all development work to date.

### Phase 7 — Micro-Batching Fix & Timeout Removal

- Fixed micro-batching: `_listen()` was calling `async_set_updated_data` once per TCP read chunk. Changed to push per-event — each event that changes data triggers its own `async_set_updated_data` call.
- `_process_event()` and all handler methods now return `bool` indicating whether data changed.
- Removed `UNAVAILABLE_TIMEOUT` constant and all related logic. Sensors now hold their last received value indefinitely (no forced `None` after timeout).
- Updated `source.py`: `TcpSource.read()` now raises `OSError("Socket is closed")` when the socket is `None` and `ConnectionResetError` on peer close, instead of silently returning `b''`.

### Phase 8 — Entity Migration (v0.2.0)

- Split `voltage` → `voltage_in`/`voltage_out` and `current` → `current_in`/`current_out`, then expanded per-optimizer sensor count to 10 with daily/total energy.
- Added entity registry cleanup in `_async_cleanup_legacy_entities()` to remove orphaned `voltage`/`current` entities from pre-v0.2.0 installs.
- Bumped config entry version to 2 and added `async_migrate_entry()` for v1→v2 migration.
- Bumped manifest version to 0.2.0.
- Added 5 entity migration tests.

### Phase 9 — Shutdown & Threading Fixes

- Changed `_stop_event` from `asyncio.Event` to `threading.Event` — the former is not thread-safe across loops and caused HA shutdown to hang.
- Added `_source_lock` (`threading.Lock`) to protect concurrent `_source` access between the executor thread and the main loop's stop path.
- `async_stop_listener()` now uses `asyncio.timeout(5)` to prevent indefinite blocking if the listener task doesn't exit.
- Registered `coordinator.async_stop_listener` via `entry.async_on_unload()` so the listener is stopped on HA shutdown/reload, not just explicit unload.
- Added 17 coordinator persistence and lifecycle tests.

### Phase 10 — Barcode Persistence & Node Table Fix

- **Node table sentinel tolerance** — Fixed parser `_handle_node_table_command` to tolerate trailing bytes on the end-of-table sentinel page (`entries_count=0`). The gateway commonly sends padding/CRC bytes after the zero count, which was previously rejected as "corrupt", preventing the node table from completing and barcodes from being resolved.
- **Trailing-byte tolerance on data pages** — Data pages with more bytes than expected now parse the declared entries and ignore trailing bytes (changed strict equality check to minimum-length check).
- **First infrastructure event differentiation** — `_handle_infrastructure` now distinguishes between infra events with and without barcodes. The first event often arrives from gateway identity/version discovery before the node table is received. Previously this logged a misleading "0/N matched" WARNING; now it logs an INFO explaining that resolution will activate once the node table arrives.
- **Configured barcode mismatch logging** — Infrastructure events now log which specific configured barcodes are NOT found in the node table, helping users identify typos or incorrect barcodes.
- **Discovery persistence fix** — Discovered (unconfigured) barcodes from infrastructure events now properly trigger `_schedule_save()`. Previously only mapping changes triggered saves, leaving discovered barcodes unpersisted.
- **Coordinator-saved mapping preservation** — `_init_mappings_from_parser` now preserves coordinator-saved barcode↔node mappings when the parser state is empty (merge instead of replace). This prevents previously-learned mappings from being wiped on reconnect when the parser state file has no node table.
- **Instant barcode resolution on module add** — `reload_modules` now checks if newly-added barcodes already exist in the saved barcode↔node mapping and pre-populates placeholder node data so sensor entities can bind immediately without waiting for the next power report.

### Phase 11 — Node Address Bit-15 Masking

- **Node address bit-15 flag masking** — Fixed parser `_handle_node_table_command` to mask node addresses to 15 bits (`& 0x7FFF`) when parsing `NODE_TABLE_RESPONSE` entries. Bit 15 of the `NodeAddress` in node table entries is a protocol flag (indicating router/repeater status), not part of the node ID. Two nodes with barcodes `4-D39A3ES` and `4-D39CB6R` were observed with raw addresses `0x8019` (32793) and `0x801A` (32794) instead of the expected 25 and 26. Without masking, node table keys did not match the 15-bit node IDs used in power reports, causing those nodes' power data to be unresolvable to barcodes.
- **Debug logging for flagged nodes** — When bit 15 is detected on a node address, the parser now emits a `DEBUG`-level log with the raw and masked values for protocol analysis.
- **Test added** — `test_node_table_bit15_flag_masked` in `test_parser.py` verifies that addresses `0x8019`/`0x801A` resolve to node IDs 25/26 and not 32793/32794.

### Phase 12 — Storage Consolidation & CLI Removal

- **Consolidated storage** — Merged the parser's raw JSON state file and the coordinator's HA Store into a single `homeassistant.helpers.storage.Store` (version 2). The store now holds barcode↔node mappings, discovered barcodes, and parser infrastructure state (`PersistentState.to_dict()`). This eliminates raw file I/O, ensures proper HA backup inclusion, and enables automatic cleanup on config entry removal.
- **Parser decoupled from file I/O** — `Parser.__init__` now accepts an optional `PersistentState` object instead of a `state_file` path. The parser mutates the state in memory; the coordinator owns persistence.
- **`PersistentState` serialization** — Replaced `save(path)` / `load(path)` file I/O methods with `to_dict()` / `from_dict()` for JSON-compatible serialization via the HA Store.
- **`create_parser()` API updated** — Accepts `persistent_state: Optional[PersistentState]` instead of `state_file: str | Path | None`.
- **CLI removed** — Deleted `pytap/cli/` module, `pytap/setup.py`, and `pytap.egg-info/`. The library is now embedded-only, used exclusively through the HA integration coordinator.
- **`observe()` function removed** — The blocking streaming loop with callback was only used by the CLI. Callers now use `create_parser()` + `connect()` + manual `feed()` loop.
- **Store version bumped to 2** — Added migration path from v1 (barcode mappings + discovered barcodes only) to v2 (adds `parser_state`).
- **Tests updated** — Replaced `_state_file_path` assertions with `_persistent_state` checks, added `test_load_restores_parser_state` and `test_load_handles_corrupt_parser_state`. 51 tests passing.
- **Documentation updated** — All docs (README, architecture, implementation, API reference) updated to reflect the consolidated storage model and removed CLI.

### Phase 13 — Aggregate Sensors & Mandatory String Labels

- Added aggregate sensors for each string and for the full installation (`power`, `daily_energy`, `total_energy`).
- Added `PyTapAggregateSensor` and dedicated virtual devices (`Tigo String <name>`, `Tigo Installation`).
- Made module `string` mandatory in config and options flow (`missing_string` validation).
- Bumped config entry version to 3 with `v2 → v3` migration defaulting missing strings to `"Default"`.
- Added aggregate and migration tests to validate sums, IDs, availability, metadata, and migration behavior.

### Phase 14 — Performance Sensors & Peak Power Configuration

- Added `CONF_MODULE_PEAK_POWER` and `DEFAULT_PEAK_POWER` (455 Wp) constants to `const.py`.
- Extended `ADD_MODULE_SCHEMA` in `config_flow.py` with optional `peak_power` field (`vol.Range(min=1, max=1000)`).
- Updated `_modules_description` to display peak power per module.
- Bumped `PyTapConfigFlow.VERSION` and `CONFIG_ENTRY_VERSION` to 4, aligning the previously mismatched versions.
- Added v3→v4 migration in `async_migrate_entry` to backfill `DEFAULT_PEAK_POWER` on existing modules.
- Extended `coordinator._handle_power_report` to compute `performance = (power / peak_power) × 100` with defensive parsing and fallback for invalid peak_power values.
- Added `peak_power` and `performance` to coordinator node data dict and `reload_modules` placeholder.
- Added `performance` sensor description to all three description tuples (`SENSOR_DESCRIPTIONS`, `STRING_SENSOR_DESCRIPTIONS`, `INSTALLATION_SENSOR_DESCRIPTIONS`).
- Implemented capacity-weighted aggregate performance in `PyTapAggregateSensor._handle_coordinator_update` (sensor-side).
- Updated entity count formula: `M × 11 + S × 4 + 4` (was `M × 10 + S × 3 + 3`).
- Added 3 sensor translation keys (`performance`, `string_performance`, `installation_performance`) and `peak_power` config field to `strings.json` and `translations/en.json`.
- Added tests: v3→v4 migration (3 tests), config flow peak_power (2 tests), sensor performance (6 tests), coordinator performance (3 tests).
- Bumped `manifest.json` version to 0.3.0.
- 99 tests passing, ruff clean.

### Phase 15 — Diagnostics Platform & Readings Counter

- Added `custom_components/pytap/diagnostics.py` with `async_get_config_entry_diagnostics`.
- Added coordinator `get_diagnostics_data()` snapshot including node mappings, connection state, and per-barcode accumulator summary.
- Redacted host/IP in diagnostics output while preserving barcodes for troubleshooting.
- Added per-optimizer `readings_today` as a diagnostic sensor (`SensorStateClass.TOTAL`, unitless).
- Extended `EnergyAccumulator` with `readings_today`, increment-on-report, and reset-on-new-day behavior.
- Persisted `readings_today` in coordinator store load/save, including date-rollover reset on restore.
- Added tests in `tests/test_diagnostics.py` and extended sensor/coordinator/energy tests.
- Full suite status after feature: 105 tests passing, ruff clean.

### Phase 16 — Proactive Midnight Daily Reset

- **Bug fix:** Daily sensors (`daily_energy`, `readings_today`) previously reset lazily on the first power report after midnight. Since solar panels produce no data overnight, the reset occurred at a random time in the morning when the first reading arrived.
- Added `_schedule_midnight_reset()` and `_perform_midnight_reset()` to the coordinator. A `call_later` timer on the HA event loop fires at exactly midnight local time, zeroes all daily accumulators, updates node data, pushes the update to sensors, and reschedules itself for the next midnight.
- `async_start_listener()` now schedules the midnight reset timer at startup.
- `async_stop_listener()` cancels the midnight reset timer on shutdown.
- Added 4 tests in `TestMidnightReset`: zeroing daily values, skip-if-already-reset, timer rescheduling, and cancellation on stop.

### Phase 17 — Live Reporting Toggle & Time-Weighted Averaging

- Added a `live_reporting` boolean config option (default `True`) and reworked the options flow's *Change reporting settings* step to present a live-reporting checkbox plus a reporting-interval field.
- In live mode the coordinator pushes every changed reading immediately; in interval mode it buffers per-barcode `(timestamp, fields)` readings and pushes a **time-weighted average** via `_build_averaged_snapshot(flush_ts)` once per `write_interval`.
- Replaced the previous arithmetic-mean averaging with time-weighted averaging (each reading weighted by the duration it remained the latest value), giving representative values when readings arrive at uneven intervals.
- Changed `DEFAULT_WRITE_INTERVAL` from 5 to 30 seconds and the interval range from 1–300 to 5–300.
- Bumped `CONFIG_ENTRY_VERSION` and `PyTapConfigFlow.VERSION` to 5 with a v4→v5 migration adding `live_reporting=True`.
- Added `tests/test_write_interval.py` covering reporting-mode init, throttle behaviour, and time-weighted averaging; updated migration, config-flow, and sensor tests.

### Phase 18 — Reporting Mode Hardening

- **Buffer leak fix** — `_handle_power_report` now only appends to `_reading_buffers` in interval mode (`if not self._live_reporting`). Previously the buffer was populated in live mode too but never consumed, growing without bound.
- **Thread-safe live push** — extracted `_push_live_update()`, which publishes `{**self.data, "nodes": dict(self.data["nodes"])}` (an immutable copy of `nodes`) so HA-side consumers never iterate the dict while the listener thread mutates it. The reconnect/error `finally` flush uses the same detached-copy pattern.
- **Idle interval flush** — extracted `_maybe_flush_interval()` and call it from both the data and no-data branches of `_listen`, so buffered readings are flushed on schedule during production lulls instead of waiting for the next frame or a reconnect.
- **Migration clamp** — the v4→v5 migration now clamps any stale `write_interval` into `[MIN_WRITE_INTERVAL, MAX_WRITE_INTERVAL]` (5–300) so the reporting form stays submittable after upgrade. Added `MIN_WRITE_INTERVAL`/`MAX_WRITE_INTERVAL` constants shared by the migration and the config-flow schema.
- Added regression tests (`TestLivePushAndIdleFlush`, migration clamp tests) and corrected the previously misnamed live-mode buffer test. Full suite: 162 tests passing, ruff clean.

## Fork development (1.3 onward)

### Phase 19 — Device naming and reading health (1.3–1.4)

- Structured device names (`Tigo [inverter] [string] [module]`), an optional inverter name per entry, and `Tigo Total` instead of `Tigo Installation`.
- Sun-aware reading health: optimizers read 0 when asleep, become unavailable only after a long daytime silence, and the gateway's **Bus status** sensor tells a converter outage from a quiet bus.
- Estimating silent optimizers on the String and Total devices from their share of the string, including estimated energy, and bridging restart gaps of up to 10 minutes.
- Per-optimizer **Data validity** sensor, **Node mapping** sensor, averaged reporting by default for new entries (60 s), rounded states and no per-reading attributes (from seaosair's upstream PR #25), and the PV Panels dashboard.
- Decoding fixes from Bobsilvio's protocol notes: input current scale by hardware ID, the temperature-valid bit, RSSI 0, 12-byte reports.

### Phase 20 — Tim Laing's PR series (1.5–1.6)

Tim Laing's upstream pull requests #32–#39, adopted one at a time and fitted to the fork:

- **#32** — strict walk of power reports with invalid slot counters.
- **#33** — listener-owned state with numbered published copies, targeted entity refreshes, bounded shutdown with parking, DST-safe midnight reset, never overwriting unreadable saved state, interrupting blocked reads.
- **#34** — configurable stale-reading behaviour, fitted to the sun-aware rules (night: zero/hold; silent by day: unavailable/hold/zero; silent after 5–240 min).
- **#35** — String and Total energy as their own running totals of members' increases, restored through extra restore data.
- **#36** — serial and ESPHome serial-proxy connections (`connection.py`, `serialx`), config entry v7 in the fork; minimum Home Assistant 2026.11.
- **#37** — passive module discovery, barcode claims across entries, excluded barcodes, device removal from the UI.
- **#38** — opt-in raw packet capture and offline replay; the parser takes a clock.
- **#39** — status flag entities and reading-fault detection, exposed in the fork as single bits rather than named codes.

### Phase 21 — Naming, inverters and research tools (1.5–1.6)

- **Barcode entity IDs** (`sensor.tigo_<barcode>_*`): moving or renaming a panel never changes them; device names show inverter, string and position and follow every edit. Existing entity IDs are never renamed by the integration.
- **Inverter per string** for one CCA serving several inverters, with a Total device per inverter and the entry's Total renamed to the CCA total.
- **Config entry v8**: one additive migration from upstream (v5), Tim's stack (v6 serial, v7 discovery) or earlier fork versions.
- **Flag bytes** read bit by bit from a 99,618-report dawn capture ([protocol-status-bits.md](protocol-status-bits.md)), decoded in diagnostics and entities; `scripts/capture_reports.py` exports every report with its decoded flags.
- **`via_device_id`**: optimizer devices link to their string device by registry ID (Home Assistant 2026.11 deprecated identifiers).
- **CI on HA 2026.11**: a harness-free smoke-test job on the minimum supported version, alongside the main suite on the test harness.
