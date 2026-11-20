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

**Size:** measured over one morning, about **10–12 MB an hour per gateway** (6 and 12 optimizers), so plan on up to about 300 MB a day per gateway; the default 1 GiB budget lasts about 3 days. Night-time traffic hasn't been measured yet.

**Privacy:** captures contain your optimizers' barcodes and everything on the bus. Files are readable only by Home Assistant, and contain no host names, IP addresses or credentials, but share a capture only deliberately.

## When you're done: delete the files

Captures stay until you delete them (except when the PyTap entry itself is deleted). They live in your configuration folder, under `/config/pytap_captures/<config_entry_id>/` (one folder per capture, with any ZIP exports next to it), so **they are also included in Home Assistant backups**.

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

- **`replay_capture.py`** checks a capture and exports its index, every frame (including ones the live parser would drop, and why) and every decoded event as JSON lines, with a summary.

`capture_reports.py` also reads simple recordings made by other tools: a file of records, each an 8-byte big-endian Unix time, a 4-byte big-endian length and that many raw bytes. The output CSV contains barcodes too: keep it private and delete it when done.
