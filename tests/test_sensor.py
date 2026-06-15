"""Tests for Entity Availability sensor entities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.const import (
    CONF_BATTERY_THRESHOLD,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)
from custom_components.entity_availability.models import DeviceState
from custom_components.entity_availability.sensor import (
    AvailabilitySensor,
    DegradedDevicesSensor,
    GroupSummarySensor,
    LowBatteryCountSensor,
    MAX_STATE_LENGTH,
    OfflineCountSensor,
    OfflineDevicesSensor,
    RecentlyOfflineSensor,
    RecentlyRecoveredSensor,
    async_setup_entry,
)


@pytest.fixture
def mock_coordinator(mock_hass: HomeAssistant, mock_config_entry):
    """Create a mock coordinator with test device states."""
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(mock_hass, mock_config_entry)
        coord._device_states = {
            "binary_sensor.device_a": DeviceState(
                entity_id="binary_sensor.device_a",
                is_offline=False,
            ),
            "binary_sensor.device_b": DeviceState(
                entity_id="binary_sensor.device_b",
                is_offline=True,
                offline_since=datetime.now(timezone.utc) - timedelta(minutes=5),
            ),
            "binary_sensor.device_c": DeviceState(
                entity_id="binary_sensor.device_c",
                is_offline=False,
            ),
        }
    return coord


class TestOfflineCountSensor:
    """Tests for OfflineCountSensor."""

    def test_native_value_counts_offline(self, mock_coordinator, mock_hass):
        """Test native_value returns count of offline non-suppressed devices."""
        sensor = OfflineCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 1

    def test_native_value_excludes_suppressed(self, mock_coordinator, mock_hass):
        """Test that suppressed devices are not counted."""
        mock_coordinator._device_states["binary_sensor.device_b"].is_suppressed = True
        sensor = OfflineCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 0

    def test_native_value_zero_when_all_online(self, mock_coordinator, mock_hass):
        """Test zero offline count when all devices are online."""
        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = False
        sensor = OfflineCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 0

    def test_native_value_counts_multiple_offline(self, mock_coordinator, mock_hass):
        """Test counting multiple offline devices."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_offline = True
        mock_coordinator._device_states[
            "binary_sensor.device_a"
        ].offline_since = datetime.now(timezone.utc)
        sensor = OfflineCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 2

    def test_extra_state_attributes(self, mock_coordinator, mock_hass):
        """Test extra attributes contain offline device info."""
        sensor = OfflineCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert "binary_sensor.device_b" in attrs
        assert attrs["binary_sensor.device_b"]["offline"] is True

    def test_unique_id(self, mock_coordinator, mock_hass):
        """Test unique_id format."""
        sensor = OfflineCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.unique_id == "test_entry_id_offline_count"


class TestOfflineDevicesSensor:
    """Tests for OfflineDevicesSensor."""

    def test_native_value_shows_friendly_names(self, mock_coordinator, mock_hass):
        """Test that friendly names are shown for offline devices."""
        sensor = OfflineDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        # device_b is offline and has friendly_name "Device B"
        assert sensor.native_value == "Device B"

    def test_native_value_none_when_all_online(self, mock_coordinator, mock_hass):
        """Test 'None' string when no devices offline."""
        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = False
        sensor = OfflineDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_native_value_truncation(self, mock_coordinator, mock_hass):
        """Test that very long lists are truncated."""
        # Add many offline devices with long names
        for i in range(50):
            entity_id = f"binary_sensor.device_{i:03d}"
            mock_coordinator._device_states[entity_id] = DeviceState(
                entity_id=entity_id,
                is_offline=True,
            )
            mock_hass.states.async_set(
                entity_id,
                STATE_UNAVAILABLE,
                {"friendly_name": f"Very Long Device Name Number {i:03d}"},
            )

        # Add entity to monitored list so it's accessible
        sensor = OfflineDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        value = sensor.native_value
        assert len(value) <= MAX_STATE_LENGTH
        assert value.endswith("...")

    def test_native_value_excludes_suppressed(self, mock_coordinator, mock_hass):
        """Test suppressed devices excluded from list."""
        mock_coordinator._device_states["binary_sensor.device_b"].is_suppressed = True
        sensor = OfflineDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_extra_state_attributes_has_entities_list(
        self, mock_coordinator, mock_hass
    ):
        """Test extra attributes contain full entity list."""
        sensor = OfflineDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert "entities" in attrs
        assert "count" in attrs
        assert attrs["count"] == 1
        assert "binary_sensor.device_b" in attrs["entities"]

    def test_friendly_name_fallback(self, mock_coordinator, mock_hass):
        """Test fallback friendly name when no friendly_name attribute."""
        # Remove the state so it falls back to entity_id parsing
        mock_hass.states.async_remove("binary_sensor.device_b")
        sensor = OfflineDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        # Fallback: entity_id.split(".")[-1].replace("_", " ").title()
        assert sensor.native_value == "Device B"


