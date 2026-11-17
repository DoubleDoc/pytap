# PyTap

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io/)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Validate](https://github.com/azebro/pytap/actions/workflows/validate.yml/badge.svg)](https://github.com/azebro/pytap/actions/workflows/validate.yml)

A Home Assistant custom integration for monitoring **Tigo TAP solar energy systems**. PyTap connects to your Tigo gateway over TCP, passively listens to the RS-485 bus protocol, and exposes real-time per-optimizer sensor entities — power, voltage, current, temperature, and more.

---

## Hardware requirements
Please note that this integration requires RS485 to TCP converter that needs to be connected to Tigo CCA to read the data from TAPs. It should be connected exactly the same way the TAP is connected to CCA. For detailed hardware requirements and connection details review this repo: [taptap](https://github.com/willglynn/taptap).

## Features

- **Real-time streaming** — Push-based data delivery with sub-second latency (no polling).
- **Configurable reporting** — Choose **live reporting** (default; every reading delivered as it arrives) or a **time-weighted averaged interval** that caches readings and pushes a single representative value every few seconds to reduce CPU and recorder load on large installations.
- **Per-optimizer sensors** — 12 sensor entities per Tigo TS4 module: performance,
  power, voltage in/out, current in/out, temperature, DC-DC duty cycle, RSSI,
  daily energy, total energy, and readings today.
- **Aggregate sensors** — 4 sensors per string and 4 for the full installation: performance, power, daily energy, total energy.
- **Performance tracking** — Per-optimizer, per-string, and installation-wide performance percentage based on configurable peak panel power (Wp).
- **Diagnostics download** — Integration diagnostics export includes parser counters, infrastructure/mapping state, and per-node summaries.
- **Menu-driven setup** — Add optimizer modules one at a time with guided form fields.
- **Barcode-based identification** — Stable hardware barcodes as entity identifiers (survives gateway restarts).
- **Discovery logging** — Unconfigured barcodes seen on the bus are logged for easy identification.
- **Persistent barcode mapping** — Discovered barcodes and node mappings are
    saved across restarts. When you add a previously-discovered barcode,
    it resolves instantly without waiting for the next gateway enumeration.
- **Restart-safe availability** — Last known node readings are persisted and
    restored on startup, so sensors stay available with the most recent value
    even before the first live frame arrives (for example after a night restart).
- **Energy accumulation** — Trapezoidal Wh integration with proactive midnight reset and monotonic lifetime totals. Daily sensors zero at exactly midnight local time.
- **No external dependencies** — The protocol parser library is fully embedded; nothing to install from PyPI.
- **Options flow** — Add, edit or remove optimizer modules and rename strings at any time without reconfiguring.
- **Structured device names** — `Tigo [inverter] [string] [module]`, with an optional inverter name for installations with more than one gateway (see [Device naming](#device-naming)).
- **Node mapping sensor** — Shows whether readings can be matched to your modules yet, so a new installation that is still waiting for its first node table is visibly working (see [First start: what to expect](#first-start-what-to-expect)).

## Sensors

Each configured Tigo TS4 optimizer exposes the following sensors:

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
| Readings Today | — | — |

PyTap also creates aggregate virtual devices:

- **Tigo [inverter] String <name>** — `performance`, `power`, `daily_energy`, `total_energy`. Created for every string, even when there is only one, so a string keeps its history when more strings are added later.
- **Tigo [inverter] Total** — `performance`, `power`, `daily_energy`, `total_energy`, plus the diagnostic **Node mapping** sensor.

### Device naming

Device names are built from up to three levels, leaving out any that aren't set:

| Level | Set in | Example |
| --- | --- | --- |
| Inverter | Optional per gateway: **Inverter name** | `Master` |
| String | Each module's **String group** | `2` (numbers are shown as `S2`) or `East` |
| Module | Each module's **Name**, usually its position | `P01` |

| Device | One gateway (no inverter name) | Two gateways (`Master`, `Slave`) |
| --- | --- | --- |
| Optimizer | `Tigo S2 P01` | `Tigo Master S2 P01` |
| String total | `Tigo String 2` | `Tigo Master String 2` |
| Gateway total | `Tigo Total` | `Tigo Master Total` |

Most installations have one CCA and one PyTap entry, and can leave the inverter name blank. With more than one CCA (for example one per inverter), add one PyTap entry per gateway and give each an inverter name so their devices don't share names.

Entity IDs are generated from the device names when entities are first created, for example `sensor.tigo_master_s2_p01_power`. Home Assistant doesn't change existing entity IDs when names change later; rename them in Home Assistant if you want them to match. History is kept when you do.

### Dual-module optimizers

- **TS4-A-O-DUO / TS4-R-O-DUO** connect two panels in series to one optimizer. It has one barcode and sends one set of readings, so it appears as one device whose voltage, power and energy cover both panels. Name it for both positions (for example `P01+P02`) and set its peak power to the two panels combined.
- **TS4-A-F / TS4-A-2F** are rapid-shutdown-only devices that don't report to the TAP, so PyTap can't see them.

---

## Installation

### Manual

1. Copy the `custom_components/pytap` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

### HACS (Recommended)

1. Open **HACS** in your Home Assistant instance.
2. Click the **three dots** menu in the top right and select **Custom repositories**.
3. Add `https://github.com/azebro/pytap` with category **Integration**.
4. Search for **PyTap** and click **Download**.
5. Restart Home Assistant.

---

## Configuration

PyTap is configured entirely through the Home Assistant UI — no YAML needed.

### Step 1: Add the Integration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **PyTap**.
3. Enter your Tigo gateway's **host** (IP address or hostname) and **port** (default: 502).
4. Optionally enter an **Inverter name**. Leave it blank if you have one gateway; see [Device naming](#device-naming).

> **Note:** The connection test is non-blocking. If the gateway is temporarily
> unreachable (e.g., powered off at night), setup will proceed and connect
> when the gateway becomes available.

### Step 2: Add Modules

After entering connection details, you'll see a modules menu:

1. Click **"Add a module"**.
2. Fill in the fields:
   - **String group** — Required label to group optimizers by string (e.g., `1`, `2`, `East`).
   - **Name** — The optimizer's position within the string (e.g., `P01`). The device name adds the inverter and string for you, so don't repeat them here.
   - **Barcode** — The Tigo optimizer barcode printed on the module (e.g., `A-1234567B`).
   - **Peak power (Wp)** — The panel's peak power rating in watts (default: 455 Wp). Used to calculate performance percentage.
3. Repeat for each optimizer you want to monitor.
4. Click **"Finish setup"** when done.

### First start: what to expect

**On a new installation, expect no readings until the next morning.** That's normal; here's why.

1. Optimizers identify themselves in every reading by a short **node ID** (for example `node 12`), not by the barcode you entered.
2. The link between node IDs and barcodes is the gateway's **node table**. PyTap only listens on the RS-485 bus, so it learns the table only when it overhears the CCA reading it from the gateway.
3. That happens when the optimizer network forms, which in practice means **when the optimizers power up at sunrise** after being dark overnight.
4. Until then PyTap can't tell which panel sent each reading, so it drops them, and your sensors stay *unknown*.
5. Once a node table has been seen, the mapping is saved. Later Home Assistant restarts resolve readings straight away, without waiting for another morning.

The **Node mapping** sensor on the **Tigo Total** device shows where things stand:

| State | Meaning |
| --- | --- |
| Waiting for node table | No configured module can be matched yet. If **Unidentified nodes reporting** is above 0, readings are arriving and the wiring is fine: wait for the next morning. |
| Partly mapped | A node table arrived, but some configured barcodes weren't in it. Check **Unmapped barcodes** for typos. |
| Complete | Every configured module is matched. |

If it still says *Waiting for node table* after a full morning and **Unidentified nodes reporting** is 0, PyTap isn't receiving data: check the RS-485 converter's wiring and the host and port.

The log also explains what's happening: `Power report for node … waiting for node table` when readings first arrive, and `First node table this session … configured barcodes matched` when the table is seen.

### Finding Barcodes

Barcodes are printed on each Tigo TS4 optimizer module. If you can't physically access them, PyTap will log any **unconfigured** barcodes it discovers on the bus:

```
INFO: Discovered unconfigured Tigo optimizer barcode: A-9999999Z
      (gateway=1, node=55). Add it to your PyTap module list to start tracking.
```

Barcodes are discovered from the node table, so they appear in the log after the first node table is seen, normally the first morning after setup (see [First start: what to expect](#first-start-what-to-expect)).

### Managing Modules After Setup

Go to **Settings → Devices & Services → PyTap → Configure** to:

- **Change connection** — Update the gateway IP address and port.
- **Change reporting settings** — Toggle live reporting or set the averaged reporting interval (see [Reporting modes](#reporting-modes)).
- **Change inverter name** — Set or clear the inverter name used in device names.
- **Add** new optimizer modules (if the barcode was previously seen on the bus, it resolves instantly).
- **Edit** a module's string, name or peak power. The barcode can't be edited; remove and re-add the module to replace an optimizer.
- **Rename a string** for all of its modules. The string's total sensors keep their history.
- **Remove** modules you no longer want to track.
- **Save and close** to apply changes.

### Reporting modes

PyTap can deliver readings to Home Assistant in two ways, selectable under **Configure → Change reporting settings**:

- **Live reporting (default)** — Each reading is pushed to Home Assistant as soon as it arrives from the gateway, giving sub-second latency. Best for most installations.
- **Averaged interval** — When live reporting is turned off, readings are cached and a **time-weighted average** is pushed once per *reporting interval* (5–300 seconds, default 30). Weighting each reading by how long it remained the latest value yields a representative average even when readings arrive at uneven intervals. This reduces event-loop, CPU, and recorder/database load on installations with many panels.

> **Setting the interval:** The reporting interval only applies in averaged mode, so it is hidden while **Live reporting** is enabled. To change it, open **Change reporting settings**, untick **Live reporting**, and click **Submit** — a follow-up **Reporting Interval** screen then lets you enter the value. Leaving **Live reporting** ticked skips that screen entirely.

Energy totals (daily and lifetime) are always integrated from the raw readings, so the reporting mode only affects the instantaneous values (power, voltage, current, temperature) shown on dashboards — never the accumulated energy figures.

### Diagnostics Download

PyTap diagnostics are available from the integration page:

1. Go to **Settings → Devices & Services → Integrations**.
2. Open **PyTap**.
3. Use the menu (⋮) and select **Download diagnostics**.

The diagnostics JSON includes parser counters, infrastructure and node mappings,
discovered barcodes, connection state, and per-node summaries. Host/IP is redacted.

---

## How It Works

```
Tigo Gateway (RS-485 bus)
    │
    │  TCP stream (port 502)
    ▼
PyTap Coordinator
    │
    ├── Embedded protocol parser (pytap library)
    │   └── Parses frames → PowerReport, Infrastructure, Topology events
    │
    ├── Barcode allowlist filter
    │   ├── Configured → update sensor entities
    │   └── Unconfigured → log for discovery
    │
    └── Push to Home Assistant event loop
        └── Sensor entities update in real time
```

PyTap uses a background listener thread that streams data from the gateway, parses protocol frames, and dispatches events to the Home Assistant event loop. Only events matching your configured barcodes create or update sensor entities. In the default **live reporting** mode each update is forwarded immediately; in **averaged interval** mode updates are buffered and flushed as a time-weighted average every few seconds (see [Reporting modes](#reporting-modes)).

On startup, PyTap restores persisted coordinator state (barcode mappings, energy accumulators, and last node snapshots). Sensor entities also use Home Assistant restore fallback if needed, so a restart during low/no production does not force entities into unavailable when prior data exists.

---

## Development

This repository includes a dev container with Home Assistant pre-installed for local development and testing.

### Project Structure

```
pytap/
├── custom_components/pytap/     # The HA custom component
│   ├── __init__.py              # Integration lifecycle
│   ├── config_flow.py           # Config & options flows
│   ├── const.py                 # Constants
│   ├── coordinator.py           # Push-based data coordinator
│   ├── sensor.py                # Sensor platform (12 entity types)
│   ├── manifest.json            # Integration metadata
│   ├── strings.json             # UI strings
│   ├── translations/en.json     # English translations
│   └── pytap/                   # Embedded protocol parser library
├── deploy/                      # Deployment tooling
│   ├── deploy.sh                # Deploy script (SSH-based)
│   └── .deploy.env.example      # Example credentials config
├── tests/                       # Integration tests
├── docs/
│   ├── architecture.md          # Architecture & design document
│   └── implementation.md        # Implementation details & history
├── config/                      # Dev HA config directory
├── requirements.txt             # HA + dev dependencies
└── pytest.ini                   # Test configuration
```

### Running Home Assistant (Dev)

```bash
python3 -m homeassistant --config config/ --debug
```

### Running Tests

```bash
# Integration tests (config flow + sensor platform)
python3 -m pytest tests/ -vv --tb=short

# Parser library tests
python3 -m pytest custom_components/pytap/pytap/tests/ -vv
```

### Linting

```bash
python3 -m ruff check custom_components/pytap/
```

### Deploying to a Home Assistant Instance

A deployment script is provided in `deploy/` to push the component to a remote HA OS installation via SSH.

**Setup:**

```bash
# Copy the example config and fill in your credentials
cp deploy/.deploy.env.example deploy/.deploy.env
# Edit deploy/.deploy.env with your host, username, and password
```

> **Note:** `deploy/.deploy.env` is gitignored — credentials are never committed.

**Deploy:**

```bash
# Using the config file
./deploy/deploy.sh

# Or pass credentials directly
./deploy/deploy.sh --host 192.168.1.184 --user 'user' --password 'yourpass'

# Deploy without restarting HA
./deploy/deploy.sh --no-restart
```

The script uploads the component via tar-over-SSH (compatible with HA OS SSH add-on), verifies the deployment, and optionally restarts Home Assistant Core.

Run `./deploy/deploy.sh --help` for all options.

### Documentation

- **[Architecture](docs/architecture.md)** — System design, module responsibilities, data flow, and design decisions.
- **[Implementation](docs/implementation.md)** — Current implementation state, module details, test coverage, and development history.

---

## Acknowledgements

Inspired by the [taptap](https://github.com/litinoveweedle/taptap) and [taptap Home Assistant add-on](https://github.com/litinoveweedle/hassio-addons).

---

## License

[MIT](LICENSE) © Adam Zebrowski
