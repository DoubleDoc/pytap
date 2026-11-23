# Packet capture and replay

[← PyTap README](../README.md)

PyTap can record the raw bytes it reads from the bus, so problems can be studied later, offline, and so protocol details such as the [flag bytes](protocol-status-bits.md) can be researched from real data. It's useful for reporting a problem too: a capture shows exactly what your gateway sent. Adapted from [Tim Laing](https://github.com/timlaing)'s upstream [PR #38](https://github.com/azebro/pytap/pull/38).

**It's off unless you start it, and only listens.** A capture copies what the existing connection reads, before decoding: no second connection to the converter (so it works with serial ports too), nothing sent on the bus. It never resumes after a restart or reload, and is stopped and finalised when Home Assistant shuts down.

## Recording

All actions are admin-only. Run them from the **Actions** tab of Home Assistant's developer tools; each shows its result there.

1. **`pytap.start_capture`**: pick the PyTap instance (one per gateway), a maximum duration (`duration_minutes`, default 1440 = 24 h, up to 7 days) and a storage budget (`max_storage_mib`, default 1024 MiB). For example:

   ```yaml
   action: pytap.start_capture
   data:
     config_entry_id: <your PyTap entry>   # picked from a list in the UI
     duration_minutes: 240
   ```

2. **`pytap.capture_status`**: progress, bytes recorded, and why a capture stopped. Diagnostics show the same counters (never the captured data).
3. **`pytap.stop_capture`** when you're done, or let the duration or budget stop it. A capture that reaches its budget stops; it never deletes older data to make room.
4. **`pytap.list_captures`**: the latest 20 captures of an instance, with their `capture_id`.
5. **`pytap.export_capture`** (optional): checks a stopped capture and writes a private ZIP next to it, to copy off with Samba or a file manager add-on. It isn't offered for download over the web on purpose.

**Size:** measured on two gateways (6 and 12 optimizers), the raw bus runs about **10–12 MB an hour**, and the timing index adds 17–76% more depending on how the converter delivers bytes (many small reads make a bigger index): about **14–17 MB an hour** in all, so 330–420 MB a day per gateway. It doesn't drop at night: the CCA keeps polling the bus after the optimizers go to sleep. A 24-hour capture fits the default 1 GiB budget. Check `pytap.capture_status` rather than assuming a long capture fits: a capture that reaches its budget stops.

**Privacy:** captures contain your optimizers' barcodes and everything on the bus. Files are readable only by Home Assistant, and contain no host names, IP addresses or credentials, but share a capture only deliberately. Never move captures into `/config/www` (anything there is served over the web) or have them uploaded automatically.

**Tips:**

- **Include a sunrise.** The node table that links optimizers to their barcodes is broadcast when the optimizers power up in the morning. A capture that includes one can be fully decoded on its own.
- **One capture per gateway.** Each PyTap entry (CCA) records its own bus; start one on each if you have several.
- **Stopping isn't losing.** A restart, reload or shutdown stops and finalises a capture cleanly; start a new one afterwards if you need more.

## What's in a capture

Each capture is a folder named by its `capture_id` (UTC start time plus a random suffix):

| File | Contents |
| --- | --- |
| `bus-000000.bin`, … | The bytes exactly as read, including noise, bad checksums and unknown frames; a new file every 16 MiB |
| `chunks-000000.bin`, … | A timing index: when each read returned (UTC and monotonic), its offset, length and connection (32 bytes per read) |
| `events.jsonl` | Connection opened, closed or failed (error type only, no addresses) |
| `manifest.json` | Software versions, the starting node table, SHA-256 checksums and sizes of every file, and whether the capture finished cleanly |

Timestamps are when PyTap's read returned, not the exact time each byte crossed the wire. A capture that stopped unexpectedly (for example a power cut) can still be exported and replayed up to where it ends; its ZIP includes a `recovery.json` describing what couldn't be verified.

## When you're done: delete the files

Captures live in your configuration folder, under `/config/pytap_captures/<config_entry_id>/` (one folder per capture, with any ZIP exports next to it), so **they are also included in Home Assistant backups**. Since 1.6.3, captures that ended more than **14 days** ago are deleted automatically, at startup and every night (the running capture never is). Change the number of days, or set 0 to keep captures until you delete them, under **Configure → Change packet capture retention**. To remove them sooner:

1. Copy off anything you want to keep (export it first if you want a ZIP).
2. Run **`pytap.list_captures`** for the instance to see the `capture_id`s.
3. Run **`pytap.delete_capture`** with each `capture_id`. This deletes the capture and its ZIP exports. A running capture must be stopped first.

   ```yaml
   action: pytap.delete_capture
   data:
     config_entry_id: <your PyTap entry>
     capture_id: <capture_id from list_captures>
   ```

4. Run `pytap.list_captures` again: it should list nothing you meant to delete.
5. **Backups:** backups made while captures existed still contain them. Delete those backups too if you don't want the data kept (Settings → System → Backups).

Deleting the PyTap entry deletes all of its captures. Files can also be removed by hand from `/config/pytap_captures/` with Samba, SSH or a file manager add-on, while no capture is running.

## Reading captures offline

Two scripts in [`scripts/`](../scripts/) read captures without Home Assistant (run with Python 3 from a copy of this repository). They never change the capture.

- **`capture_reports.py`** replays a capture through PyTap's own parser, at the recorded times, and writes every power report to a CSV: measurements plus the flag bytes decoded bit by bit (low input voltage, the state field, idle pass-through, producing, jstatus level; see [protocol-status-bits.md](protocol-status-bits.md)). `--summary` prints how often each status and jstatus value occurred per optimizer, `--start`/`--end` select a time window, and `--state` starts from PyTap's saved state (`.storage/pytap_<config_entry_id>_coordinator`) so barcodes are known from the first report.

  ```sh
  python scripts/capture_reports.py /path/to/capture --output reports.csv --summary
  ```

- **`replay_capture.py`** checks a capture and writes to a new folder: `chunks.jsonl` (the timing index, readable), `frames.jsonl` (every frame, including noise and ones the live parser would drop, with the reason, such as `crc_mismatch`), `events.jsonl` (every decoded event at its recorded time) and `summary.json` (completeness, losses and counts). Add `--allow-incomplete` for a capture that didn't finish cleanly.

  ```sh
  python scripts/replay_capture.py /path/to/capture --output /path/to/new-folder
  ```

`capture_reports.py` also reads simple recordings made by other tools: a file of records, each an 8-byte big-endian Unix time, a 4-byte big-endian length and that many raw bytes. The output CSV contains barcodes too: keep it private and delete it when done.
