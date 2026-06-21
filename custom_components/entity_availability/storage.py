"""Availability storage using 5-minute buckets."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .const import BUCKET_INTERVAL, BUCKETS_MAX

_LOGGER = logging.getLogger(__name__)


class AvailabilityBucket:
    """One 5-minute interval of availability data for a device."""

    __slots__ = ("interval_start", "online_seconds", "total_seconds")

    def __init__(self, interval_start: datetime, online_seconds: float = 0.0) -> None:
        """Initialize bucket."""
        self.interval_start = interval_start
        self.online_seconds = online_seconds
        self.total_seconds = float(BUCKET_INTERVAL)


class AvailabilityStorage:
    """Manages 5-minute availability buckets per device."""

    def __init__(self) -> None:
        """Initialize storage."""
        self._buckets: dict[str, list[AvailabilityBucket]] = {}

    @property
    def buckets(self) -> dict[str, list[AvailabilityBucket]]:
        """Return the buckets."""
        return self._buckets

    def _get_interval_start(self, now: datetime) -> datetime:
        """Get the start of the current 5-minute interval."""
        minute = (now.minute // 5) * 5
        return now.replace(minute=minute, second=0, microsecond=0)

    def get_or_create_bucket(self, entity_id: str, now: datetime) -> AvailabilityBucket:
        """Get the current interval's bucket, creating it if needed."""
        if entity_id not in self._buckets:
            self._buckets[entity_id] = []

        interval_start = self._get_interval_start(now)
        buckets = self._buckets[entity_id]

        if buckets and buckets[-1].interval_start == interval_start:
            return buckets[-1]

        bucket = AvailabilityBucket(interval_start=interval_start)
        buckets.append(bucket)
        _LOGGER.debug(
            "New bucket for %s at %s (total=%d)",
            entity_id,
            interval_start,
            len(buckets),
        )

        while len(buckets) > BUCKETS_MAX:
            buckets.pop(0)
            _LOGGER.debug(
                "Pruned oldest bucket for %s (now %d)", entity_id, len(buckets)
            )

        return bucket

    def record_online(self, entity_id: str, seconds: float, now: datetime) -> None:
        """Record online seconds for the current interval."""
        if seconds <= 0:
            return
        bucket = self.get_or_create_bucket(entity_id, now)
        remaining = bucket.total_seconds - bucket.online_seconds
        bucket.online_seconds += min(seconds, remaining)

    def record_offline(self, entity_id: str, seconds: float, now: datetime) -> None:
        """Record offline seconds (ensures bucket exists; offline is implicit)."""
        if seconds <= 0:
            return
        self.get_or_create_bucket(entity_id, now)

    def get_availability(
        self, entity_id: str, window: str, now: datetime
    ) -> float | None:
        """Calculate availability % for a time window.

        Returns None if insufficient data.
        """
        if entity_id not in self._buckets or not self._buckets[entity_id]:
            return None

        if window == "today":
            # Rolling 24h window — timezone-agnostic and consistent across DST changes.
            cutoff = now - timedelta(hours=24)
        else:
            window_hours = self._window_to_hours(window)
            cutoff = now - timedelta(hours=window_hours)

        relevant_buckets = [
            b for b in self._buckets[entity_id] if b.interval_start >= cutoff
        ]

        if not relevant_buckets:
            _LOGGER.debug(
                "No buckets in window '%s' for %s (cutoff=%s)",
                window,
                entity_id,
                cutoff,
            )
            return None

        # Require at least 1 bucket for "today", 10% for longer windows
        if window == "today":
            min_required = 1
        else:
            expected_buckets = window_hours * 12  # 12 buckets per hour
            min_required = max(1, int(expected_buckets * 0.1))
        if len(relevant_buckets) < min_required:
            return None

        total_online = sum(b.online_seconds for b in relevant_buckets)
        total_time = sum(b.total_seconds for b in relevant_buckets)

        if total_time == 0:
            return None

        return round((total_online / total_time) * 100, 1)

    def get_entity_availability(
        self, entity_id: str, windows: list[str], now: datetime
    ) -> dict[str, float | None]:
        """Get availability for all configured windows."""
        return {w: self.get_availability(entity_id, w, now) for w in windows}

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage."""
        result: dict[str, Any] = {}
        for entity_id, buckets in self._buckets.items():
            result[entity_id] = [
                {
                    "s": b.interval_start.isoformat(),
                    "o": round(b.online_seconds, 1),
                }
                for b in buckets
            ]
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AvailabilityStorage":
        """Deserialize from dict."""
        storage = cls()
        for entity_id, buckets_data in data.items():
            buckets: list[AvailabilityBucket] = []
            try:
                for b in buckets_data:
                    try:
                        online = float(b["o"])
                        interval_start = datetime.fromisoformat(b["s"])
                        if interval_start.tzinfo is None:
                            interval_start = interval_start.replace(tzinfo=timezone.utc)
                        bucket = AvailabilityBucket(
                            interval_start=interval_start,
                            online_seconds=min(online, float(BUCKET_INTERVAL)),
                        )
                        buckets.append(bucket)
                    except (KeyError, ValueError, TypeError):
                        continue
            except TypeError:
                continue
            storage._buckets[entity_id] = buckets
        return storage

    @staticmethod
    def _window_to_hours(window: str) -> int:
        """Convert window string to hours."""
        if window == "today":
            return 24
        if window == "3d":
            return 72
        if window == "5d":
            return 120
        if window == "7d":
            return 168
        _LOGGER.warning("Unknown availability window '%s', defaulting to 24h", window)
        return 24
