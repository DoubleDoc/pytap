# Troubleshooting

[← PyTap README](../README.md)

## Diagnostics download

PyTap diagnostics are available from the integration page:

1. Go to **Settings → Devices & Services → Integrations**.
2. Open **PyTap**.
3. Use the menu (⋮) and select **Download diagnostics**.

The diagnostics JSON includes parser counters, infrastructure and node mappings,
the node mapping status, discovered barcodes, connection state, module discovery, packet capture counters, and per-node summaries with their decoded flag bytes. Host/IP and serial port are redacted.
## Log messages

Every PyTap line starts with the inverter name (or the converter's address when no inverter name is set), e.g. `[Master]`, so installations with more than one gateway can tell them apart. At the default log level you only see problems:

- **Connection to the RS-485 converter failed** or **No data from the RS-485 converter**: logged once per outage; retries back off from 5 to 60 seconds and are logged at debug level. An info line follows when data has flowed again for a minute.
- **Converter unreachable: no connection to the RS-485 converter … for 5 min** or **Bus quiet: the RS-485 converter is connected but has received nothing for 5 min**: the outage has lasted long enough that live readings are unavailable; the message says what to check (see [Bus status](entities.md#bus-status)).
- **Optimizer P03 (…) has not reported for 30 min while others have**: one optimizer is silent by day. If it persists, check that optimizer and its distance from the TAP. If every optimizer is silent while the converter is still sending data, a single warning points at the CCA instead.
- **N of M RS-485 frames failed their checks**: more than 2% of frames are corrupted, so readings are being lost; check the wiring, termination and the converter's serial settings. Repeated at most hourly.
- **Configured barcodes NOT found in node table**: a configured barcode doesn't exist on the bus; check it for typos.
- **Discarded impossible reading from P03 (…)**: a report was physically impossible (for example output voltage above input) and was ignored; Reading fault turns on until the next valid report (see [Status flags and reading faults](entities.md#status-flags-and-reading-faults)). An occasional one is a corrupted frame; frequent ones point at wiring or the converter.
- **Ignored implausible temperature from P03 (… °C); its power and energy are kept**: the optimizer reported a temperature outside −40…125 °C, for example from a failed sensor. Only the temperature is ignored; Reading fault turns on until a report with a plausible temperature.
- **Optimizer … is heard here but another PyTap entry owns it**: an optimizer configured in another entry was heard on this bus; it isn't tracked here. A repair notice lists them. If they were moved to this bus, remove them from the other entry and this one picks them up, with their lifetime energy (see [Moving optimizers](configuration.md#moving-optimizers-or-strings-to-another-cca)).
- **This connection hears the same Tigo bus as PyTap entry …, so it has stopped**: two connections listen to one bus (a second converter or proxy wired to it, or an entry pointed at the wrong one). This entry stopped listening so nothing is counted twice; a repair notice says the same. Remove or rewire the extra hardware, fix the connection under **Configure → Change connection**, or delete the entry you don't need.
- **Continuing P07's lifetime energy from its previous PyTap entry**: an optimizer moved from another bus; its lifetime energy carries on.
- **Unexpected error while processing data**: a bug. It is logged once with a traceback; please report it.

## Debug logging

To see what PyTap receives, add this to `configuration.yaml` and restart, or turn on debug logging from the integration's page:

```yaml
logger:
  logs:
    custom_components.pytap: debug
```

In normal running, debug adds one line per write interval and gateway listing how many readings each module sent, e.g. `[Master] HA update: 6 node(s) — P01: 16 reading(s), …`, plus each node table page, reconnect attempt and change in a module's reading state.
## Known limitations

- **No data until the first node table** on a new installation, normally the next morning. See [First start: what to expect](configuration.md#first-start-what-to-expect).
- **Listen-only.** PyTap never transmits, so it can't request data, change settings or trigger rapid shutdown. It needs a working Tigo CCA on the bus.
- **One reading per optimizer.** A dual-module optimizer reports both panels combined. See [Dual-module optimizers](entities.md#dual-module-optimizers).
- **Per-optimizer energy** is integrated from power readings by PyTap, so it can differ slightly from Tigo's cloud figures.
- **Nothing is recorded while Home Assistant is down.** PyTap only listens, and the bus doesn't replay missed readings (the CCA still uploads to Tigo's cloud as usual). PyTap saves its state when Home Assistant stops and resumes at once on start, without waiting for a node table. For each optimizer that was reporting when Home Assistant stopped, the first reading after the restart is compared with the last one before it: a gap of up to 10 minutes is bridged with the average of the two and counted as estimated energy (when **Estimate silent optimizers** is on). Energy produced during a longer daytime outage, such as a host reboot or OS update, is lost: daily and lifetime totals carry on from where they were. At night nothing is lost. See [Future work](how-it-works.md#future-work).
- **Estimates can't see a shorted panel.** If a panel's bypass diode shorts, the string keeps running at full current but that panel contributes nothing. Its neighbours look normal, so while it is silent its estimate over-counts. Only the inverter's string voltage would reveal this.
