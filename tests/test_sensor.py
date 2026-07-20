"""Tests for Entity Availability sensor entities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
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
    AffectedAreasCountSensor,
    AffectedAreasRecentlyOfflineSensor,
    AffectedAreasRecentlyRecoveredSensor,
    AffectedAreasSensor,
    AvailabilitySensor,
    DegradedDevicesSensor,
    GroupSummarySensor,
    LowBatteryCountSensor,
    MAX_STATE_LENGTH,
    MTTRSensor,
    OfflineCountSensor,
    OfflineDevicesSensor,
    RecentlyOfflineSensor,
    RecentlyRecoveredSensor,
    MTBFSensor,
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
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = True
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
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 10
        mock_coordinator._device_states["binary_sensor.device_a"].is_suppressed = True
        sensor = DegradedDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"

    def test_extra_state_attributes_shows_battery(self, mock_coordinator, mock_hass):
        """Test that battery levels are reported in attributes."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = True
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
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 10
        mock_coordinator._device_states["binary_sensor.device_b"].is_low_battery = True
        mock_coordinator._device_states["binary_sensor.device_b"].battery_level = 5
        sensor = LowBatteryCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 2

    def test_excludes_suppressed(self, mock_coordinator, mock_hass):
        """Test suppressed devices are excluded from count."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 10
        mock_coordinator._device_states["binary_sensor.device_a"].is_suppressed = True
        sensor = LowBatteryCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 0

    def test_excludes_degraded_without_battery(self, mock_coordinator, mock_hass):
        """Test degraded-only devices (stale, not low battery) are excluded."""
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


class TestStaleButHealthyBatteryNotLowBattery:
    """Regression tests: stale entities with battery above threshold must not appear as low battery."""

    def test_stale_healthy_battery_not_counted_by_low_battery_count_sensor(
        self, mock_coordinator, mock_hass
    ):
        """Stale entity with battery above threshold must NOT be counted as low battery."""
        # Entity is stale (is_degraded=True, is_stale=True) but battery is above threshold
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].is_stale = True
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = False
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 50
        sensor = LowBatteryCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 0

    def test_stale_healthy_battery_not_listed_by_degraded_devices_sensor(
        self, mock_coordinator, mock_hass
    ):
        """Stale entity with battery above threshold must NOT appear in low battery list."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].is_stale = True
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = False
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 50
        sensor = DegradedDevicesSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "None"
        assert sensor.extra_state_attributes["count"] == 0

    def test_stale_healthy_battery_not_in_group_summary_low_battery_entities(
        self, mock_coordinator, mock_hass
    ):
        """Stale entity with battery above threshold must NOT be in low_battery_entities."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].is_stale = True
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = False
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 50
        sensor = GroupSummarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert attrs["low_battery"] == 0
        assert "binary_sensor.device_a" not in attrs["low_battery_entities"]

    def test_genuine_low_battery_still_counted(self, mock_coordinator, mock_hass):
        """Entity with battery genuinely below threshold must be counted as low battery."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 10
        sensor = LowBatteryCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == 1

    def test_genuine_low_battery_appears_in_group_summary(
        self, mock_coordinator, mock_hass
    ):
        """Entity with battery below threshold must appear in group summary low_battery_entities."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_degraded = True
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = True
        mock_coordinator._device_states["binary_sensor.device_a"].battery_level = 10
        sensor = GroupSummarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert attrs["low_battery"] == 1
        assert "binary_sensor.device_a" in attrs["low_battery_entities"]


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

    def test_no_state_class(self, mock_coordinator, mock_hass):
        """Count only changes on group edit — must not generate statistics."""
        sensor = GroupSummarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.state_class is None

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
        assert "display_names" in attrs
        assert set(attrs["display_names"].keys()) == {
            "binary_sensor.device_a",
            "binary_sensor.device_b",
            "binary_sensor.device_c",
        }

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
        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = True
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

        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = True
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

        mock_coordinator._device_states["binary_sensor.device_a"].is_low_battery = True
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
                is_low_battery=True,
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


# ---------------------------------------------------------------------------
# AvailabilitySensor — sub-percent drift quantization (v5.5 audit F-EA-1)
# ---------------------------------------------------------------------------


class TestAvailabilitySensorQuantization:
    """``native_value`` and ``per_device`` are quantized to 1 decimal."""

    def test_native_value_one_decimal(self, mock_coordinator, mock_hass):
        """``native_value`` returns a 1-decimal float (public API unchanged)."""
        now = datetime.now(timezone.utc)
        storage = mock_coordinator.availability_storage
        for eid in mock_coordinator.monitored_entities:
            for i in range(24):
                storage.record_online(eid, 3600.0, now - timedelta(hours=i))

        sensor = AvailabilitySensor(
            mock_coordinator, "Test Group", "test_group", "today", "test_entry_id"
        )
        sensor.hass = mock_hass
        value = sensor.native_value
        assert value == 100.0
        assert isinstance(value, float)

    def test_per_device_values_one_decimal(self, mock_coordinator, mock_hass):
        """``extra_state_attributes['per_device']`` values rounded to 1 decimal.

        Previously these were unrounded floats — drift on every tick defeated
        ``WriteDedupMixin``. Must match ``native_value`` precision.
        """
        sensor = AvailabilitySensor(
            mock_coordinator, "Test Group", "test_group", "today", "test_entry_id"
        )
        sensor.hass = mock_hass

        storage = mock_coordinator.availability_storage

        def _drifty(_eid, _window, _now):
            # Unrounded float typical of pre-fix storage behaviour.
            return 99.84736821

        with patch.object(storage, "get_availability", side_effect=_drifty):
            per_device = sensor.extra_state_attributes["per_device"]
        for eid, value in per_device.items():
            assert value is None or value == 99.8, (
                f"{eid} must be 1-decimal rounded, got {value!r}"
            )

    def test_per_device_preserves_none(self, mock_coordinator, mock_hass):
        """Devices with no data still report ``None`` (not rounded to 0.0)."""
        sensor = AvailabilitySensor(
            mock_coordinator, "Test Group", "test_group", "today", "test_entry_id"
        )
        sensor.hass = mock_hass
        per_device = sensor.extra_state_attributes["per_device"]
        for value in per_device.values():
            assert value is None

    def test_dedup_catches_sub_percent_drift(self, mock_coordinator, mock_hass):
        """1000 ticks with sub-0.1% jitter must produce <10 state writes.

        Reproduces v5.5 audit F-EA-1: rolling-window numerator drifts by
        ~SCAN_INTERVAL/window each tick. Pre-fix the unrounded per_device
        floats moved on every tick, defeating dedup. Post-fix both
        native_value and per_device are rounded to 1 decimal — sub-0.1%
        drift collapses below the rounding step.

        Boundary-flip behaviour is covered separately in test_write_dedup.py.
        """
        sensor = AvailabilitySensor(
            mock_coordinator, "Test Group", "test_group", "today", "test_entry_id"
        )
        sensor.hass = mock_hass

        storage = mock_coordinator.availability_storage
        tick = {"n": 0}

        def _drifting(_eid, _window, _now):
            # 30s/86400s ≈ 0.035% per-tick drift around 99.5% — below 0.1%
            # rounding step so the published values stay flat.
            return 99.5 + (tick["n"] * 30 / 86400)

        with (
            patch.object(storage, "get_availability", side_effect=_drifting),
            patch.object(sensor, "async_write_ha_state") as write,
        ):
            for i in range(1000):
                tick["n"] = i
                sensor._handle_coordinator_update()

        assert write.call_count < 10, (
            f"dedup failed to suppress sub-0.1% drift: {write.call_count} writes"
        )


# ---------------------------------------------------------------------------
# _resolve_display_name — unit tests
# ---------------------------------------------------------------------------


from custom_components.entity_availability.sensor import _resolve_display_name  # noqa: E402
from custom_components.entity_availability.const import CONF_USE_DEVICE_NAMES  # noqa: E402


class TestResolveDisplayName:
    """Unit tests for the module-level _resolve_display_name helper."""

    def test_returns_entity_friendly_name_by_default(self, mock_hass):
        """use_device_names=False returns hass.states friendly_name."""
        result = _resolve_display_name(mock_hass, "binary_sensor.device_a", False)
        assert result == "Device A"

    def test_returns_entity_slug_when_no_state(self, mock_hass):
        """No state object falls back to entity_id slug."""
        mock_hass.states.async_remove("binary_sensor.device_a")
        result = _resolve_display_name(mock_hass, "binary_sensor.device_a", False)
        assert result == "Device A"

    def test_returns_entity_slug_when_no_friendly_name(self, mock_hass):
        """State exists but no friendly_name attr falls back to slug."""
        mock_hass.states.async_set("binary_sensor.device_a", "on", {})
        result = _resolve_display_name(mock_hass, "binary_sensor.device_a", False)
        assert result == "Device A"

    def test_returns_device_name_when_use_device_names_true(self, mock_hass):
        """use_device_names=True returns device.name from device registry."""
        mock_entry = MagicMock()
        mock_entry.device_id = "device_abc123"
        mock_device = MagicMock()
        mock_device.name_by_user = None
        mock_device.name = "Test Device"

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry
        mock_dr = MagicMock()
        mock_dr.async_get.return_value = mock_device

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            result = _resolve_display_name(mock_hass, "binary_sensor.device_a", True)
        assert result == "Test Device"

    def test_returns_device_name_by_user_preferred_over_name(self, mock_hass):
        """device.name_by_user takes priority over device.name."""
        mock_entry = MagicMock()
        mock_entry.device_id = "device_abc123"
        mock_device = MagicMock()
        mock_device.name_by_user = "User Preferred Name"
        mock_device.name = "Hardware Name"

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry
        mock_dr = MagicMock()
        mock_dr.async_get.return_value = mock_device

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            result = _resolve_display_name(mock_hass, "binary_sensor.device_a", True)
        assert result == "User Preferred Name"

    def test_falls_back_to_friendly_name_when_no_device_id(self, mock_hass):
        """Entity has no device_id → falls back to friendly_name."""
        mock_entry = MagicMock()
        mock_entry.device_id = None

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry

        with patch(
            "custom_components.entity_availability.helpers.er.async_get",
            return_value=mock_er,
        ):
            result = _resolve_display_name(mock_hass, "binary_sensor.device_a", True)
        assert result == "Device A"

    def test_falls_back_to_friendly_name_when_device_not_found(self, mock_hass):
        """dr.async_get returns None → friendly_name fallback."""
        mock_entry = MagicMock()
        mock_entry.device_id = "device_abc123"

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry
        mock_dr = MagicMock()
        mock_dr.async_get.return_value = None

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            result = _resolve_display_name(mock_hass, "binary_sensor.device_a", True)
        assert result == "Device A"

    def test_falls_back_to_friendly_name_when_device_has_no_name(self, mock_hass):
        """Device exists but name and name_by_user both None → fallback."""
        mock_entry = MagicMock()
        mock_entry.device_id = "device_abc123"
        mock_device = MagicMock()
        mock_device.name_by_user = None
        mock_device.name = None

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry
        mock_dr = MagicMock()
        mock_dr.async_get.return_value = mock_device

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            result = _resolve_display_name(mock_hass, "binary_sensor.device_a", True)
        assert result == "Device A"

    def test_falls_back_when_entity_not_in_registry(self, mock_hass):
        """er.async_get returns None → friendly_name fallback."""
        mock_er = MagicMock()
        mock_er.async_get.return_value = None

        with patch(
            "custom_components.entity_availability.helpers.er.async_get",
            return_value=mock_er,
        ):
            result = _resolve_display_name(mock_hass, "binary_sensor.device_a", True)
        assert result == "Device A"


# ---------------------------------------------------------------------------
# OfflineDevicesSensor — use_device_names integration
# ---------------------------------------------------------------------------


class TestOfflineDevicesSensorWithDeviceNames:
    """Test OfflineDevicesSensor respects use_device_names coordinator flag."""

    def _make_coordinator(self, mock_hass, mock_config_entry, use_device_names: bool):
        config = dict(mock_config_entry.data)
        config[CONF_USE_DEVICE_NAMES] = use_device_names
        entry = MockConfigEntry(
            version=1,
            domain=mock_config_entry.domain,
            title=mock_config_entry.title,
            data=config,
            entry_id="dn_offline_entry",
        )
        with patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ):
            coord = EntityAvailabilityCoordinator(mock_hass, entry)
        coord._device_states = {
            "binary_sensor.device_b": DeviceState(
                entity_id="binary_sensor.device_b",
                is_offline=True,
                offline_since=datetime.now(timezone.utc) - timedelta(minutes=5),
            ),
        }
        return coord

    def test_uses_device_name_when_flag_set(self, mock_hass, mock_config_entry):
        """use_device_names=True in entry.data → device name appears in native_value."""
        coord = self._make_coordinator(mock_hass, mock_config_entry, True)

        mock_entry = MagicMock()
        mock_entry.device_id = "device_abc123"
        mock_device = MagicMock()
        mock_device.name_by_user = None
        mock_device.name = "Test Device"

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry
        mock_dr = MagicMock()
        mock_dr.async_get.return_value = mock_device

        sensor = OfflineDevicesSensor(
            coord, "Test Group", "test_group", "dn_offline_entry"
        )
        sensor.hass = mock_hass

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            value = sensor.native_value

        assert "Test Device" in value

    def test_uses_friendly_name_when_flag_false(self, mock_hass, mock_config_entry):
        """use_device_names=False → friendly_name used (regression guard)."""
        coord = self._make_coordinator(mock_hass, mock_config_entry, False)
        sensor = OfflineDevicesSensor(
            coord, "Test Group", "test_group", "dn_offline_entry"
        )
        sensor.hass = mock_hass
        assert sensor.native_value == "Device B"

    def test_falls_back_to_friendly_name_for_entity_without_device(
        self, mock_hass, mock_config_entry
    ):
        """use_device_names=True but entity has no device_id → friendly_name."""
        coord = self._make_coordinator(mock_hass, mock_config_entry, True)

        mock_entry = MagicMock()
        mock_entry.device_id = None
        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry

        sensor = OfflineDevicesSensor(
            coord, "Test Group", "test_group", "dn_offline_entry"
        )
        sensor.hass = mock_hass

        with patch(
            "custom_components.entity_availability.helpers.er.async_get",
            return_value=mock_er,
        ):
            value = sensor.native_value

        assert value == "Device B"


# ---------------------------------------------------------------------------
# RecentlyOfflineSensor — use_device_names integration
# ---------------------------------------------------------------------------


class TestRecentlyOfflineSensorWithDeviceNames:
    """Test RecentlyOfflineSensor respects use_device_names coordinator flag."""

    def _make_coordinator(self, mock_hass, mock_config_entry, use_device_names: bool):
        config = dict(mock_config_entry.data)
        config[CONF_USE_DEVICE_NAMES] = use_device_names
        entry = MockConfigEntry(
            version=1,
            domain=mock_config_entry.domain,
            title=mock_config_entry.title,
            data=config,
            entry_id="dn_recent_offline_entry",
        )
        with patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ):
            coord = EntityAvailabilityCoordinator(mock_hass, entry)
        coord._device_states = {
            "binary_sensor.device_b": DeviceState(
                entity_id="binary_sensor.device_b",
                is_offline=True,
                recently_offline_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ),
        }
        return coord

    def test_uses_device_name_when_flag_set(self, mock_hass, mock_config_entry):
        """use_device_names=True → device name appears in native_value."""
        coord = self._make_coordinator(mock_hass, mock_config_entry, True)

        mock_entry = MagicMock()
        mock_entry.device_id = "device_abc123"
        mock_device = MagicMock()
        mock_device.name_by_user = None
        mock_device.name = "Test Device"

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry
        mock_dr = MagicMock()
        mock_dr.async_get.return_value = mock_device

        sensor = RecentlyOfflineSensor(
            coord, "Test Group", "test_group", "dn_recent_offline_entry"
        )
        sensor.hass = mock_hass

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            value = sensor.native_value

        assert "Test Device" in value

    def test_falls_back_gracefully_when_no_device(self, mock_hass, mock_config_entry):
        """use_device_names=True but no device found → friendly_name fallback."""
        coord = self._make_coordinator(mock_hass, mock_config_entry, True)

        mock_entry = MagicMock()
        mock_entry.device_id = None
        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry

        sensor = RecentlyOfflineSensor(
            coord, "Test Group", "test_group", "dn_recent_offline_entry"
        )
        sensor.hass = mock_hass

        with patch(
            "custom_components.entity_availability.helpers.er.async_get",
            return_value=mock_er,
        ):
            value = sensor.native_value

        assert value == "Device B"


# ---------------------------------------------------------------------------
# RecentlyRecoveredSensor — use_device_names integration
# ---------------------------------------------------------------------------


class TestRecentlyRecoveredSensorWithDeviceNames:
    """Test RecentlyRecoveredSensor respects use_device_names coordinator flag."""

    def _make_coordinator(self, mock_hass, mock_config_entry, use_device_names: bool):
        config = dict(mock_config_entry.data)
        config[CONF_USE_DEVICE_NAMES] = use_device_names
        entry = MockConfigEntry(
            version=1,
            domain=mock_config_entry.domain,
            title=mock_config_entry.title,
            data=config,
            entry_id="dn_recent_recovered_entry",
        )
        with patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ):
            coord = EntityAvailabilityCoordinator(mock_hass, entry)
        coord._device_states = {
            "binary_sensor.device_a": DeviceState(
                entity_id="binary_sensor.device_a",
                is_offline=False,
                last_recovery=datetime.now(timezone.utc) - timedelta(minutes=2),
            ),
        }
        return coord

    def test_uses_device_name_when_flag_set(self, mock_hass, mock_config_entry):
        """use_device_names=True → device name appears in native_value."""
        coord = self._make_coordinator(mock_hass, mock_config_entry, True)

        mock_entry = MagicMock()
        mock_entry.device_id = "device_abc123"
        mock_device = MagicMock()
        mock_device.name_by_user = None
        mock_device.name = "Test Device"

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry
        mock_dr = MagicMock()
        mock_dr.async_get.return_value = mock_device

        sensor = RecentlyRecoveredSensor(
            coord, "Test Group", "test_group", "dn_recent_recovered_entry"
        )
        sensor.hass = mock_hass

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            value = sensor.native_value

        assert "Test Device" in value

    def test_falls_back_gracefully_when_no_device(self, mock_hass, mock_config_entry):
        """use_device_names=True but no device found → friendly_name fallback."""
        coord = self._make_coordinator(mock_hass, mock_config_entry, True)

        mock_entry = MagicMock()
        mock_entry.device_id = None
        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry

        sensor = RecentlyRecoveredSensor(
            coord, "Test Group", "test_group", "dn_recent_recovered_entry"
        )
        sensor.hass = mock_hass

        with patch(
            "custom_components.entity_availability.helpers.er.async_get",
            return_value=mock_er,
        ):
            value = sensor.native_value

        assert value == "Device A"


# ---------------------------------------------------------------------------
# AffectedAreas group sensors
# ---------------------------------------------------------------------------


class TestAffectedAreasSensors:
    """Tests for the 4 affected-areas group sensors."""

    # ------------------------------------------------------------------
    # AffectedAreasCountSensor
    # ------------------------------------------------------------------

    def test_count_entity_with_area(self, mock_coordinator, mock_hass):
        """Offline entity with area → count=1."""
        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasCountSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == 1

    def test_count_two_offline_same_area_deduped(self, mock_coordinator, mock_hass):
        """Two offline entities in the same area → count=1 (dedup)."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_offline = True
        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasCountSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == 1

    def test_count_no_area_uses_sentinel(self, mock_coordinator, mock_hass):
        """Offline entity with no area → counts as (No Area) sentinel → count=1."""
        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value=None,
        ):
            sensor = AffectedAreasCountSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == 1

    def test_count_suppressed_excluded(self, mock_coordinator, mock_hass):
        """Suppressed offline entity excluded → count=0."""
        mock_coordinator._device_states["binary_sensor.device_b"].is_suppressed = True
        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasCountSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == 0

    def test_count_unique_id(self, mock_coordinator, mock_hass):
        """unique_id follows expected format."""
        sensor = AffectedAreasCountSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.unique_id == "test_entry_id_affected_areas_count"

    # ------------------------------------------------------------------
    # AffectedAreasSensor
    # ------------------------------------------------------------------

    def test_areas_state_single_area(self, mock_coordinator, mock_hass):
        """Offline entity with area → state='Kitchen'."""
        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == "Kitchen"

    def test_areas_state_none_when_no_offline(self, mock_coordinator, mock_hass):
        """All online → state='None'."""
        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = False
        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == "None"

    def test_areas_multiple_areas_sorted(self, mock_coordinator, mock_hass):
        """Multiple offline entities in different areas → sorted output."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_offline = True
        area_map = {
            "binary_sensor.device_a": "Garage",
            "binary_sensor.device_b": "Kitchen",
            "binary_sensor.device_c": None,
        }

        def _area_side_effect(hass, entity_id):
            return area_map.get(entity_id)

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            side_effect=_area_side_effect,
        ):
            sensor = AffectedAreasSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            value = sensor.native_value
        # Sorted: Garage, Kitchen
        assert value == "Garage, Kitchen"

    def test_areas_unassigned_entities_in_attrs(self, mock_coordinator, mock_hass):
        """Offline entity with no area shows up in unassigned_entities attr."""
        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value=None,
        ):
            sensor = AffectedAreasSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            sensor.native_value  # populate cache
            attrs = sensor.extra_state_attributes
        assert "binary_sensor.device_b" in attrs["unassigned_entities"]
        assert attrs["count"] == 1  # (No Area) sentinel appears in areas list

    def test_areas_truncation_at_255(self, mock_coordinator, mock_hass):
        """Long area list is truncated to MAX_STATE_LENGTH."""
        for i in range(50):
            eid = f"binary_sensor.device_x{i:03d}"
            mock_coordinator._device_states[eid] = DeviceState(
                entity_id=eid, is_offline=True
            )

        def _area_side_effect(hass, entity_id):
            return f"Very Long Area Name Number {entity_id[-3:]}"

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            side_effect=_area_side_effect,
        ):
            sensor = AffectedAreasSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            value = sensor.native_value
        assert len(value) <= MAX_STATE_LENGTH
        assert value.endswith("...")

    # ------------------------------------------------------------------
    # AffectedAreasRecentlyOfflineSensor
    # ------------------------------------------------------------------

    def test_recently_offline_entity_with_area_in_window(
        self, mock_coordinator, mock_hass
    ):
        """Offline entity with recent offline_at within window → area shown."""
        from datetime import timedelta

        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = datetime.now(timezone.utc) - timedelta(minutes=1)

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasRecentlyOfflineSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == "Kitchen"

    def test_recently_offline_outside_window_excluded(
        self, mock_coordinator, mock_hass
    ):
        """recently_offline_at outside window → excluded."""
        from datetime import timedelta

        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasRecentlyOfflineSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == "None"

    def test_recently_offline_no_timestamp_excluded(self, mock_coordinator, mock_hass):
        """Entity without recently_offline_at → excluded."""
        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasRecentlyOfflineSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == "None"

    def test_recently_offline_attrs_have_window(self, mock_coordinator, mock_hass):
        """extra_state_attributes includes window_minutes."""
        from datetime import timedelta

        mock_coordinator._device_states[
            "binary_sensor.device_b"
        ].recently_offline_at = datetime.now(timezone.utc) - timedelta(minutes=1)

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasRecentlyOfflineSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            sensor.native_value
            attrs = sensor.extra_state_attributes
        assert "window_minutes" in attrs
        assert attrs["count"] == 1

    # ------------------------------------------------------------------
    # AffectedAreasRecentlyRecoveredSensor
    # ------------------------------------------------------------------

    def test_recently_recovered_all_online_with_recent_recovery(
        self, mock_coordinator, mock_hass
    ):
        """All non-suppressed entities online + recent last_recovery → area shown."""
        from datetime import timedelta

        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=2)
        )

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasRecentlyRecoveredSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            # device_b is still offline → Kitchen NOT fully recovered
            assert sensor.native_value == "None"

    def test_recently_recovered_one_entity_still_offline_area_absent(
        self, mock_coordinator, mock_hass
    ):
        """If one entity in area is still offline, area is absent."""
        from datetime import timedelta

        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=2)
        )
        # device_b is already offline by default — so Kitchen is not fully recovered

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasRecentlyRecoveredSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == "None"

    def test_recently_recovered_recovery_outside_window_absent(
        self, mock_coordinator, mock_hass
    ):
        """Recovery outside window → area absent."""
        from datetime import timedelta

        # Make all online
        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = False
        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        )

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasRecentlyRecoveredSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == "None"

    def test_recently_recovered_suppressed_entity_not_blocking(
        self, mock_coordinator, mock_hass
    ):
        """Suppressed entity not counted in 'all offline' check → area can still qualify."""
        from datetime import timedelta

        # device_b is offline but suppressed — should not block recovery
        mock_coordinator._device_states["binary_sensor.device_b"].is_suppressed = True
        mock_coordinator._device_states["binary_sensor.device_a"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=2)
        )
        # device_c is online with no recovery
        area_map = {
            "binary_sensor.device_a": "Kitchen",
            "binary_sensor.device_b": "Kitchen",  # suppressed → ignored
            "binary_sensor.device_c": "Kitchen",
        }

        def _area_side_effect(hass, entity_id):
            return area_map.get(entity_id)

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            side_effect=_area_side_effect,
        ):
            sensor = AffectedAreasRecentlyRecoveredSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            # device_a and device_c both online, device_a has recent recovery → Kitchen qualifies
            assert sensor.native_value == "Kitchen"

    def test_recently_recovered_attrs_have_window(self, mock_coordinator, mock_hass):
        """extra_state_attributes includes window_minutes."""
        from datetime import timedelta

        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = False
        mock_coordinator._device_states["binary_sensor.device_b"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value="Kitchen",
        ):
            sensor = AffectedAreasRecentlyRecoveredSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            sensor.native_value
            attrs = sensor.extra_state_attributes
        assert "window_minutes" in attrs

    def test_recently_offline_truncation(self, mock_coordinator, mock_hass):
        """Long area list in AffectedAreasRecentlyOfflineSensor is truncated."""
        from datetime import timedelta

        # Add 50 offline entities with recently_offline_at within window
        now = datetime.now(timezone.utc) - timedelta(minutes=1)
        for i in range(50):
            eid = f"binary_sensor.offline_{i:03d}"
            mock_coordinator._device_states[eid] = DeviceState(
                entity_id=eid,
                is_offline=True,
                recently_offline_at=now,
            )

        def _area_side_effect(hass, entity_id):
            return f"Very Long Area Name For Entity {entity_id[-3:]}"

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            side_effect=_area_side_effect,
        ):
            sensor = AffectedAreasRecentlyOfflineSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            value = sensor.native_value
        assert len(value) <= MAX_STATE_LENGTH
        assert value.endswith("...")

    def test_recently_recovered_no_area_uses_sentinel(
        self, mock_coordinator, mock_hass
    ):
        """Entities with no area grouped under (No Area) sentinel; all online + recent recovery → sentinel qualifies."""
        from datetime import timedelta

        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = False
        mock_coordinator._device_states["binary_sensor.device_b"].last_recovery = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )
        # All entities have no area → all go into (No Area) bucket
        # device_b is online with recent recovery; device_a, device_c are online with no recovery
        # → (No Area) bucket: all online + recent recovery event → qualifies
        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            return_value=None,
        ):
            sensor = AffectedAreasRecentlyRecoveredSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            assert sensor.native_value == "(No Area)"

    def test_recently_recovered_truncation(self, mock_coordinator, mock_hass):
        """Long area list in AffectedAreasRecentlyRecoveredSensor is truncated."""
        from datetime import timedelta

        # All devices online, all with recent recovery, all in unique areas
        for d in mock_coordinator._device_states.values():
            d.is_offline = False
            d.last_recovery = datetime.now(timezone.utc) - timedelta(minutes=1)
        # Add more devices in unique areas
        for i in range(50):
            eid = f"binary_sensor.rec_{i:03d}"
            mock_coordinator._device_states[eid] = DeviceState(
                entity_id=eid,
                is_offline=False,
                last_recovery=datetime.now(timezone.utc) - timedelta(minutes=1),
            )

        def _area_side_effect(hass, entity_id):
            return f"Very Long Area Name For Entity {entity_id[-3:]}"

        with patch(
            "custom_components.entity_availability.sensor.resolve_area_name",
            side_effect=_area_side_effect,
        ):
            sensor = AffectedAreasRecentlyRecoveredSensor(
                mock_coordinator, "Test Group", "test_group", "test_entry_id"
            )
            sensor.hass = mock_hass
            value = sensor.native_value
        assert len(value) <= MAX_STATE_LENGTH
        assert value.endswith("...")


# ---------------------------------------------------------------------------
# resolve_area_name helper — branch coverage
# ---------------------------------------------------------------------------


class TestResolveAreaName:
    """Unit tests for resolve_area_name helper in helpers.py."""

    def test_returns_none_when_entity_not_in_registry(self, mock_hass):
        """Entity not in registry → None."""
        from custom_components.entity_availability.helpers import resolve_area_name

        mock_er = MagicMock()
        mock_er.async_get.return_value = None
        with patch(
            "custom_components.entity_availability.helpers.er.async_get",
            return_value=mock_er,
        ):
            result = resolve_area_name(mock_hass, "binary_sensor.device_a")
        assert result is None

    def test_returns_area_from_entity_area_id(self, mock_hass):
        """Entity has area_id → returns area.name."""
        from custom_components.entity_availability.helpers import resolve_area_name

        mock_entry = MagicMock()
        mock_entry.area_id = "area_abc"
        mock_entry.device_id = None

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry

        mock_area = MagicMock()
        mock_area.name = "Kitchen"
        mock_ar = MagicMock()
        mock_ar.async_get_area.return_value = mock_area

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.ar.async_get",
                return_value=mock_ar,
            ),
        ):
            result = resolve_area_name(mock_hass, "binary_sensor.device_a")
        assert result == "Kitchen"

    def test_falls_back_to_device_area_id(self, mock_hass):
        """Entity has no area_id → falls back to device.area_id."""
        from custom_components.entity_availability.helpers import resolve_area_name

        mock_entry = MagicMock()
        mock_entry.area_id = None
        mock_entry.device_id = "device_xyz"

        mock_device = MagicMock()
        mock_device.area_id = "area_garage"

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry
        mock_dr = MagicMock()
        mock_dr.async_get.return_value = mock_device

        mock_area = MagicMock()
        mock_area.name = "Garage"
        mock_ar = MagicMock()
        mock_ar.async_get_area.return_value = mock_area

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.dr.async_get",
                return_value=mock_dr,
            ),
            patch(
                "custom_components.entity_availability.helpers.ar.async_get",
                return_value=mock_ar,
            ),
        ):
            result = resolve_area_name(mock_hass, "binary_sensor.device_a")
        assert result == "Garage"

    def test_returns_none_when_no_area_id(self, mock_hass):
        """Entity and device both have no area_id → None."""
        from custom_components.entity_availability.helpers import resolve_area_name

        mock_entry = MagicMock()
        mock_entry.area_id = None
        mock_entry.device_id = None

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry

        with patch(
            "custom_components.entity_availability.helpers.er.async_get",
            return_value=mock_er,
        ):
            result = resolve_area_name(mock_hass, "binary_sensor.device_a")
        assert result is None

    def test_returns_none_when_area_not_found(self, mock_hass):
        """area_id set but area not found in registry → None."""
        from custom_components.entity_availability.helpers import resolve_area_name

        mock_entry = MagicMock()
        mock_entry.area_id = "area_missing"
        mock_entry.device_id = None

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry

        mock_ar = MagicMock()
        mock_ar.async_get_area.return_value = None

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.ar.async_get",
                return_value=mock_ar,
            ),
        ):
            result = resolve_area_name(mock_hass, "binary_sensor.device_a")
        assert result is None

    def test_returns_none_when_device_not_found(self, mock_hass):
        """Entity has device_id but device not in registry → None."""
        from custom_components.entity_availability.helpers import resolve_area_name

        mock_entry = MagicMock()
        mock_entry.area_id = None
        mock_entry.device_id = "device_missing"

        mock_er = MagicMock()
        mock_er.async_get.return_value = mock_entry

        mock_dr = MagicMock()
        mock_dr.async_get.return_value = None

        with (
            patch(
                "custom_components.entity_availability.helpers.er.async_get",
                return_value=mock_er,
            ),
            patch(
                "custom_components.entity_availability.helpers.dr.async_get",
                return_value=mock_dr,
            ),
        ):
            result = resolve_area_name(mock_hass, "binary_sensor.device_a")
        assert result is None


class TestMTBFSensor:
    """Tests for MTBFSensor."""

    def _seed(self, coord, entity_id, events, total_offline_s, monitored_min_ago):
        """Set reliability counters on a device."""
        d = coord.device_states[entity_id]
        d.offline_event_count = events
        d.total_offline_seconds = total_offline_s
        d.monitored_since = datetime.now(timezone.utc) - timedelta(
            minutes=monitored_min_ago
        )

    def test_no_state_class(self, mock_coordinator):
        """MTBF fires on events, not on a cadence — must not generate statistics."""
        sensor = MTBFSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.state_class is None

    def test_native_value_none_without_events(self, mock_coordinator):
        """No offline events → native_value None."""
        sensor = MTBFSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.native_value is None

    def test_native_value_averages_mtbf(self, mock_coordinator):
        """native_value averages per-entity MTBF hours."""
        # 2 events over 24h monitored, 1h total offline → uptime 23h / 2 = 11.5h
        self._seed(mock_coordinator, "binary_sensor.device_a", 2, 3600.0, 24 * 60)
        sensor = MTBFSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.native_value == 11.5

    def test_native_value_skips_suppressed(self, mock_coordinator):
        """Suppressed entities excluded from the average."""
        self._seed(mock_coordinator, "binary_sensor.device_a", 2, 3600.0, 24 * 60)
        mock_coordinator.device_states["binary_sensor.device_a"].is_suppressed = True
        sensor = MTBFSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.native_value is None

    def test_attributes(self, mock_coordinator):
        """extra_state_attributes carries total events and per-device MTBF only."""
        self._seed(mock_coordinator, "binary_sensor.device_a", 2, 3600.0, 24 * 60)
        sensor = MTBFSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        attrs = sensor.extra_state_attributes
        assert attrs["total_offline_events"] == 2
        assert "mttr_minutes" not in attrs  # MTTR is now its own sensor
        dev_a = attrs["per_device"]["binary_sensor.device_a"]
        assert dev_a["offline_events"] == 2
        assert dev_a["mtbf_hours"] == 11.5
        # per_device on the MTBF sensor exposes only MTBF, not MTTR
        assert "mttr_minutes" not in dev_a
        # device_b/c have no events
        assert attrs["per_device"]["binary_sensor.device_b"]["mtbf_hours"] is None

    def test_attributes_zero_events(self, mock_coordinator):
        """total_offline_events is 0 when no entity has events."""
        sensor = MTBFSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        attrs = sensor.extra_state_attributes
        assert attrs["total_offline_events"] == 0

    def test_diagnostic_and_device_class(self, mock_coordinator):
        """MTBF sensor is diagnostic with duration device class, hours."""
        sensor = MTBFSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC
        assert sensor.device_class == SensorDeviceClass.DURATION
        assert sensor.native_unit_of_measurement == "h"


class TestMTTRSensor:
    """Tests for MTTRSensor."""

    def _seed(self, coord, entity_id, events, total_offline_s, monitored_min_ago):
        d = coord.device_states[entity_id]
        d.offline_event_count = events
        d.total_offline_seconds = total_offline_s
        d.monitored_since = datetime.now(timezone.utc) - timedelta(
            minutes=monitored_min_ago
        )

    def test_no_state_class(self, mock_coordinator):
        sensor = MTTRSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.state_class is None

    def test_native_value_none_without_events(self, mock_coordinator):
        sensor = MTTRSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.native_value is None

    def test_native_value_averages_mttr(self, mock_coordinator):
        """native_value averages per-entity MTTR minutes."""
        # 2 events, 3600s total offline → 1800s each → 30 min
        self._seed(mock_coordinator, "binary_sensor.device_a", 2, 3600.0, 24 * 60)
        sensor = MTTRSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.native_value == 30.0

    def test_native_value_skips_suppressed(self, mock_coordinator):
        self._seed(mock_coordinator, "binary_sensor.device_a", 2, 3600.0, 24 * 60)
        mock_coordinator.device_states["binary_sensor.device_a"].is_suppressed = True
        sensor = MTTRSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.native_value is None

    def test_diagnostic_and_device_class(self, mock_coordinator):
        """MTTR sensor is diagnostic with duration device class, minutes."""
        sensor = MTTRSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC
        assert sensor.device_class == SensorDeviceClass.DURATION
        assert sensor.native_unit_of_measurement == "min"
        assert sensor.unique_id == "test_entry_id_mttr"

    def test_attributes(self, mock_coordinator):
        """per_device on MTTR exposes only MTTR (not MTBF)."""
        self._seed(mock_coordinator, "binary_sensor.device_a", 2, 3600.0, 24 * 60)
        sensor = MTTRSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        attrs = sensor.extra_state_attributes
        assert attrs["total_offline_events"] == 2
        dev_a = attrs["per_device"]["binary_sensor.device_a"]
        assert dev_a["mttr_minutes"] == 30.0
        assert dev_a["offline_events"] == 2
        assert "mtbf_hours" not in dev_a  # MTBF lives on its own sensor

    def test_attributes_skip_suppressed(self, mock_coordinator):
        """Suppressed entities omitted from MTTR per_device."""
        mock_coordinator.device_states["binary_sensor.device_a"].is_suppressed = True
        sensor = MTTRSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        attrs = sensor.extra_state_attributes
        assert "binary_sensor.device_a" not in attrs["per_device"]


class TestMTBFSensorAttrSuppressed:
    """MTBFSensor attribute suppression skip."""

    def test_attributes_skip_suppressed(self, mock_coordinator):
        """Suppressed entities are omitted from per_device attrs."""
        mock_coordinator.device_states["binary_sensor.device_a"].is_suppressed = True
        sensor = MTBFSensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        attrs = sensor.extra_state_attributes
        assert "binary_sensor.device_a" not in attrs["per_device"]
        assert "binary_sensor.device_b" in attrs["per_device"]
