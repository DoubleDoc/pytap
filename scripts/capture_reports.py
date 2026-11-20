#!/usr/bin/env python3
"""Replay a capture through PyTap's parser and export every power report.

python scripts/capture_reports.py CAPTURE --output reports.csv [options]

CAPTURE is either a PyTap capture folder (from the pytap.start_capture action)
or a simple recording file (records of: 8-byte big-endian Unix time, 4-byte
big-endian length, raw bytes). Each power report becomes one CSV row with its
measurements and its flag bytes decoded bit by bit (see
docs/protocol-status-bits.md). Runs without Home Assistant; the capture is
never changed.

Options:
  --state FILE        Parser state to start from, so barcodes are known from
                      the first report: PyTap's saved state
                      (.storage/pytap_<entry_id>_coordinator) or a JSON file
                      holding a parser_state. A PyTap capture's own starting
                      state is used by default.
  --start / --end     Only reports in this window, as local
                      "YYYY-MM-DD HH:MM[:SS]".
  --summary           Also print, per optimizer, how often each status and
                      jstatus value was seen.
  --allow-incomplete  Read a PyTap capture that wasn't stopped cleanly.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "pytap"))

from pytap.core.capture import iter_capture, read_manifest  # noqa: E402
from pytap.core.flags import describe_flags  # noqa: E402
from pytap.core.replay import iter_simple_recording, replay_events  # noqa: E402
from pytap.core.state import PersistentState  # noqa: E402

COLUMNS = [
    "time", "gateway_id", "node_id", "barcode",
    "voltage_in", "voltage_out", "current_in", "current_out", "power",
    "dc_dc_duty_cycle", "temperature", "rssi",
    "status", "low_input_voltage", "state_field", "idle_pass_through", "temperature_valid",
    "jstatus", "producing", "jstatus_level",
]


def _load_state(path: Path | None, manifest: dict | None) -> PersistentState:
    if path is not None:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data.get("data", data)  # A Home Assistant Store file wraps it.
        return PersistentState.from_dict(data.get("parser_state", data))
    if manifest is not None:
        return PersistentState.from_dict(manifest.get("metadata", {}).get("parser_state", {}))
    return PersistentState()


def _local(value: str | None) -> float | None:
    return datetime.fromisoformat(value).timestamp() if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="CSV file (must not exist)")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()

    if args.capture.is_dir():
        manifest = read_manifest(args.capture, allow_incomplete=args.allow_incomplete)
        chunks = iter_capture(args.capture, allow_incomplete=args.allow_incomplete)
    else:
        manifest = None
        chunks = iter_simple_recording(args.capture)
    state = _load_state(args.state, manifest)
    start, end = _local(args.start), _local(args.end)

    rows = 0
    codes: dict[str, Counter] = defaultdict(Counter)
    # The output holds optimizer barcodes: keep it private, like the capture.
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, COLUMNS)
        writer.writeheader()
        for item in replay_events(chunks, state):
            event = item["event"]
            if event.get("event_type") != "power_report":
                continue
            seconds = item["observed_utc_ns"] / 1_000_000_000
            if (start is not None and seconds < start) or (end is not None and seconds > end):
                continue
            flags = describe_flags(event.get("status"), event.get("jstatus")) or {}
            row = {key: event.get(key) for key in COLUMNS if key in event}
            row.update({key: flags[key] for key in COLUMNS if key in flags})
            row["time"] = datetime.fromtimestamp(seconds).isoformat(timespec="milliseconds")
            writer.writerow(row)
            rows += 1
            if args.summary:
                who = event.get("barcode") or f"node {event.get('gateway_id')}/{event.get('node_id')}"
                codes[who][f"status {flags.get('status')} jstatus {flags.get('jstatus')}"] += 1

    print(f"{rows} power reports written to {args.output}")
    for who in sorted(codes):
        total = sum(codes[who].values())
        print(f"{who} ({total} reports)")
        for code, count in codes[who].most_common():
            print(f"  {code}: {count} ({count / total:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
