"""Tests for the Entity Availability storage module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.entity_availability.const import BUCKET_INTERVAL, BUCKETS_MAX
from custom_components.entity_availability.storage import (
    AvailabilityBucket,
    AvailabilityStorage,
)

# Retention large enough to keep every bucket a test creates — isolates the
# entity-filter / window-split behaviour under test from retention trimming.
_WIDE_RETENTION = 10**9


@pytest.fixture
def storage() -> AvailabilityStorage:
    """Return a fresh AvailabilityStorage instance."""
    return AvailabilityStorage()


@pytest.fixture
def now() -> datetime:
    """Return a fixed datetime for testing."""
    return datetime(2024, 6, 15, 12, 30, 0, tzinfo=timezone.utc)


class TestBucketCreation:
    """Tests for bucket creation logic."""

    def test_get_or_create_bucket_new_entity(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test bucket creation for new entity."""
        bucket = storage.get_or_create_bucket("sensor.test", now)
        assert bucket is not None
        expected_start = now.replace(minute=30, second=0, microsecond=0)
        assert bucket.interval_start == expected_start
        assert bucket.online_seconds == 0.0
        assert bucket.total_seconds == float(BUCKET_INTERVAL)

    def test_get_or_create_bucket_reuses_current_bucket(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test that same 5-minute interval returns existing bucket."""
        bucket1 = storage.get_or_create_bucket("sensor.test", now)
        bucket2 = storage.get_or_create_bucket(
            "sensor.test", now + timedelta(minutes=2)
        )
        assert bucket1 is bucket2

    def test_get_or_create_bucket_new_interval(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test new bucket for a new 5-minute interval."""
        bucket1 = storage.get_or_create_bucket("sensor.test", now)
        next_interval = now + timedelta(minutes=5)
        bucket2 = storage.get_or_create_bucket("sensor.test", next_interval)
        assert bucket1 is not bucket2
        assert len(storage._buckets["sensor.test"]) == 2

    def test_bucket_pruning(self, storage: AvailabilityStorage, now: datetime) -> None:
        """Test that old buckets are pruned beyond max."""
        entity = "sensor.test"
        for i in range(BUCKETS_MAX + 10):
            t = now + timedelta(minutes=i * 5)
            storage.get_or_create_bucket(entity, t)

        assert len(storage._buckets[entity]) == BUCKETS_MAX


class TestRecording:
    """Tests for recording online/offline time."""

    def test_record_online(self, storage: AvailabilityStorage, now: datetime) -> None:
        """Test recording online seconds."""
        storage.record_online("sensor.test", 30.0, now)
        bucket = storage.get_or_create_bucket("sensor.test", now)
        assert bucket.online_seconds == 30.0

    def test_record_online_caps_at_total(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test online seconds don't exceed total_seconds."""
        storage.record_online("sensor.test", 200.0, now)
        storage.record_online("sensor.test", 200.0, now)
        bucket = storage.get_or_create_bucket("sensor.test", now)
        assert bucket.online_seconds == float(BUCKET_INTERVAL)

    def test_record_offline_does_not_add_online(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test that record_offline does not increase online_seconds."""
        storage.record_offline("sensor.test", 30.0, now)
        bucket = storage.get_or_create_bucket("sensor.test", now)
        assert bucket.online_seconds == 0.0

    def test_record_online_accumulates(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test multiple record_online calls accumulate."""
        storage.record_online("sensor.test", 10.0, now)
        storage.record_online("sensor.test", 20.0, now)
        bucket = storage.get_or_create_bucket("sensor.test", now)
        assert bucket.online_seconds == 30.0


class TestWindowCalculation:
    """Tests for availability window calculations."""

    def test_get_availability_no_data(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test that missing entity returns None."""
        result = storage.get_availability("sensor.missing", "today", now)
        assert result is None

    def test_get_availability_empty_buckets(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test entity with no buckets returns None."""
        storage._buckets["sensor.test"] = []
        result = storage.get_availability("sensor.test", "today", now)
        assert result is None

    def test_get_availability_insufficient_data(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test that too few buckets for long window returns None."""
        # "7d" = 168 hours * 12 = 2016 expected, need 10% = 202 buckets
        # Only add 5 buckets
        for i in range(5):
            t = now - timedelta(minutes=i * 5)
            storage.record_online("sensor.test", float(BUCKET_INTERVAL), t)

        result = storage.get_availability("sensor.test", "7d", now)
        assert result is None

    def test_get_availability_today_one_bucket_sufficient(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test that 'today' needs only one COMPLETED bucket.

        The current in-progress bucket is excluded from the ratio (it would jitter
        the value every poll), so a single completed bucket 5 min ago is the
        minimum data — recorded here at now-5min, then read at now (a new interval,
        so that bucket is complete)."""
        storage.record_online(
            "sensor.test", float(BUCKET_INTERVAL), now - timedelta(minutes=5)
        )
        result = storage.get_availability("sensor.test", "today", now)
        assert result == 100.0

    def test_get_availability_sufficient_data(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test correct availability calculation with enough data."""
        # Fill 24 hours of 5-min buckets (288 buckets), 50% online
        for i in range(288):
            t = now - timedelta(minutes=i * 5)
            storage.record_online("sensor.test", float(BUCKET_INTERVAL) / 2, t)

        result = storage.get_availability("sensor.test", "today", now)
        assert result == 50.0

    def test_get_availability_100_percent(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test 100% availability."""
        for i in range(288):
            t = now - timedelta(minutes=i * 5)
            storage.record_online("sensor.test", float(BUCKET_INTERVAL), t)

        result = storage.get_availability("sensor.test", "today", now)
        assert result == 100.0

    def test_get_availability_stable_across_polls_no_write_amp(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Availability % must NOT jitter poll-to-poll when nothing real changes.

        Realistic fixture (the one that reproduced the live regression): a history
        whose ratio sits near a rounding boundary, plus a current in-progress
        bucket whose online_seconds accrues in discrete ~30 s chunks while real
        time grows continuously. Reading at varying elapsed offsets within each
        poll gap must yield a SINGLE rounded value per window — otherwise every
        read crosses a 0.1 boundary and writes a recorder row (~7000/day observed).
        The in-progress bucket is excluded from the ratio, so the value changes
        only when a bucket completes. Checks today AND 3d AND 5d (they share the
        newest in-progress bucket and previously flipped in lockstep).
        """
        eid = "sensor.test"
        # ~96.05% history across enough completed buckets to weight every window.
        # 5d window = 120h = 1440 buckets; fill that many so 3d/5d aren't None.
        for i in range(1, 1441):
            t = now - timedelta(minutes=5 * i)
            storage.record_online(eid, 0.9605 * float(BUCKET_INTERVAL), t)

        for window in ("today", "3d", "5d"):
            values = []
            for poll in range(9):
                # 9 polls x 30 s = 270 s — stays WITHIN the current 5-min bucket
                # (no legitimate bucket-close transition), so the ONLY way the
                # value can change is per-poll rounding jitter, which must not
                # happen. (A bucket close is a legitimate ≤1 change, tested by the
                # existing window tests; here we isolate the jitter contract.)
                pt = now + timedelta(seconds=30 * poll)
                if poll > 0:
                    storage.record_online(eid, 30.0, pt)
                # Read at several elapsed offsets within the poll gap — the skew
                # that turned the old elapsed-denominator into an oscillator.
                for off in (5, 15, 25):
                    values.append(
                        storage.get_availability(
                            eid, window, pt + timedelta(seconds=off)
                        )
                    )
            distinct = {v for v in values if v is not None}
            assert len(distinct) == 1, (
                f"{window} availability jittered across polls: {sorted(distinct)}"
            )

    def test_get_availability_only_in_progress_bucket_shows_value(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """A brand-new entity with only the current in-progress bucket (no completed
        bucket yet) shows a value IMMEDIATELY, not None — the sensor must not read
        "unknown" for the first 5 minutes after setup or after a calendar-"today"
        rollover. Its denominator is the ELAPSED time in the bucket, not the full
        5-minute interval, so a fully-online device reads ~100% from the first poll
        instead of a spurious low value that ramps up (the midnight-collapse bug)."""
        # Bucket starts at the 5-min slot of `now`; place the reading 120 s into it
        # with the device online the whole time → 120 online / 120 elapsed = 100%.
        start = storage._get_interval_start(now)
        probe = start + timedelta(seconds=120)
        storage.record_online("sensor.new", 120.0, probe)
        result = storage.get_availability("sensor.new", "today", probe)
        assert result is not None
        # online == elapsed → fully online → 100%, NOT 40% (=120/300, the old bug).
        assert result == 100.0

    def test_get_availability_in_progress_partial_online_elapsed_ratio(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """In-progress-only ratio uses online/elapsed. A device online 90 s of the
        first 120 s of the bucket reads 75%, not 30% (=90/300)."""
        start = storage._get_interval_start(now)
        probe = start + timedelta(seconds=120)
        # 90 s online out of 120 s elapsed.
        storage.record_online("sensor.new", 90.0, probe)
        result = storage.get_availability("sensor.new", "today", probe)
        assert result == 75.0

    def test_get_availability_in_progress_clamps_at_100(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """online_seconds marginally exceeding elapsed (poll-timing skew) must clamp
        to 100%, never print 100.x%."""
        start = storage._get_interval_start(now)
        probe = start + timedelta(seconds=100)
        # Book MORE online than elapsed (101 > 100) — skew. Must clamp to 100.0.
        storage.record_online("sensor.new", 101.0, probe)
        result = storage.get_availability("sensor.new", "today", probe)
        assert result == 100.0

    def test_get_availability_midnight_rollover_online_not_unknown(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """At a calendar-"today" rollover landing on a 5-min bucket boundary, the
        sensor reads with a minute-truncated `now` so elapsed == 0 for the fresh
        bucket, while record_online has already booked this poll's online seconds.
        The today sensor MUST show 100.0 (device was online), NOT "unknown" (None).
        Regression guard for the midnight "today bars disappear for 60 s" bug: the
        in-progress denominator is floored at total_online so total_time > 0."""
        start = storage._get_interval_start(now)
        # Probe AT the bucket start → elapsed == 0, but a full poll of online booked.
        storage.record_online("sensor.new", 30.0, start)
        result = storage.get_availability("sensor.new", "today", start)
        assert result == 100.0

    def test_get_availability_fresh_bucket_offline_still_none(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Twin of the rollover guard: a genuinely-offline fresh bucket (no online
        seconds booked, elapsed == 0) still returns None — the floor only raises the
        denominator when online > 0, so an all-offline brand-new bucket with no data
        yet is unchanged. NB: this passes on the pre-fix code too; it guards the fix
        from OVER-reaching (floor must not turn genuine-offline into a value), not
        the midnight bug itself — that guard is the rollover test above."""
        start = storage._get_interval_start(now)
        # Bucket exists (offline recorded) but zero online, probed at elapsed == 0.
        storage.record_offline("sensor.new", 30.0, start)
        result = storage.get_availability("sensor.new", "today", start)
        assert result is None

    def test_get_availability_0_percent(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test 0% availability (all offline)."""
        for i in range(288):
            t = now - timedelta(minutes=i * 5)
            storage.record_offline("sensor.test", float(BUCKET_INTERVAL), t)

        result = storage.get_availability("sensor.test", "today", now)
        assert result == 0.0

    def test_get_availability_7d_window(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test 7-day window calculation."""
        # Fill 7 days with 75% online
        for i in range(2016):
            t = now - timedelta(minutes=i * 5)
            storage.record_online("sensor.test", float(BUCKET_INTERVAL) * 0.75, t)

        result = storage.get_availability("sensor.test", "7d", now)
        assert result == 75.0

    def test_get_availability_3d_window(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test 3-day window calculation."""
        # Fill 3 days = 864 buckets
        for i in range(864):
            t = now - timedelta(minutes=i * 5)
            storage.record_online("sensor.test", float(BUCKET_INTERVAL), t)

        result = storage.get_availability("sensor.test", "3d", now)
        assert result == 100.0

    def test_get_entity_availability_multiple_windows(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test get_entity_availability returns all windows."""
        for i in range(2016):
            t = now - timedelta(minutes=i * 5)
            storage.record_online("sensor.test", float(BUCKET_INTERVAL), t)

        result = storage.get_entity_availability("sensor.test", ["today", "7d"], now)
        assert "today" in result
        assert "7d" in result
        assert result["today"] == 100.0
        assert result["7d"] == 100.0

    def test_window_to_hours(self) -> None:
        """Test window string to hours conversion."""
        assert AvailabilityStorage._window_to_hours("today") == 24
        assert AvailabilityStorage._window_to_hours("3d") == 72
        assert AvailabilityStorage._window_to_hours("5d") == 120
        assert AvailabilityStorage._window_to_hours("7d") == 168
        assert AvailabilityStorage._window_to_hours("unknown") == 24

    def test_window_to_hours_unknown_logs_warning(self, caplog) -> None:
        """Unknown window string logs a warning and returns 24."""
        import logging

        with caplog.at_level(logging.WARNING):
            result = AvailabilityStorage._window_to_hours("2d")
        assert result == 24
        assert "Unknown availability window" in caplog.text
        assert "2d" in caplog.text


class TestSerialization:
    """Tests for serialization and deserialization."""

    def test_to_dict_empty(self, storage: AvailabilityStorage, now: datetime) -> None:
        """Test serialization of empty storage."""
        assert storage.to_dict(now, _WIDE_RETENTION, ["sensor.test"]) == {}

    def test_to_dict_with_data(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test serialization with data (compact v2 format)."""
        storage.record_online("sensor.test", 100.0, now)
        result = storage.to_dict(now, _WIDE_RETENTION, ["sensor.test"])

        assert result["v"] == 2
        assert result["base"] is not None
        assert "sensor.test" in result["entities"]
        arr = result["entities"]["sensor.test"]
        assert len(arr) == 1
        assert arr[0] == 100.0

    def test_total_seconds_never_serialized(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """total_seconds must NOT be persisted — the availability denominator for
        the in-progress bucket is recomputed from elapsed time at read time. If a
        stored total leaked back in, a restart mid-bucket would resurface the
        per-poll write-amplification the elapsed-denominator fix removes.

        In the compact v2 format each bucket is a single scalar (online_seconds) or
        ``None`` (gap), so there is structurally nowhere for total_seconds to appear.
        """
        storage.record_online("sensor.test", 100.0, now)
        serialized = storage.to_dict(now, _WIDE_RETENTION, ["sensor.test"])
        for value in serialized["entities"]["sensor.test"]:
            assert value is None or isinstance(value, (int, float))

    def test_from_dict_empty(self) -> None:
        """Test deserialization of empty dict."""
        storage = AvailabilityStorage.from_dict({})
        assert storage._buckets == {}

    def test_from_dict_roundtrip(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test serialization -> deserialization roundtrip."""
        storage.record_online("sensor.a", 150.0, now)
        storage.record_online("sensor.b", 300.0, now)

        serialized = storage.to_dict(now, _WIDE_RETENTION, ["sensor.a", "sensor.b"])
        restored = AvailabilityStorage.from_dict(serialized)
        assert "sensor.a" in restored._buckets
        assert "sensor.b" in restored._buckets
        assert restored._buckets["sensor.a"][0].online_seconds == 150.0
        assert restored._buckets["sensor.b"][0].online_seconds == float(BUCKET_INTERVAL)

    def test_from_dict_invalid_bucket_skipped(self) -> None:
        """Test that invalid bucket data is skipped gracefully."""
        data = {
            "sensor.test": [
                {"s": "invalid-date", "o": 100.0},
                {"o": 200.0},  # missing "s"
                {
                    "s": "2024-06-15T12:30:00+00:00",
                    "o": 250.0,
                },
            ]
        }
        storage = AvailabilityStorage.from_dict(data)
        assert len(storage._buckets["sensor.test"]) == 1
        assert storage._buckets["sensor.test"][0].online_seconds == 250.0

    def test_from_dict_preserves_interval_start(self, now: datetime) -> None:
        """Test that interval_start is correctly preserved through roundtrip."""
        storage = AvailabilityStorage()
        storage.record_online("sensor.test", 100.0, now)

        serialized = storage.to_dict(now, _WIDE_RETENTION, ["sensor.test"])
        restored = AvailabilityStorage.from_dict(serialized)

        expected_start = now.replace(minute=30, second=0, microsecond=0)
        assert restored._buckets["sensor.test"][0].interval_start == expected_start

    def test_v2_gap_distinct_from_present_offline(self, now: datetime) -> None:
        """A None entry (HA down → no bucket) must stay distinct from a stored 0.0
        (bucket existed, device offline the whole interval). Collapsing them would
        corrupt the availability denominator."""
        enc = {
            "v": 2,
            "base": now.replace(minute=0, second=0, microsecond=0).isoformat(),
            # slot 0 online, slot 1 GAP (None), slot 2 present-offline (0.0)
            "entities": {"sensor.test": [300.0, None, 0.0]},
        }
        restored = AvailabilityStorage.from_dict(enc)
        buckets = restored._buckets["sensor.test"]
        # gap dropped → 2 buckets, not 3; the 0.0 bucket is kept
        assert len(buckets) == 2
        base = now.replace(minute=0, second=0, microsecond=0)
        assert buckets[0].interval_start == base
        assert buckets[0].online_seconds == 300.0
        # slot 2 (index 2), NOT slot 1 — index-to-time arithmetic skips the gap
        assert buckets[1].interval_start == base + timedelta(
            seconds=2 * BUCKET_INTERVAL
        )
        assert buckets[1].online_seconds == 0.0

    def test_v2_ha_downtime_gaps_roundtrip_and_ratio(self, now: datetime) -> None:
        """HA-downtime regression: when the coordinator didn't run for some slots
        (HA itself down), those slots have NO bucket → the v2 array carries explicit
        None gaps. This is the null-heavy intermittently-run-instance case the dense
        EC146 smoke path doesn't cover. Assert: (a) the gap round-trips bit-exact in
        position (a missing slot stays missing, not resurrected as 0.0), (b) the
        availability ratio counts the present 0.0 bucket and excludes the gap, (c) the
        file stays bounded (no runaway from null padding).
        """
        base = now.replace(minute=0, second=0, microsecond=0)

        def g(k):
            return base + timedelta(seconds=k * BUCKET_INTERVAL)

        # slot 0 online, slots 1-2 HA DOWN (no bucket), slot 3 present-offline (0.0)
        storage = AvailabilityStorage()
        storage.record_online("sensor.a", float(BUCKET_INTERVAL), g(0))
        storage.record_offline("sensor.a", float(BUCKET_INTERVAL), g(3))

        serialized = storage.to_dict(now, _WIDE_RETENTION, ["sensor.a"])
        arr = serialized["entities"]["sensor.a"]
        # (a) explicit gap: index 0 present, 1 & 2 None (HA down), 3 present (0.0)
        assert arr[0] == float(BUCKET_INTERVAL)
        assert arr[1] is None and arr[2] is None
        assert arr[3] == 0.0

        restored = AvailabilityStorage.from_dict(serialized)
        back = restored._buckets["sensor.a"]
        # gap slots are NOT resurrected as buckets — only the 2 real buckets survive,
        # at their original grid positions (0 and 3), NOT contiguous.
        assert [b.interval_start for b in back] == [g(0), g(3)]
        assert [b.online_seconds for b in back] == [float(BUCKET_INTERVAL), 0.0]

        # (b) availability over a window: 1 fully-online + 1 fully-offline COMPLETED
        # bucket → 300/600 = 50% (the 2 missing slots are excluded, the 0.0 counts).
        # Use "24h" (min_required=1) so the 2-bucket sample isn't rejected by the
        # longer windows' 10%-of-window minimum.
        later = g(4)  # make both buckets COMPLETED (not the in-progress newest slot)
        assert storage.get_availability("sensor.a", "24h", later) == 50.0

        # (c) bounded: the null gap adds at most one entry per missing slot, never
        # more than BUCKETS_MAX entries total for the entity.
        assert len(arr) <= BUCKETS_MAX

    def test_v2_roundtrip_with_gap(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """to_dict (v2) → from_dict reconstructs interval_start + online byte-faithful."""
        base = now.replace(minute=0, second=0, microsecond=0)
        storage.record_online("sensor.a", 150.0, base)
        storage.record_online(
            "sensor.a", 200.0, base + timedelta(seconds=BUCKET_INTERVAL)
        )
        serialized = storage.to_dict(now, _WIDE_RETENTION, ["sensor.a"])
        assert serialized["v"] == 2
        restored = AvailabilityStorage.from_dict(serialized)
        orig = storage._buckets["sensor.a"]
        back = restored._buckets["sensor.a"]
        assert [b.interval_start for b in back] == [b.interval_start for b in orig]
        assert [b.online_seconds for b in back] == [b.online_seconds for b in orig]

    def test_v2_reads_legacy_file(self) -> None:
        """A pre-v2 file (no 'v' key, list-of-{s,o}) still loads after upgrade."""
        legacy = {
            "sensor.c": [
                {"s": "2024-06-15T12:30:00+00:00", "o": 300.0},
                {"s": "2024-06-15T12:35:00+00:00", "o": 150.5},
            ]
        }
        restored = AvailabilityStorage.from_dict(legacy)
        assert len(restored._buckets["sensor.c"]) == 2
        assert restored._buckets["sensor.c"][1].online_seconds == 150.5

    def test_v2_empty(self) -> None:
        """Empty v2 snapshot round-trips to empty storage."""
        assert (
            AvailabilityStorage.from_dict(
                {"v": 2, "base": None, "entities": {}}
            )._buckets
            == {}
        )

    def test_v2_naive_base_normalized_to_utc(self) -> None:
        """A naive 'base' in a hand-edited/corrupt file is read as UTC (trust boundary)."""
        restored = AvailabilityStorage.from_dict(
            {"v": 2, "base": "2024-06-15T12:00:00", "entities": {"sensor.a": [300.0]}}
        )
        assert restored._buckets["sensor.a"][0].interval_start == datetime(
            2024, 6, 15, 12, 0, tzinfo=timezone.utc
        )

    def test_v2_corrupt_base_degrades_to_empty(self) -> None:
        """A malformed v2 base (bit-rot / truncated write) must not crash the whole
        load — it degrades to empty storage, same as a missing base. The base is the
        one shared field every bucket timestamp derives from, so a naked parse here
        would wipe the load with a ValueError."""
        restored = AvailabilityStorage.from_dict(
            {
                "v": 2,
                "base": "2026-13-99T99:99:99",  # not a valid datetime
                "entities": {"sensor.a": [300.0]},
            }
        )
        assert restored._buckets == {}

    def test_v2_non_dict_entities_degrades_to_empty(self) -> None:
        """A v2 file whose `entities` is present but not a dict (list/None/scalar)
        must not crash the load — `.items()` on a non-dict would raise AttributeError
        and abort setup. Degrade to empty, matching the legacy reader's never-raise
        bar for a hostile/hand-edited file."""
        for bad in ([{"s": "x", "o": 1}], None, 42, "oops"):
            restored = AvailabilityStorage.from_dict(
                {"v": 2, "base": "2024-06-15T12:00:00+00:00", "entities": bad}
            )
            assert restored._buckets == {}, f"entities={bad!r}"

    def test_from_dict_non_dict_data_degrades_to_empty(self) -> None:
        """The outermost guard: stored["availability"] can itself be a non-dict in a
        corrupt/hand-edited file (the caller only type-checks the outer Store object).
        from_dict(list/str/number) must degrade to empty, not raise AttributeError on
        data.get("v") and brick setup. Closes the last never-raise hole."""
        for bad in ([], [{"s": "x", "o": 1}], "oops", 42, None):
            restored = AvailabilityStorage.from_dict(bad)
            assert restored._buckets == {}, f"data={bad!r}"

    def test_negative_online_seconds_clamped_to_zero(self) -> None:
        """A hand-edited file with a NEGATIVE online_seconds must clamp to 0.0 in BOTH
        decoders — get_availability only clamps the ratio's upper bound (<=1.0), so a
        negative online would emit a negative availability %. Pins the max(0.0, ...)
        lower clamp so reverting it reds."""
        # v2 path
        v2 = AvailabilityStorage.from_dict(
            {
                "v": 2,
                "base": "2024-06-15T12:00:00+00:00",
                "entities": {"sensor.a": [-50.0, 300.0]},
            }
        )
        assert v2._buckets["sensor.a"][0].online_seconds == 0.0
        assert v2._buckets["sensor.a"][1].online_seconds == 300.0
        # legacy path
        legacy = AvailabilityStorage.from_dict(
            {"sensor.b": [{"s": "2024-06-15T12:00:00+00:00", "o": -50.0}]}
        )
        assert legacy._buckets["sensor.b"][0].online_seconds == 0.0

    def test_v2_non_ascending_and_duplicate_interval_start(self, now: datetime) -> None:
        """V1 regression guard (mutation gap): every other round-trip test inserts
        buckets ASCENDING, so the snapshot() sort+slot-fold would stay green if
        reverted. This inserts NON-ASCENDING interval_starts AND a DUPLICATE directly
        into _buckets (as a backward clock step / VM-resume would), then asserts the
        encode→decode round-trip yields CORRECT ascending timestamps with the dup
        folded last-write-wins — NOT shifted forward a grid step. Reverting the
        snapshot sort or the slot-fold must red this test."""
        base = now.replace(minute=0, second=0, microsecond=0)

        def slot(k):
            return base + timedelta(seconds=k * BUCKET_INTERVAL)

        storage = AvailabilityStorage()
        # non-ascending order + a DUPLICATE interval_start (slot 1 appears twice):
        # [slot2=100, slot0=200, slot1=300, slot1=50 (dup, later write)]
        storage._buckets["sensor.x"] = [
            AvailabilityBucket(slot(2), 100.0),
            AvailabilityBucket(slot(0), 200.0),
            AvailabilityBucket(slot(1), 300.0),
            AvailabilityBucket(slot(1), 50.0),  # duplicate slot 1, last-write-wins
        ]
        restored = AvailabilityStorage.from_dict(
            storage.to_dict(slot(5), _WIDE_RETENTION, ["sensor.x"])
        )
        back = restored._buckets["sensor.x"]
        # correct ascending timestamps, exactly 3 distinct slots, NO forward shift,
        # dup slot 1 folded to the last-written value (50.0)
        assert [b.interval_start for b in back] == [slot(0), slot(1), slot(2)]
        assert [b.online_seconds for b in back] == [200.0, 50.0, 100.0]

    def test_v2_non_list_entity_skipped(self) -> None:
        """A corrupt non-list entity array is skipped (key omitted), not crashed on."""
        restored = AvailabilityStorage.from_dict(
            {"v": 2, "base": "2024-06-15T12:00:00+00:00", "entities": {"sensor.a": 42}}
        )
        assert "sensor.a" not in restored._buckets

    def test_v2_non_numeric_entry_skipped(self) -> None:
        """A non-numeric array entry is skipped (mirrors legacy per-bucket guard)."""
        restored = AvailabilityStorage.from_dict(
            {
                "v": 2,
                "base": "2024-06-15T12:00:00+00:00",
                "entities": {"sensor.a": ["oops", 300.0]},
            }
        )
        # the bad entry at index 0 is dropped; index 1 survives at base + 1*300
        assert len(restored._buckets["sensor.a"]) == 1
        assert restored._buckets["sensor.a"][0].online_seconds == 300.0

    def test_v2_far_future_base_degrades_no_crash(self) -> None:
        """B1: a corrupt/hostile file with a base near datetime.max must NOT crash the
        load with an uncaught OverflowError (which would brick setup into a retry
        loop). The cursor advance (cur += step) overflows past datetime.max and must
        break cleanly — slot 0 still decodes, later slots are dropped."""
        restored = AvailabilityStorage.from_dict(
            {
                "v": 2,
                "base": "9999-12-31T23:59:59+00:00",
                "entities": {"sensor.a": [300.0, 300.0, 300.0]},
            }
        )
        # no raise; slot 0 (at the base) decodes, the cursor overflow breaks the rest
        assert len(restored._buckets["sensor.a"]) == 1

    def test_v2_nan_inf_dropped(self) -> None:
        """N5: a non-finite online_seconds (NaN/Inf from a hand-edited file) must be
        dropped — it would poison the availability ratio. Finite neighbours survive."""
        restored = AvailabilityStorage.from_dict(
            {
                "v": 2,
                "base": "2024-06-15T12:00:00+00:00",
                "entities": {"sensor.a": [float("nan"), float("inf"), 250.0]},
            }
        )
        # both non-finite entries dropped; the 250.0 survives at index 2
        buckets = restored._buckets["sensor.a"]
        assert len(buckets) == 1
        assert buckets[0].online_seconds == 250.0
        assert buckets[0].interval_start == datetime(
            2024, 6, 15, 12, 10, tzinfo=timezone.utc
        )

    def test_v2_oversized_array_capped(self) -> None:
        """A decoded array longer than BUCKETS_MAX is corrupt/hostile (the live write
        path caps each entity at BUCKETS_MAX). Reading must bound the walk to
        BUCKETS_MAX so a giant crafted array cannot freeze the loop at restart."""
        restored = AvailabilityStorage.from_dict(
            {
                "v": 2,
                "base": "2024-06-15T12:00:00+00:00",
                "entities": {"sensor.a": [300.0] * (BUCKETS_MAX + 500)},
            }
        )
        assert len(restored._buckets["sensor.a"]) == BUCKETS_MAX

    def test_v2_cursor_equals_base_plus_index(self) -> None:
        """S1: the running cursor must reconstruct exactly the same interval_start as
        base + i*BUCKET_INTERVAL would, across leading/interior/trailing gaps."""
        base = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        # None | 300 | None | None | 250 | None  → present at index 1 and 4
        restored = AvailabilityStorage.from_dict(
            {
                "v": 2,
                "base": base.isoformat(),
                "entities": {"sensor.a": [None, 300.0, None, None, 250.0, None]},
            }
        )
        starts = [b.interval_start for b in restored._buckets["sensor.a"]]
        assert starts == [
            base + timedelta(seconds=1 * BUCKET_INTERVAL),
            base + timedelta(seconds=4 * BUCKET_INTERVAL),
        ]

    def test_v2_encode_emits_int_for_whole_values(self) -> None:
        """V3: whole online_seconds serialize as JSON ints (300 not 300.0) — the
        headline size win; a fractional value stays a float. Pins the int-emission
        branch so a refactor that drops it is caught (decode is unaffected either way)."""
        now = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        storage = AvailabilityStorage()
        storage.record_online("sensor.a", float(BUCKET_INTERVAL), now)  # whole → 300
        storage.record_online("sensor.b", 272.46, now)  # fractional → 272.5 (float)
        out = storage.to_dict(now, _WIDE_RETENTION, ["sensor.a", "sensor.b"])
        a = out["entities"]["sensor.a"][0]
        b = out["entities"]["sensor.b"][0]
        assert a == 300 and isinstance(a, int) and not isinstance(a, bool)
        assert b == 272.5 and isinstance(b, float)

    def test_import_math_present(self) -> None:
        """N5: storage.py must import math — math.isfinite is used in both decoders;
        a missing import would NameError on the first non-finite value (a new crash)."""
        from custom_components.entity_availability import storage as storage_mod

        assert hasattr(storage_mod, "math")

    def test_legacy_nan_and_oversized_guarded(self) -> None:
        """The legacy ({"s","o"}) reader has the SAME hostile-file guards as v2: a
        non-finite value is dropped, and the array is capped at BUCKETS_MAX."""
        legacy = {
            "sensor.a": [
                {"s": "2024-06-15T12:00:00+00:00", "o": float("inf")},  # dropped
                {"s": "2024-06-15T12:05:00+00:00", "o": 150.0},  # kept
            ]
        }
        restored = AvailabilityStorage.from_dict(legacy)
        assert len(restored._buckets["sensor.a"]) == 1
        assert restored._buckets["sensor.a"][0].online_seconds == 150.0
        # oversized legacy array is capped
        big = {
            "sensor.b": [{"s": "2024-06-15T12:00:00+00:00", "o": 300.0}]
            * (BUCKETS_MAX + 100)
        }
        assert (
            len(AvailabilityStorage.from_dict(big)._buckets["sensor.b"]) == BUCKETS_MAX
        )

    def test_legacy_non_iterable_item_degrades(self) -> None:
        """Legacy outer guard: a non-iterable buckets value degrades to empty, not a
        crash (the outer `except TypeError`)."""
        # a buckets_data that is a list but whose iteration yields a non-subscriptable
        # item exercises the inner guard; a non-list is caught by the isinstance check.
        restored = AvailabilityStorage.from_dict({"sensor.a": [42, "x"]})
        assert restored._buckets["sensor.a"] == []


# ---------------------------------------------------------------------------
# record_online — zero/negative seconds skipped (line 74)
# record_offline — zero/negative seconds skipped (line 82)
# ---------------------------------------------------------------------------


class TestRecordEdgeCases:
    """Tests for zero/negative seconds handling in record_online and record_offline."""

    def test_record_online_zero_seconds_no_bucket(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """record_online with 0 seconds does not create a bucket."""
        storage.record_online("sensor.test", 0, now)
        assert "sensor.test" not in storage._buckets

    def test_record_online_negative_seconds_no_bucket(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """record_online with negative seconds does not create a bucket."""
        storage.record_online("sensor.test", -10.0, now)
        assert "sensor.test" not in storage._buckets

    def test_record_offline_zero_seconds_no_bucket(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """record_offline with 0 seconds does not create a bucket."""
        storage.record_offline("sensor.test", 0, now)
        assert "sensor.test" not in storage._buckets

    def test_record_offline_negative_seconds_no_bucket(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """record_offline with negative seconds does not create a bucket."""
        storage.record_offline("sensor.test", -5.0, now)
        assert "sensor.test" not in storage._buckets


# ---------------------------------------------------------------------------
# get_availability — no relevant buckets (lines 103-109)
# get_availability — total_time == 0 path (line 128)
# ---------------------------------------------------------------------------


class TestGetAvailabilityEdgeCases:
    """Edge case tests for get_availability."""

    def test_all_buckets_older_than_window_returns_none(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Returns None when all buckets are older than the requested window."""
        # Record data 48 hours ago — outside the 'today' (24h) window
        old_time = now - timedelta(hours=48)
        storage.record_online("sensor.test", float(BUCKET_INTERVAL), old_time)

        result = storage.get_availability("sensor.test", "today", now)
        assert result is None

    def test_total_time_zero_returns_none(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Returns None if the sum of bucket total_seconds is 0."""
        from custom_components.entity_availability.storage import AvailabilityBucket

        # Inject a bucket with total_seconds=0 (edge case)
        bucket = AvailabilityBucket(interval_start=now, online_seconds=0.0)
        bucket.total_seconds = 0.0
        storage._buckets["sensor.test"] = [bucket]

        result = storage.get_availability("sensor.test", "today", now)
        assert result is None


# ---------------------------------------------------------------------------
# from_dict — outer TypeError when buckets_data is not iterable (lines 168-169)
# ---------------------------------------------------------------------------


class TestFromDictOuterTypeError:
    """Test from_dict outer except TypeError branch."""

    def test_from_dict_non_iterable_buckets_data_skipped(self) -> None:
        """from_dict skips entity when buckets_data is not iterable (e.g. None)."""
        storage = AvailabilityStorage.from_dict({"sensor.test": None})
        assert "sensor.test" not in storage._buckets

    def test_from_dict_integer_buckets_data_skipped(self) -> None:
        """from_dict skips entity when buckets_data is an integer."""
        storage = AvailabilityStorage.from_dict({"sensor.test": 42})
        assert "sensor.test" not in storage._buckets

    def test_from_dict_naive_interval_start_gets_utc(self) -> None:
        """from_dict normalizes naive interval_start to UTC-aware."""
        from datetime import timezone

        storage = AvailabilityStorage.from_dict(
            {"sensor.test": [{"s": "2024-01-01T12:00:00", "o": 100.0}]}
        )
        assert "sensor.test" in storage._buckets
        bucket = storage._buckets["sensor.test"][0]
        assert bucket.interval_start.tzinfo is not None
        assert bucket.interval_start.tzinfo == timezone.utc


class TestReset:
    """Tests for AvailabilityStorage.reset."""

    def test_reset_all(self, storage: AvailabilityStorage, now: datetime) -> None:
        """reset(None) clears every entity's buckets."""
        storage.record_online("sensor.a", 30.0, now)
        storage.record_online("sensor.b", 30.0, now)
        storage.reset(None)
        assert storage.buckets == {}

    def test_reset_specific(self, storage: AvailabilityStorage, now: datetime) -> None:
        """reset([id]) clears only the named entity, leaves others."""
        storage.record_online("sensor.a", 30.0, now)
        storage.record_online("sensor.b", 30.0, now)
        storage.reset(["sensor.a"])
        assert "sensor.a" not in storage.buckets
        assert "sensor.b" in storage.buckets

    def test_reset_unknown_id_noop(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """reset of an id with no buckets is a no-op."""
        storage.record_online("sensor.a", 30.0, now)
        storage.reset(["sensor.missing"])
        assert "sensor.a" in storage.buckets


# ---------------------------------------------------------------------------
# Retention-aware to_dict / prune + window split (today=calendar, new 24h)
# ---------------------------------------------------------------------------


# margin applied by the coordinator; mirrored here so the retention-edge tests
# exercise the exact cutoff the save path uses. Keep in sync with
# coordinator._retention_seconds (BUCKET_INTERVAL * 3 == 15 min).
_MARGIN = BUCKET_INTERVAL * 3


def _fill_online(storage: AvailabilityStorage, eid: str, start: datetime, count: int):
    """Record one fully-online bucket per 5-min slot starting at `start`."""
    for i in range(count):
        t = start + timedelta(seconds=BUCKET_INTERVAL * i)
        storage.record_online(eid, float(BUCKET_INTERVAL), t)


class TestRetentionHours:
    """_retention_hours vs _window_to_hours — today retains 25h for DST long days."""

    def test_window_to_hours_24h(self) -> None:
        assert AvailabilityStorage._window_to_hours("24h") == 24

    def test_window_to_hours_today(self) -> None:
        assert AvailabilityStorage._window_to_hours("today") == 24

    def test_retention_hours_today_is_25(self) -> None:
        # calendar today spans 25h on a DST fall-back day; retention must cover it
        assert AvailabilityStorage._retention_hours("today") == 25

    def test_retention_hours_24h_is_24(self) -> None:
        assert AvailabilityStorage._retention_hours("24h") == 24

    @pytest.mark.parametrize(
        ("window", "hours"), [("3d", 72), ("5d", 120), ("7d", 168)]
    )
    def test_retention_matches_read_span_for_fixed_windows(
        self, window: str, hours: int
    ) -> None:
        # fixed-duration windows: retention == read span (only today differs)
        assert AvailabilityStorage._retention_hours(window) == hours
        assert AvailabilityStorage._window_to_hours(window) == hours

    def test_retention_hours_unknown_defaults_24(self) -> None:
        assert AvailabilityStorage._retention_hours("bogus") == 24


class TestToDictRetention:
    """to_dict filters by retention window and monitored-entity keep set."""

    def test_empty_retention_serializes_nothing(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        # no windows selected -> retention_seconds <= 0 -> persist nothing
        storage.record_online("sensor.a", 100.0, now)
        assert storage.to_dict(now, 0, ["sensor.a"]) == {}
        assert storage.to_dict(now, -1, ["sensor.a"]) == {}

    def test_drops_buckets_older_than_retention(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        old = now - timedelta(hours=48)
        storage.record_online("sensor.a", float(BUCKET_INTERVAL), old)
        storage.record_online("sensor.a", float(BUCKET_INTERVAL), now)
        out = storage.to_dict(now, 24 * 3600 + _MARGIN, ["sensor.a"])
        # only the recent bucket survives the 24h+margin cutoff
        assert len(out["entities"]["sensor.a"]) == 1

    def test_drops_orphan_entities(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        storage.record_online("sensor.keep", 100.0, now)
        storage.record_online("sensor.orphan", 100.0, now)
        out = storage.to_dict(now, _WIDE_RETENTION, ["sensor.keep"])
        assert "sensor.keep" in out["entities"]
        assert "sensor.orphan" not in out["entities"]

    def test_monitored_but_all_buckets_out_of_window_omitted(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        # monitored entity whose ONLY buckets fall outside retention -> kept list
        # empty -> entity omitted from the file (no dead empty-list key).
        storage.record_online(
            "sensor.a", float(BUCKET_INTERVAL), now - timedelta(hours=48)
        )
        out = storage.to_dict(now, 24 * 3600 + _MARGIN, ["sensor.a"])
        assert out == {}


class TestPrune:
    """prune mirrors the to_dict filter, mutating _buckets in place."""

    def test_prune_drops_old_buckets(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        old = now - timedelta(hours=48)
        storage.record_online("sensor.a", float(BUCKET_INTERVAL), old)
        storage.record_online("sensor.a", float(BUCKET_INTERVAL), now)
        storage.prune(now, 24 * 3600 + _MARGIN, ["sensor.a"])
        assert len(storage._buckets["sensor.a"]) == 1

    def test_prune_removes_orphan_entity(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        storage.record_online("sensor.keep", 100.0, now)
        storage.record_online("sensor.orphan", 100.0, now)
        storage.prune(now, _WIDE_RETENTION, ["sensor.keep"])
        assert "sensor.keep" in storage._buckets
        assert "sensor.orphan" not in storage._buckets

    def test_prune_drops_monitored_entity_with_no_in_window_buckets(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        # monitored entity whose buckets all fall outside retention -> kept empty
        # -> key deleted, not held as an empty list.
        storage.record_online(
            "sensor.a", float(BUCKET_INTERVAL), now - timedelta(hours=48)
        )
        storage.prune(now, 24 * 3600 + _MARGIN, ["sensor.a"])
        assert "sensor.a" not in storage._buckets

    def test_prune_empty_retention_clears_all(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        storage.record_online("sensor.a", 100.0, now)
        storage.prune(now, 0, ["sensor.a"])
        assert storage._buckets == {}

    def test_prune_never_tighter_than_persist(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        # Guard the load/save invariant: prune with the SAME retention as to_dict
        # must keep exactly the buckets to_dict would serialize — never fewer, or
        # an options-widen (reload from file) would lose data the file still held.
        _fill_online(storage, "sensor.a", now - timedelta(hours=30), 360)
        retention = 25 * 3600 + _MARGIN
        persisted = storage.to_dict(now, retention, ["sensor.a"])
        storage.prune(now, retention, ["sensor.a"])
        assert len(storage._buckets["sensor.a"]) == len(
            persisted["entities"]["sensor.a"]
        )


class TestSnapshotOffload:
    """snapshot() (on-loop, value-freeze) + encode_snapshot() (executor-safe).

    The save path builds the on-disk availability dict off the event loop:
    snapshot() freezes buckets BY VALUE on the loop, encode_snapshot() does the
    expensive isoformat/dict build in the executor. to_dict == their composition.
    """

    def test_snapshot_encode_equals_old_to_dict(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        # encode_snapshot(snapshot(...)) must be byte-identical to to_dict(...),
        # including the empty / retention<=0 cases.
        _fill_online(storage, "sensor.a", now - timedelta(hours=20), 12)
        _fill_online(storage, "sensor.b", now - timedelta(hours=10), 6)
        ids = ["sensor.a", "sensor.b"]
        ret = 24 * 3600 + _MARGIN
        combined = AvailabilityStorage.encode_snapshot(storage.snapshot(now, ret, ids))
        assert combined == storage.to_dict(now, ret, ids)
        # empty / no-window cases
        assert storage.snapshot(now, 0, ids) == {}
        assert AvailabilityStorage.encode_snapshot({}) == {}
        assert storage.to_dict(now, 0, ids) == {}

    def test_snapshot_freezes_by_value(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        # RACE GUARD: online_seconds is mutated in place by record_online every
        # tick. snapshot must capture the float BY VALUE so the executor never reads
        # a climbing value. Mutate a bucket AFTER snapshot -> frozen is unchanged.
        storage.record_online("sensor.a", 100.0, now)
        frozen = storage.snapshot(now, _WIDE_RETENTION, ["sensor.a"])
        captured = frozen["sensor.a"][0][1]  # (interval_start, online_seconds)
        # mutate the live bucket's online_seconds after the snapshot
        storage._buckets["sensor.a"][0].online_seconds += 123.0
        assert frozen["sensor.a"][0][1] == captured  # frozen value did not move
        # and it reflects the value AT snapshot time (100.0), not 223.0 — proving the
        # capture is by value (snapshot stores the raw float; encode_snapshot rounds)
        assert captured == 100.0

    def test_encode_snapshot_is_static_and_pure(self) -> None:
        # encode_snapshot is a @staticmethod: callable without an instance, touches
        # only its argument (executor-safe — no self / no _buckets access).
        # Uses an UNROUNDED online_seconds (272.46) and asserts the output is rounded
        # to 1 decimal (272.5) — this PINS the round() that moved into encode_snapshot,
        # so a future drop or wrong-ndigits is caught (a whole-number input would not).
        frozen = {
            "sensor.x": [(datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc), 272.46)]
        }
        out = AvailabilityStorage.encode_snapshot(frozen)
        assert out == {
            "v": 2,
            "base": "2026-11-03T18:00:00+00:00",
            "entities": {"sensor.x": [272.5]},
        }

    def test_snapshot_no_buckets_handles_cleanly(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        # monitored entity with no in-window buckets -> omitted (no empty key),
        # mirroring to_dict's drop-empty behavior.
        storage.record_online(
            "sensor.a", float(BUCKET_INTERVAL), now - timedelta(hours=48)
        )
        frozen = storage.snapshot(now, 24 * 3600 + _MARGIN, ["sensor.a"])
        assert frozen == {}
        assert AvailabilityStorage.encode_snapshot(frozen) == {}


class TestWindow24h:
    """New rolling 24h window: fixed now-24h lookback, min_required=1 (OLD-today)."""

    def test_24h_is_rolling_lookback(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        # 24h uses min_required=1 (faithful replacement for old rolling today), so
        # a couple of completed in-window buckets already yield a value.
        _fill_online(storage, "sensor.a", now - timedelta(hours=20), 3)
        result = storage.get_availability("sensor.a", "24h", now)
        assert result == 100.0

    def test_24h_value_shows_with_single_bucket(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        # min_required=1: one completed bucket inside the window is enough (this is
        # the whole point of 24h vs the 10% longer-window rule — immediate value).
        _fill_online(storage, "sensor.a", now - timedelta(hours=20), 2)
        assert storage.get_availability("sensor.a", "24h", now) == 100.0

    def test_24h_excludes_beyond_window(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        # only a bucket 30h back -> outside rolling 24h -> no relevant buckets -> None
        storage.record_online(
            "sensor.a", float(BUCKET_INTERVAL), now - timedelta(hours=30)
        )
        assert storage.get_availability("sensor.a", "24h", now) is None


class TestCalendarTodayDST:
    """Calendar 'today' + 25h retention on a DST fall-back day.

    America/New_York falls back 2024-11-03: local clocks run 01:59 -> 01:00, so
    that local day is 25h long. Just before the NEXT local midnight the calendar
    read reaches ~25h back. Retention MUST be 25h or the save path prunes the
    oldest buckets the read still needs.
    """

    def _ny(self):
        return ZoneInfo("America/New_York")

    def test_calendar_today_cutoff_is_local_midnight(
        self, storage: AvailabilityStorage
    ) -> None:
        tz = self._ny()
        orig = dt_util.DEFAULT_TIME_ZONE
        dt_util.set_default_time_zone(tz)
        try:
            # 23:55 local on the long day
            now = datetime(2024, 11, 3, 23, 55, tzinfo=tz).astimezone(timezone.utc)
            # a bucket just after local midnight (inside today) and one just before
            # it (yesterday -> excluded)
            after_midnight = datetime(2024, 11, 3, 0, 5, tzinfo=tz).astimezone(
                timezone.utc
            )
            before_midnight = datetime(2024, 11, 2, 23, 55, tzinfo=tz).astimezone(
                timezone.utc
            )
            storage.record_online("sensor.a", float(BUCKET_INTERVAL), after_midnight)
            storage.record_online("sensor.a", float(BUCKET_INTERVAL), before_midnight)
            val = storage.get_availability("sensor.a", "today", now)
            # today must include the after-midnight bucket -> a value, not None
            assert val is not None
        finally:
            dt_util.set_default_time_zone(orig)

    def test_25h_retention_covers_calendar_today_on_long_day(
        self, storage: AvailabilityStorage
    ) -> None:
        tz = self._ny()
        orig = dt_util.DEFAULT_TIME_ZONE
        dt_util.set_default_time_zone(tz)
        try:
            now = datetime(2024, 11, 3, 23, 55, tzinfo=tz).astimezone(timezone.utc)
            local_midnight = datetime(2024, 11, 3, 0, 0, tzinfo=tz).astimezone(
                timezone.utc
            )
            # earliest bucket of the calendar day, ~25h before `now`
            storage.record_online("sensor.a", float(BUCKET_INTERVAL), local_midnight)
            # a mid-day bucket so there is more than one sample
            storage.record_online(
                "sensor.a", float(BUCKET_INTERVAL), local_midnight + timedelta(hours=12)
            )

            # 25h retention (today) must KEEP the local-midnight bucket...
            kept = storage.to_dict(now, 25 * 3600 + _MARGIN, ["sensor.a"])
            starts_25 = {
                b.interval_start
                for b in AvailabilityStorage.from_dict(kept)._buckets["sensor.a"]
            }
            assert local_midnight in starts_25

            # ...whereas a naive 24h retention would PRUNE it (regression guard:
            # proves why _retention_hours('today') must be 25, not 24).
            kept_24 = storage.to_dict(now, 24 * 3600 + _MARGIN, ["sensor.a"])
            starts_24 = {
                b.interval_start
                for b in AvailabilityStorage.from_dict(kept_24)._buckets["sensor.a"]
            }
            assert local_midnight not in starts_24
        finally:
            dt_util.set_default_time_zone(orig)
