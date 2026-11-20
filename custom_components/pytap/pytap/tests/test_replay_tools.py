"""Offline tools: simple recordings and the power report export.

Synthetic data only: real captures hold optimizer barcodes and stay private.
"""

import csv
from datetime import datetime
from pathlib import Path
import struct
import subprocess
import sys

from ..core.replay import iter_simple_recording, replay_events
from .test_parser import (
    _build_receive_request,
    _build_receive_response,
    _h_status,
    _power_packet_with,
)

GW = 0x1203


def _frames() -> list[bytes]:
    return [
        _build_receive_request(GW, 0x0049),
        _build_receive_response(GW, _h_status(0x4A, 0xD457), _power_packet_with(27, slot=0xD457)),
    ]


def _write_recording(path: Path, start: float = 1_795_000_000.0) -> None:
    with open(path, "wb") as file:
        for i, frame in enumerate(_frames()):
            file.write(struct.pack(">dI", start + i, len(frame)) + frame)
        file.write(b"\x00\x01")  # a recording cut off mid-record is tolerated


def test_simple_recording_reads_as_capture_chunks(tmp_path):
    path = tmp_path / "Master.bin"
    _write_recording(path)
    chunks = list(iter_simple_recording(path))
    assert [c.utc_ns for c in chunks] == [1_795_000_000_000_000_000, 1_795_000_001_000_000_000]
    assert chunks[1].stream_offset == len(chunks[0].data)
    events = [e["event"] for e in replay_events(iter(chunks))]
    reports = [e for e in events if e["event_type"] == "power_report"]
    assert len(reports) == 1
    # Event time comes from the recording (the slot clock captured with the
    # receive request at t0), not from when the replay ran.
    assert datetime.fromisoformat(reports[0]["timestamp"]).timestamp() == 1_795_000_000.0


def test_capture_reports_script_writes_flags(tmp_path):
    recording = tmp_path / "Master.bin"
    _write_recording(recording)
    output = tmp_path / "reports.csv"
    script = Path(__file__).resolve().parents[4] / "scripts" / "capture_reports.py"
    result = subprocess.run(
        [sys.executable, "-I", str(script), str(recording), "--output", str(output), "--summary"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "1 power reports" in result.stdout
    (row,) = list(csv.DictReader(output.open()))
    assert row["gateway_id"] == str(GW)
    assert row["status"].startswith("0x") and row["jstatus"].startswith("0x")
    assert row["producing"] in ("True", "False")
    assert oct(output.stat().st_mode & 0o777) == "0o600"
