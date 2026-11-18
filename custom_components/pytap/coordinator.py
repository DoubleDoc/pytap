"""DataUpdateCoordinator for the PyTap integration.

Bridges the blocking pytap parser library into Home Assistant's async event loop.
Uses a background executor thread for streaming data from the Tigo gateway,
and filters events by user-configured barcodes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from datetime import time as dt_time
import logging
import math
import threading
import time
from typing import Any

from astral import Observer
from astral.sun import elevation as solar_elevation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    BUS_CONVERTER_UNREACHABLE,
    BUS_ERROR_LOG_INTERVAL_SECONDS,
    BUS_ERROR_MIN_FRAMES,
    BUS_ERROR_RATE_WARN,
    BUS_ERROR_WINDOW_SECONDS,
    BUS_NORMAL,
    BUS_QUIET,
    CONF_ESTIMATE_SILENT,
    CONF_INVERTER,
    CONF_LIVE_REPORTING,
    CONF_MODULE_BARCODE,
    CONF_MODULE_NAME,
    CONF_MODULE_PEAK_POWER,
    CONF_MODULE_STRING,
    CONF_MODULES,
    CONF_WRITE_INTERVAL,
    DEFAULT_ESTIMATE_SILENT,
    DEFAULT_LIVE_REPORTING,
    DEFAULT_PEAK_POWER,
    DEFAULT_PORT,
    DEFAULT_WRITE_INTERVAL,
    DOMAIN,
    ENERGY_GAP_THRESHOLD_SECONDS,
    ENERGY_LOW_POWER_THRESHOLD_W,
    ESTIMATE_AFTER_SECONDS,
    ESTIMATE_MIN_PEER_FRACTION,
    HEALTH_CHECK_INTERVAL_SECONDS,
    LINK_DOWN_SECONDS,
    LOW_SUN_ELEVATION,
    MAPPING_COMPLETE,
    MAPPING_PARTIAL,
    MAPPING_WAITING,
    NIGHT_QUIET_SECONDS,
    PENDING_LOG_INTERVAL_SECONDS,
    READING_ASLEEP,
    READING_OFFLINE,
    READING_OK,
    READING_SILENT,
    RECONNECT_DELAY,
    RECONNECT_MAX_DELAY,
    RECONNECT_RETRIES,
    RECONNECT_TIMEOUT,
    RESTART_BRIDGE_MAX_SECONDS,
    SHARE_MAX,
    SHARE_MIN_PEER_POWER_W,
    SHARE_SMOOTHING,
    SILENT_UNAVAILABLE_SECONDS,
    VALIDITY_ASLEEP,
    VALIDITY_ESTIMATED,
    VALIDITY_HOLDING,
    VALIDITY_REPORTING,
    VALIDITY_SILENT,
    VALIDITY_SILENT_ESTIMATED,
)
from .energy import (
    EnergyAccumulator,
    accumulate_energy,
    add_estimated_energy,
    reset_daily,
)
from .pytap.core.events import (
    Event,
    InfrastructureEvent,
    PowerReportEvent,
    StringEvent,
    TopologyEvent,
)
from .pytap.core.state import PersistentState

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 2
SAVE_DELAY_SECONDS = 10

# A reconnected stream counts as recovered once data has flowed this long,
# so a connection that drops straight after connecting doesn't log
# "connected" and "lost" every few seconds.
STABLE_CONNECTION_SECONDS = 60


class _GatewayLog(logging.LoggerAdapter):
    """Prefix log lines with the inverter name or host of the entry."""

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        return f"[{self.extra['gateway']}] {msg}", kwargs


def _format_duration(seconds: float) -> str:
    """Return a short human duration, e.g. 45 s, 12 min, 3.5 h."""
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"

# Numeric node fields that are averaged over the write interval before being
# pushed to Home Assistant.  Averaging prevents the HA database from receiving
# every raw reading while still giving a representative value per interval.
_AVERAGED_FIELDS = (
    "voltage_in",
    "voltage_out",
    "current_in",
    "current_out",
    "power",
    "temperature",
    "dc_dc_duty_cycle",
    "rssi",
)


class _MigratingStore(Store):
    """Store subclass with explicit migration support.

    The default Store raises NotImplementedError on major-version
    mismatches, which silently drops all persisted state.  This subclass
    returns old data as-is because all format changes between v1 and v2
    are backward-compatible (the ``energy_data`` key was added and the
    load code already handles its absence via ``.get()``).
    """

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict,
    ) -> dict:
        """Migrate v1 → v2: return data as-is (format is backward-compatible)."""
        return old_data


class PyTapDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage streaming data from the Tigo gateway via pytap parser.

    Runs a background listener thread that reads from the TCP/serial source,
    feeds bytes into the parser, and dispatches parsed events to the HA event loop.
    Only events matching user-configured barcodes are stored in coordinator data.
    """

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
        )
        self._host: str = entry.data[CONF_HOST]
        self._port: int = entry.data.get(CONF_PORT, DEFAULT_PORT)
        self._log = _GatewayLog(
            _LOGGER, {"gateway": entry.data.get(CONF_INVERTER) or self._host}
        )
        self._modules: list[dict[str, Any]] = entry.data.get(CONF_MODULES, [])
        self._live_reporting: bool = entry.data.get(
            CONF_LIVE_REPORTING, DEFAULT_LIVE_REPORTING
        )
        raw_write_interval = entry.data.get(CONF_WRITE_INTERVAL, DEFAULT_WRITE_INTERVAL)
        try:
            self._write_interval: float = float(raw_write_interval)
        except (TypeError, ValueError):
            self._write_interval = float(DEFAULT_WRITE_INTERVAL)
        self._write_interval = max(0.0, self._write_interval)
        self._estimate_enabled: bool = bool(
            entry.data.get(CONF_ESTIMATE_SILENT, DEFAULT_ESTIMATE_SILENT)
        )

        # Build barcode allowlist from configured modules
        self._configured_barcodes: set[str] = {
            m[CONF_MODULE_BARCODE] for m in self._modules if m.get(CONF_MODULE_BARCODE)
        }
        # Module lookup by barcode for name/string metadata
        self._module_lookup: dict[str, dict[str, Any]] = {
            m[CONF_MODULE_BARCODE]: m
            for m in self._modules
            if m.get(CONF_MODULE_BARCODE)
        }

        # Barcode ↔ node_id mapping learned from InfrastructureEvents
        self._barcode_to_node: dict[str, int] = {}
        self._node_to_barcode: dict[int, str] = {}

        # Whether the current session has received an InfrastructureEvent
        # that includes a non-empty node table.  Until a node table arrives,
        # stale mappings from the previous session may be incorrect (node-ID
        # reassignment overnight) so fallback resolution is suppressed to
        # avoid mis-routing power reports.
        self._infra_received: bool = False

        # Track barcodes seen on the bus but not in user config
        self._discovered_barcodes: set[str] = set()

        # Counter for power reports dropped because barcode could not be
        # resolved.  Used to emit periodic INFO-level messages so operators
        # know data IS flowing but resolution is blocked.
        self._pending_power_reports: int = 0
        self._pending_last_log: float | None = None

        # Node IDs seen in power reports this session that could not be
        # resolved to a barcode, and when the last full node table arrived.
        # Both feed the node mapping diagnostic sensor.
        self._unidentified_nodes: set[int] = set()
        self._last_node_table: datetime | None = None

        # --- Reading health (see reading_state) ---
        # Monotonic times: last bytes from the converter, last power report
        # per configured barcode, and when daylight (sun above
        # LOW_SUN_ELEVATION) began. Written by the listener thread, read on
        # the event loop; single assignments, so no lock is needed.
        self._started_mono: float = time.monotonic()
        self._last_data_mono: float = self._started_mono
        self._last_report: dict[str, float] = {}
        self._sun_low: bool = False
        self._day_since: float | None = None
        self._health_states: dict[str, str] = {}
        self._validity_states: dict[str, str] = {}
        self._link_was_up: bool = True
        # Why data stopped, as last seen by the listener thread: the TCP
        # connection failed/dropped, or it was up but no bytes arrived.
        self._outage_cause: str = BUS_CONVERTER_UNREACHABLE
        self._health_unsub: Any = None
        # Estimating silent optimizers from their string (see
        # _update_estimates). share: learned power relative to the string
        # average; est_power: current estimates; est_cursor: monotonic time
        # estimated energy has been added up to; est_close: when a silent
        # optimizer reported again (set by the listener thread).
        self._share: dict[str, float] = {}
        self._est_power: dict[str, float] = {}
        self._est_cursor: dict[str, float] = {}
        self._est_close: dict[str, float] = {}
        # Restart gaps: when state was last saved before this start, and which
        # optimizers have reported since (only an optimizer's first reading
        # after a restart can close a restart gap).
        self._stopped_at: datetime | None = None
        self._seen_since_start: set[str] = set()
        # RS-485 frame error tracking
        self._parser: Any = None
        self._bus_window: tuple[float, int, int] | None = None
        self._bus_warned_at: float | None = None

        # Listener task handle
        self._listener_task: asyncio.Task | None = None
        self._stop_event = threading.Event()

        # Write-interval throttle state (accessed only from the listener thread)
        # Only used when live_reporting is False.
        self._last_ha_update: float = 0.0
        self._ha_update_pending: bool = False
        # Per-barcode buffer of timestamped numeric readings accumulated
        # between HA writes.  Each entry is (monotonic_ts, field_dict).
        # Flushed (time-weighted averaged) each time the write interval fires.
        self._reading_buffers: dict[str, list[tuple[float, dict[str, Any]]]] = {}

        # Midnight reset timer handle
        self._midnight_reset_unsub: asyncio.TimerHandle | None = None

        # Source handle for cancellation — accessed from both threads
        self._source: Any = None
        self._source_lock = threading.Lock()

        # --- Persistence (single HA Store for all state) ---
        self._store = _MigratingStore(
            hass, STORE_VERSION, f"pytap_{entry.entry_id}_coordinator"
        )
        self._unsaved_changes: bool = False
        self._save_task: asyncio.TimerHandle | None = None

        # Parser infrastructure state — shared between coordinator and parser
        self._persistent_state: PersistentState = PersistentState()

        # Per-module energy accumulation state (persisted)
        self._energy_state: dict[str, EnergyAccumulator] = {}

        # Initialize data structure
        self.data: dict[str, Any] = {
            "gateways": {},
            "nodes": {},
            "counters": {},
            "discovered_barcodes": [],
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Return current data (push-based, no polling needed)."""
        return self.data

    @property
    def configured_barcodes(self) -> set[str]:
        """Barcodes of the modules configured for this entry."""
        return set(self._configured_barcodes)

    @property
    def link_up(self) -> bool:
        """Return False once no bytes have arrived for LINK_DOWN_SECONDS."""
        return time.monotonic() - self._last_data_mono < LINK_DOWN_SECONDS

    @property
    def bus_status(self) -> str:
        """Return the CCA bus status: normal, converter unreachable or quiet.

        Not normal once no bytes have arrived for LINK_DOWN_SECONDS. The
        listener tells the two apart: a failed or dropped TCP connection
        means the converter (or the network to it) is down; a connection
        that stays up with nothing on it means the bus is quiet, so the
        CCA is off or the converter's wiring to the bus is broken.
        """
        if self.link_up:
            return BUS_NORMAL
        return self._outage_cause

    @property
    def last_data(self) -> datetime | None:
        """Return the wall-clock time bytes last arrived, if any did."""
        if self._last_data_mono == self._started_mono:
            return None
        return dt_util.utcnow() - timedelta(
            seconds=time.monotonic() - self._last_data_mono
        )

    def reading_state(self, barcode: str) -> str:
        """Return how far an optimizer's live readings can be trusted.

        ok:      reporting, or quiet for less than the thresholds below.
        asleep:  low sun and quiet for NIGHT_QUIET_SECONDS, or not yet heard
                 from since daylight began; live readings read 0.
        silent:  daylight and quiet for SILENT_UNAVAILABLE_SECONDS; live
                 readings are unavailable. Optimizers can drop off the radio
                 link for a while, so this waits a long time.
        offline: no bytes from the converter for LINK_DOWN_SECONDS.
        """
        now = time.monotonic()
        if not self.link_up:
            return READING_OFFLINE
        last = self._last_report.get(barcode, self._started_mono)
        if self._sun_low or self._day_since is None:
            if now - last >= NIGHT_QUIET_SECONDS:
                return READING_ASLEEP
            return READING_OK
        quiet_in_daylight = now - max(last, self._day_since)
        if quiet_in_daylight >= SILENT_UNAVAILABLE_SECONDS:
            return READING_SILENT
        if last < self._day_since and now - last >= NIGHT_QUIET_SECONDS:
            # Not heard from since before daylight: still asleep.
            return READING_ASLEEP
        return READING_OK

    @property
    def estimate_enabled(self) -> bool:
        """Return True when silent optimizers are estimated from their string."""
        return self._estimate_enabled

    def _strings(self) -> dict[str, list[str]]:
        """Return configured barcodes grouped by string label."""
        groups: dict[str, list[str]] = {}
        for module in self._modules:
            barcode = module.get(CONF_MODULE_BARCODE)
            string = (module.get(CONF_MODULE_STRING) or "").strip()
            if barcode and string:
                groups.setdefault(string, []).append(barcode)
        return groups

    def _is_fresh(self, barcode: str, now: float) -> bool:
        last = self._last_report.get(barcode)
        return last is not None and now - last < ESTIMATE_AFTER_SECONDS

    def estimated_power(self, barcode: str) -> float | None:
        """Return the estimated power of a silent optimizer, or None.

        Only string and total sensors use this; the optimizer's own sensors
        always show what it reported.
        """
        if barcode not in self._est_power or self._is_fresh(
            barcode, time.monotonic()
        ):
            return None
        return self._est_power[barcode]

    def estimated_energy(self, barcode: str) -> tuple[float, float]:
        """Return (today, lifetime) energy estimated for a silent optimizer."""
        acc = self._energy_state.get(barcode)
        if acc is None:
            return 0.0, 0.0
        if acc.daily_reset_date != dt_util.now().date().isoformat():
            return 0.0, acc.estimated_total_wh
        return acc.estimated_daily_wh, acc.estimated_total_wh

    def share(self, barcode: str) -> float | None:
        """Return the optimizer's learned share of its string's average power."""
        return self._share.get(barcode)

    def data_validity(self, barcode: str) -> str | None:
        """Return how trustworthy the optimizer's current readings are.

        None while the bus status isn't normal: the gateway-level bus
        status sensor explains why, and every live reading is unavailable.
        """
        state = self.reading_state(barcode)
        if state == READING_OFFLINE:
            return None
        if state == READING_ASLEEP and self._sun_low:
            # At dusk the string's last awake optimizers keep reporting about
            # 0 W, so a sleeping optimizer would otherwise stay "estimated"
            # (at 0 W) until half its string sleeps. Asleep is what it is.
            return VALIDITY_ASLEEP
        if self.estimated_power(barcode) is not None:
            # After SILENT_UNAVAILABLE_SECONDS its own live sensors are
            # unavailable even though the string still estimates it.
            if state == READING_SILENT:
                return VALIDITY_SILENT_ESTIMATED
            return VALIDITY_ESTIMATED
        if state == READING_ASLEEP:
            return VALIDITY_ASLEEP
        if state == READING_SILENT:
            return VALIDITY_SILENT
        if not self._is_fresh(barcode, time.monotonic()):
            return VALIDITY_HOLDING
        return VALIDITY_REPORTING

    def _update_estimates(self, now: float) -> bool:
        """Learn shares, estimate silent optimizers and add estimated energy.

        Optimizers in a string carry the same current. While an optimizer
        and most of its string report, its power relative to the string
        average (its share, which captures shading) is learned. Once it has
        been quiet for ESTIMATE_AFTER_SECONDS and at least
        ESTIMATE_MIN_PEER_FRACTION of the rest of its string is reporting,
        its power is estimated as share × the reporting optimizers' average,
        and that power is added up as estimated energy from its last report.
        If the string's current collapses (e.g. a broken panel), the
        estimate collapses with it.

        Returns True if any estimate is active or just ended.
        """
        nodes = self.data.get("nodes", {})

        def power(barcode: str) -> float | None:
            node = nodes.get(barcode)
            value = node.get("power") if node else None
            return max(float(value), 0.0) if value is not None else None

        new_est: dict[str, float] = {}
        for members in self._strings().values():
            for barcode in members:
                peers = [b for b in members if b != barcode]
                if not peers:
                    continue
                reporting = [
                    pw
                    for b in peers
                    if self._is_fresh(b, now) and (pw := power(b)) is not None
                ]
                needed = max(1, math.ceil(len(peers) * ESTIMATE_MIN_PEER_FRACTION))
                if len(reporting) < needed:
                    continue
                peer_avg = sum(reporting) / len(reporting)
                if self._is_fresh(barcode, now):
                    own = power(barcode)
                    if own is not None and peer_avg >= SHARE_MIN_PEER_POWER_W:
                        ratio = min(own / peer_avg, SHARE_MAX)
                        old = self._share.get(barcode)
                        self._share[barcode] = (
                            ratio if old is None else old + SHARE_SMOOTHING * (ratio - old)
                        )
                    continue
                if self._estimate_enabled and self.link_up:
                    new_est[barcode] = self._share.get(barcode, 1.0) * peer_avg

        today = dt_util.now().date().isoformat()
        backfill_limit = ESTIMATE_AFTER_SECONDS + HEALTH_CHECK_INTERVAL_SECONDS
        for barcode in set(self._est_cursor) | set(new_est):
            cursor = self._est_cursor.pop(barcode, None)
            close = self._est_close.pop(barcode, None)
            previous = self._est_power.get(barcode)
            energy_wh = 0.0
            if cursor is not None and close is not None and previous is not None:
                # Reported again: estimate up to the first new report.
                energy_wh += previous * max(0.0, close - cursor) / 3600
                cursor = None
            if barcode in new_est:
                if cursor is None:
                    # Start from the last report (its measured energy stops
                    # there), but never further back than the gap that
                    # triggered estimating.
                    last = self._last_report.get(barcode, now)
                    cursor = max(last, now - backfill_limit)
                energy_wh += new_est[barcode] * max(0.0, now - cursor) / 3600
                self._est_cursor[barcode] = now
            if energy_wh > 0:
                acc = self._energy_state.setdefault(
                    barcode, EnergyAccumulator(daily_reset_date=today)
                )
                add_estimated_energy(acc, energy_wh, today)
                self._schedule_save()

        active = bool(new_est) or bool(self._est_power)
        for barcode in set(new_est) - set(self._est_power):
            self._log.debug(
                "Estimating %s from its string: %.0f W (share %.2f)",
                self._module_label(barcode),
                new_est[barcode],
                self._share.get(barcode, 1.0),
            )
        self._est_power = new_est
        return active

    def _module_label(self, barcode: str) -> str:
        """Return 'P03 (4-ABC123D)' style label for log lines."""
        name = self._module_lookup.get(barcode, {}).get(CONF_MODULE_NAME)
        return f"{name} ({barcode})" if name and name != barcode else barcode

    def silent_modules(self) -> list[str]:
        """Return the names of configured modules whose readings are silent."""
        return sorted(
            self._module_lookup.get(b, {}).get(CONF_MODULE_NAME, b)
            for b in self._configured_barcodes
            if self.reading_state(b) == READING_SILENT
        )

    def _update_sun(self) -> None:
        """Refresh the cached low-sun flag and when daylight began."""
        try:
            # astral directly: HA's get_astral_location is deprecated and
            # its replacement doesn't exist in older HA versions.
            config = self.hass.config
            observer = Observer(config.latitude, config.longitude, config.elevation)
            sun = solar_elevation(observer, dt_util.utcnow())
        except Exception:  # noqa: BLE001 - no location: never treat as night
            self._log.debug("Could not compute sun elevation", exc_info=True)
            sun = None
        low = sun is not None and sun < LOW_SUN_ELEVATION
        if low:
            self._day_since = None
        elif self._day_since is None:
            self._day_since = time.monotonic()
        self._sun_low = low

    @callback
    def _async_health_check(self, _now: Any = None) -> None:
        """Re-evaluate reading states, log changes and refresh sensors."""
        self._update_sun()
        now = time.monotonic()
        link_up = self.link_up
        changed = link_up != self._link_was_up
        if not link_up and self._link_was_up:
            quiet_for = _format_duration(now - self._last_data_mono)
            if self._outage_cause == BUS_QUIET:
                self._log.warning(
                    "Bus quiet: the RS-485 converter is connected but has "
                    "received nothing for %s. Check that the Tigo CCA is "
                    "powered and the converter's A/B wiring to the bus. Live "
                    "readings are unavailable; energy totals are kept",
                    quiet_for,
                )
            else:
                self._log.warning(
                    "Converter unreachable: no connection to the RS-485 "
                    "converter at %s:%s for %s. Check its power and network. "
                    "Live readings are unavailable; energy totals are kept",
                    self._host,
                    self._port,
                    quiet_for,
                )
        elif link_up and not self._link_was_up:
            self._log.info("Bus status normal again; live readings restored")
        self._link_was_up = link_up

        states = {b: self.reading_state(b) for b in self._configured_barcodes}
        if link_up:
            self._log_state_changes(states, now)
        if self._update_estimates(now):
            changed = True
        validity = {b: self.data_validity(b) for b in self._configured_barcodes}
        if states != self._health_states or validity != self._validity_states:
            changed = True
        self._health_states = states
        self._validity_states = validity

        self._check_bus_errors(now)
        if changed:
            self.async_update_listeners()

    def _log_state_changes(self, states: dict[str, str], now: float) -> None:
        """Log optimizers going silent or coming back."""
        old = self._health_states
        went_silent = [
            b
            for b, st in states.items()
            if st == READING_SILENT and old.get(b) != READING_SILENT
        ]
        if went_silent:
            if all(st == READING_SILENT for st in states.values()):
                self._log.warning(
                    "No optimizer has reported for %s although the converter "
                    "is sending data; live readings are unavailable. Check "
                    "the Tigo CCA if this persists",
                    _format_duration(SILENT_UNAVAILABLE_SECONDS),
                )
            else:
                for b in sorted(went_silent):
                    self._log.warning(
                        "Optimizer %s has not reported for %s while others "
                        "have; its live readings are unavailable. Optimizers "
                        "can drop out for a while on radio interference; "
                        "check it if this persists",
                        self._module_label(b),
                        _format_duration(SILENT_UNAVAILABLE_SECONDS),
                    )
        for b, st in states.items():
            prev = old.get(b)
            if prev == READING_SILENT and st == READING_OK:
                self._log.info(
                    "Optimizer %s is reporting again", self._module_label(b)
                )
            elif prev is not None and prev != st:
                self._log.debug(
                    "Optimizer %s reading state %s -> %s",
                    self._module_label(b),
                    prev,
                    st,
                )

    def _check_bus_errors(self, now: float) -> None:
        """Warn when many RS-485 frames fail their checks (wiring problem)."""
        parser = self._parser
        if parser is None:
            return
        try:
            c = parser.counters
        except Exception:  # noqa: BLE001 - counters are best-effort
            return
        frames = int(c.get("frames_received", 0))
        errors = int(c.get("crc_errors", 0)) + int(c.get("runts", 0)) + int(
            c.get("giants", 0)
        )
        window = self._bus_window
        if window is None or frames < window[1] or errors < window[2]:
            # First sample, or a new parser after a reconnect.
            self._bus_window = (now, frames, errors)
            return
        if now - window[0] < BUS_ERROR_WINDOW_SECONDS:
            return
        d_frames = frames - window[1]
        d_errors = errors - window[2]
        self._bus_window = (now, frames, errors)
        total = d_frames + d_errors
        if total < BUS_ERROR_MIN_FRAMES:
            return
        rate = d_errors / total
        if rate >= BUS_ERROR_RATE_WARN:
            if (
                self._bus_warned_at is None
                or now - self._bus_warned_at >= BUS_ERROR_LOG_INTERVAL_SECONDS
            ):
                self._bus_warned_at = now
                self._log.warning(
                    "%d of %d RS-485 frames (%.1f%%) failed their checks in the "
                    "last %s. Readings are being lost; check the A/B wiring, "
                    "termination and the converter's serial settings (38400 8N1)",
                    d_errors,
                    total,
                    rate * 100,
                    _format_duration(now - window[0]),
                )
        elif self._bus_warned_at is not None:
            self._bus_warned_at = None
            self._log.info(
                "RS-485 frame errors back to normal (%.1f%%)", rate * 100
            )

    def mapping_status(self) -> dict[str, Any]:
        """Summarise barcode resolution for the node mapping sensor.

        Power reports identify optimizers by a short node ID. The gateway's
        node table maps those IDs to barcodes; PyTap learns it only by
        overhearing the CCA read it, which normally happens when the
        optimizers power up in the morning. Until then reports are dropped.
        """
        mapped = self._configured_barcodes & set(self._barcode_to_node)
        unmapped = self._configured_barcodes - mapped
        if not mapped:
            state = MAPPING_WAITING
        elif unmapped:
            state = MAPPING_PARTIAL
        else:
            state = MAPPING_COMPLETE
        return {
            "state": state,
            "configured": len(self._configured_barcodes),
            "mapped": len(mapped),
            "unmapped_barcodes": sorted(unmapped),
            "unidentified_nodes": len(self._unidentified_nodes),
            "last_node_table": (
                self._last_node_table.isoformat() if self._last_node_table else None
            ),
            "silent_modules": self.silent_modules(),
        }

    def get_diagnostics_data(self) -> dict[str, Any]:
        """Return coordinator diagnostics snapshot."""
        now = time.monotonic()
        return {
            "node_mapping_status": self.mapping_status(),
            "node_mappings": {
                "barcode_to_node": dict(self._barcode_to_node),
                "node_to_barcode": {
                    str(node_id): barcode
                    for node_id, barcode in self._node_to_barcode.items()
                },
            },
            "reading_health": {
                "bus_status": self.bus_status,
                "seconds_since_data": round(now - self._last_data_mono, 1),
                "sun_low": self._sun_low,
                "estimate_enabled": self._estimate_enabled,
                "modules": {
                    barcode: {
                        "state": self.reading_state(barcode),
                        "validity": self.data_validity(barcode),
                        "share": (
                            round(self._share[barcode], 3)
                            if barcode in self._share
                            else None
                        ),
                        "estimated_power": self.estimated_power(barcode),
                        "seconds_since_report": (
                            round(now - self._last_report[barcode], 1)
                            if barcode in self._last_report
                            else None
                        ),
                    }
                    for barcode in sorted(self._configured_barcodes)
                },
            },
            "connection_state": {
                "infra_received": self._infra_received,
                "pending_power_reports": self._pending_power_reports,
                "host": self._host,
                "port": self._port,
            },
            "energy_state": {
                barcode: {
                    "daily_energy_wh": round(acc.daily_energy_wh, 2),
                    "total_energy_wh": round(acc.total_energy_wh, 2),
                    "daily_reset_date": acc.daily_reset_date,
                    "last_power_w": acc.last_power_w,
                    "last_reading_ts": (
                        acc.last_reading_ts.isoformat()
                        if acc.last_reading_ts is not None
                        else None
                    ),
                    "readings_today": acc.readings_today,
                    "estimated_daily_wh": round(acc.estimated_daily_wh, 2),
                    "estimated_total_wh": round(acc.estimated_total_wh, 2),
                }
                for barcode, acc in self._energy_state.items()
            },
        }

    async def async_start_listener(self) -> None:
        """Start the background listener task."""
        await self._async_load_coordinator_state()
        self._stop_event.clear()
        self._listener_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_listen(),
            name="pytap_listener",
        )
        self._schedule_midnight_reset()
        self._update_sun()
        self._health_unsub = async_track_time_interval(
            self.hass,
            self._async_health_check,
            timedelta(seconds=HEALTH_CHECK_INTERVAL_SECONDS),
        )

    async def _async_listen(self) -> None:
        """Async wrapper to run the blocking listener in an executor."""
        await self.hass.async_add_executor_job(self._listen)

    async def async_stop_listener(self) -> None:
        """Stop the background listener task."""
        self._stop_event.set()
        if self._health_unsub is not None:
            self._health_unsub()
            self._health_unsub = None
        # Cancel midnight reset timer
        if self._midnight_reset_unsub is not None:
            self._midnight_reset_unsub.cancel()
            self._midnight_reset_unsub = None
        # Close source to unblock the read()/connect() call in the executor,
        # and let the listener finish before the final save, so no reading
        # arrives after it.
        with self._source_lock:
            if self._source is not None:
                try:
                    self._source.close()
                except Exception:
                    pass
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                async with asyncio.timeout(5):
                    await self._listener_task
            except (asyncio.CancelledError, TimeoutError, Exception):
                pass
            self._listener_task = None
        # Flush any pending state save
        if self._save_task is not None:
            self._save_task.cancel()
            self._save_task = None
        if self._unsaved_changes:
            await self._async_save_coordinator_state()

    def _schedule_midnight_reset(self) -> None:
        """Schedule a callback at the next local midnight to reset daily accumulators."""
        now = dt_util.now()
        next_midnight = datetime.combine(
            now.date() + timedelta(days=1), dt_time.min, tzinfo=now.tzinfo
        )
        delay = (next_midnight - now).total_seconds()
        self._midnight_reset_unsub = self.hass.loop.call_later(
            delay, self._perform_midnight_reset
        )
        self._log.debug(
            "Scheduled daily reset in %.0f seconds (at %s)", delay, next_midnight
        )

    @callback
    def _perform_midnight_reset(self) -> None:
        """Reset all daily accumulators and push updated data to sensors."""
        self._midnight_reset_unsub = None
        today = dt_util.now().date().isoformat()
        self._log.info(
            "Midnight daily reset — resetting daily accumulators for %s", today
        )

        data_changed = False
        for barcode, acc in self._energy_state.items():
            if acc.daily_reset_date == today:
                continue
            reset_daily(acc, today)

            node_data = self.data.get("nodes", {}).get(barcode)
            if node_data is not None:
                node_data["daily_energy_wh"] = 0.0
                node_data["readings_today"] = 0
                node_data["daily_reset_date"] = today
                data_changed = True

        if data_changed:
            self._schedule_save()
            self.async_set_updated_data(dict(self.data))

        # Re-schedule for the next midnight
        self._schedule_midnight_reset()

    def _listen(self) -> None:
        """Blocking listener loop (runs in executor thread).

        Connects to the RS-485 converter, reads bytes, feeds them into the
        parser, and dispatches events back to the HA event loop.

        Logging: the first failure of an outage is a warning, repeats are
        debug, and recovery is logged once data has flowed for
        STABLE_CONNECTION_SECONDS. Retries back off from RECONNECT_DELAY to
        RECONNECT_MAX_DELAY.
        """
        from .pytap.api import connect, create_parser

        retries = 0
        delay = RECONNECT_DELAY
        outage_since: float | None = None
        unexpected_logged = False
        ever_connected = False

        while not self._stop_event.is_set():
            parser = create_parser(
                persistent_state=self._persistent_state, log=self._log
            )
            self._parser = parser
            self._init_mappings_from_parser(parser)
            self._infra_received = False
            with self._source_lock:
                self._source = None

            try:
                source_config = {"tcp": self._host, "port": self._port}
                source = connect(source_config)
                with self._source_lock:
                    if self._stop_event.is_set():
                        source.close()
                        return
                    self._source = source
                connected_at = time.monotonic()
                if not ever_connected:
                    self._log.info(
                        "Connected to RS-485 converter at %s:%s",
                        self._host,
                        self._port,
                    )
                    ever_connected = True
                else:
                    self._log.debug(
                        "Connected to RS-485 converter at %s:%s",
                        self._host,
                        self._port,
                    )
                last_data_time = connected_at

                while not self._stop_event.is_set():
                    data = self._source.read(4096)
                    if data:
                        now = time.monotonic()
                        last_data_time = now
                        self._last_data_mono = now
                        if (
                            outage_since is not None
                            and now - connected_at >= STABLE_CONNECTION_SECONDS
                        ):
                            self._log.info(
                                "Receiving data again after %s without it",
                                _format_duration(connected_at - outage_since),
                            )
                            outage_since = None
                            unexpected_logged = False
                            retries = 0
                            delay = RECONNECT_DELAY
                        elif outage_since is None:
                            retries = 0
                            delay = RECONNECT_DELAY
                        events = parser.feed(data)
                        for event in events:
                            if self._process_event(event):
                                self._ha_update_pending = True

                        if self._live_reporting:
                            # Live mode — push every change immediately
                            self._push_live_update(parser)
                        else:
                            # Interval mode — buffer readings and push
                            # time-weighted averages once per write_interval.
                            self._maybe_flush_interval(parser, time.monotonic())
                    else:
                        # No data this read cycle (socket read timeout).
                        now = time.monotonic()
                        if not self._live_reporting:
                            # Flush buffered readings on schedule so sensors
                            # keep updating during production lulls.
                            self._maybe_flush_interval(parser, now)
                        if (
                            RECONNECT_TIMEOUT > 0
                            and (now - last_data_time) > RECONNECT_TIMEOUT
                        ):
                            self._outage_cause = BUS_QUIET
                            if outage_since is None:
                                outage_since = last_data_time
                                self._log.warning(
                                    "No data from the RS-485 converter for "
                                    "%ds, reconnecting. Further attempts are "
                                    "logged at debug level until data returns",
                                    RECONNECT_TIMEOUT,
                                )
                            else:
                                self._log.debug(
                                    "Still no data after reconnecting"
                                )
                            break

            except OSError as err:
                # Socket errors, refused/timed-out connects, peer resets.
                if self._stop_event.is_set():
                    return
                self._outage_cause = BUS_CONVERTER_UNREACHABLE
                if outage_since is None:
                    outage_since = time.monotonic()
                    self._log.warning(
                        "Connection to the RS-485 converter at %s:%s failed: "
                        "%s. Retrying; further failures are logged at debug "
                        "level until data returns",
                        self._host,
                        self._port,
                        err,
                    )
                else:
                    self._log.debug("Connection attempt failed: %s", err)
            except Exception:
                # Anything else is a bug (parser or coordinator), not the
                # network: log the traceback once per outage.
                if self._stop_event.is_set():
                    return
                if outage_since is None:
                    outage_since = time.monotonic()
                if not unexpected_logged:
                    unexpected_logged = True
                    self._log.exception(
                        "Unexpected error while processing data; reconnecting. "
                        "Please report this with the traceback"
                    )
                else:
                    self._log.debug("Unexpected error again", exc_info=True)
            finally:
                # Flush any data held back by the write interval.
                if self._ha_update_pending:
                    if not self._live_reporting and self._reading_buffers:
                        flush_now = time.monotonic()
                        snapshot = self._build_averaged_snapshot(flush_now)
                        self._reading_buffers.clear()
                        self.hass.loop.call_soon_threadsafe(
                            self.async_set_updated_data,
                            snapshot,
                        )
                    else:
                        self.hass.loop.call_soon_threadsafe(
                            self.async_set_updated_data,
                            {**self.data, "nodes": dict(self.data["nodes"])},
                        )
                    self._ha_update_pending = False
                with self._source_lock:
                    if self._source is not None:
                        try:
                            self._source.close()
                        except Exception:  # noqa: BLE001
                            pass
                        self._source = None

            if self._stop_event.is_set():
                break

            retries += 1
            if RECONNECT_RETRIES > 0 and retries > RECONNECT_RETRIES:
                self._log.error("Max retries (%d) exceeded", RECONNECT_RETRIES)
                return

            self._log.debug(
                "Reconnecting in %ds (attempt %d/%s)",
                delay,
                retries,
                str(RECONNECT_RETRIES) if RECONNECT_RETRIES else "∞",
            )
            # Sleep in small increments so we can respond to stop quickly
            for _ in range(int(delay * 10)):
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    def _push_live_update(self, parser: Any) -> None:
        """Push an immediate snapshot to HA (live reporting mode).

        Publishes an *immutable* copy of the ``nodes`` mapping so the listener
        thread can keep mutating ``self.data["nodes"]`` without HA-side
        consumers (sensors, diagnostics) ever iterating a dict that changes
        size mid-read.  Runs in the executor thread.
        """
        if not self._ha_update_pending:
            return
        self.data["counters"] = parser.counters
        self.hass.loop.call_soon_threadsafe(
            self.async_set_updated_data,
            {**self.data, "nodes": dict(self.data["nodes"])},
        )
        self._ha_update_pending = False

    def _maybe_flush_interval(self, parser: Any, now: float) -> None:
        """Flush buffered readings as a time-weighted average (interval mode).

        Pushes a snapshot once the write interval has elapsed since the last
        update.  Called both when data arrives and during idle read cycles so
        buffered readings are not stranded when the stream goes quiet.  Runs
        in the executor thread.
        """
        if not self._ha_update_pending or (
            now - self._last_ha_update < self._write_interval
        ):
            return
        self.data["counters"] = parser.counters
        snapshot = self._build_averaged_snapshot(now)
        per_node_counts = {
            barcode: len(readings)
            for barcode, readings in self._reading_buffers.items()
            if readings and barcode in snapshot["nodes"]
        }
        self._log.debug(
            "HA update: %d node(s) — %s",
            len(per_node_counts),
            ", ".join(
                f"{snapshot['nodes'][b].get('name', b)}: {n} reading(s)"
                for b, n in per_node_counts.items()
            ),
        )
        self._reading_buffers.clear()
        self.hass.loop.call_soon_threadsafe(
            self.async_set_updated_data,
            snapshot,
        )
        self._last_ha_update = now
        self._ha_update_pending = False

    def _init_mappings_from_parser(self, parser: Any) -> None:
        """Pre-populate barcode/node mappings from the parser's persistent state.

        Parser state is the freshest source of truth for barcode ↔ node
        relationships.  When the parser has a non-empty node table, its
        mappings fully replace the coordinator's saved ones.  When the
        parser state is empty (e.g. state file was lost or first run),
        the coordinator's saved mappings are preserved so that barcodes
        discovered in earlier sessions survive and can be matched
        immediately when the user adds them as modules.
        """
        try:
            infra = parser.infrastructure
            parser_barcode_to_node: dict[str, int] = {}
            parser_node_to_barcode: dict[int, str] = {}
            for node_id, node_info in infra.get("nodes", {}).items():
                barcode = node_info.get("barcode")
                if barcode:
                    parser_barcode_to_node[barcode] = node_id
                    parser_node_to_barcode[node_id] = barcode

            if parser_barcode_to_node:
                # Parser has data — use it as ground truth
                purged = set(self._barcode_to_node) - set(parser_barcode_to_node)
                self._barcode_to_node = parser_barcode_to_node
                self._node_to_barcode = parser_node_to_barcode
                self._log.info(
                    "Restored %d barcode↔node mappings from parser state%s",
                    len(parser_barcode_to_node),
                    f" (purged {len(purged)} stale)" if purged else "",
                )
            else:
                # Parser state is empty — keep coordinator-saved mappings
                self._log.info(
                    "Parser state has no node table; keeping %d "
                    "coordinator-saved barcode mappings as fallback",
                    len(self._barcode_to_node),
                )
        except Exception:
            self._log.debug(
                "Could not read parser infrastructure for pre-population",
                exc_info=True,
            )

    def _process_event(self, event: Event) -> bool:
        """Process a parsed event, filtering by configured barcodes.

        Returns True if coordinator data was modified (triggers HA update).
        """
        if isinstance(event, PowerReportEvent):
            return self._handle_power_report(event)
        if isinstance(event, InfrastructureEvent):
            return self._handle_infrastructure(event)
        if isinstance(event, TopologyEvent):
            return self._handle_topology(event)
        if isinstance(event, StringEvent):
            self._log.debug(
                "String event (gw=%d, node=%d, %s): %s",
                event.gateway_id,
                event.node_id,
                event.direction,
                event.content,
            )
        return False

    def _handle_power_report(self, event: PowerReportEvent) -> bool:
        """Handle a power report event. Returns True if data was modified."""
        barcode = event.barcode

        # Try to resolve barcode from node_id via the coordinator mapping.
        # Only use the fallback after the *current* session has received an
        # InfrastructureEvent with a non-empty node table — before that,
        # the mapping may contain stale entries from a previous session
        # where node-IDs differed.
        if (
            not barcode
            and self._infra_received
            and event.node_id in self._node_to_barcode
        ):
            barcode = self._node_to_barcode[event.node_id]

        if not barcode:
            self._pending_power_reports += 1
            new_unidentified = event.node_id not in self._unidentified_nodes
            if new_unidentified:
                self._unidentified_nodes.add(event.node_id)
            if not self._infra_received:
                # Remind at INFO every PENDING_LOG_INTERVAL_SECONDS so the
                # operator sees data is flowing but resolution is blocked.
                mono = time.monotonic()
                if self._pending_power_reports == 1:
                    self._pending_last_log = mono
                    self._log.info(
                        "Power report for node %d — waiting for node table "
                        "before barcode resolution (gateway=%d)",
                        event.node_id,
                        event.gateway_id,
                    )
                elif (
                    self._pending_last_log is None
                    or mono - self._pending_last_log >= PENDING_LOG_INTERVAL_SECONDS
                ):
                    self._pending_last_log = mono
                    self._log.info(
                        "%d power reports received but barcode resolution "
                        "still pending — waiting for gateway to send "
                        "the full node table (normally when the optimizers "
                        "wake up in the morning)",
                        self._pending_power_reports,
                    )
                else:
                    self._log.debug(
                        "Power report for node %d deferred — waiting for "
                        "node table (gateway=%d) [%d pending]",
                        event.node_id,
                        event.gateway_id,
                        self._pending_power_reports,
                    )
            else:
                self._log.debug(
                    "Power report for node %d with no barcode yet (gateway=%d)",
                    event.node_id,
                    event.gateway_id,
                )
            # Push an update the first time a node is seen so the node
            # mapping sensor shows that data is arriving.
            return new_unidentified

        # Check if this barcode is in our configured allowlist
        if barcode not in self._configured_barcodes:
            if barcode not in self._discovered_barcodes:
                self._discovered_barcodes.add(barcode)
                self.data["discovered_barcodes"] = sorted(self._discovered_barcodes)
                self._schedule_save()
                self._log.info(
                    "Discovered unconfigured Tigo optimizer barcode: %s "
                    "(gateway=%d, node=%d). Add it to your PyTap module "
                    "list to start tracking.",
                    barcode,
                    event.gateway_id,
                    event.node_id,
                )
                return True
            return False

        mono = time.monotonic()
        if barcode in self._est_cursor and barcode not in self._est_close:
            self._est_close[barcode] = mono
        self._last_report[barcode] = mono

        # Get module metadata
        module_meta = self._module_lookup.get(barcode, {})
        peak_power_raw = module_meta.get(CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER)
        try:
            peak_power = int(peak_power_raw)
        except (TypeError, ValueError):
            peak_power = DEFAULT_PEAK_POWER
        if peak_power <= 0:
            peak_power = DEFAULT_PEAK_POWER

        performance: float | None = None
        if event.power is not None:
            performance = (max(event.power, 0.0) / peak_power) * 100.0
        now = dt_util.now()

        acc = self._energy_state.setdefault(
            barcode,
            EnergyAccumulator(daily_reset_date=now.date().isoformat()),
        )
        previous_ts = acc.last_reading_ts
        first_since_start = barcode not in self._seen_since_start
        self._seen_since_start.add(barcode)
        update_result = accumulate_energy(
            acc,
            power=event.power,
            now=now,
            gap_threshold=ENERGY_GAP_THRESHOLD_SECONDS,
            low_power_threshold=ENERGY_LOW_POWER_THRESHOLD_W,
        )
        if update_result.discarded_gap_during_production:
            gap = update_result.gap_seconds
            short_gap = gap <= ESTIMATE_AFTER_SECONDS + HEALTH_CHECK_INTERVAL_SECONDS
            # A restart gap: the first reading since Home Assistant started,
            # from an optimizer that was still reporting when PyTap stopped
            # (so none of the gap was estimated from its string before).
            restart_gap = (
                first_since_start
                and self._stopped_at is not None
                and previous_ts is not None
                and previous_ts
                >= self._stopped_at - timedelta(seconds=ESTIMATE_AFTER_SECONDS)
                and gap <= RESTART_BRIDGE_MAX_SECONDS
            )
            if (
                self._estimate_enabled
                and barcode not in self._est_cursor
                and (short_gap or restart_gap)
            ):
                # A short gap that ended before the health check started
                # estimating, or a restart gap: bridge it with the
                # optimizer's own readings either side, counted as
                # estimated energy.
                add_estimated_energy(
                    acc, update_result.discarded_wh, now.date().isoformat()
                )
                self._schedule_save()
                self._log.debug(
                    "Bridged a %.0f s gap for %s: %.2f Wh estimated",
                    update_result.gap_seconds,
                    self._module_label(barcode),
                    update_result.discarded_wh,
                )
            else:
                self._log.debug(
                    "Discarded energy trapezoid for %s due to long gap during "
                    "production",
                    barcode,
                )

        # A report without a valid temperature (status bit 0x20 clear) or
        # RSSI (12-byte report, or 0) keeps the last valid value, as the CCA
        # does.
        previous = self.data["nodes"].get(barcode) or {}
        temperature = (
            event.temperature
            if event.temperature is not None
            else previous.get("temperature")
        )
        rssi = event.rssi if event.rssi is not None else previous.get("rssi")

        self.data["nodes"][barcode] = {
            "gateway_id": event.gateway_id,
            "node_id": event.node_id,
            "barcode": barcode,
            "name": module_meta.get(CONF_MODULE_NAME, barcode),
            "string": module_meta.get(CONF_MODULE_STRING, ""),
            "peak_power": peak_power,
            "voltage_in": event.voltage_in,
            "voltage_out": event.voltage_out,
            "current_in": event.current_in,
            "current_out": event.current_out,
            "power": event.power,
            "performance": round(performance, 2) if performance is not None else None,
            "temperature": temperature,
            "dc_dc_duty_cycle": event.dc_dc_duty_cycle,
            "rssi": rssi,
            "daily_energy_wh": round(acc.daily_energy_wh, 2),
            "total_energy_wh": round(acc.total_energy_wh, 2),
            "readings_today": acc.readings_today,
            "daily_reset_date": acc.daily_reset_date,
            "last_update": now.isoformat(),
        }
        # Only buffer readings in interval/averaged mode.  In live mode the
        # raw values are pushed immediately, so buffering would grow without
        # bound and never be consumed.
        if not self._live_reporting:
            self._reading_buffers.setdefault(barcode, []).append(
                (
                    time.monotonic(),
                    {
                        field: self.data["nodes"][barcode][field]
                        for field in _AVERAGED_FIELDS
                    },
                )
            )
        self._schedule_save()
        return True

    def _handle_infrastructure(self, event: InfrastructureEvent) -> bool:
        """Handle an infrastructure event — rebuild gateway/node mappings.

        Each InfrastructureEvent carries the *complete* current node table,
        so mappings are rebuilt from scratch rather than incrementally
        appended.  This purges stale entries that accumulate after overnight
        reconnections, gateway re-enumerations, or node-ID reassignments
        and would otherwise cause incorrect barcode resolution.

        Returns True (always modifies gateway data).
        """
        event_barcodes = [
            n.get("barcode", "?") for n in event.nodes.values() if n.get("barcode")
        ]
        has_nodes = bool(event.nodes)
        self._log.info(
            "Infrastructure event received: %d gateways, %d nodes (barcodes: %s)",
            len(event.gateways),
            len(event.nodes),
            ", ".join(event_barcodes) or "none",
        )

        # Only treat _infra_received as True once we have an event that
        # actually contains a node table.  Gateway-only events (0 nodes)
        # arrive early in the session before the gateway has sent the
        # full node table, and should not be treated as authoritative.
        first_infra_with_nodes = not self._infra_received and has_nodes
        if has_nodes:
            self._infra_received = True

        # Update gateways
        self.data["gateways"] = event.gateways

        # Rebuild barcode ↔ node_id mappings from scratch
        new_barcode_to_node: dict[str, int] = {}
        new_node_to_barcode: dict[int, str] = {}
        discovered_changed = False
        for node_id, node_info in event.nodes.items():
            barcode = node_info.get("barcode")
            if barcode:
                new_barcode_to_node[barcode] = node_id
                new_node_to_barcode[node_id] = barcode

                # Log discovery of unconfigured barcodes
                if barcode not in self._configured_barcodes:
                    if barcode not in self._discovered_barcodes:
                        self._discovered_barcodes.add(barcode)
                        discovered_changed = True
                        self._log.info(
                            "Discovered unconfigured Tigo optimizer barcode: %s "
                            "(node=%d). Add it to your PyTap module list to "
                            "start tracking.",
                            barcode,
                            node_id,
                        )

        if discovered_changed:
            self.data["discovered_barcodes"] = sorted(self._discovered_barcodes)

        # When the event has no nodes, preserve existing coordinator
        # mappings — they may have been loaded from saved state and are
        # better than nothing until a real node table replaces them.
        if not has_nodes:
            self._log.info(
                "Infrastructure event has no node table — keeping %d "
                "existing barcode mappings as fallback",
                len(self._barcode_to_node),
            )
            # Still schedule a save for gateway data changes
            if event.gateways:
                self._schedule_save()
            return True

        mappings_changed = (
            new_barcode_to_node != self._barcode_to_node
            or new_node_to_barcode != self._node_to_barcode
        )

        if mappings_changed:
            purged_barcodes = set(self._barcode_to_node) - set(new_barcode_to_node)
            if purged_barcodes:
                self._log.info(
                    "Purged %d stale barcode mappings: %s",
                    len(purged_barcodes),
                    purged_barcodes,
                )

        self._barcode_to_node = new_barcode_to_node
        self._node_to_barcode = new_node_to_barcode
        self._last_node_table = dt_util.utcnow()
        self._unidentified_nodes = set()

        # Reset pending counter now that resolution is possible
        if self._pending_power_reports > 0:
            self._log.info(
                "Node table received — %d power reports were pending "
                "barcode resolution",
                self._pending_power_reports,
            )
            self._pending_power_reports = 0

        configured_matched = set(new_barcode_to_node) & self._configured_barcodes
        configured_missing = self._configured_barcodes - set(new_barcode_to_node)

        if first_infra_with_nodes:
            self._log.info(
                "First node table this session — barcode "
                "resolution now active. %d/%d configured barcodes matched "
                "in node table.",
                len(configured_matched),
                len(self._configured_barcodes),
            )
            if configured_missing:
                self._log.warning(
                    "Configured barcodes NOT found in node table: %s. "
                    "Check that these barcodes are correct.",
                    ", ".join(sorted(configured_missing)),
                )
        elif mappings_changed and new_barcode_to_node:
            self._log.info(
                "Barcode mappings updated — %d/%d configured barcodes now "
                "matched in node table.",
                len(configured_matched),
                len(self._configured_barcodes),
            )
            if configured_missing:
                self._log.info(
                    "Configured barcodes still NOT found in node table: %s",
                    ", ".join(sorted(configured_missing)),
                )

        if mappings_changed or discovered_changed:
            self._schedule_save()

        return True

    def _handle_topology(self, event: TopologyEvent) -> bool:
        """Handle a topology event for matched nodes.

        Returns True if node data was updated.
        """
        barcode = self._node_to_barcode.get(event.node_id)
        if barcode and barcode in self._configured_barcodes:
            node_data = self.data["nodes"].get(barcode)
            if node_data:
                node_data["topology"] = event.to_dict()
                return True
        return False

    # -------------------------------------------------------------------
    #  Persistence helpers
    # -------------------------------------------------------------------

    def _build_averaged_snapshot(self, flush_ts: float) -> dict[str, Any]:
        """Return the data snapshot to push to Home Assistant.

        For each barcode in ``_reading_buffers``, computes a **time-weighted
        average** of numeric readings and writes the result into a copy of
        the node dict.  ``self.data["nodes"]`` is **not** mutated — the raw
        latest values are preserved there for persistence; only the returned
        snapshot carries averaged values.

        Time-weighted averaging weights each reading by the duration it was
        "active" (time until the next reading, or until ``flush_ts`` for the
        last one).  This gives a physically representative average when
        readings arrive at uneven intervals.

        ``flush_ts`` should be ``time.monotonic()`` at the moment of flush.

        Must be called before ``_reading_buffers`` is cleared.
        """
        snapshot_nodes = dict(self.data["nodes"])
        for barcode, readings in self._reading_buffers.items():
            node = snapshot_nodes.get(barcode)
            if node is None or not readings:
                continue

            if len(readings) == 1:
                # Single reading — no averaging needed
                _, fields = readings[0]
                avg_node = dict(node)
                for field in _AVERAGED_FIELDS:
                    avg_node[field] = fields[field]
            else:
                avg_node = dict(node)
                for field in _AVERAGED_FIELDS:
                    weighted_sum = 0.0
                    total_weight = 0.0
                    for i, (ts, fields) in enumerate(readings):
                        value = fields[field]
                        if value is None:
                            continue
                        # Weight = duration until next reading (or flush)
                        if i + 1 < len(readings):
                            weight = readings[i + 1][0] - ts
                        else:
                            weight = flush_ts - ts
                        if weight <= 0:
                            weight = 1e-6  # avoid zero-weight edge case
                        weighted_sum += value * weight
                        total_weight += weight
                    avg_node[field] = (
                        round(weighted_sum / total_weight, 3)
                        if total_weight > 0
                        else None
                    )

            if avg_node.get("power") is not None:
                peak = avg_node.get("peak_power", DEFAULT_PEAK_POWER)
                avg_node["performance"] = round(
                    (max(avg_node["power"], 0.0) / peak) * 100.0,
                    2,
                )
            else:
                avg_node["performance"] = None
            snapshot_nodes[barcode] = avg_node
        return {**self.data, "nodes": snapshot_nodes}

    def _build_node_payload(
        self,
        barcode: str,
        module_meta: dict[str, Any],
        node_id: int | None,
    ) -> dict[str, Any]:
        """Build a baseline node payload for restored/startup state."""
        return {
            "gateway_id": None,
            "node_id": node_id,
            "barcode": barcode,
            "name": module_meta.get(CONF_MODULE_NAME, barcode),
            "string": module_meta.get(CONF_MODULE_STRING, ""),
            "peak_power": module_meta.get(CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER),
            "voltage_in": None,
            "voltage_out": None,
            "current_in": None,
            "current_out": None,
            "power": None,
            "performance": None,
            "temperature": None,
            "dc_dc_duty_cycle": None,
            "rssi": None,
            "daily_energy_wh": 0.0,
            "total_energy_wh": 0.0,
            "readings_today": 0,
            "daily_reset_date": "",
            "last_update": None,
        }

    def _merge_snapshot_into_node(
        self,
        node_payload: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> None:
        """Merge persisted node snapshot fields into a baseline node payload."""
        for key in (
            "gateway_id",
            "node_id",
            "voltage_in",
            "voltage_out",
            "current_in",
            "current_out",
            "power",
            "performance",
            "temperature",
            "dc_dc_duty_cycle",
            "rssi",
            "daily_energy_wh",
            "total_energy_wh",
            "readings_today",
            "daily_reset_date",
            "last_update",
            "topology",
        ):
            if key in snapshot:
                node_payload[key] = snapshot[key]

    def _merge_energy_into_node(
        self,
        node_payload: dict[str, Any],
        acc: EnergyAccumulator,
    ) -> None:
        """Merge persisted accumulator values into a node payload."""
        node_payload["daily_energy_wh"] = round(acc.daily_energy_wh, 2)
        node_payload["total_energy_wh"] = round(acc.total_energy_wh, 2)
        node_payload["readings_today"] = acc.readings_today
        node_payload["daily_reset_date"] = acc.daily_reset_date

    async def _async_load_coordinator_state(self) -> None:
        """Load all persisted state (barcode mappings, discovered barcodes, parser state) from HA Store."""
        try:
            stored = await self._store.async_load()
        except Exception:
            self._log.warning(
                "Could not read saved PyTap state; starting without it",
                exc_info=True,
            )
            stored = None

        if stored is None:
            return

        if raw_saved_at := stored.get("saved_at"):
            try:
                self._stopped_at = datetime.fromisoformat(raw_saved_at)
            except (TypeError, ValueError):
                self._stopped_at = None

        # Restore barcode ↔ node_id mappings
        barcode_to_node = stored.get("barcode_to_node", {})
        for barcode, node_id in barcode_to_node.items():
            self._barcode_to_node[barcode] = int(node_id)
            self._node_to_barcode[int(node_id)] = barcode

        # Restore discovered barcodes
        discovered = stored.get("discovered_barcodes", [])
        self._discovered_barcodes = set(discovered)
        self.data["discovered_barcodes"] = sorted(self._discovered_barcodes)

        # Restore parser infrastructure state
        parser_state_data = stored.get("parser_state")
        if parser_state_data:
            try:
                self._persistent_state = PersistentState.from_dict(parser_state_data)
            except Exception:
                self._log.warning(
                    "Failed to restore parser state from store, starting "
                    "fresh; the node table will be relearned",
                    exc_info=True,
                )
                self._persistent_state = PersistentState()

        # Restore energy accumulation state
        today = dt_util.now().date().isoformat()
        for barcode, energy_data in stored.get("energy_data", {}).items():
            last_reading_ts = None
            if raw_ts := energy_data.get("last_reading_ts"):
                try:
                    last_reading_ts = datetime.fromisoformat(raw_ts)
                except (TypeError, ValueError):
                    last_reading_ts = None

            daily_reset_date = str(energy_data.get("daily_reset_date", ""))
            daily_energy_wh = float(energy_data.get("daily_energy_wh", 0.0))
            readings_today = int(energy_data.get("readings_today", 0))
            if daily_reset_date != today:
                daily_reset_date = today
                daily_energy_wh = 0.0
                readings_today = 0

            self._energy_state[barcode] = EnergyAccumulator(
                daily_energy_wh=daily_energy_wh,
                total_energy_wh=float(energy_data.get("total_energy_wh", 0.0)),
                daily_reset_date=daily_reset_date,
                last_power_w=float(energy_data.get("last_power_w", 0.0)),
                last_reading_ts=last_reading_ts,
                readings_today=readings_today,
                estimated_daily_wh=(
                    float(energy_data.get("estimated_daily_wh", 0.0))
                    if energy_data.get("daily_reset_date") == today
                    else 0.0
                ),
                estimated_total_wh=float(energy_data.get("estimated_total_wh", 0.0)),
            )

        # Restore last known node snapshots for configured barcodes, and
        # merge energy accumulator values where available.
        node_snapshots = stored.get("node_snapshots", {})
        for barcode in self._configured_barcodes:
            snapshot = node_snapshots.get(barcode)
            acc = self._energy_state.get(barcode)
            if not isinstance(snapshot, dict) and acc is None:
                continue

            module_meta = self._module_lookup.get(barcode, {})
            node_id = self._barcode_to_node.get(barcode)
            node_payload = self._build_node_payload(barcode, module_meta, node_id)

            if isinstance(snapshot, dict):
                self._merge_snapshot_into_node(node_payload, snapshot)

            if acc is not None:
                self._merge_energy_into_node(node_payload, acc)

            self.data["nodes"][barcode] = node_payload

        self._log.info(
            "Restored coordinator state: %d barcode mappings, %d discovered barcodes, "
            "%d gateway identities, %d energy states, %d node snapshots",
            len(barcode_to_node),
            len(discovered),
            len(self._persistent_state.gateway_identities),
            len(self._energy_state),
            len(node_snapshots),
        )

    async def _async_save_coordinator_state(self) -> None:
        """Save all state (barcode mappings, discovered barcodes, parser state) to HA Store."""
        self._unsaved_changes = False
        data = {
            # When PyTap last saved, i.e. roughly when it stopped hearing the
            # bus if Home Assistant restarts next; see restart gaps.
            "saved_at": dt_util.now().isoformat(),
            "barcode_to_node": {
                barcode: node_id for barcode, node_id in self._barcode_to_node.items()
            },
            "discovered_barcodes": sorted(self._discovered_barcodes),
            "parser_state": self._persistent_state.to_dict(),
            "energy_data": {
                barcode: {
                    "daily_energy_wh": acc.daily_energy_wh,
                    "daily_reset_date": acc.daily_reset_date,
                    "total_energy_wh": acc.total_energy_wh,
                    "readings_today": acc.readings_today,
                    "last_power_w": acc.last_power_w,
                    "last_reading_ts": (
                        acc.last_reading_ts.isoformat()
                        if acc.last_reading_ts is not None
                        else None
                    ),
                    "estimated_daily_wh": acc.estimated_daily_wh,
                    "estimated_total_wh": acc.estimated_total_wh,
                }
                for barcode, acc in self._energy_state.items()
            },
            "node_snapshots": {
                barcode: {
                    key: value
                    for key, value in node.items()
                    if key
                    in {
                        "gateway_id",
                        "node_id",
                        "voltage_in",
                        "voltage_out",
                        "current_in",
                        "current_out",
                        "power",
                        "performance",
                        "temperature",
                        "dc_dc_duty_cycle",
                        "rssi",
                        "daily_energy_wh",
                        "total_energy_wh",
                        "readings_today",
                        "daily_reset_date",
                        "last_update",
                        "topology",
                    }
                }
                for barcode, node in self.data["nodes"].items()
                if barcode in self._configured_barcodes and isinstance(node, dict)
            },
        }
        try:
            await self._store.async_save(data)
        except Exception:
            self._log.warning("Failed to save coordinator state", exc_info=True)

    def _schedule_save(self) -> None:
        """Schedule a throttled save of coordinator state.

        Uses a throttle pattern: if a save is already scheduled, let it fire
        rather than cancelling and restarting. This guarantees the store is
        written within SAVE_DELAY_SECONDS of the first change, even under
        continuous high-frequency updates.

        Safe to call from the executor thread — dispatches to the HA event loop.
        """
        self._unsaved_changes = True

        def _do_schedule() -> None:
            if self._save_task is not None:
                return  # Save already pending — let it fire on time
            self._save_task = self.hass.loop.call_later(
                SAVE_DELAY_SECONDS,
                lambda: self.hass.async_create_task(self._do_save()),
            )

        self.hass.loop.call_soon_threadsafe(_do_schedule)

    async def _do_save(self) -> None:
        """Execute the scheduled save and clear the timer handle."""
        self._save_task = None
        await self._async_save_coordinator_state()

    def reload_modules(self, modules: list[dict[str, Any]]) -> None:
        """Reload the module configuration (called from options flow).

        After updating the allowlist, checks whether any newly-configured
        barcodes already have a known node mapping from a previous
        infrastructure event.  If so, a placeholder entry is created in
        coordinator data so sensor entities can bind immediately instead
        of waiting for the next power report.
        """
        old_configured = set(self._configured_barcodes)
        self._modules = modules
        self._configured_barcodes = {
            m[CONF_MODULE_BARCODE] for m in modules if m.get(CONF_MODULE_BARCODE)
        }
        self._module_lookup = {
            m[CONF_MODULE_BARCODE]: m for m in modules if m.get(CONF_MODULE_BARCODE)
        }

        newly_added = self._configured_barcodes - old_configured
        already_resolved = newly_added & set(self._barcode_to_node)
        not_yet_resolved = newly_added - set(self._barcode_to_node)

        # Pre-populate node data for newly added barcodes that already
        # have a known node mapping so sensors can start immediately.
        for barcode in already_resolved:
            if barcode not in self.data["nodes"]:
                module_meta = self._module_lookup.get(barcode, {})
                now = dt_util.now()
                acc = self._energy_state.setdefault(
                    barcode,
                    EnergyAccumulator(daily_reset_date=now.date().isoformat()),
                )
                self.data["nodes"][barcode] = {
                    "gateway_id": None,
                    "node_id": self._barcode_to_node[barcode],
                    "barcode": barcode,
                    "name": module_meta.get(CONF_MODULE_NAME, barcode),
                    "string": module_meta.get(CONF_MODULE_STRING, ""),
                    "peak_power": module_meta.get(
                        CONF_MODULE_PEAK_POWER, DEFAULT_PEAK_POWER
                    ),
                    "voltage_in": None,
                    "voltage_out": None,
                    "current_in": None,
                    "current_out": None,
                    "power": None,
                    "performance": None,
                    "temperature": None,
                    "dc_dc_duty_cycle": None,
                    "rssi": None,
                    "daily_energy_wh": round(acc.daily_energy_wh, 2),
                    "total_energy_wh": round(acc.total_energy_wh, 2),
                    "readings_today": acc.readings_today,
                    "daily_reset_date": acc.daily_reset_date,
                    "last_update": None,
                }

        self._log.info(
            "Reloaded module config: tracking %d barcodes",
            len(self._configured_barcodes),
        )
        if already_resolved:
            self._log.info(
                "Newly added barcodes already resolved from saved mappings: %s",
                ", ".join(sorted(already_resolved)),
            )
        if not_yet_resolved:
            self._log.info(
                "Newly added barcodes not yet in node table (will resolve "
                "on next infrastructure event): %s",
                ", ".join(sorted(not_yet_resolved)),
            )