class TestDegradedDevicesSensor:
    """Tests for DegradedDevicesSensor (Low Battery)."""

    def test_native_value_lists_low_battery(self, mock_coordinator, mock_hass):
        """Test native_value returns low battery device names."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 15
        sensor = DegradedDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert "Device A (15%)" in sensor.native_value

    def test_native_value_empty_when_none_degraded(self, mock_coordinator, mock_hass):
        """Test 'None' string when no devices degraded."""
        sensor = DegradedDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_excludes_suppressed_degraded(self, mock_coordinator, mock_hass):
        """Test suppressed degraded devices are excluded."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 10
        mock_coordinator._device_states["binary_sensor.device_a"].is_suppressed = True
        sensor = DegradedDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_extra_state_attributes_shows_battery(self, mock_coordinator, mock_hass):
        """Test that battery levels are reported in attributes."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 15
        sensor = DegradedDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert "devices" in attrs
        assert "count" in attrs
        assert attrs["count"] == 1
        assert attrs["devices"]["binary_sensor.device_a"] == "15%"


class TestLowBatteryCountSensor:
    """Tests for LowBatteryCountSensor."""

    def test_native_value_zero_when_none_degraded(self, mock_coordinator, mock_hass):
        """Test count is 0 when no devices have low battery."""
        sensor = LowBatteryCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 0

    def test_native_value_counts_low_battery(self, mock_coordinator, mock_hass):
        """Test count reflects low battery devices."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 10
        mock_coordinator._device_states["binary_sensor.device_b"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_b"].battery_level = 5
        sensor = LowBatteryCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 2

    def test_excludes_suppressed(self, mock_coordinator, mock_hass):
        """Test suppressed devices are excluded from count."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 10
        mock_coordinator._device_states["binary_sensor.device_a"].is_suppressed = True
        sensor = LowBatteryCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 0

    def test_excludes_degraded_without_battery(self, mock_coordinator, mock_hass):
        """Test degraded devices without battery_level are excluded."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = None
        sensor = LowBatteryCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 0

    def test_unique_id(self, mock_coordinator, mock_hass):
        """Test unique_id format."""
        sensor = LowBatteryCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.unique_id == "test_entry_id_low_battery_count"


class TestAvailabilitySensor:
    """Tests for AvailabilitySensor."""

    def test_native_value_with_data(self, mock_coordinator, mock_hass):
        """Test availability percentage calculation."""
        now = datetime.now(timezone.utc)
        storage = mock_coordinator.availability_storage

        # Fill data for all entities - 24 hours of 100% for "today"
        for entity_id in mock_coordinator.monitored_entities:
            for i in range(24):
                t = now - timedelta(hours=i)
                storage.record_online(entity_id, 3600.0, t)

        sensor = AvailabilitySensor(
            mock_coordinator, "Test Group", "test_group", "today", "test_entry_id"
        )
        sensor.hass = mock_hass
        value = sensor.native_value
        assert value == 100.0

    def test_native_value_none_insufficient_data(self, mock_coordinator, mock_hass):
        """Test returns None when insufficient data."""
        sensor = AvailabilitySensor(
            mock_coordinator, "Test Group", "test_group", "today", "test_entry_id"
        )
        sensor.hass = mock_hass
        # No data recorded
        assert sensor.native_value is None

    def test_native_value_excludes_suppressed(self, mock_coordinator, mock_hass):
        """Test suppressed devices excluded from availability calc."""
        now = datetime.now(timezone.utc)
        storage = mock_coordinator.availability_storage

        # Only fill data for device_a
        for i in range(24):
            t = now - timedelta(hours=i)
            storage.record_online("binary_sensor.device_a", 3600.0, t)

        # Suppress device_a - so it won't be counted
        mock_coordinator._device_states["binary_sensor.device_a"].is_suppressed = True

        sensor = AvailabilitySensor(
            mock_coordinator, "Test Group", "test_group", "today", "test_entry_id"
        )
        sensor.hass = mock_hass
        # device_b and device_c have no data, so None for them
        # device_a is suppressed, so excluded
        assert sensor.native_value is None

    def test_extra_state_attributes_per_device(self, mock_coordinator, mock_hass):
        """Test per_device breakdown in attributes."""
        sensor = AvailabilitySensor(
            mock_coordinator, "Test Group", "test_group", "today", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert "per_device" in attrs

    def test_unique_id_includes_window(self, mock_coordinator, mock_hass):
        """Test unique_id includes window identifier."""
        sensor = AvailabilitySensor(
            mock_coordinator, "Test Group", "test_group", "7d", "test_entry_id"
        )
        assert sensor.unique_id == "test_entry_id_availability_7d"


class TestGroupSummarySensor:
    """Tests for GroupSummarySensor."""

    def test_native_value_is_total_count(self, mock_coordinator, mock_hass):
        """Test native_value returns total entity count."""
        sensor = GroupSummarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 3

    def test_attributes_breakdown(self, mock_coordinator, mock_hass):
        """Test extra attributes contain full breakdown."""
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 85
        sensor = GroupSummarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert attrs["total_entities"] == 3
        assert attrs["offline"] == 1  # device_b
        assert attrs["online"] == 2
        assert attrs["suppressed"] == 0
        assert attrs["battery_powered"] == 1  # device_a has battery_level
        assert attrs["low_battery"] == 0

    def test_attributes_online_excludes_unprocessed_entities(
        self, mock_coordinator, mock_hass
    ):
        """online count uses monitored_entities as denominator, not device_states keys."""
        # Remove device_c from device_states (simulates entity not yet processed)
        del mock_coordinator._device_states["binary_sensor.device_c"]
        sensor = GroupSummarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        # total=3, offline=1 (device_b), suppressed=0, online should be 2 not 3
        assert attrs["total_entities"] == 3
        assert attrs["offline"] == 1
        assert attrs["online"] == 2

    def test_attributes_with_suppressed(self, mock_coordinator, mock_hass):
        """Test that suppressed entities are counted correctly."""
        mock_coordinator._device_states["binary_sensor.device_c"].is_suppressed = True
        sensor = GroupSummarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert attrs["suppressed"] == 1
        assert attrs["online"] == 1  # total(3) - offline(1) - suppressed(1) = 1

    def test_attributes_low_battery_count(self, mock_coordinator, mock_hass):
        """Test low battery count in attributes."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 10
        sensor = GroupSummarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert attrs["low_battery"] == 1
        assert attrs["battery_powered"] == 1

    def test_unique_id(self, mock_coordinator, mock_hass):
        """Test unique_id format."""
        sensor = GroupSummarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.unique_id == "test_entry_id_group_summary"


class TestRecentlyOfflineSensor:
    """Tests for RecentlyOfflineSensor."""

    def test_native_value_none_when_no_recent_transitions(
        self, mock_coordinator, mock_hass
    ):
        """Test 'None' string when no entity went offline recently."""
        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_native_value_shows_friendly_name(self, mock_coordinator, mock_hass):
        """Test friendly name shown when entity recently went offline."""
        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "Device B"

    def test_native_value_excludes_expired_window(self, mock_coordinator, mock_hass):
        """Test 'None' returned after window expires."""
        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_native_value_excludes_suppressed(self, mock_coordinator, mock_hass):
        """Test suppressed entities not shown."""
        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        mock_coordinator._device_states["binary_sensor.device_b"].is_suppressed = True
        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_extra_state_attributes_contains_entity(self, mock_coordinator, mock_hass):
        """Test attributes list recently offline entity ID."""
        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        sensor.native_value
        attrs = sensor.extra_state_attributes
        assert "binary_sensor.device_b" in attrs["entities"]
        assert attrs["count"] == 1
        assert attrs["window_minutes"] == 5

    def test_unique_id(self, mock_coordinator, mock_hass):
        """Test unique_id format."""
        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.unique_id == "test_entry_id_recently_offline"

    def test_extra_state_attributes_reuses_cached_devices_not_recomputed(
        self, mock_coordinator, mock_hass
    ):
        """extra_state_attributes reads _cached_devices set by native_value, not a second _refresh_cache."""
        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        # Drive native_value to populate _cached_devices
        sensor.native_value
        cached_before = sensor._cached_devices
        # Poison the underlying data so a re-computation would return different results
        for d in mock_coordinator._device_states.values():
            d.recently_offline_at = None
        # extra_state_attributes must reflect the already-cached result, not the poisoned state
        attrs = sensor.extra_state_attributes
        assert attrs["count"] == len(cached_before)
        assert attrs["entities"] == [d.entity_id for d in cached_before]


class TestRecentlyRecoveredSensor:
    """Tests for RecentlyRecoveredSensor."""

    def test_native_value_none_when_no_recent_recovery(
        self, mock_coordinator, mock_hass
    ):
        """Test 'None' string when no entity recovered recently."""
        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_native_value_shows_friendly_name(self, mock_coordinator, mock_hass):
        """Test friendly name shown when entity recently recovered."""
        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=2)
        )
        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "Device A"

    def test_native_value_excludes_expired_window(self, mock_coordinator, mock_hass):
        """Test 'None' returned after window expires."""
        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        )
        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_native_value_excludes_offline_devices(self, mock_coordinator, mock_hass):
        """Test currently offline devices not shown even if last_recovery set."""
        mock_coordinator._device_states["binary_sensor.device_b"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )
        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_extra_state_attributes_contains_entity(self, mock_coordinator, mock_hass):
        """Test attributes list recently recovered entity ID."""
        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=2)
        )
        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        sensor.native_value
        attrs = sensor.extra_state_attributes
        assert "binary_sensor.device_a" in attrs["entities"]
        assert attrs["count"] == 1
        assert attrs["window_minutes"] == 5

    def test_native_value_excludes_suppressed(self, mock_coordinator, mock_hass):
        """Test suppressed entities not shown."""
        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=2)
        )
        mock_coordinator._device_states["binary_sensor.device_a"].is_suppressed = True
        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_unique_id(self, mock_coordinator, mock_hass):
        """Test unique_id format."""
        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.unique_id == "test_entry_id_recently_recovered"

    def test_extra_state_attributes_reuses_cached_devices_not_recomputed(
        self, mock_coordinator, mock_hass
    ):
        """extra_state_attributes reads _cached_devices set by native_value, not a second _refresh_cache."""
        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=2)
        )
        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        # Drive native_value to populate _cached_devices
        sensor.native_value
        cached_before = sensor._cached_devices
        # Poison the underlying data so a re-computation would return different results
        for d in mock_coordinator._device_states.values():
            d.last_recovery = None
        # extra_state_attributes must reflect the already-cached result, not the poisoned state
        attrs = sensor.extra_state_attributes
        assert attrs["count"] == len(cached_before)
        assert attrs["entities"] == [d.entity_id for d in cached_before]

    def test_native_value_at_exactly_window_boundary(self, mock_coordinator, mock_hass):
        """Device recovered at exactly window_minutes*60 seconds ago should still show."""
        # Pin a fixed 'now' so the boundary comparison doesn't drift
        fixed_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        window_seconds = mock_coordinator.recovery_window_minutes * 60
        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            fixed_now - timedelta(seconds=window_seconds)
        )
        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        with patch("custom_components.entity_availability.sensor.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            # At exactly the boundary (<=) the device should still appear
            assert sensor.native_value == "Device A"

    def test_native_value_truncation_of_long_list(self, mock_coordinator, mock_hass):
        """Long list of recently recovered devices is truncated to MAX_STATE_LENGTH."""
        now = datetime.now(timezone.utc) - timedelta(minutes=1)
        # Add many online devices with recent recovery
        for i in range(50):
            entity_id = f"binary_sensor.recovered_{i:03d}"
            mock_coordinator._device_states[entity_id] = DeviceState(
                entity_id=entity_id,
                is_offline=False,
                last_recovery=now,
            )
            mock_hass.states.async_set(
                entity_id,
                "on",
                {"friendly_name": f"Very Long Recovered Device Name {i:03d}"},
            )

        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        value = sensor.native_value
        assert len(value) <= MAX_STATE_LENGTH
        assert value.endswith("...")


class TestRecentlyOfflineSensorBoundary:
    """Boundary and truncation tests for RecentlyOfflineSensor."""

    def test_native_value_at_exactly_window_boundary(self, mock_coordinator, mock_hass):
        """Device went offline at exactly window_minutes*60 seconds ago should still show."""
        # Pin a fixed 'now' so the boundary comparison doesn't drift
        fixed_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        window_seconds = mock_coordinator.recovery_window_minutes * 60
        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = fixed_now - timedelta(seconds=window_seconds)
        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        with patch("custom_components.entity_availability.sensor.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            # At exactly the boundary (<=) the device should still appear
            assert sensor.native_value == "Device B"

    def test_native_value_one_second_past_boundary_excluded(
        self, mock_coordinator, mock_hass
    ):
        """Device that went offline one second past the window should not appear."""
        window_seconds = mock_coordinator.recovery_window_minutes * 60
        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = datetime.now(timezone.utc) - timedelta(
            seconds=window_seconds + 1
        )
        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_native_value_truncation_of_long_list(self, mock_coordinator, mock_hass):
        """Long list of recently offline devices is truncated to MAX_STATE_LENGTH."""
        now = datetime.now(timezone.utc) - timedelta(minutes=1)
        for i in range(50):
            entity_id = f"binary_sensor.offline_{i:03d}"
            mock_coordinator._device_states[entity_id] = DeviceState(
                entity_id=entity_id,
                is_offline=True,
                recently_offline_at=now,
            )
            mock_hass.states.async_set(
                entity_id,
                "unavailable",
                {"friendly_name": f"Very Long Offline Device Name {i:03d}"},
            )

        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        value = sensor.native_value
        assert len(value) <= MAX_STATE_LENGTH
        assert value.endswith("...")


# ---------------------------------------------------------------------------
# async_setup_entry — combined path (lines 40-44)
# ---------------------------------------------------------------------------


async def test_sensor_setup_entry_combined_delegates(
    mock_hass: HomeAssistant,
) -> None:
    """sensor.async_setup_entry for combined entry delegates to combined_sensor module."""
    hass = mock_hass
    combined_entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="My Combined",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
            CONF_GROUP_NAME: "My Combined",
        },
        entry_id="comb_sensor_id",
    )
    combined_entry.add_to_hass(hass)

    added = []

    with patch(
        "custom_components.entity_availability.combined_sensor.async_setup_entry",
        new_callable=AsyncMock,
    ) as mock_combined:
        await async_setup_entry(hass, combined_entry, added.append)
        mock_combined.assert_called_once_with(hass, combined_entry, added.append)


# ---------------------------------------------------------------------------
# async_setup_entry — group path (lines 46-89)
# ---------------------------------------------------------------------------


async def test_sensor_setup_entry_group_with_battery_threshold_zero(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """When battery_threshold=0, DegradedDevicesSensor and LowBatteryCountSensor are NOT added."""
    hass = mock_hass
    config = dict(mock_config_data)
    config[CONF_BATTERY_THRESHOLD] = 0

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="No Battery Group",
        data=config,
        entry_id="no_bat_entry",
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coord

    added = []

    def capture(entities):
        added.extend(entities)

    await async_setup_entry(hass, entry, capture)

    types = [type(e).__name__ for e in added]
    assert "DegradedDevicesSensor" not in types
    assert "LowBatteryCountSensor" not in types
    # AvailabilitySensor(s) and core sensors should still be there
    assert "OfflineCountSensor" in types
    assert "AvailabilitySensor" in types


async def test_sensor_setup_entry_group_with_battery_threshold_positive(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """When battery_threshold>0, DegradedDevicesSensor and LowBatteryCountSensor ARE added."""
    hass = mock_hass
    config = dict(mock_config_data)
    config[CONF_BATTERY_THRESHOLD] = 20

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Battery Group",
        data=config,
        entry_id="bat_entry",
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coord

    added = []

    def capture(entities):
        added.extend(entities)

    await async_setup_entry(hass, entry, capture)

    types = [type(e).__name__ for e in added]
    assert "DegradedDevicesSensor" in types
    assert "LowBatteryCountSensor" in types


async def test_sensor_setup_entry_group_creates_availability_sensors_per_window(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """One AvailabilitySensor is created per configured window."""
    from custom_components.entity_availability.const import CONF_AVAILABILITY_WINDOWS

    hass = mock_hass
    config = dict(mock_config_data)
    config[CONF_AVAILABILITY_WINDOWS] = ["today", "7d", "3d"]

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Window Group",
        data=config,
        entry_id="window_entry",
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coord

    added = []

    def capture(entities):
        added.extend(entities)

    await async_setup_entry(hass, entry, capture)

    availability_sensors = [e for e in added if isinstance(e, AvailabilitySensor)]
    assert len(availability_sensors) == 3


# ---------------------------------------------------------------------------
# DegradedDevicesSensor._format_device — no-friendly-name fallback (line 258)
# ---------------------------------------------------------------------------


class TestDegradedDevicesFallbackName:
    """Test _format_device falls back to entity_id parsing when no friendly_name."""

    def test_format_device_no_state(self, mock_coordinator, mock_hass):
        """_format_device falls back to entity_id slug when state is None."""
        mock_hass.states.async_remove("binary_sensor.device_a")

        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 12
        sensor = DegradedDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        value = sensor.native_value
        assert "Device A (12%)" in value

    def test_format_device_state_without_friendly_name_attr(
        self, mock_coordinator, mock_hass
    ):
        """_format_device uses entity_id slug when state exists but has no friendly_name."""
        mock_hass.states.async_set("binary_sensor.device_a", "on", {})

        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 8
        sensor = DegradedDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        value = sensor.native_value
        assert "Device A (8%)" in value


# ---------------------------------------------------------------------------
# DegradedDevicesSensor.native_value truncation (line 240)
# ---------------------------------------------------------------------------


class TestDegradedDevicesTruncation:
    """Test native_value truncation for DegradedDevicesSensor."""

    def test_native_value_truncated_for_long_list(self, mock_coordinator, mock_hass):
        """native_value is truncated to MAX_STATE_LENGTH when the list is very long."""
        for i in range(60):
            eid = f"binary_sensor.bat_device_{i:03d}"
            mock_coordinator._device_states[eid] = DeviceState(
                entity_id=eid,
                is_degraded=True,
                battery_level=5,
            )
            mock_hass.states.async_set(
                eid,
                "on",
                {"friendly_name": f"Very Long Battery Device Name Number {i:03d}"},
            )

        sensor = DegradedDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        value = sensor.native_value
        assert len(value) <= MAX_STATE_LENGTH
        assert value.endswith("...")


# ---------------------------------------------------------------------------
# AvailabilitySensor.extra_state_attributes — suppressed skip (line 353)
# ---------------------------------------------------------------------------


class TestAvailabilitySensorAttributesSuppressed:
    """Test that suppressed devices are skipped in extra_state_attributes breakdown."""

    def test_suppressed_excluded_from_per_device(self, mock_coordinator, mock_hass):
        """Suppressed device does not appear in extra_state_attributes per_device."""
        now = datetime.now(timezone.utc)
        storage = mock_coordinator.availability_storage

        for eid in mock_coordinator.monitored_entities:
            storage.record_online(eid, 3600.0, now)

        mock_coordinator._device_states["binary_sensor.device_a"].is_suppressed = True

        sensor = AvailabilitySensor(
            mock_coordinator, "Test Group", "test_group", "today", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert "binary_sensor.device_a" not in attrs["per_device"]
        assert "binary_sensor.device_b" in attrs["per_device"]


# ---------------------------------------------------------------------------
# GroupSummarySensor — battery_map branch (line 408)
# ---------------------------------------------------------------------------


class TestGroupSummaryBatteryMap:
    """Test battery_powered calculation when battery_entity_map is set."""

    def test_battery_powered_from_map(self, mock_hass, mock_config_data):
        """battery_powered counts truthy values in battery_entity_map."""
        from custom_components.entity_availability.const import CONF_BATTERY_ENTITY_MAP

        config = dict(mock_config_data)
        config[CONF_BATTERY_ENTITY_MAP] = {
            "binary_sensor.device_a": "sensor.device_a_battery",
            "binary_sensor.device_b": "",  # falsy — not counted
            "binary_sensor.device_c": "sensor.device_c_battery",
        }
        entry = MockConfigEntry(
            version=1,
            domain=DOMAIN,
            title="Battery Map Group",
            data=config,
            entry_id="bat_map_entry",
        )

        with patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ):
            coord = EntityAvailabilityCoordinator(mock_hass, entry)
        coord._device_states = {
            "binary_sensor.device_a": DeviceState(entity_id="binary_sensor.device_a"),
            "binary_sensor.device_b": DeviceState(entity_id="binary_sensor.device_b"),
            "binary_sensor.device_c": DeviceState(entity_id="binary_sensor.device_c"),
        }

        sensor = GroupSummarySensor(
            coord, "Battery Map Group", "battery_map_group", entry.entry_id
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        # 2 truthy entries in the map
        assert attrs["battery_powered"] == 2


# ---------------------------------------------------------------------------
# RecentlyOfflineSensor._friendly_name — no-state fallback (line 490)
# ---------------------------------------------------------------------------


class TestRecentlyOfflineFriendlyNameFallback:
    """Test _friendly_name fallback when entity has no state."""

    def test_friendly_name_falls_back_when_no_state(self, mock_coordinator, mock_hass):
        """_friendly_name returns title-cased slug when hass has no state for entity."""
        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = datetime.now(timezone.utc) - timedelta(minutes=1)

        mock_hass.states.async_remove("binary_sensor.device_b")

        sensor = RecentlyOfflineSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "Device B"


# ---------------------------------------------------------------------------
# RecentlyRecoveredSensor._friendly_name — no-state fallback (line 555)
# ---------------------------------------------------------------------------


class TestRecentlyRecoveredFriendlyNameFallback:
    """Test _friendly_name fallback when entity has no state."""

    def test_friendly_name_falls_back_when_no_state(self, mock_coordinator, mock_hass):
        """_friendly_name returns title-cased slug when hass has no state."""
        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )

        mock_hass.states.async_remove("binary_sensor.device_a")

        sensor = RecentlyRecoveredSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "Device A"


# ---------------------------------------------------------------------------
# group_slug sanitization — forward slash and special chars (GH issue)
# ---------------------------------------------------------------------------


async def test_sensor_setup_entry_slug_sanitizes_slash_in_group_name(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """Group names with slashes produce valid entity IDs (no slash in slug)."""
    hass = mock_hass
    config = dict(mock_config_data)
    config[CONF_GROUP_NAME] = "Motion/Presence Sensors"

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Motion/Presence Sensors",
        data=config,
        entry_id="slash_entry",
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coord

    added = []

    def capture(entities):
        added.extend(entities)

    await async_setup_entry(hass, entry, capture)

    for entity in added:
        assert "/" not in entity.entity_id, (
            f"entity_id '{entity.entity_id}' contains forward slash"
        )


async def test_sensor_setup_entry_slug_fallback(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """All-special-char group name falls back to entry_id[:8] for sensor slug."""
    hass = mock_hass
    config = dict(mock_config_data)
    config[CONF_GROUP_NAME] = "!!!"

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="!!!",
        data=config,
        entry_id="abcdef1234567890",
        unique_id=f"{DOMAIN}_fallback_slug_sensor",
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coord

    added = []

    def capture(entities):
        added.extend(entities)

    await async_setup_entry(hass, entry, capture)

    assert len(added) > 0
    for entity in added:
        assert "abcdef12" in entity.entity_id
