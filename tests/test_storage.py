"""Tests for the Entity Availability storage module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.entity_availability.const import BUCKET_INTERVAL, BUCKETS_MAX
from custom_components.entity_availability.storage import AvailabilityStorage


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
        """Test that 'today' only requires 1 bucket."""
        storage.record_online("sensor.test", float(BUCKET_INTERVAL), now)
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

    def test_to_dict_empty(self, storage: AvailabilityStorage) -> None:
        """Test serialization of empty storage."""
        assert storage.to_dict() == {}

    def test_to_dict_with_data(
        self, storage: AvailabilityStorage, now: datetime
    ) -> None:
        """Test serialization with data."""
        storage.record_online("sensor.test", 100.0, now)
        result = storage.to_dict()

        assert "sensor.test" in result
        assert len(result["sensor.test"]) == 1
        bucket_data = result["sensor.test"][0]
        assert "s" in bucket_data
        assert bucket_data["o"] == 100.0

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

        serialized = storage.to_dict()
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

        serialized = storage.to_dict()
        restored = AvailabilityStorage.from_dict(serialized)

        expected_start = now.replace(minute=30, second=0, microsecond=0)
        assert restored._buckets["sensor.test"][0].interval_start == expected_start


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
