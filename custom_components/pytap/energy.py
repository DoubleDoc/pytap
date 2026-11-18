"""Energy accumulation helpers for PyTap.

This module is intentionally Home Assistant-independent so it can be unit-tested
without coordinator/event-loop setup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .const import ENERGY_GAP_THRESHOLD_SECONDS, ENERGY_LOW_POWER_THRESHOLD_W


@dataclass
class EnergyAccumulator:
    """Per-barcode energy accumulation state."""

    daily_energy_wh: float = 0.0
    total_energy_wh: float = 0.0
    daily_reset_date: str = ""
    last_power_w: float = 0.0
    last_reading_ts: datetime | None = None
    readings_today: int = 0
    # Energy estimated while the optimizer was silent (string/total only;
    # never shown on the optimizer's own sensors).
    estimated_daily_wh: float = 0.0
    estimated_total_wh: float = 0.0


@dataclass(frozen=True)
class EnergyUpdateResult:
    """Result metadata for a single accumulation step."""

    increment_wh: float = 0.0
    discarded_gap_during_production: bool = False
    # The trapezoid that was left out because the gap was too long, and the
    # gap's length, so a caller can count a short gap as estimated energy.
    discarded_wh: float = 0.0
    gap_seconds: float = 0.0


def accumulate_energy(
    acc: EnergyAccumulator,
    power: float,
    now: datetime,
    gap_threshold: int = ENERGY_GAP_THRESHOLD_SECONDS,
    low_power_threshold: float = ENERGY_LOW_POWER_THRESHOLD_W,
) -> EnergyUpdateResult:
    """Integrate a power reading into the accumulator.

    Uses trapezoidal integration over the interval from the previous reading to
    ``now``. Mutates ``acc`` in place.
    """
    power_w = max(power, 0.0)
    today = now.date().isoformat()

    if acc.daily_reset_date != today:
        reset_daily(acc, today)

    increment_wh = 0.0
    discarded_gap_during_production = False
    discarded_wh = 0.0
    delta_seconds = 0.0

    if acc.last_reading_ts is not None:
        delta_seconds = (now - acc.last_reading_ts).total_seconds()
        previous_power_w = max(acc.last_power_w, 0.0)

        if 0 < delta_seconds <= gap_threshold:
            increment_wh = ((previous_power_w + power_w) / 2.0) * (
                delta_seconds / 3600.0
            )
            acc.daily_energy_wh += increment_wh
            acc.total_energy_wh += increment_wh
        elif delta_seconds > gap_threshold and (
            previous_power_w > low_power_threshold or power_w > low_power_threshold
        ):
            discarded_gap_during_production = True
            discarded_wh = ((previous_power_w + power_w) / 2.0) * (
                delta_seconds / 3600.0
            )

    acc.last_power_w = power_w
    acc.last_reading_ts = now
    acc.readings_today += 1

    return EnergyUpdateResult(
        increment_wh=increment_wh,
        discarded_gap_during_production=discarded_gap_during_production,
        discarded_wh=discarded_wh,
        gap_seconds=delta_seconds,
    )


def reset_daily(acc: EnergyAccumulator, today: str) -> None:
    """Start a new day: zero the daily measured and estimated energy."""
    acc.daily_energy_wh = 0.0
    acc.estimated_daily_wh = 0.0
    acc.readings_today = 0
    acc.daily_reset_date = today


def add_estimated_energy(acc: EnergyAccumulator, energy_wh: float, today: str) -> None:
    """Add energy estimated for a silent optimizer."""
    if energy_wh <= 0:
        return
    if acc.daily_reset_date != today:
        reset_daily(acc, today)
    acc.estimated_daily_wh += energy_wh
    acc.estimated_total_wh += energy_wh
