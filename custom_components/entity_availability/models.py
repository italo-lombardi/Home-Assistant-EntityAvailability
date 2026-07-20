"""Data models for Entity Availability."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .storage import AvailabilityBucket


@dataclass
class DeviceState:
    """Tracks the state of a single monitored device."""

    entity_id: str
    is_offline: bool = False
    is_degraded: bool = False
    is_stale: bool = False
    is_low_battery: bool = False
    is_suppressed: bool = False
    suppress_until: datetime | None = None
    offline_since: datetime | None = None
    last_recovery: datetime | None = None
    last_downtime_seconds: float | None = None
    cooldown_start: datetime | None = None
    battery_level: int | None = None
    last_changed: datetime | None = None
    recently_offline_at: datetime | None = None
    # Reliability counters (all-time, event-driven — feed MTBF/MTTR).
    monitored_since: datetime | None = None
    offline_event_count: int = 0
    total_offline_seconds: float = 0.0


@dataclass
class EntityAvailabilityData:
    """Full data structure for a device group."""

    devices: dict[str, DeviceState] = field(default_factory=dict)
    buckets: dict[str, list[AvailabilityBucket]] = field(default_factory=dict)
