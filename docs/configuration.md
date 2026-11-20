# Configuration

[← PyTap README](../README.md)

PyTap is configured entirely through the Home Assistant UI — no YAML needed.

## Step 1: Add the Integration

1. Go to **Settings → Devices & Services → Add Integration** and search for **PyTap**.
2. Choose how PyTap reaches the bus (see [Hardware](hardware.md#ways-to-connect)):
   - **RS-485 to TCP converter**: enter its **host** (IP address or hostname) and **port** (default 502).
   - **Serial port or ESPHome serial proxy**: pick the port or proxy from the list; the **baud rate** is 38400 unless you know otherwise.
3. Optionally enter an **Inverter name**, such as `Master`. Leave it blank if you have one inverter; see [Device naming](entities.md#device-naming).

> **Note:** The connection test only warns. If the converter can't be reached
> yet, setup continues and PyTap connects when it becomes available.

## Step 2: Add Modules

After entering connection details, you'll see a modules menu. **Module discovery** is on by default for new setups, so you can select **Finish setup** without adding anything:

- **Discovery on (Automatic):** every optimizer PyTap hears on the bus is added by itself, usually the first morning after setup (see [First start](#first-start-what-to-expect)). It starts with no name (its device is `Tigo [inverter] Discovered <barcode>`; its entity IDs are `sensor.tigo_<barcode>_…`, as for every optimizer), in string **Discovered**, with an unknown rating (0 Wp). Until it has a rating, its performance entities and those of the totals it belongs to are disabled. Name it, set its string and rating under **Configure → Edit a module**. Its entity IDs don't change. Discovery only listens; it never sends anything to the gateway.
- **Discovery off (Manual):** only the modules you add are tracked. Turn it off under **Module discovery**.

You can also add modules yourself, with or without discovery:

1. Click **"Add a module"**.
2. Fill in the fields:
   - **String group** — Required label to group optimizers by string (e.g., `1`, `2`, `East`).
   - **Name** — The optimizer's position within the string (e.g., `P01`). The device name adds the inverter and string for you, so don't repeat them here.
   - **Barcode** — The Tigo optimizer barcode printed on the module (e.g., `A-1234567B`).
   - **Peak power (Wp)** — The panel's peak power rating in watts (1–1000, default 455 Wp). Used to calculate performance percentage.
3. Repeat for each optimizer you want to monitor.
4. Click **"Finish setup"** when done (with discovery off, add at least one module first).

Installations set up before discovery existed keep manual mode; turn discovery on under **Configure → Change module discovery**. A diagnostic **Module discovery** sensor on the Total device shows *Waiting for modules* until the first optimizer is tracked.

## First start: what to expect

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

## Finding Barcodes

Barcodes are printed on each Tigo TS4 optimizer module. If you can't physically access them, PyTap will log any **unconfigured** barcodes it discovers on the bus:

```
INFO: Discovered unconfigured Tigo optimizer barcode: A-9999999Z
      (gateway=1, node=55). Add it to your PyTap module list to start tracking.
```

With [module discovery](#step-2-add-modules) on, they're added automatically instead. Barcodes are discovered from the node table, so they appear in the log after the first node table is seen, normally the first morning after setup (see [First start: what to expect](#first-start-what-to-expect)).

## Managing Modules After Setup

Go to **Settings → Devices & Services → PyTap → Configure** to:

- **Change connection settings** — Switch between an RS-485 to TCP converter and a serial port or ESPHome serial proxy, or change the address, port or baud rate. Entities and energy history are kept.
- **Change reporting settings** — Toggle live reporting, set the averaged reporting interval (see [Reporting modes](#reporting-modes)), and turn estimating silent optimizers on or off.
- **Change stale reading behaviour** — What an optimizer's live sensors show at night and when silent by day (see [When readings stop](readings.md#when-readings-go-to-0-or-unavailable)).
- **Change inverter name** — Set or clear the inverter name used in device names.
- **Add** new optimizer modules (if the barcode was previously seen on the bus, it resolves instantly).
- **Change module discovery** — Turn automatic discovery on or off (see [Step 2](#step-2-add-modules)).
- **Edit** a module's string, name or peak power (0 = unknown), including discovered optimizers. The barcode can't be edited; remove and re-add the module to replace an optimizer.
  Entity IDs never change (see [Device naming](entities.md#device-naming)).
- **Set a string's inverter** — For one CCA serving several inverters: name the inverter a string is wired to (see [Device naming](entities.md#device-naming)).
- **Rename a string** for all of its modules. The String device's sensors keep their history.
- **Remove** modules you no longer want to track. A removed optimizer is excluded, so discovery won't add it back; add it again to track it. You can also delete an optimizer's device from its device page (same effect), or an empty string device. The Total device and strings with modules can't be deleted.
- **Save and close** to apply changes.

Connection and reporting changes are saved as soon as you submit them. Inverter name, discovery and module changes (add, edit, rename string, remove) are saved only when you select **Save and close**; closing the dialog without it discards them.

## Reporting modes

PyTap can deliver readings to Home Assistant in two ways, selectable under **Configure → Change reporting settings**:

- **Live reporting** — Each reading is pushed to Home Assistant as soon as it arrives from the gateway, giving sub-second latency. Live reporting will likely swell your HA database and load your system if run constantly.
- **Averaged interval (default for new entries)** — Readings are cached and a **time-weighted average** is pushed once per *reporting interval* (5–300 seconds, default 60). Weighting each reading by how long it remained the latest value yields a representative average even when readings arrive at uneven intervals. This reduces event-loop, CPU, and recorder/database load on installations with many panels.

> **Setting the interval:** The reporting interval only applies in averaged mode, so it is hidden while **Live reporting** is enabled. To change it, open **Change reporting settings**, untick **Live reporting**, and click **Submit** — a follow-up **Reporting Interval** screen then lets you enter the value. Leaving **Live reporting** ticked skips that screen entirely.

> **Database size:** in live mode every reading is written to Home Assistant's recorder. On an 18-panel installation this was about 4 million state rows a day, enough to grow the database by several GB within a week and make backups too large to upload. Unless you need sub-second updates, use the averaged interval (on that installation, 30 seconds cut PyTap's rows by about 90%, and 60 seconds halves that again), or exclude the per-optimizer sensors from the [recorder](https://www.home-assistant.io/integrations/recorder/).

States are rounded to a sensible precision (power 1 W, voltage 0.1 V, current 0.01 A, temperature 1 °C, performance 0.1 %, duty cycle 1 %), and no attribute changes with every reading. A value that hasn't changed then doesn't add a database row. Both ideas, and the measurements that showed they matter, come from [seaosair](https://github.com/seaosair) in upstream [PR #25](https://github.com/azebro/pytap/pull/25).

Energy totals (daily and lifetime) are always integrated from the raw readings, so the reporting mode only affects the instantaneous values (power, voltage, current, temperature) shown on dashboards — never the accumulated energy figures.

The same screen has **Estimate silent optimizers** (on by default); see [Estimating silent optimizers](readings.md#estimating-silent-optimizers).
