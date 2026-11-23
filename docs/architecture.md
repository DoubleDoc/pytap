# PyTap architecture

[← PyTap README](../README.md) · [Development](development.md)

How the integration is built, for people changing it. For using it, see the [README](../README.md) and the topic pages in this folder.

## Overview

PyTap passively monitors Tigo TS4 optimizers by listening to the RS-485 bus between a Tigo CCA and its TAP(s). One PyTap config entry handles one CCA (one bus). For each entry, a listener thread reads the bus through an RS-485 to TCP converter, a serial adapter or an ESPHome serial proxy, parses the proprietary protocol with the embedded `pytap` library, and keeps its own state. Home Assistant entities only ever read published copies of that state.

```
                 Tigo CCA ◄──── RS-485 (GATEWAY A/B) ────► TAP(s) ◄── 2.4 GHz ──► TS4 optimizers
                    │
                    │ the same two wires, listen-only
                    ▼
   RS-485 to TCP converter │ serial adapter │ ESPHome serial proxy
                    │
┌───────────────────┼─────────────────────────────────────────────────────────┐
│ Home Assistant    ▼                                                         │
│   connection.py ── open_source() ──► ByteSource.read()                      │
│                                          │                                  │
│   coordinator.py  listener thread ◄──────┘                                  │
│     ├─ packet capture (optional, raw bytes before parsing)                  │
│     ├─ pytap.Parser.feed(bytes) → events                                    │
│     ├─ barcode filter, discovery, plausibility check                        │
│     ├─ listener state (_state), energy integration, Store saves             │
│     └─ _publish() ──► numbered copy ──► event loop: coordinator.data        │
│                                                │                            │
│   sensor.py / binary_sensor.py entities ◄──────┘  (refresh only if affected)│
└─────────────────────────────────────────────────────────────────────────────┘
```

The parser library (`custom_components/pytap/pytap/`) has no Home Assistant imports; it can be used on its own, and the offline tools in `scripts/` use it that way.

## Modules

| Module | Responsibility |
| --- | --- |
| `__init__.py` | Setup, unload, removal, config entry migration, device removal from the UI, orphan device cleanup, registering the capture actions |
| `config_flow.py` | Config flow (connection, discovery, modules) and options flow (connection, reporting, stale readings, inverter, discovery, modules, strings) |
| `connection.py` | Validating TCP and serial settings, opening a byte source, titles and log labels that never show proxy URLs |
| `coordinator.py` | The listener thread, listener-owned state, publishing, persistence, energy, reading health, estimates, discovery, barcode ownership and energy hand-off, duplicate-bus detection, capture control |
| `energy.py` | Pure energy accumulation (trapezoidal Wh integration, midnight reset) |
| `sensor.py` | Optimizer sensors, String / inverter / Total aggregates, Node mapping, Bus status, Module discovery, flag sensors |
| `binary_sensor.py` | Flag binary sensors and Reading fault |
| `entity.py` | Shared optimizer device info and the flag entity base class |
| `naming.py` | Device names, barcode entity IDs, string inverters |
| `diagnostics.py` | Diagnostics download (redacted) |
| `capture_services.py` | The `pytap.*_capture` actions |
| `pytap/core/` | Parser, events, protocol types, state (slot clock, node tables), TCP source, capture writer, replay, flag decoding |

## Lifecycle (`__init__.py`)

**`async_setup`** registers the capture actions once for the domain.

**`async_setup_entry`**:

