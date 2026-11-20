# How it works

[← PyTap README](../README.md)

```
Tigo CCA ⇄ TAP (RS-485 bus)
    │
    │  RS-485 to TCP converter, serial adapter or ESPHome serial proxy
    ▼
PyTap listener thread (one per CCA)
    │
    ├── Optional packet capture (raw bytes, before parsing)
    │
    ├── Embedded protocol parser (pytap library)
    │   └── Frames → power report, infrastructure and topology events
    │
    ├── Barcode filter
    │   ├── Tracked optimizer → plausibility check → listener state
    │   └── Unknown optimizer → added if discovery is on, else logged
    │
    └── Published copies → Home Assistant event loop
        └── Sensor and binary sensor entities
```

PyTap runs a background listener thread per CCA that reads the bus, parses protocol frames and keeps its own state; Home Assistant only ever sees published copies of it. Only tracked optimizers (configured, or added by discovery) create or update entities. In the default **averaged interval** mode, readings are buffered and published as a time-weighted average once per interval (60 s by default); in **live reporting** mode each reading is published as it arrives (see [Reporting modes](configuration.md#reporting-modes)).

On startup, PyTap restores persisted state (barcode mappings, discovered optimizers, energy accumulators, the last readings and flags of each optimizer). Sensor entities also use Home Assistant restore fallback if needed, so a restart during low/no production does not force entities into unavailable when prior data exists.

## Future work

PyTap's two biggest limitations come from where it runs: inside Home Assistant.

- **Database load.** Each optimizer has 13 measurement sensors (plus flag entities that write only on change), and every update is a state row in Home Assistant's recorder. The averaged interval, rounding and the removal of per-reading timestamp attributes have cut PyTap's writes by well over 90% compared with live reporting (see [Reporting modes](configuration.md#reporting-modes)), but a large installation still writes many rows a day for values that change slowly, and Home Assistant has to receive and process the full stream of readings first.
- **Losses while Home Assistant is offline.** PyTap can only hear the bus while Home Assistant is running. Restarts of up to 10 minutes are bridged, but anything longer, such as a host reboot, an OS update or a crash, loses that time's energy for good (see [Known limitations](troubleshooting.md#known-limitations)).

Both point the same way: **much of PyTap would work better on a small device at the RS-485 bus itself**, such as an ESP32 next to the CCA, or the RS-485 converter if it can run its own code. Such a device would:

- listen around the clock, independently of Home Assistant, so restarts and updates lose nothing;
- integrate energy where the readings arrive, keeping per-panel daily and lifetime counters that Home Assistant reads, rather than reconstructs;
- send Home Assistant only what it needs (averaged values, energy counters, data validity and bus status) at a rate it chooses, instead of every raw reading;
- keep the node table and barcode mapping on the device, so they never have to be relearned.

Home Assistant would keep what it's good at: history, dashboards, the Energy dashboard, automations, and string-level views and estimates across many devices.

[Bobsilvio/tigo_server](https://github.com/Bobsilvio/tigo_server) shows this approach working. An ESP32 with a receive-only RS-485 module listens to the same TAP–CCA bus and decodes each panel's power, voltages, current and temperature. It runs as an [ESPHome device](https://github.com/Bobsilvio/tigo_server/blob/main/esphome/README.md) with native Home Assistant entities. An optional add-on puts its dashboard in the Home Assistant sidebar. Prepackaged hardware is its main holdup at present.
