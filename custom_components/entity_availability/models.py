"""Data models for Entity Availability."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .const import SignalQuality
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
    is_non_essential: bool = False
    suppress_until: datetime | None = None
    offline_since: datetime | None = None
    last_recovery: datetime | None = None
    last_downtime_seconds: float | None = None
    cooldown_start: datetime | None = None
    battery_level: int | None = None
    last_changed: datetime | None = None
    signal_level: int | None = None  # raw value from bound signal sensor
    signal_quality: SignalQuality | None = None  # "good" | "ok" | "poor" | None
    signal_unit: str | None = None  # unit for signal_level (e.g. "dBm", "%")
    # Collapse provenance: WHICH sensor supplied battery/signal (static config, not a
    # live value). Drives the device-collapse key so a wobbling reading can't reshuffle
    # rows. Written live each refresh, never persisted (recomputed on load).
    battery_source: str | None = None
    signal_source: str | None = None
    # transition tracker for EVENT_POOR_SIGNAL / EVENT_SIGNAL_OK
    prev_signal_poor: bool = False
    # Debounce state for the poor↔non-poor boundary (wall-clock). Transient like
    # signal_source — never persisted (storage serializes buckets only, never
    # DeviceState). signal_pending_poor = the candidate side of an in-flight
    # crossing (None = no crossing pending); signal_pending_since = when that
    # crossing was first seen. The crossing commits once it has persisted
    # SIGNAL_DEBOUNCE_SECONDS of real time, independent of how many refreshes
    # (push- or poll-driven) land in between.
    signal_pending_poor: bool | None = None
    signal_pending_since: datetime | None = None
    recently_offline_at: datetime | None = None
    # Reliability counters (all-time, event-driven — feed MTBF/MTTR).
    monitored_since: datetime | None = None
    offline_event_count: int = 0
    total_offline_seconds: float = 0.0
    # Wall-clock instant suppression began for this device (None = not suppressed).
    # Stamped at suppression onset, persisted so it survives restart. Used to book
    # only the OBSERVED (pre-suppression) portion of an outage that recovers while
    # suppressed — suppressed time is a gap in the availability metric, so counting
    # it as downtime would make MTTR disagree with availability%.
    suppressed_at: datetime | None = None
    # How far (wall-clock) the CURRENT outage's downtime has already been booked
    # into total_offline_seconds (None = nothing booked yet this outage). Set when
    # an outage's observed pre-suppression portion is booked at the suppression-END
    # edge (expiry/unsuppress while still offline); the eventual normal recovery
    # then books only the remainder [booked_until, now], never re-adding the
    # pre-portion. Keeps offline_since the TRUE physical start (card/display/payload
    # read it) while the reliability counter tracks its own progress. Cleared when
    # the outage ends. Persisted so a restart mid-post-suppression outage doesn't
    # re-book the pre-portion.
    reliability_booked_until: datetime | None = None


@dataclass
class EntityAvailabilityData:
    """Full data structure for a device group."""

    devices: dict[str, DeviceState] = field(default_factory=dict)
    buckets: dict[str, list[AvailabilityBucket]] = field(default_factory=dict)
