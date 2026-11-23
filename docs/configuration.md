# Configuration

[← PyTap README](../README.md)

PyTap is configured entirely through the Home Assistant UI — no YAML needed.

## Step 1: Add the Integration

1. Go to **Settings → Devices & Services → Add Integration** and search for **PyTap**.
2. Choose how PyTap reaches the bus (see [Hardware](hardware.md#ways-to-connect)):
   - **RS-485 to TCP converter**: enter its **host** (IP address or hostname) and **port** (default 502).
   - **Serial port or ESPHome serial proxy**: pick the port or proxy from the list; the **baud rate** is 38400 unless you know otherwise.
3. Optionally enter an **Inverter name**, such as `Master`. Leave it blank if you have one inverter; see [Device naming](entities.md#device-naming).

> **Note:** PyTap tests the connection when you submit. If the converter
> can't be reached (for example it's powered off right now), the form shows
> **Could not connect**: check the settings, or submit the same settings again
> to save them anyway. PyTap then keeps retrying in the background and connects
> when the converter becomes available.

## Step 2: Assign your modules

PyTap needs to know each optimizer's **barcode**, and for useful names and totals also its **name** (position), **string**, **rating** and, with several inverters on one CCA, each string's **inverter**. There are three ways to give it this; most installations should use the first.

### Recommended: import a spreadsheet (CSV)

Most Tigo installations already have this data in a spreadsheet: the installer's commissioning sheet or the panel layout lists every optimizer's barcode next to its panel. Importing that sheet is usually the quickest and least error-prone way to set up, and it's the same for 6 panels or 600.

1. In Step 1's modules menu, leave **Module discovery** on and select **Finish setup**.
2. Prepare the CSV. In your spreadsheet, keep (or add) these columns, with these names in the first row, and save or export as CSV:

   | Column | Required | Example | Meaning |
   | --- | --- | --- | --- |
   | `barcode` | yes | `4-000002B` | The barcode printed on the optimizer (`X-NNNNNNNC`) |
   | `name` | | `P01` | The panel's position; device names add the inverter and string, so don't repeat them |
   | `string` | | `2` | The string label (`1`, `2`, `East` …) |
   | `peak_power` | | `455` | The panel's rating in Wp (0 = unknown), for performance % |
   | `inverter` | | `Master` | The inverter this string is wired to, for one CCA with several inverters (needs `string`) |

   ```csv
   barcode,name,string,peak_power,inverter
   4-000002B,P01,2,455,Master
   4-000009I,P02,2,455,Master
   4-000016P,P01,2,455,Slave
   ```
3. Go to **Settings → Devices & services → PyTap → Configure → Import modules from CSV**, paste the CSV text, and submit.
4. Fix anything listed: every problem is shown with its line number (a mistyped barcode, a duplicate, a rating out of range, two inverters for one string), and nothing is applied until the whole sheet is valid.
5. Check the preview (new, changed, unchanged) and submit, then select **Save and close**.

You can import before the panels have ever been heard: the barcodes are tracked from then on and resolve at the first node table, normally the next morning (see [First start](#first-start-what-to-expect)). You can also import again at any time, for example after maintenance: only the columns in the sheet are changed, optimizers not in it are left alone, and entity IDs never change. **Configure → Export modules as CSV** gives the current assignments in the same format, as a backup or a starting point.

### Automatic discovery

**Module discovery** is on by default for new setups. Every optimizer PyTap hears on the bus is added by itself, usually the first morning after setup. A discovered optimizer has no name (its device is `Tigo [inverter] Discovered <barcode>`; its entity IDs are `sensor.tigo_<barcode>_…`, as for every optimizer), is in string **Discovered** and has an unknown rating (0 Wp); until it has a rating, its performance entities and those of the totals it belongs to are disabled. Discovery only listens; it never sends anything to the gateway.

Discovery finds the optimizers but can't know where they are, so it works best together with a CSV import: discovery tracks everything on the bus, and the import names and assigns it. Without a spreadsheet, export the discovered optimizers (**Export modules as CSV**), fill in the columns, and import the result.

Turn discovery off under **Module discovery** (or later **Configure → Change module discovery**) to track only the optimizers you list. Installations set up before discovery existed keep manual mode. A diagnostic **Module discovery** sensor on the Total device shows *Waiting for modules* until the first optimizer is tracked.

### Secondary: adding or editing one module at a time

For a single change, for example a replaced optimizer, the forms are quicker than a CSV:

- **Add a module** (in the setup menu, or **Configure → Add a module**): enter the **String group**, **Name** (the position, such as `P01`), **Barcode** (such as `A-1234567B`) and **Peak power** (1–1000 Wp, default 455). With discovery off, add at least one module before **Finish setup**.
- **Configure → Edit a module**: change one optimizer's name, string or rating, including discovered ones.

For more than a few optimizers, use the CSV import instead.

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

The installer's spreadsheet or panel layout usually lists every optimizer's barcode by panel, which is what the [CSV import](#recommended-import-a-spreadsheet-csv) needs. Barcodes are also printed on each Tigo TS4 optimizer module. If you have neither, PyTap logs any **unconfigured** barcodes it discovers on the bus:

```
INFO: Discovered unconfigured Tigo optimizer barcode: A-9999999Z
      (gateway=1, node=55). Add it to your PyTap module list to start tracking.
```

With [module discovery](#automatic-discovery) on, they're added automatically instead, and the log says `Automatically tracking Tigo optimizer A-9999999Z`. Barcodes are discovered from the node table, so they appear in the log after the first node table is seen, normally the first morning after setup (see [First start: what to expect](#first-start-what-to-expect)).

## Managing Modules After Setup

Go to **Settings → Devices & Services → PyTap → Configure** to:

- **Change connection settings** — Switch between an RS-485 to TCP converter and a serial port or ESPHome serial proxy, or change the address, port or baud rate. Entities and energy history are kept.
- **Change reporting settings** — Toggle live reporting, set the averaged reporting interval (see [Reporting modes](#reporting-modes)), and turn estimating silent optimizers on or off.
- **Change stale reading behaviour** — What an optimizer's live sensors show at night and when silent by day (see [When readings stop](readings.md#when-readings-go-to-0-or-unavailable)).
- **Change inverter name** — Set or clear the inverter name used in device names.
- **Import modules from CSV** — Name, assign, rate and set inverters for many optimizers at once (see [Import a spreadsheet](#recommended-import-a-spreadsheet-csv)). **Export modules as CSV** gives the current assignments in the same format.
- **Add** a single optimizer module (if the barcode was previously seen on the bus, it resolves instantly).
- **Change module discovery** — Turn automatic discovery on or off (see [Automatic discovery](#automatic-discovery)). Turning it on also adds optimizers PyTap logged earlier in manual mode; turning it off keeps the ones already tracked.
- **Change packet capture retention** — How many days [packet captures](capture.md) are kept before they're deleted automatically (default 14; 0 keeps them).
- **Edit** a module's string, name or peak power (0 = unknown), including discovered optimizers. The barcode can't be edited; remove and re-add the module to replace an optimizer.
  Entity IDs never change (see [Device naming](entities.md#device-naming)).
- **Set a string's inverter** — For one CCA serving several inverters: name the inverter a string is wired to (see [Device naming](entities.md#device-naming)).
- **Rename a string** for all of its modules. The String device's sensors keep their history.
- **Remove** modules you no longer want to track. A removed optimizer is excluded, so discovery won't add it back; add it again to track it. You can also delete an optimizer's device from its device page (same effect), or an empty string device. The Total device and strings with modules can't be deleted.
- **Save and close** to apply changes.

## One entry per bus, one bus per optimizer

Each Tigo bus (one CCA and its TAPs) needs exactly one PyTap entry. Several inverters on one CCA don't need several entries: set each string's inverter instead.

- **The same converter twice is refused.** Setup and *Change connection* refuse an address and port, serial port or ESPHome proxy that another PyTap entry already uses (host names are compared by the addresses they resolve to, serial ports by the device they point to).
- **Two connections on one bus stop the newer one.** If a second converter or proxy is wired to a bus another entry already listens to, it hears that entry's optimizers report at the same time. After a few of them, it stops listening and a repair notice says so. Remove or rewire the extra hardware, fix that entry's connection, or delete the entry. A brand-new connection can only tell optimizers apart once it has the bus's node table, which the CCA sends mainly at night, so it may take until then to be detected; until then it can't identify any optimizer, so it doesn't count anything either.
- **An optimizer belongs to one entry.** An optimizer configured or discovered in one entry isn't tracked by another, even while the first entry is disabled. If another entry hears it, a repair notice lists it there.

### Moving optimizers or strings to another CCA

Moving panels or whole strings between CCAs needs no special steps in Home Assistant, even after a lot of changes during powered-down maintenance:

1. After power-up, the moved optimizers go quiet in their old entry, and the new entry lists them in a repair notice ("heard here, configured in …").
2. Remove them from the old entry (**Configure → Remove a module**, or delete their devices). The old entry hands their **lifetime energy** over.
3. The new entry picks them up (with discovery on; otherwise add them), keeping their entity IDs, history and lifetime energy, so an optimizer's lifetime energy stays a running total, like a generator's run hours. Until you edit them they're in the string *Discovered*. Assign strings with **Edit a module** or **Import modules from CSV**.

String totals only count what their members produce while they're members, so nothing is counted twice or lost.

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
