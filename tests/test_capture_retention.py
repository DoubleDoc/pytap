"""Automatic deletion of old packet captures (review of azebro/pytap#38).

Forgotten captures end up in every Home Assistant backup, so captures that
ended more than ``capture_keep_days`` days ago are deleted at startup and
every night. 0 keeps them.
"""

import json
import os
import time
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pytap.const import (
    CONF_CAPTURE_KEEP_DAYS,
    CONF_MODULES,
    DEFAULT_CAPTURE_KEEP_DAYS,
    DOMAIN,
)
from custom_components.pytap.pytap.core.capture import CaptureWriter, prune_captures
from tests.test_capture_integrity import _stop
from tests.test_reading_health import MODULES, _coordinator

DAY = 86400


def _writer(tmp_path):
    writer = CaptureWriter(
        tmp_path / "pytap_captures" / "entry", {"source": {"connection_type": "tcp"}}
    )
    writer.start()
    return writer


def _capture(root, capture_id, *, state="stopped", ended_days_ago=None, mtime_days_ago=0):
    directory = root / capture_id
    directory.mkdir(parents=True)
    manifest = {"capture_id": capture_id, "state": state, "complete": state == "stopped"}
    if ended_days_ago is not None:
        manifest["ended_utc_ns"] = int((time.time() - ended_days_ago * DAY) * 1e9)
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest))
    stamp = time.time() - mtime_days_ago * DAY
    os.utime(path, (stamp, stamp))
    return directory


def test_a_real_capture_is_deleted_once_old(tmp_path) -> None:
    root = tmp_path / "pytap_captures" / "entry"
    writer = _writer(tmp_path)
    _stop(writer)
    assert writer.root == root
    assert prune_captures(root, 14, now=time.time() + 13 * DAY) == []
    assert prune_captures(root, 14, now=time.time() + 15 * DAY) == [writer.capture_id]
    assert not writer.directory.exists()


def test_cut_off_capture_ages_from_its_last_write(tmp_path) -> None:
    root = tmp_path / "pytap_captures" / "entry"
    recent = _capture(root, "20260102T000000Z_bbbbbbbbbbbb", state="capturing", mtime_days_ago=3)
    # Cut off by a restart: no end time, manifest last written 30 days ago.
    cut_off = _capture(root, "20260103T000000Z_cccccccccccc", state="capturing", mtime_days_ago=30)
    assert prune_captures(root, 14) == [cut_off.name]
    assert not cut_off.exists() and recent.exists()


def test_zero_keeps_everything_and_active_is_never_deleted(tmp_path) -> None:
    root = tmp_path / "pytap_captures" / "entry"
    old = _capture(root, "20260101T000000Z_aaaaaaaaaaaa", ended_days_ago=400, mtime_days_ago=400)
    assert prune_captures(root, 0) == []
    assert prune_captures(root, 14, active=old.name) == []
    assert old.exists()


def test_running_capture_is_kept(tmp_path) -> None:
    root = tmp_path / "pytap_captures" / "entry"
    running = _capture(root, "20260101T000000Z_aaaaaaaaaaaa", state="capturing")
    assert prune_captures(root, 14) == []
    assert running.exists()


def test_missing_root_is_fine(tmp_path) -> None:
    assert prune_captures(tmp_path / "pytap_captures" / "entry", 14) == []


async def test_coordinator_uses_setting(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    assert coordinator._capture_keep_days == DEFAULT_CAPTURE_KEEP_DAYS
    coordinator._capture_root = hass.config.path("pytap_captures", "x")
    coordinator._capture_keep_days = 0
    coordinator.hass.async_add_executor_job = MagicMock()
    assert await coordinator.async_prune_captures() == []
    coordinator.hass.async_add_executor_job.assert_not_called()


async def test_options_set_retention(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=8,
        data={"host": "192.168.1.100", "port": 502, CONF_MODULES: MODULES},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "change_captures"}
    )
    assert result["step_id"] == "change_captures"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CAPTURE_KEEP_DAYS: 30}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert entry.data[CONF_CAPTURE_KEEP_DAYS] == 30
