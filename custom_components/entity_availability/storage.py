"""Availability storage using 5-minute buckets."""

from __future__ import annotations

import logging
import math
from bisect import bisect_left
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
        # Memo for the COMPLETED-bucket sums per (entity_id, window). The completed
        # set is invariant between bucket closings: it changes only when a bucket
        # closes (newest grid slot advances), when the rolling cutoff crosses a
        # 5-min boundary (a trailing bucket leaves the window), or when the list
        # length changes (append on close/backward-clock, pop on prune/BUCKETS_MAX).
        # get_availability is called every ~30 s tick but the completed sum over
        # ~2000 buckets only changes ~once per 5 min, so ~90% of the walks re-sum
        # an identical value. We cache (total_online, total_time, completed_count,
        # relevant_count) keyed on a fingerprint of exactly those three triggers
        # and reuse it on a hit, recomputing only the cheap O(1) in-progress term
        # fresh each call (its online_seconds climbs per poll). Miss → full walk +
        # refill, so a stale key can never return a wrong number, only a slower one.
        # ponytail: flat key tuple, no per-entity object; cleared wholesale on any
        # structural reset below.
        self._avail_sum_cache: dict[
            tuple[str, str], tuple[tuple, float, float, int, int]
        ] = {}

    @property
    def buckets(self) -> dict[str, list[AvailabilityBucket]]:
        """Return the buckets."""
        return self._buckets

    def reset(self, entity_ids: list[str] | None = None) -> None:
        """Drop availability buckets. entity_ids=None clears everything."""
        if entity_ids is None:
            self._buckets.clear()
            self._avail_sum_cache.clear()
        else:
            for eid in entity_ids:
                self._buckets.pop(eid, None)
                # Drop every (eid, window) completed-sum entry for this entity so a
                # later re-created entity cannot hit a stale sum whose fingerprint
                # happens to collide. ponytail: windows are few, scan is trivial.
                for key in [k for k in self._avail_sum_cache if k[0] == eid]:
                    del self._avail_sum_cache[key]

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
        """Calculate availability % for a time window. None if insufficient data."""
        components = self._availability_components(entity_id, window, now)
        if components is None:
            return None
        total_online, total_time = components
        # Clamp to 100%: in the elapsed-denominator (in-progress-only) path,
        # online_seconds can marginally exceed elapsed on poll-timing skew.
        ratio = min(total_online / total_time, 1.0)
        return round(ratio * 100, 1)

    def availability_components(
        self, entity_id: str, window: str, now: datetime
    ) -> tuple[float, float] | None:
        """Public: (online_seconds, total_seconds) for a window, or None.

        The time-weighted GROUP availability sums these across members then divides
        once. Shares the same per-entity completed-sum cache as get_availability.
        """
        return self._availability_components(entity_id, window, now)

    def _availability_components(
        self, entity_id: str, window: str, now: datetime
    ) -> tuple[float, float] | None:
        """Return (online_seconds, total_seconds) for a window, or None if insufficient
        data. The time-weighted group availability sums these across members then
        divides ONCE, so a member with little history contributes proportionally
        little weight (no equal-weight-mean swing when membership changes). The
        single-entity percentage is this pair divided + clamped (see get_availability).
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

        # The COMPLETED-bucket sums (total_online/total_time/completed_count and
        # relevant_count) are invariant between structural changes to the window:
        # they move only when a bucket closes (newest grid slot advances), when the
        # rolling cutoff crosses the oldest in-window bucket (a trailing bucket
        # leaves the window), or when the list mutates (an append on close or a
        # backward wall-clock step — get_or_create_bucket only dedups against
        # buckets[-1] — or a prune/BUCKETS_MAX pop). The fingerprint captures these:
        #   (newest_start, first_idx, len(buckets), tail_completed_online)
        # - newest_start → a bucket closed.
        # - first_idx = bisect_left(cutoff) → the rolling cutoff crossed a boundary
        #   (an eviction). bisect is O(log n) (not an O(n) front scan, which would
        #   cost what the memo saves on narrow windows like "today"). bisect is only
        #   valid on a SORTED list; see the monotonic guard below.
        # - len → any append/pop.
        # - tail_completed_online → the ONE content change the membership terms
        #   miss: a COMPLETED bucket growing IN PLACE. record_online tops up
        #   get_or_create_bucket's return = buckets[-1] when the record slot matches
        #   it. Read `now` is minute-truncated (sensor.py) and independent of record
        #   `now` (coordinator.py), so near a 5-min boundary / on a backward step /
        #   after a backward-clock dup-append, buckets[-1] can be COMPLETED w.r.t.
        #   this read yet still grow — len/first_idx/newest_start all hold. Fold its
        #   online_seconds, but ONLY when buckets[-1] is completed (!= newest_start);
        #   on the happy path buckets[-1] IS the in-progress bucket (grows every
        #   tick) so folding it would miss every poll — there the term is None.
        #
        # MONOTONIC GUARD: a backward wall-clock step appends an interval_start
        # SMALLER than the current tail (get_or_create only dedups vs buckets[-1]),
        # leaving the list non-monotonic. bisect_left on a non-monotonic list returns
        # a garbage-but-STABLE index, so as the rolling cutoff sweeps past the
        # out-of-order bucket the true membership changes while first_idx/newest/len
        # all hold → a stale HIT (verified: 50%→should-be-100% sustained). A single
        # append can only invert the tail, but a later forward append re-sorts the
        # tail while the earlier inversion persists mid-list, so a tail-only check is
        # NOT enough. Instead the full walk (the miss path, order-independent — it
        # filters by interval_start vs cutoff per element) records whether the list
        # was monotonic for free; the cache is populated AND trusted only when the
        # last walk saw it sorted. An unsorted list simply never caches (always full
        # walk) until it self-heals (the out-of-order bucket evicts, or a reload
        # re-sorts via snapshot) — rare backward-clock case, zero happy-path cost.
        buckets = self._buckets[entity_id]
        newest_start = self._get_interval_start(now)
        first_idx = bisect_left(buckets, cutoff, key=lambda b: b.interval_start)
        last = buckets[-1] if buckets else None
        tail_completed_online = (
            last.online_seconds
            if last is not None and last.interval_start != newest_start
            else None
        )
        fingerprint = (newest_start, first_idx, len(buckets), tail_completed_online)

        cache_key = (entity_id, window)
        cached = self._avail_sum_cache.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            _fp, total_online, total_time, completed_count, relevant_count = cached
            # Re-scan ONLY for the in-progress bucket (slot == newest_start). Scan
            # from the TAIL: the list is append-ascending so the newest slot sits
            # at/near the end — this is the O(1) that makes the cache hit cheap
            # (a front scan would re-walk all ~2000 buckets, defeating the memo).
            # Bound by interval_start == newest_start equality, not list[-1], so a
            # backward-clock bucket appended AFTER the in-progress one (an older
            # slot at the tail) is correctly skipped rather than mistaken for it.
            in_progress = None
            for b in reversed(buckets):
                if b.interval_start == newest_start:
                    in_progress = b  # already guaranteed >= cutoff (== newest)
                    break
        else:
            # Miss (first call, bucket closed, eviction, or structural mutation):
            # full single pass, then refill the completed sums for reuse.
            relevant_count = 0
            completed_count = 0
            total_online = 0.0
            total_time = 0.0
            in_progress = None  # the newest-start bucket, if present in-window
            prev_start = None
            monotonic = True
            for b in buckets:
                # Track sortedness for free during the walk the miss already does.
                # Any backward-clock append lands a smaller interval_start than its
                # predecessor; bisect (used for first_idx) is unsound on such a list,
                # so we must NOT populate the cache from this walk.
                if prev_start is not None and b.interval_start < prev_start:
                    monotonic = False
                prev_start = b.interval_start
                if b.interval_start < cutoff:
                    continue
                relevant_count += 1
                # Prefer COMPLETED buckets for the ratio: the in-progress bucket's
                # online_seconds climbs every poll while real time also grows, so
                # mixing it with completed history makes the whole-window rounded %
                # cross a 0.1 boundary every poll — a recorder write each poll with
                # no real change (~7000 rows/day observed). Summing only COMPLETED
                # buckets keeps the value STABLE between polls: it changes only when
                # membership changes (a bucket closing, or the trailing bucket
                # evicted as the cutoff advances) — real ~per-5-min data points, not
                # a per-poll sawtooth. Trade-off: the % lags reality by up to one
                # bucket (≤5 min); offline_count / the binary sensors give instant
                # status. A bucket is completed iff its interval_start differs from
                # the newest (in-progress) slot — duplicate same-slot buckets (a
                # backward wall-clock step) are each counted individually,
                # matching the old per-object filter.
                if b.interval_start != newest_start:
                    completed_count += 1
                    total_online += b.online_seconds
                    total_time += b.total_seconds
                else:
                    in_progress = b
            # Store the completed sums (NOT the in-progress term) ONLY when the list
            # is monotonic — otherwise first_idx (bisect) is unsound and a future
            # HIT could serve a stale pre-eviction sum. relevant_count is frozen
            # with the sums: it only changes when window membership does, which the
            # fingerprint tracks (on a monotonic list). An unsorted list self-heals
            # (the out-of-order bucket evicts, or a reload re-sorts) and caching
            # resumes; until then every read is a correct full walk.
            if monotonic:
                self._avail_sum_cache[cache_key] = (
                    fingerprint,
                    total_online,
                    total_time,
                    completed_count,
                    relevant_count,
                )

        if relevant_count == 0:
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
        if relevant_count < min_required:
            return None

        if completed_count == 0:
            # No completed bucket in the window yet — the first ≤5 min after
            # setup, AND the first ≤5 min after a calendar-"today" rollover (the
            # window resets at local midnight, emptying the completed set every
            # day). The in-progress bucket is all we have; use it so the sensor
            # shows a value immediately instead of "unknown".
            #
            # Its denominator MUST be the ELAPSED time in the bucket, not the
            # full BUCKET_INTERVAL. online_seconds only accrues while online and
            # tracks elapsed, so online/elapsed ≈ the true ratio (≈100% for a
            # healthy device) from the first poll. Dividing by the full 300 s
            # instead made a healthy device read ~0% at the bucket start and ramp
            # ~10 %/poll as it filled — the spurious "today% collapses to ~0% at
            # midnight then climbs" artifact users saw after calendar-today
            # shipped.
            #
            # `in_progress` is provably non-None here: completed_count == 0 means
            # EVERY in-window bucket has interval_start == newest_start, and
            # relevant_count > 0 (guarded above), so at least one such bucket was
            # seen and bound to `in_progress`. Binding by equality (not list[-1])
            # is robust to the backward-clock duplicate case.
            b = in_progress
            elapsed = (now - b.interval_start).total_seconds()
            total_online = b.online_seconds
            # Floor the denominator at total_online. The sensor reads with a
            # minute-truncated `now`, so at a calendar-"today" rollover that
            # lands on a 5-min bucket boundary (e.g. 00:00 UTC) the fresh
            # bucket's interval_start == the truncated read instant → elapsed ==
            # 0 for the whole first minute, while record_online has already
            # booked this poll's online seconds. Without the floor, total_time =
            # min(0, 300) = 0 → the `<= 0` guard returned None → every today
            # sensor flashed "unknown" for 60 s at midnight (card hid the bar).
            # You cannot accrue N online-seconds in fewer than N elapsed-seconds,
            # so total_time must be ≥ total_online; this keeps total_time > 0
            # whenever online > 0, even when elapsed rounds to 0. A
            # genuinely-offline fresh bucket (online == 0, elapsed == 0) still
            # yields total_time == 0 → None below, unchanged — the floor only
            # ever raises a denominator that was spuriously below the online
            # seconds already booked, so it can never hide a real outage (an
            # offline device books 0 online).
            total_time = max(min(elapsed, b.total_seconds), total_online)

        if total_time <= 0:
            return None

        return (total_online, total_time)

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
            # Sort ascending by interval_start AND collapse duplicate interval_starts:
            # the v2 on-disk format is index-based (decode reconstructs each timestamp
            # as base + i*BUCKET_INTERVAL), so rows MUST be in strict grid order with
            # one row per 5-min slot or decode mislabels them. Buckets are normally
            # appended in time order with get_or_create_bucket deduping the CURRENT
            # slot, but a backward wall-clock step (NTP correction / VM resume) can
            # append an earlier-start bucket after a later one AND re-create a slot that
            # already has a bucket — two rows sharing an interval_start would otherwise
            # be encoded into consecutive array slots, shifting the duplicate forward one
            # grid step on decode (even into the future). Fold same-slot rows keeping the
            # last value (matches get_or_create_bucket's last-write-wins on buckets[-1]).
            by_slot: dict[datetime, float] = {}
            for b in buckets:
                if b.interval_start >= cutoff:
                    by_slot[b.interval_start] = b.online_seconds
            rows = sorted(by_slot.items(), key=lambda r: r[0])
            if rows:  # skip monitored-but-dataless entities — keep the file minimal
                frozen[entity_id] = rows
        return frozen

    @staticmethod
    def encode_snapshot(
        frozen: dict[str, list[tuple[datetime, float]]],
    ) -> dict[str, Any]:
        """Build the on-disk dict from a ``snapshot``. Executor-safe (no ``self``).

        Compact format (``"v": 2``): one shared ``base`` timestamp + a dense
        per-entity array of ``online_seconds``. Buckets sit on a fixed 5-min UTC grid
        (``_get_interval_start``), so index ``i`` in an entity's array maps to
        ``interval_start = base + i * BUCKET_INTERVAL``; the full ISO string per
        bucket (the old {"s","o"} shape) is redundant and is dropped.

        Size: ~75-90% smaller than the old {"s","o"} format for a CONTINUOUSLY-running
        HA on disk (measured on a real install; HA Store writes indented JSON, and the
        exact figure depends on how many buckets hold a whole-second value) — the
        coordinator records a bucket for every monitored entity every tick
        (``_async_update_data`` sweeps all entities, record_online/offline either way),
        so a healthy instance's arrays are dense (no nulls) and the per-bucket ISO
        string + key overhead is what the compact form removes. An INTERMITTENTLY-run
        instance (e.g. a dev box that isn't always on) stores explicit ``None`` gaps
        for the slots when HA ITSELF was down — those arrays are null-heavy and the
        file is correspondingly larger (still bounded by BUCKETS_MAX per entity, and
        null entries decode FASTER — ``from_dict`` skips them). The gaps reflect
        HA-downtime, NOT slow-polling devices: a slow RTL-SDR/MQTT sensor still gets a
        (stale-value) bucket every tick, so slow polling does not create nulls.

        Also cheaper to decode at load than the old format (``from_dict`` reconstructs
        timestamps by integer arithmetic instead of parsing one ISO string per bucket).

        ``None`` entries mark a genuine GAP — a 5-min slot with no bucket, i.e. HA was
        down for that interval. A stored ``0.0`` is DISTINCT: a bucket that existed
        with the device offline the whole interval (``record_offline`` creates it), a
        real data point the availability ratio counts. The two must never collapse.

        Runs off the loop via ``async_add_executor_job`` (touches only ``frozen``,
        which the event loop never mutates). ``round(o, 1)`` is deferred to here.
        """
        if not frozen:
            return {}
        # Shared base = earliest grid point across the snapshot. Every rows[0][0] is a
        # grid point, so base is too, and base + i*300 reproduces every interval_start
        # bit-for-bit on decode.
        base = min(rows[0][0] for rows in frozen.values())
        entities: dict[str, list[float | None]] = {}
        for entity_id, rows in frozen.items():
            arr: list[float | None] = []
            # Leading nulls for an entity that started recording after `base`.
            lead = int((rows[0][0] - base).total_seconds()) // BUCKET_INTERVAL
            arr.extend([None] * lead)
            expected = rows[0][0]
            for s, o in rows:
                # Contiguous missing slots (HA down) between the last bucket and this
                # one become nulls; snapshot() yields rows ascending on the grid.
                gap = int((s - expected).total_seconds()) // BUCKET_INTERVAL
                if gap:
                    arr.extend([None] * gap)
                # Emit a JSON int when the value is whole (300 not 300.0) — the
                # dominant steady-state values (300.0 fully-online, 0.0 fully-offline)
                # are whole, so dropping the ".0" shrinks the file ~33% further on top
                # of the dense-array win. Decode is unaffected: from_dict does
                # float(o), and float(300) == float(300.0).
                r = round(o, 1)
                arr.append(int(r) if r == int(r) else r)
                expected = s + timedelta(seconds=BUCKET_INTERVAL)
            entities[entity_id] = arr
        return {"v": 2, "base": base.isoformat(), "entities": entities}

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
            self._avail_sum_cache.clear()
            return
        monitored = set(entity_ids)
        cutoff = now - timedelta(seconds=retention_seconds)
        for entity_id in list(self._buckets):
            if entity_id not in monitored:
                del self._buckets[entity_id]
                for key in [k for k in self._avail_sum_cache if k[0] == entity_id]:
                    del self._avail_sum_cache[key]
                continue
            kept = [b for b in self._buckets[entity_id] if b.interval_start >= cutoff]
            if kept:
                self._buckets[entity_id] = kept
            else:  # drop the key entirely rather than hold an empty list
                del self._buckets[entity_id]
                for key in [k for k in self._avail_sum_cache if k[0] == entity_id]:
                    del self._avail_sum_cache[key]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvailabilityStorage:
        """Deserialize from dict. Reads both the compact v2 format and legacy files.

        v2 (``"v": 2``): base + per-entity dense array (see ``encode_snapshot``).
        Anything without a ``"v": 2`` marker is an old file (bare
        ``dict[eid, list[{"s","o"}]]``) and is read by ``_from_dict_legacy`` — so an
        in-place upgrade just works; the first save after upgrade rewrites compact.
        """
        if not isinstance(data, dict):
            # `data` is stored["availability"] from a possibly-corrupt/hand-edited
            # file; the caller only type-checks the outer Store object, not this
            # value. A non-dict (list/str/number) would make data.get("v") /
            # data.items() raise AttributeError and abort the whole load (setup-retry
            # loop) — degrade to empty, matching every other corrupt-file guard below.
            return cls()
        if data.get("v") != 2:
            return cls._from_dict_legacy(data)
        storage = cls()
        base = data.get("base")
        if not base:
            return storage
        try:
            base_dt = datetime.fromisoformat(base)
        except (ValueError, TypeError):
            # Corrupt/malformed base (bit-rot, truncated write, hand-edit). The base
            # is the single shared field every v2 bucket's timestamp derives from, so
            # a bad parse here must not crash the whole load — degrade to empty, same
            # as a missing base above. Per-entity values are already guarded below.
            return storage
        if base_dt.tzinfo is None:
            base_dt = base_dt.replace(tzinfo=timezone.utc)
        ents = data.get("entities")
        if not isinstance(ents, dict):
            # Corrupt/hand-edited file: `entities` present but not a dict (list,
            # None, scalar) → .items() would raise AttributeError and abort the
            # whole load (setup-retry loop), below the legacy reader's never-raise
            # bar. Degrade to empty, consistent with the base guard above.
            return storage
        step = timedelta(seconds=BUCKET_INTERVAL)
        for entity_id, arr in ents.items():
            buckets: list[AvailabilityBucket] = []
            if not isinstance(arr, list):
                continue
            # Cap the decoded length: the live write path hard-caps each entity at
            # BUCKETS_MAX (get_or_create_bucket), so a longer array on READ is a
            # corrupt/hostile file. Slicing bounds the on-loop decode walk + bucket
            # RAM and keeps a giant crafted array from freezing the loop at restart.
            cur = base_dt
            for o in arr[:BUCKETS_MAX]:
                if o is not None:
                    try:
                        online = float(o)
                        # Reject NaN/Inf from a hand-edited file — a non-finite
                        # online_seconds would poison the availability ratio.
                        if math.isfinite(online):
                            buckets.append(
                                AvailabilityBucket(
                                    interval_start=cur,
                                    online_seconds=max(
                                        0.0, min(online, float(BUCKET_INTERVAL))
                                    ),
                                )
                            )
                    except (ValueError, TypeError, OverflowError):
                        pass
                # Advance the cursor for EVERY slot (incl. None gaps) so index→time
                # stays aligned (running cursor = cheap vs per-index base+i*step, and
                # matches the dense-array semantics). A base near datetime.max can make
                # this overflow; stop there — every later slot would be out of range too.
                try:
                    cur += step
                except OverflowError:
                    break
            storage._buckets[entity_id] = buckets
        return storage

    @classmethod
    def _from_dict_legacy(cls, data: dict[str, Any]) -> AvailabilityStorage:
        """Deserialize the pre-v2 per-bucket {"s","o"} format (upgrade path)."""
        storage = cls()
        for entity_id, buckets_data in data.items():
            buckets: list[AvailabilityBucket] = []
            if not isinstance(buckets_data, list):
                continue
            # Same read-side length cap as the v2 path: a legacy list longer than the
            # live write cap (BUCKETS_MAX) is corrupt/hostile; bound the decode walk.
            # (buckets_data is a list here, so the slice + iteration cannot raise — the
            # inner try guards every per-item error.)
            for b in buckets_data[:BUCKETS_MAX]:
                try:
                    online = float(b["o"])
                    if not math.isfinite(online):
                        continue
                    interval_start = datetime.fromisoformat(b["s"])
                    if interval_start.tzinfo is None:
                        interval_start = interval_start.replace(tzinfo=timezone.utc)
                    bucket = AvailabilityBucket(
                        interval_start=interval_start,
                        online_seconds=max(0.0, min(online, float(BUCKET_INTERVAL))),
                    )
                    buckets.append(bucket)
                except (KeyError, ValueError, TypeError, OverflowError):
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