1. If a previous coordinator for this entry is still *parked* (its listener didn't stop in time), wait for it to stop; if it still won't, raise `ConfigEntryNotReady` so a new listener never shares the connection.
2. Clean up legacy entity unique IDs from before 0.2.0.
3. Create the coordinator. Invalid saved connection settings raise `ConfigEntryError` (the user is asked to fix them rather than retried forever).
4. Load the Store, start the listener (`async_start_listener`).
5. If the Store was read without error, remove devices of excluded optimizers and empty strings (nothing is removed if saved state couldn't be read, since discovered optimizers would then be unknown).
6. Set `entry.runtime_data`, stop the listener on `EVENT_HOMEASSISTANT_STOP` (entries aren't unloaded at shutdown), forward the `binary_sensor` and `sensor` platforms, and reload on options changes.

A setup that fails part-way stops (or parks) its listener again.

**`async_unload_entry`** stops the listener within `LISTENER_STOP_TIMEOUT` (15 s), interrupting a blocked read. If it doesn't stop in time, the coordinator is *parked* (`parked_coordinators(hass)`) and finishes its own cleanup when the thread exits. Barcode claims are released once it has stopped.

**`async_remove_entry`** stops any parked listener (discarding state, so a late final save can't recreate the file), deletes the entry's packet captures and its Store.

**`async_remove_config_entry_device`** lets users delete an optimizer device (its barcode is removed from the module list and excluded from discovery) or an empty string or inverter device; the Total device and strings with modules can't be deleted.

### Config entry versions

| Version | Change |
| --- | --- |
| 1 → 2 | Voltage and current sensors split into In and Out (legacy entities cleaned up at setup) |
| 2 → 3 | String labels mandatory |
| 3 → 4 | Peak power added (default 455 Wp) |
| 4 → 5 | Live reporting setting added (existing entries keep live mode) |
| 5–7 → 8 | Missing settings added, existing ones never overwritten: inverter name (blank), connection type (TCP), discovery off, no exclusions. Versions 6 and 7 meant different things in this fork and in Tim Laing's upstream PR stack (his v6 = serial, v7 = discovery), so they all migrate the same way and a serial entry stays serial. |

A version newer than the code knows is refused. Entity IDs are never changed by a migration.

### Config entry data

| Key | Meaning |
| --- | --- |
| `connection_type` | `tcp` or `serial` |
| `host`, `port` | TCP converter (port default 502) |
| `serial_port`, `baud_rate` | Serial device path or `esphome-hass://` proxy URL; baud rate default 38400 |
| `inverter` | Optional inverter name of the entry, used in device names |
| `modules` | `[{"string", "name", "barcode", "peak_power"}]`; `peak_power` 0 means unknown |
| `auto_discovery`, `excluded_barcodes` | Passive discovery on/off; barcodes the user removed |
| `string_inverters` | `{string: inverter}` for one CCA serving several inverters |
| `live_reporting`, `write_interval` | Reporting mode; entries without the key keep live mode, new entries start averaged at 60 s |
| `estimate_silent` | Estimate silent optimizers in aggregates (default on) |
| `night_action`, `silent_action`, `silent_minutes` | What an optimizer's live sensors show when asleep (zero/hold) or silent by day (unavailable/hold/zero), and after how long |

## Connections (`connection.py`)

`normalize_connection()` validates saved or entered settings and raises `InvalidConnectionSettings` with the offending field. Serial ports must be character devices under `/dev` (not `/dev/fd`, `/dev/std*` and similar) or Home Assistant-managed `esphome-hass://` URLs without credentials and with a port filter. `open_source(loop, data)` returns a `ByteSource`:

- **TCP:** `pytap.core.source.TcpSource`, a blocking socket with a read timeout.
- **Serial / ESPHome proxy:** `SerialProxySource`, which opens `serialx`'s async serial transport on Home Assistant's event loop and bridges it to the listener thread with bounded, timed reads. It never writes and never changes DTR/RTS.

A connection is exclusive to one entry (`endpoint_in_use()`: serial ports by resolved path, TCP by port and resolved addresses). Titles and log labels (`connection_title()`, `endpoint_label`) never contain proxy URLs.

The config and options flows test a connection when submitted (`connection_errors()`): an unreachable converter shows *Could not connect*, and submitting the same settings again saves them anyway, since a converter may simply be off.

## The coordinator (`coordinator.py`)

`PyTapDataUpdateCoordinator` is push-based: it never polls. Its pieces:

### Listener thread and listener-owned state

`async_start_listener()` runs `_listen()` in the executor. The thread loops: open the source, create a parser with the saved `PersistentState`, read up to 4096 bytes at a time, record them to an active capture, feed the parser, process the events, and publish. On errors it records the outage cause, backs off (`RECONNECT_DELAY` 5 s up to `RECONNECT_MAX_DELAY` 60 s) and reconnects; no bytes for `RECONNECT_TIMEOUT` (60 s) also forces a reconnect. Outages are logged once, then at debug level until data has flowed for `STABLE_CONNECTION_SECONDS`.

The thread owns `self._state` (nodes, gateways, counters, discovered barcodes) and the energy accumulators, guarded by `_state_lock` (an `RLock`). Home Assistant never reads them directly.

### Publishing

`_publish()` builds a copy of the state, numbers it (`_publish_seq`) and hands it to the event loop with `call_soon_threadsafe`; `_async_apply_snapshot()` drops a copy older than one already applied, so a queued snapshot can't undo a newer one (for example the midnight reset). `_publish_patch()` changes fields of the published data directly (used by the midnight reset, so averaged values aren't replaced by raw ones).

Each publish carries the set of changed barcodes (`_mark_changed`; `None` means everything). Entities call `coordinator.affects(barcodes)` and skip updates for other panels, so a report from one panel re-renders only its entities and the String and Total devices containing it.

### Reporting modes

| Mode | Behaviour |
| --- | --- |
| Averaged interval (new entries) | Readings are buffered per barcode with monotonic timestamps; once per `write_interval` (5–300 s, default 60) a **time-weighted average** of `_AVERAGED_FIELDS` is published (each reading weighted by how long it remained the latest). Performance is recomputed from the averaged power. Home Assistant never sees a raw reading between flushes. |
| Live | Every change is published as it arrives. |

Energy is always integrated from the raw readings, and persisted snapshots hold raw values, whatever the mode.

### Processing a power report

1. **Barcode.** From the parser, or from the coordinator's node ID → barcode map learned from infrastructure events (node tables).
2. **Tracked?** A configured or discovered optimizer continues. An unknown barcode is logged once (`Discovered unconfigured Tigo optimizer barcode …`) and, with discovery on, admitted (below); otherwise dropped.
3. **Plausibility** (`reading_faults()`): output more than 2 V above input, a voltage outside 0–100 V, or a valid temperature outside −40…125 °C. An impossible report sets `reading_fault`, `fault_reasons` and `last_fault_at` on the node and is otherwise discarded: no values replaced, no energy, no freshness.
4. **Freshness:** `_last_report[barcode]` (monotonic) for reading health and estimates.
5. **Energy:** `accumulate_energy()` integrates power into daily and lifetime Wh (trapezoids, gaps over `ENERGY_GAP_THRESHOLD_SECONDS` (120 s) not integrated, but bridged as estimated energy in some cases; see Estimates).
6. **Node update:** measurements, performance (only with a known rating), temperature and RSSI only when valid, the raw `status` and `jstatus` flag bytes, `reading_fault: False`.

### Discovery and barcode ownership

With `auto_discovery` on, `_add_discovered_module()` admits a barcode seen in a node table or power report, unless it's excluded or already tracked: name empty, string `Discovered`, `peak_power` 0. Discovered modules live in the coordinator (and the Store, as `auto_modules`), not in the config entry, so admitting one doesn't reload the entry; the platforms add entities from a coordinator listener. Saving the options flow moves them into `modules`.

`BarcodeOwners` (one per Home Assistant instance, `async_barcode_owners()`) records which entry owns each barcode. It's filled once from every PyTap entry's saved modules, configured (`modules`) and discovered (the Store's `auto_modules`), whether or not the entry is loaded, in entry order: the first entry wins. Ownership lasts until the barcode is removed from that entry or the entry is deleted, not just until it unloads (review of azebro/pytap#37). A configured barcode owned elsewhere isn't tracked (no entities, no energy); discovery takes a barcode atomically (`try_take()`). A barcode owned elsewhere that's heard here goes in `_foreign_barcodes` and the `owned_elsewhere_<entry>` repair issue. If at least `DUPLICATE_BUS_MIN_OPTIMIZERS` (3) of them also report to their owning entry within `DUPLICATE_BUS_WINDOW_SECONDS` (10 min), while none of this entry's own optimizers report, the connection hears the same bus as that entry: its listener stops and raises `duplicate_bus_<entry>`. Setup and the connection options refuse an endpoint another entry uses (`endpoint_in_use()`).

**Lifetime energy hand-off.** Energy counters are kept per entry, by barcode, in that entry's Store. When an entry loads and has energy for a barcode it no longer tracks (removed), it moves the lifetime figures into `BarcodeOwners.released` (its own Store, `pytap_released_energy`); deleting an entry releases all of its optimizers. An entry starting a counter for a barcode (`_accumulator()`) first takes a released record, so the count continues on the new bus.

### Reading health

A health check runs every `HEALTH_CHECK_INTERVAL_SECONDS` (60 s): it refreshes the sun position, updates estimates, logs state changes and publishes only the panels whose state changed. `reading_state(barcode)`:

| State | Condition | Optimizer's live readings |
| --- | --- | --- |
| `ok` | Reporting, or quiet for less than the limits below | Last value |
| `asleep` | Sun below `LOW_SUN_ELEVATION` (10°) and quiet for `NIGHT_QUIET_SECONDS` (10 min); or daylight but no report since before daylight began | `night_action`: 0 (default) or last value; temperature and RSSI keep their last value |
| `silent` | Daylight and quiet for `silent_minutes` (default 30), counted from the later of the last report and the start of daylight | `silent_action`: unavailable (default), last value or 0 |
| `offline` | No bytes from the converter for `LINK_DOWN_SECONDS` (5 min) | Unavailable |

**Restarts.** If the sun is low when PyTap starts, each optimizer's last report time is carried over from the Store (`_saved_report_times`, from the snapshots' `last_update`; `_resume_report_times()`), so an optimizer quiet for `NIGHT_QUIET_SECONDS` is asleep at once instead of holding its values for another 10 minutes. By day the saved times aren't used: the restart counts as when daylight began, so a saved time would make optimizers read as asleep until their next report. Estimates never start counting before PyTap started (downtime is only bridged from an optimizer's own readings).

The daytime limit is deliberately long: optimizers can drop off the TAP's radio link for minutes and recover by themselves. Without a Home Assistant location, PyTap never assumes it's night.

`bus_status` is `normal` while bytes arrive within `LINK_DOWN_SECONDS`, otherwise the cause the listener saw last: `converter_unreachable` (connect failed or the socket dropped) or `bus_quiet` (connected, no bytes). The CCA polls the bus day and night, so a quiet bus is never normal.

### Estimates

The String and Total devices can estimate an optimizer that has gone quiet (`estimate_silent`, default on); `_update_estimates()` runs in the health check:

- **Share.** While an optimizer and at least `ESTIMATE_MIN_PEER_FRACTION` (half) of the rest of its string have reported within `ESTIMATE_AFTER_SECONDS` (120 s), and their average is at least `SHARE_MIN_PEER_POWER_W`, its power ÷ their average is folded into an exponential moving average (`SHARE_SMOOTHING` 0.2 per minute, capped at `SHARE_MAX`).
- **Estimate.** Quiet for `ESTIMATE_AFTER_SECONDS` with enough reporting neighbours: share (default 1.0) × their average power. Optimizers in a series string carry the same current, so the estimate falls if the string's output collapses.
- **Energy.** The estimate is integrated into `estimated_daily_wh` / `estimated_total_wh` from the last report to the first new one. A gap that ends before the health check starts estimating would otherwise be lost: `accumulate_energy` returns the discarded trapezoid and the listener adds it as estimated energy. The same applies to a **restart gap** of up to `RESTART_BRIDGE_MAX_SECONDS` (600 s): each save records `saved_at`, loaded as `_stopped_at`, and an optimizer's first reading since start whose previous reading was at most `ESTIMATE_AFTER_SECONDS` before the stop has its gap bridged.
- **Use.** Aggregates use `estimated_power()` for power and performance and add `estimated_energy()` to energy. Optimizer sensors never include estimates.

Aggregate power per member: the estimate if there is one; otherwise 0 when asleep; when silent, left out (estimating on) or its last value (estimating off); nothing when offline.

`data_validity(barcode)` (the Data validity sensor): `reporting`, `holding`, `estimated`, `silent_estimated`, `asleep` or `silent`; unavailable while the bus status isn't normal. At low sun, `asleep` takes precedence over `estimated`.

### Midnight reset

`async_track_time_change` at local midnight (DST-safe) resets daily accumulators and the published daily values with `_publish_patch()`.

### Persistence

One Home Assistant Store per entry, `.storage/pytap_<entry_id>_coordinator` (version 2, migrating older data as-is):

| Key | Contents |
| --- | --- |
| `saved_at` | When it was saved (roughly when PyTap stopped hearing the bus, for restart gaps) |
| `barcode_to_node` | Node ID mappings learned from node tables |
| `discovered_barcodes` | Barcodes seen but not tracked |
| `auto_modules` | Discovered modules not yet saved into the entry |
| `parser_state` | `PersistentState`: gateway identities and versions, node tables, optimizer hardware IDs |
| `energy_data` | Per barcode: daily and lifetime Wh, reset date, readings today, last power and time, estimated Wh |
| `node_snapshots` | The last readings, flag bytes and reading fault of each tracked optimizer |

Saves are throttled: written within `SAVE_DELAY_SECONDS` (10 s) of the first change, posted to the event loop once per cycle, and a final save runs when the listener stops. If the Store can't be read, the coordinator starts without it and sets `_save_blocked`, so it never replaces lifetime energy with empty state; the next reload tries again. Removing the entry blocks all saves before deleting the Store.

String and Total energy are not in the Store: each aggregate energy sensor keeps its own running total of its members' increases (`_AggregateEnergyData`, restored through Home Assistant's extra restore data), so membership changes never add a member's lifetime energy or look like a meter reset.

### Packet capture

`async_start_capture()` creates a `CaptureWriter` (`pytap/core/capture.py`) under `/config/pytap_captures/<entry_id>/`. The listener hands it every block of bytes it reads, before parsing, plus connection open/close/error markers; the writer queues them, writes 16 MiB raw segments, a 32-byte-per-read timing index and a checksummed manifest, and stops (never deletes) at its duration or storage budget. Captures are finalised before the listener's thread references are dropped on stop, so a timed-out stop stays recoverable. `capture_services.py` exposes the admin-only actions; exports and deletions are tracked across reloads. `pytap/core/replay.py` scans captures frame by frame (flagging frames the live parser would drop) and replays them through the parser with a recorded clock (`Parser(clock=…)`); `iter_simple_recording()` reads simple timestamped recordings made by other tools.

## Entities (`sensor.py`, `binary_sensor.py`, `entity.py`, `naming.py`)

Platforms create entities from `coordinator.modules` and register a coordinator listener that adds entities for newly discovered modules (cheaply skipped while `module_count` is unchanged).

### Per optimizer

| Entities | Class | Notes |
| --- | --- | --- |
| Performance, Power, Voltage In/Out, Current In/Out, Temperature, DC-DC duty cycle, RSSI, Daily energy, Total energy, Readings today | `PyTapSensor` (`RestoreSensor`) | Live readings follow `reading_state()` and the stale-reading settings; energy never does. States are rounded, and no attribute changes per reading, so unchanged values add no recorder rows. |
| Data validity | `PyTapDataValiditySensor` | Enum, diagnostic |
| Status, State field, jstatus level | `PyTapFlagSensor` | From the raw flag bytes, decoded by `pytap/core/flags.py`; the last two disabled by default |
| Low input voltage, jstatus bit 7, Idle pass-through, Reading fault | `PyTapBinarySensor` | jstatus bit 7 and Idle pass-through disabled by default; Reading fault is a problem sensor that ignores reading health |

Flag entities derive from `PyTapFlagEntity`: they write state only when it changes, stay visible while reporting or asleep, and follow `silent_action` when silent. Performance entities are disabled by the integration while a covered panel has no rating, and re-enabled when it gets one (a user's own disabling is respected).

### Devices and names

| Device | Identifier | Name |
| --- | --- | --- |
| Optimizer | barcode | `Tigo [inverter] [string] <position or barcode>` |
| String | `<entry_id>_string_<string>` | `Tigo [inverter] String <string>` |
| Inverter total (one CCA, several inverters) | `<entry_id>_inverter_<inverter>` | `Tigo <inverter> Total` |
| Total (the CCA) | `<entry_id>_installation` | `Tigo [inverter] Total`, or `… CCA Total` once strings feed several inverters |

Optimizer devices link to their string's device by `via_device_id`: the sensor platform registers each string device first to get its registry ID (Home Assistant 2026.11 deprecated linking by identifier). Names are display-only and follow every edit.

**Entity IDs.** Unique IDs are `pytap_<barcode>_<key>` for optimizer entities and `pytap_<entry_id>_<string|inverter|installation>_…` for aggregates. Optimizer entity IDs are set explicitly from the barcode (`sensor.tigo_4_5db467z_power`, `naming.optimizer_object_id()`), so moving, renaming or re-stringing a panel never changes them; inverter totals use `sensor.tigo_inverter_<name>_…` so they can't collide with the entry's Total. Existing entity IDs are never renamed.

## Key design decisions

1. **Embedded parser, stdlib only.** The `pytap` library is bundled and has no Home Assistant imports; the only external requirement is `serialx` (for serial connections). The parser takes a `PersistentState` object and an optional clock; the caller owns persistence and time.
2. **Push, not poll.** The bus streams power reports continuously (each optimizer about every few seconds), so a listener thread feeding a push coordinator is the natural fit; `iot_class` is `local_push`.
3. **Listen-only.** PyTap never transmits, so it can't disturb the CCA; the cost is learning node tables only when the CCA reads them (at sunrise) and losing whatever happens while Home Assistant is down.
4. **Listener-owned state, published copies.** The thread never shares mutable state with the event loop; numbered snapshots make ordering explicit.
5. **Barcodes are identity.** Node IDs can change; barcodes don't. State, unique IDs and entity IDs are keyed by barcode.
6. **Sun-aware staleness.** Silence means different things at night and by day; the defaults zero sleeping panels and make long daytime silence visible, both configurable.
7. **Database restraint.** Averaged reporting by default, rounded states, no per-reading attributes, flags written on change, and targeted entity refreshes.

## Error handling

| Scenario | Behaviour |
| --- | --- |
| Converter unreachable when configuring | *Could not connect*; submitting again saves anyway |
| Invalid saved settings | `ConfigEntryError`, user asked to fix them |
| Connection lost, or no bytes for 60 s | Reconnect with back-off; logged once per outage; Bus status shows the cause after 5 min |
| CRC error, runt or giant frame | Counted, frame skipped; over 2% failing logs a warning (at most hourly) |
| Invalid slot counter | Reports kept only when the frame parses exactly; counted (`invalid_slots`, …) |
| Impossible reading | Discarded; Reading fault on; warning logged |
| Node table not seen yet | Reports dropped; Node mapping sensor shows *Waiting for node table* |
| Saved state unreadable | Start without it; never overwritten until the next reload |
| Listener won't stop | Parked; setup waits (`ConfigEntryNotReady`) until it has released the connection |
| Bug in processing | Logged once with a traceback; reconnects |

## Testing

| Where | What |
| --- | --- |
| `tests/` (pytest, `pytest-homeassistant-custom-component`, HA 2026.10.4) | Config and options flows, migration, lifecycle and shutdown, publishing, persistence, energy and aggregate energy, reading health, stale actions, estimates, discovery, naming and string inverters, serial, flags and faults, capture (integrity, scanner, services), diagnostics |
| `tests/core_smoke.py` (unittest, real HA 2026.11.0, CI job *HA 2026.11 smoke tests*) | Flows, migration, serial transport and proxy stub, discovery, capture, and device linking on the minimum supported Home Assistant, without the harness |
| `custom_components/pytap/pytap/tests/` | Parser: frames, slot clock, node tables, power report decoding, invalid slots, API, replay tools |

Real captures aren't committed (they contain barcodes); tests use synthetic data. Changes that affect live behaviour are also replayed against local captures in a throwaway Home Assistant before release.
