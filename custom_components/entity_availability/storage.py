"""Availability storage using 5-minute buckets."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.util import dt as dt_util

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

    def reset(self, entity_ids: list[str] | None = None) -> None:
        """Drop availability buckets. entity_ids=None clears everything."""
        if entity_ids is None:
            self._buckets.clear()
        else:
            for eid in entity_ids:
                self._buckets.pop(eid, None)

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

        window_hours = self._window_to_hours(window)
        if window == "today":
            # Calendar day: midnight in HA's local timezone, converted to UTC for
            # comparison against the UTC bucket grid. dt_util handles DST (23/25h
            # days). Known ceiling: buckets sit on a UTC 5-min grid, so in tzs with a
            # sub-hour offset (e.g. +5:30/+5:45) local midnight lands mid-bucket and
            # the start edge can be off by up to one 5-min bucket — cosmetic for a
            # daily KPI, not worth grid rework.
            cutoff = dt_util.start_of_local_day(dt_util.as_local(now)).astimezone(
                timezone.utc
            )
        else:
            # Rolling lookback (24h / 3d / 5d / 7d) — timezone-agnostic, DST-safe.
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

        # Require at least 1 bucket for the ~day windows ("today"/"24h"), 10% for
        # longer windows. "24h" matches the old "today" behaviour (value shows
        # almost immediately) so it is a faithful replacement for users who want
        # the rolling lookback back.
        if window in ("today", "24h"):
            min_required = 1
        else:
            expected_buckets = window_hours * 12  # 12 buckets per hour
            min_required = max(1, int(expected_buckets * 0.1))
        if len(relevant_buckets) < min_required:
            return None

        # Prefer COMPLETED buckets for the ratio: the in-progress bucket's
        # online_seconds climbs every poll while real time also grows, so mixing it
        # with completed history makes the whole-window rounded % cross a 0.1 boundary
        # every poll — a recorder write each poll with no real change (~7000 rows/day
        # observed). Averaging only COMPLETED buckets keeps the value STABLE between
        # polls: it changes only when membership changes (a bucket closing, or the
        # trailing bucket evicted as the cutoff advances) — real ~per-5-min data
        # points, not a per-poll sawtooth. Trade-off: the % lags reality by up to one
        # bucket (≤5 min); offline_count / the binary sensors give instant status.
        newest_start = self._get_interval_start(now)
        completed = [b for b in relevant_buckets if b.interval_start != newest_start]

        if completed:
            total_online = sum(b.online_seconds for b in completed)
            total_time = sum(b.total_seconds for b in completed)
        else:
            # No completed bucket in the window yet — the first ≤5 min after setup,
            # AND the first ≤5 min after a calendar-"today" rollover (the window
            # resets at local midnight, emptying `completed` every day). The
            # in-progress bucket is all we have; use it so the sensor shows a value
            # immediately instead of "unknown".
            #
            # Its denominator MUST be the ELAPSED time in the bucket, not the full
            # BUCKET_INTERVAL. online_seconds only accrues while online and tracks
            # elapsed, so online/elapsed ≈ the true ratio (≈100% for a healthy
            # device) from the first poll. Dividing by the full 300 s instead made a
            # healthy device read ~0% at the bucket start and ramp ~10 %/poll as it
            # filled — the spurious "today% collapses to ~0% at midnight then climbs"
            # artifact users saw after calendar-today shipped.
            #
            # relevant_buckets[-1] is provably the in-progress (newest-start)
            # bucket here, independent of list ordering: this branch runs only
            # when `completed` is empty, i.e. EVERY relevant bucket has
            # interval_start == newest_start; get_or_create_bucket dedups by
            # interval (one bucket per 5-min slot), so exactly one such bucket
            # exists and [-1] is it. (A refactor that breaks either the dedup or
            # this branch guard would reintroduce an ordering assumption here.)
            b = relevant_buckets[-1]
            elapsed = (now - b.interval_start).total_seconds()
            total_online = b.online_seconds
            total_time = min(elapsed, b.total_seconds)

        if total_time <= 0:
            return None

        # Clamp to 100%: in the elapsed-denominator (in-progress-only) path,
        # online_seconds can marginally exceed elapsed on poll-timing skew
        # (a poll books ~30 s of online slightly before the wall clock catches up),
        # which would otherwise print 100.x%. The completed path is already ≤100%.
        ratio = min(total_online / total_time, 1.0)
        return round(ratio * 100, 1)

    def get_entity_availability(
        self, entity_id: str, windows: list[str], now: datetime
    ) -> dict[str, float | None]:
        """Get availability for all configured windows."""
        return {w: self.get_availability(entity_id, w, now) for w in windows}

    def to_dict(
        self, now: datetime, retention_seconds: float, entity_ids: Iterable[str]
    ) -> dict[str, Any]:
        """Serialize to dict for storage (synchronous — builds on the caller's thread).

        Equivalent to ``encode_snapshot(self.snapshot(...))``. Kept for callers/tests
        that want the whole build in one call. The coordinator's save path instead
        calls ``snapshot`` on the event loop and ``encode_snapshot`` in the executor
        so the expensive isoformat/dict build does not block the loop (see
        ``coordinator._async_save_storage``).

        Only buckets within ``retention_seconds`` of ``now``, for entities still in
        ``entity_ids``, are serialized. retention_seconds <= 0 serializes nothing.
        """
        return self.encode_snapshot(self.snapshot(now, retention_seconds, entity_ids))

    def snapshot(
        self, now: datetime, retention_seconds: float, entity_ids: Iterable[str]
    ) -> dict[str, list[tuple[datetime, float]]]:
        """Freeze the in-window buckets into a detached, executor-safe structure.

        MUST run on the event loop. Captures ``online_seconds`` BY VALUE (not by
        reference): ``record_online`` mutates the current bucket's ``online_seconds``
        in place every tick, so a shallow copy of the lists would let the executor
        read a still-climbing value. We read the float here, under the loop, so the
        returned structure never aliases any live bucket — ``encode_snapshot`` can
        then run in the executor with no race against ``_buckets``.

        ``interval_start`` is immutable after construction, so capturing its
        reference is safe.
        """
        if retention_seconds <= 0:
            return {}
        monitored = set(entity_ids)
        cutoff = now - timedelta(seconds=retention_seconds)
        frozen: dict[str, list[tuple[datetime, float]]] = {}
        for entity_id, buckets in self._buckets.items():
            if entity_id not in monitored:
                continue
            # Capture (interval_start, online_seconds) BY VALUE. Python floats are
            # immutable and record_online does `online_seconds += ...` which REBINDS
            # the attribute to a NEW float — it never mutates the object captured
            # here — so a bare reference capture already freezes the value. round()
            # is NOT needed for the freeze; it is deferred to encode_snapshot so the
            # ~1M-float rounding runs off-loop with the isoformat build, not here.
            rows = [
                (b.interval_start, b.online_seconds)
                for b in buckets
                if b.interval_start >= cutoff
            ]
            if rows:  # skip monitored-but-dataless entities — keep the file minimal
                frozen[entity_id] = rows
        return frozen

    @staticmethod
    def encode_snapshot(
        frozen: dict[str, list[tuple[datetime, float]]],
    ) -> dict[str, Any]:
        """Build the on-disk dict from a ``snapshot``. Executor-safe (no ``self``).

        This is the expensive part — ``isoformat`` + ``round`` + dict construction
        per bucket — and it touches only ``frozen`` (which the event loop never
        mutates), so it runs via ``async_add_executor_job`` off the loop. Output is
        byte-identical to the former on-loop ``to_dict`` ({"s": iso, "o": round}).
        """
        return {
            entity_id: [{"s": s.isoformat(), "o": round(o, 1)} for (s, o) in rows]
            for entity_id, rows in frozen.items()
        }

    def prune(
        self, now: datetime, retention_seconds: float, entity_ids: Iterable[str]
    ) -> None:
        """Drop in-RAM buckets outside the retention window or for unmonitored entities.

        Mirrors the to_dict filter so RAM, disk, and serialize cost all track the
        longest selected window. Must never be MORE aggressive than the persisted
        retention, or an options-widen (which reloads the coordinator from the file)
        would lose data the file still held. retention_seconds <= 0 clears everything.
        """
        if retention_seconds <= 0:
            self._buckets.clear()
            return
        monitored = set(entity_ids)
        cutoff = now - timedelta(seconds=retention_seconds)
        for entity_id in list(self._buckets):
            if entity_id not in monitored:
                del self._buckets[entity_id]
                continue
            kept = [b for b in self._buckets[entity_id] if b.interval_start >= cutoff]
            if kept:
                self._buckets[entity_id] = kept
            else:  # drop the key entirely rather than hold an empty list
                del self._buckets[entity_id]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvailabilityStorage:
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
        """Convert window string to hours (read-side span)."""
        if window == "today":
            return 24
        if window == "24h":
            return 24
        if window == "3d":
            return 72
        if window == "5d":
            return 120
        if window == "7d":
            return 168
        _LOGGER.warning("Unknown availability window '%s', defaulting to 24h", window)
        return 24

    @staticmethod
    def _retention_hours(window: str) -> int:
        """Hours of buckets to RETAIN for a window.

        Differs from the read span only for "today": a calendar day reaches 25h on a
        DST fall-back day, so retaining 24h would prune the oldest ~45min the read
        still needs. Every other window's read is a fixed-duration lookback fully
        covered by its own hours.
        """
        if window == "today":
            return 25
        return AvailabilityStorage._window_to_hours(window)
