"""Tests for the Entity Availability coordinator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.const import (
    CONF_BATTERY_ENTITY_MAP,
    CONF_ENTITIES,
    CONF_STALENESS_THRESHOLD,
    DEFAULT_BAD_STATES,
    DEFAULT_BATTERY_THRESHOLD,
    DEFAULT_COOLDOWN,
    DEFAULT_STALENESS_THRESHOLD,
    DOMAIN,
    SCAN_INTERVAL,
    STARTUP_GRACE_PERIOD,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)


@pytest.fixture
def coordinator(
    hass: HomeAssistant, mock_config_entry
) -> EntityAvailabilityCoordinator:
    """Create a coordinator with mocked storage."""
    with (
        patch.object(
            EntityAvailabilityCoordinator,
            "_async_load_storage",
            new_callable=AsyncMock,
        ),
        patch.object(
            EntityAvailabilityCoordinator,
            "_async_save_storage",
            new_callable=AsyncMock,
        ),
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    return coord


async def test_coordinator_init(hass: HomeAssistant, mock_config_entry) -> None:
    """Test coordinator initializes with correct config values."""
    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    assert coord.monitored_entities == [
        "binary_sensor.device_a",
        "binary_sensor.device_b",
        "binary_sensor.device_c",
    ]
    assert coord._bad_states == DEFAULT_BAD_STATES
    assert coord._cooldown == DEFAULT_COOLDOWN
    assert coord._staleness_threshold == DEFAULT_STALENESS_THRESHOLD
    assert coord._battery_threshold == DEFAULT_BATTERY_THRESHOLD
    assert coord.group_name == "Test Group"


async def test_state_change_online_device(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test that online device is tracked correctly."""
    hass = mock_hass
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

    # All devices are STATE_ON, so none should be offline
    for entity_id, device in coord.device_states.items():
        assert device.is_offline is False
        assert device.is_degraded is False


async def test_cooldown_device_stays_online_within_cooldown(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test device remains online during cooldown period."""
    hass = mock_hass
    # Set device_a to unavailable
    hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None

        # First update - starts cooldown
        await coord._async_update_data()
        device_a = coord.device_states["binary_sensor.device_a"]
        assert device_a.cooldown_start is not None
        assert device_a.is_offline is False  # Still within cooldown


async def test_cooldown_device_goes_offline_after_cooldown(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test device goes offline after cooldown expires."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None

        # First update - sets cooldown_start
        await coord._async_update_data()
        device_a = coord.device_states["binary_sensor.device_a"]
        assert device_a.is_offline is False

        # Simulate time passing beyond cooldown (60s default)
        device_a.cooldown_start = datetime.now(timezone.utc) - timedelta(seconds=61)
        await coord._async_update_data()

        assert device_a.is_offline is True
        assert device_a.offline_since is not None


async def test_device_recovery_after_offline(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test device recovery is tracked correctly."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None

        # Force device offline
        await coord._async_update_data()
        device_a = coord.device_states["binary_sensor.device_a"]
        device_a.cooldown_start = datetime.now(timezone.utc) - timedelta(seconds=120)
        await coord._async_update_data()
        assert device_a.is_offline is True

        # Now bring it back online
        hass.states.async_set("binary_sensor.device_a", STATE_ON)
        await coord._async_update_data()
        assert device_a.is_offline is False
        assert device_a.last_recovery is not None
        assert device_a.last_downtime_seconds is not None
        assert device_a.cooldown_start is None


async def test_staleness_detection(mock_hass: HomeAssistant, mock_config_data) -> None:
    """Test that stale devices are detected as degraded."""
    hass = mock_hass
    # Override staleness threshold to 10 minutes
    staleness_config_data = dict(mock_config_data)
    staleness_config_data[CONF_STALENESS_THRESHOLD] = 10

    staleness_entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=staleness_config_data,
        entry_id="test_entry_staleness",
        unique_id=f"{DOMAIN}_test_staleness",
    )

    # Set last_changed to 15 minutes ago
    old_time = datetime.now(timezone.utc) - timedelta(minutes=15)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, staleness_entry)
        coord._last_update = None

        # Set state then replace with a State that has old last_changed
        hass.states.async_set(
            "binary_sensor.device_a",
            STATE_ON,
            {"friendly_name": "Device A"},
        )
        hass.states._states["binary_sensor.device_a"] = State(
            "binary_sensor.device_a",
            STATE_ON,
            {"friendly_name": "Device A"},
            last_changed=old_time,
            last_updated=old_time,
        )

        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.is_degraded is True
    assert device_a.is_offline is False


async def test_battery_detection_from_attributes(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test battery level detection from entity attributes."""
    hass = mock_hass
    hass.states.async_set(
        "binary_sensor.device_a",
        STATE_ON,
        {"friendly_name": "Device A", "battery_level": 15},
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.battery_level == 15
    assert device_a.is_degraded is True  # battery < 20 threshold


async def test_battery_detection_from_companion_entity(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test battery level detection from companion battery entity."""
    hass = mock_hass
    # No battery in attributes
    hass.states.async_set(
        "binary_sensor.device_a",
        STATE_ON,
        {"friendly_name": "Device A"},
    )
    # Add companion battery entity
    hass.states.async_set("sensor.device_a_battery", "10")

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.battery_level == 10
    assert device_a.is_degraded is True


async def test_suppression_skips_availability_tracking(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test that suppressed devices are skipped during update."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None

        # Initialize device states
        await coord._async_update_data()

        # Suppress device_a
        coord.suppress_entity(
            "binary_sensor.device_a",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )

        # Now update again - device_a should be skipped
        await coord._async_update_data()
        device_a = coord.device_states["binary_sensor.device_a"]
        assert device_a.is_suppressed is True


async def test_suppression_expiry(mock_hass: HomeAssistant, mock_config_entry) -> None:
    """Test that suppression expires when time is reached."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None

        await coord._async_update_data()

        # Suppress with a time in the past
        past_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        coord.suppress_entity("binary_sensor.device_a", past_time)
        device_a = coord.device_states["binary_sensor.device_a"]
        assert device_a.is_suppressed is True

        # Next update should clear suppression
        await coord._async_update_data()
        assert device_a.is_suppressed is False
        assert device_a.suppress_until is None


async def test_suppress_and_unsuppress(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test suppress and unsuppress methods."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

        future_time = datetime.now(timezone.utc) + timedelta(hours=2)
        coord.suppress_entity("binary_sensor.device_b", future_time)
        assert coord.device_states["binary_sensor.device_b"].is_suppressed is True

        coord.unsuppress_entity("binary_sensor.device_b")
        assert coord.device_states["binary_sensor.device_b"].is_suppressed is False
        assert coord.device_states["binary_sensor.device_b"].suppress_until is None


async def test_suppress_nonexistent_entity(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test suppress on unknown entity does nothing."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

        # Should not raise
        coord.suppress_entity("sensor.does_not_exist", None)
        coord.unsuppress_entity("sensor.does_not_exist")


async def test_state_none_is_bad(mock_hass: HomeAssistant, mock_config_data) -> None:
    """Test that entity with no state (None) is considered bad."""
    hass = mock_hass
    # Add an entity to the config that doesn't exist in states
    none_config_data = dict(mock_config_data)
    none_config_data[CONF_ENTITIES] = ["binary_sensor.nonexistent"]

    none_entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=none_config_data,
        entry_id="test_entry_none",
        unique_id=f"{DOMAIN}_test_none",
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, none_entry)
        coord._last_update = None
        await coord._async_update_data()

        device = coord.device_states["binary_sensor.nonexistent"]
        # First update: cooldown_start is set, but device is not yet offline
        assert device.cooldown_start is not None
        assert device.is_offline is False

        # After cooldown
        device.cooldown_start = datetime.now(timezone.utc) - timedelta(seconds=61)
        await coord._async_update_data()
        assert device.is_offline is True


async def test_unknown_state_is_bad(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test that 'unknown' state is treated as bad."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_UNKNOWN)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

        device_a = coord.device_states["binary_sensor.device_a"]
        assert device_a.cooldown_start is not None


async def test_battery_detection_from_device_registry(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test battery level detection via device registry lookup."""
    hass = mock_hass
    hass.states.async_set(
        "binary_sensor.device_a",
        STATE_ON,
        {"friendly_name": "Device A"},
    )
    hass.states.async_set("sensor.device_a_bat_level", "42")

    mock_ent_entry = MagicMock()
    mock_ent_entry.device_id = "device_123"

    mock_bat_entry = MagicMock()
    mock_bat_entry.entity_id = "sensor.device_a_bat_level"
    mock_bat_entry.original_device_class = "battery"
    mock_bat_entry.device_class = "battery"

    with (
        patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ),
        patch(
            "custom_components.entity_availability.coordinator.er.async_get"
        ) as mock_er,
        patch(
            "custom_components.entity_availability.coordinator.er.async_entries_for_device"
        ) as mock_entries,
    ):
        mock_ent_reg = MagicMock()
        mock_ent_reg.async_get.return_value = mock_ent_entry
        mock_er.return_value = mock_ent_reg
        mock_entries.return_value = [mock_bat_entry]

        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.battery_level == 42


class TestParseBatteryState:
    """Tests for _parse_battery_state static method."""

    def test_numeric_string(self):
        """Test numeric string returns int."""
        assert EntityAvailabilityCoordinator._parse_battery_state("85") == 85

    def test_float_string(self):
        """Test float string returns int."""
        assert EntityAvailabilityCoordinator._parse_battery_state("42.7") == 42

    def test_low_string(self):
        """Test 'low' returns 0."""
        assert EntityAvailabilityCoordinator._parse_battery_state("low") == 0

    def test_low_uppercase(self):
        """Test 'Low' returns 0."""
        assert EntityAvailabilityCoordinator._parse_battery_state("Low") == 0

    def test_invalid_string(self):
        """Test invalid string returns None."""
        assert EntityAvailabilityCoordinator._parse_battery_state("full") is None

    def test_empty_string(self):
        """Test empty string returns None."""
        assert EntityAvailabilityCoordinator._parse_battery_state("") is None


async def test_battery_low_text_state(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test battery entity with 'low' text state is treated as 0."""
    hass = mock_hass
    hass.states.async_set(
        "binary_sensor.device_a",
        STATE_ON,
        {"friendly_name": "Device A"},
    )
    hass.states.async_set("sensor.device_a_battery", "low")

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.battery_level == 0
    assert device_a.is_degraded is True


async def test_startup_grace_blocks_new_offline_transitions(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """During startup grace period, new offline transitions are suppressed."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        # Simulate startup: set startup_time to now (still within grace period)
        coord._startup_time = datetime.now(timezone.utc)
        coord._last_update = None

        # Run cooldown well past threshold by backdating cooldown_start
        await coord._async_update_data()

        coord._device_states["binary_sensor.device_a"].cooldown_start = datetime.now(
            timezone.utc
        ) - timedelta(seconds=DEFAULT_COOLDOWN + 10)
        await coord._async_update_data()

    # Grace period active — transition must be blocked
    assert coord.device_states["binary_sensor.device_a"].is_offline is False


async def test_startup_grace_allows_transition_after_expiry(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """After grace period expires, offline transitions proceed normally."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        # Startup time in the past — grace period expired
        coord._startup_time = datetime.now(timezone.utc) - timedelta(
            seconds=STARTUP_GRACE_PERIOD + 10
        )
        coord._last_update = None
        await coord._async_update_data()
        coord._device_states["binary_sensor.device_a"].cooldown_start = datetime.now(
            timezone.utc
        ) - timedelta(seconds=DEFAULT_COOLDOWN + 10)
        await coord._async_update_data()

    assert coord.device_states["binary_sensor.device_a"].is_offline is True


async def test_debounce_cancel_on_rapid_state_changes(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Rapid state changes cancel the previous debounce timer, scheduling only one refresh."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

        cancel_calls = []

        def make_cancel():
            called = []

            def cancel():
                called.append(True)
                cancel_calls.append(True)

            return cancel, called

        first_cancel, first_called = make_cancel()
        second_cancel, _ = make_cancel()

        with patch(
            "custom_components.entity_availability.coordinator.async_call_later",
            side_effect=[first_cancel, second_cancel],
        ):
            entity_id = "binary_sensor.test"

            def make_event(eid):
                evt = MagicMock()
                evt.data.get.side_effect = lambda key, default=None: (
                    eid if key == "entity_id" else None
                )
                return evt

            # First state change — schedules debounce, no previous cancel
            coord._handle_state_change(make_event(entity_id))
            assert coord._debounce_cancel_map.get(entity_id) is first_cancel
            assert len(cancel_calls) == 0

            # Second rapid state change — cancels first, schedules new
            coord._handle_state_change(make_event(entity_id))
            assert coord._debounce_cancel_map.get(entity_id) is second_cancel
            assert len(cancel_calls) == 1  # first cancel was called


async def test_recovery_attributes_across_multiple_cycles(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """last_recovery and last_downtime_seconds update correctly across multiple offline/online cycles."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._startup_time = datetime.now(timezone.utc) - timedelta(
            seconds=STARTUP_GRACE_PERIOD + 10
        )
        coord._last_update = None

        # --- Cycle 1: go offline then recover ---
        await coord._async_update_data()
        device_a = coord.device_states["binary_sensor.device_a"]
        device_a.cooldown_start = datetime.now(timezone.utc) - timedelta(seconds=120)
        await coord._async_update_data()
        assert device_a.is_offline is True

        hass.states.async_set("binary_sensor.device_a", STATE_ON)
        await coord._async_update_data()
        assert device_a.is_offline is False
        first_recovery = device_a.last_recovery
        first_downtime = device_a.last_downtime_seconds
        assert first_recovery is not None
        assert first_downtime is not None and first_downtime > 0

        # --- Cycle 2: go offline again then recover ---
        hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)
        await coord._async_update_data()
        device_a.cooldown_start = datetime.now(timezone.utc) - timedelta(seconds=120)
        await coord._async_update_data()
        assert device_a.is_offline is True

        hass.states.async_set("binary_sensor.device_a", STATE_ON)
        await coord._async_update_data()
        assert device_a.is_offline is False

        # second recovery must be a newer timestamp than first
        assert device_a.last_recovery is not None
        assert device_a.last_recovery >= first_recovery
        assert device_a.last_downtime_seconds is not None


async def test_suppressed_entity_skips_availability_storage(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Suppressed entity does not record online/offline time in availability storage."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

        coord.suppress_entity(
            "binary_sensor.device_a",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )

        # Patch storage methods to detect if they're called for device_a
        record_online_calls = []
        record_offline_calls = []
        original_online = coord._availability_storage.record_online
        original_offline = coord._availability_storage.record_offline

        def tracking_online(entity_id, *args, **kwargs):
            if entity_id == "binary_sensor.device_a":
                record_online_calls.append(entity_id)
            return original_online(entity_id, *args, **kwargs)

        def tracking_offline(entity_id, *args, **kwargs):
            if entity_id == "binary_sensor.device_a":
                record_offline_calls.append(entity_id)
            return original_offline(entity_id, *args, **kwargs)

        coord._availability_storage.record_online = tracking_online
        coord._availability_storage.record_offline = tracking_offline

        await coord._async_update_data()

    assert record_online_calls == [], "suppressed entity should not record online time"
    assert record_offline_calls == [], (
        "suppressed entity should not record offline time"
    )


async def test_async_shutdown_cancels_debounce_map(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """async_shutdown calls every cancel in _debounce_cancel_map and clears it."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

        cancel_called = []

        def fake_cancel():
            cancel_called.append(True)

        coord._debounce_cancel_map["binary_sensor.x"] = fake_cancel

        await coord.async_shutdown()

    assert len(cancel_called) == 1
    assert coord._debounce_cancel_map == {}


async def test_async_update_data_save_error_logs_warning(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """When _async_save_storage raises, a warning is logged and _update_count resets."""
    hass = mock_hass

    from custom_components.entity_availability.coordinator import _SAVE_INTERVAL_UPDATES

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ) as mock_save:
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

        # Prime the counter so the next update triggers the save path
        coord._update_count = _SAVE_INTERVAL_UPDATES - 1
        mock_save.side_effect = RuntimeError("disk full")

        with patch(
            "custom_components.entity_availability.coordinator._LOGGER"
        ) as mock_logger:
            await coord._async_update_data()

        mock_logger.warning.assert_called_once()
        assert coord._update_count == 0


async def test_device_state_persistence_prevents_restart_retrigger(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Offline state persisted and restored — no False→True transition on restart."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)

    offline_since = datetime.now(timezone.utc) - timedelta(hours=1)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)

        # Simulate what _async_load_storage restores: device was already offline
        from custom_components.entity_availability.models import DeviceState

        restored = DeviceState(entity_id="binary_sensor.device_a")
        restored.is_offline = True
        restored.offline_since = offline_since
        coord._device_states["binary_sensor.device_a"] = restored

        # Grace period expired (simulating past startup)
        coord._startup_time = datetime.now(timezone.utc) - timedelta(
            seconds=STARTUP_GRACE_PERIOD + 10
        )
        coord._last_update = None
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    # Still offline — no transition — no automation trigger
    assert device_a.is_offline is True
    # offline_since preserved from storage, not reset
    assert device_a.offline_since == offline_since


async def test_elapsed_capped_after_long_gap(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Elapsed time is capped at SCAN_INTERVAL*2 to prevent availability gaps after HA sleep."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        # Simulate 10-minute gap (e.g. after HA restart or sleep)
        coord._last_update = datetime.now(timezone.utc) - timedelta(minutes=10)

        recorded_seconds = []
        original = coord._availability_storage.record_online

        def capturing_record_online(entity_id, seconds, now):
            recorded_seconds.append(seconds)
            return original(entity_id, seconds, now)

        coord._availability_storage.record_online = capturing_record_online

        await coord._async_update_data()

    assert recorded_seconds, "record_online should have been called"
    assert all(s <= SCAN_INTERVAL * 2 for s in recorded_seconds), (
        f"elapsed not capped: {recorded_seconds}"
    )


async def test_offline_since_equals_cooldown_start(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """offline_since is set to cooldown_start (first detection), not now (transition time)."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_UNAVAILABLE)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._startup_time = datetime.now(timezone.utc) - timedelta(
            seconds=STARTUP_GRACE_PERIOD + 10
        )
        coord._last_update = None

        await coord._async_update_data()
        device_a = coord.device_states["binary_sensor.device_a"]

        backdate = datetime.now(timezone.utc) - timedelta(seconds=DEFAULT_COOLDOWN + 10)
        device_a.cooldown_start = backdate

        await coord._async_update_data()

    assert device_a.is_offline is True
    assert device_a.offline_since == backdate, (
        "offline_since must equal cooldown_start, not now"
    )


async def test_periodic_save_triggers_after_10_updates(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Storage is saved every _SAVE_INTERVAL_UPDATES (10) update cycles."""
    hass = mock_hass

    save_calls = []

    async def counting_save(self_inner=None):
        save_calls.append(True)

    with patch.object(
        EntityAvailabilityCoordinator,
        "_async_save_storage",
        new=counting_save,
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None

        # Run 9 updates — should not save yet
        for _ in range(9):
            await coord._async_update_data()
        assert len(save_calls) == 0, "save should not fire before 10 updates"

        # 10th update — should trigger save and reset counter
        await coord._async_update_data()
        assert len(save_calls) == 1, "save should fire on 10th update"
        assert coord._update_count == 0, "_update_count should reset after save"


async def test_shutdown_save_only_when_dirty(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """async_shutdown saves storage only when dirty flag is set."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ) as mock_save:
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)

        # Not dirty — shutdown should not save
        coord._dirty = False
        await coord.async_shutdown()
        mock_save.assert_not_called()

        # Dirty — shutdown should save
        coord._dirty = True
        await coord.async_shutdown()
        mock_save.assert_called_once()


async def test_battery_map_falsy_returns_none(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """Battery map entry of None or empty string returns None (entity skipped)."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_ON)

    for falsy_value in [None, ""]:
        config = dict(mock_config_data)
        config[CONF_BATTERY_ENTITY_MAP] = {"binary_sensor.device_a": falsy_value}

        entry = MockConfigEntry(
            version=1,
            domain=DOMAIN,
            title="Test Group",
            data=config,
            entry_id=f"test_entry_falsy_{falsy_value}",
            unique_id=f"{DOMAIN}_test_falsy_{falsy_value}",
        )

        with patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ):
            coord = EntityAvailabilityCoordinator(hass, entry)
            coord._last_update = None
            await coord._async_update_data()

        device_a = coord.device_states["binary_sensor.device_a"]
        assert device_a.battery_level is None, (
            f"falsy map value {falsy_value!r} should produce battery_level=None"
        )


# ---------------------------------------------------------------------------
# Storage load/save — recently_offline_at persistence
# ---------------------------------------------------------------------------


async def test_load_storage_restores_recently_offline_at_within_window(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """recently_offline_at is restored when the timestamp is within the recovery window."""
    hass = mock_hass
    recent_ts = datetime.now(timezone.utc) - timedelta(minutes=2)

    stored_data = {
        "availability": {},
        "suppressed": {},
        "device_states": {
            "binary_sensor.device_a": {
                "is_offline": True,
                "offline_since": (
                    datetime.now(timezone.utc) - timedelta(minutes=5)
                ).isoformat(),
                "cooldown_start": None,
                "recently_offline_at": recent_ts.isoformat(),
            }
        },
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    device = coord._device_states["binary_sensor.device_a"]
    assert device.recently_offline_at is not None
    # Should be within 1 second of the stored timestamp
    diff = abs((device.recently_offline_at - recent_ts).total_seconds())
    assert diff < 1


async def test_load_storage_discards_recently_offline_at_outside_window(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """recently_offline_at is discarded when the timestamp exceeds the recovery window."""
    hass = mock_hass
    # Timestamp is 10 minutes ago — outside the default 5-minute window
    old_ts = datetime.now(timezone.utc) - timedelta(minutes=10)

    stored_data = {
        "availability": {},
        "suppressed": {},
        "device_states": {
            "binary_sensor.device_a": {
                "is_offline": True,
                "offline_since": None,
                "cooldown_start": None,
                "recently_offline_at": old_ts.isoformat(),
            }
        },
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    device = coord._device_states["binary_sensor.device_a"]
    # Should be cleared because it is outside the window
    assert device.recently_offline_at is None


async def test_load_storage_ignores_bad_recently_offline_at_string(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Invalid ISO string for recently_offline_at is silently ignored."""
    hass = mock_hass

    stored_data = {
        "availability": {},
        "suppressed": {},
        "device_states": {
            "binary_sensor.device_a": {
                "is_offline": False,
                "offline_since": None,
                "cooldown_start": None,
                "recently_offline_at": "not-a-valid-iso-string",
            }
        },
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    # Should not raise
    await coord._async_load_storage()

    device = coord._device_states["binary_sensor.device_a"]
    assert device.recently_offline_at is None


# ---------------------------------------------------------------------------
# Storage load — indefinite suppression (until_str is None)
# ---------------------------------------------------------------------------


async def test_load_storage_restores_indefinite_suppression(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Indefinite suppression (until=None) is restored after a restart."""
    hass = mock_hass

    stored_data = {
        "availability": {},
        "suppressed": {
            "binary_sensor.device_a": None,  # indefinite — no expiry
        },
        "device_states": {},
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    # The entity must appear in _suppressed with None as the value
    assert "binary_sensor.device_a" in coord._suppressed
    assert coord._suppressed["binary_sensor.device_a"] is None


async def test_load_storage_restores_indefinite_suppression_via_update(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """After loading indefinite suppression, the device shows as suppressed on next update."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", STATE_ON)

    stored_data = {
        "availability": {},
        "suppressed": {
            "binary_sensor.device_a": None,
        },
        "device_states": {},
    }

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._store = MagicMock()
        coord._store.async_load = AsyncMock(return_value=stored_data)

        await coord._async_load_storage()

        coord._last_update = None
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.is_suppressed is True
    assert device_a.suppress_until is None


# ---------------------------------------------------------------------------
# recovery_window_minutes reads live from entry.data
# ---------------------------------------------------------------------------


async def test_recovery_window_minutes_reads_live_from_entry_data(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """recovery_window_minutes always reflects the current entry.data value."""
    from custom_components.entity_availability.const import CONF_RECOVERY_WINDOW

    hass = mock_hass
    config = dict(mock_config_data)
    config[CONF_RECOVERY_WINDOW] = 15

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=config,
        entry_id="test_entry_recovery",
        unique_id=f"{DOMAIN}_test_recovery",
    )
    # Register the entry so async_update_entry can find it
    entry.add_to_hass(hass)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)

    # Value set in config
    assert coord.recovery_window_minutes == 15

    # Simulate live update to entry.data (e.g., options flow)
    new_data = dict(entry.data)
    new_data[CONF_RECOVERY_WINDOW] = 30
    hass.config_entries.async_update_entry(entry, data=new_data)

    # Property reads from entry.data directly — no stale cache
    assert coord.recovery_window_minutes == 30


async def test_recovery_window_minutes_uses_default_when_absent(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """recovery_window_minutes falls back to DEFAULT_RECOVERY_WINDOW when key absent."""
    from custom_components.entity_availability.const import DEFAULT_RECOVERY_WINDOW

    hass = mock_hass
    # config without CONF_RECOVERY_WINDOW key
    config = dict(mock_config_data)
    config.pop("recovery_window", None)

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=config,
        entry_id="test_entry_recovery_default",
        unique_id=f"{DOMAIN}_test_recovery_default",
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)

    assert coord.recovery_window_minutes == DEFAULT_RECOVERY_WINDOW


# ---------------------------------------------------------------------------
# Storage load/save — stale entity pruning after group edit
# ---------------------------------------------------------------------------


async def test_load_storage_skips_removed_entities(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Entities removed from config are not restored from storage."""
    hass = mock_hass

    # Storage contains device_a (in config) and device_removed (no longer in config)
    stored_data = {
        "availability": {},
        "suppressed": {},
        "device_states": {
            "binary_sensor.device_a": {
                "is_offline": True,
                "offline_since": (
                    datetime.now(timezone.utc) - timedelta(minutes=5)
                ).isoformat(),
                "cooldown_start": None,
                "recently_offline_at": None,
            },
            "binary_sensor.device_removed": {
                "is_offline": True,
                "offline_since": (
                    datetime.now(timezone.utc) - timedelta(minutes=5)
                ).isoformat(),
                "cooldown_start": None,
                "recently_offline_at": None,
            },
        },
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    assert "binary_sensor.device_a" in coord._device_states
    assert "binary_sensor.device_removed" not in coord._device_states


async def test_save_storage_skips_removed_entities(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Entities not in current config are excluded from saved storage."""
    from custom_components.entity_availability.models import DeviceState

    hass = mock_hass

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=None)
    coord._store.async_save = AsyncMock()

    # Inject a stale entity into device_states (simulates pre-fix state)
    stale = DeviceState(entity_id="binary_sensor.device_removed")
    stale.is_offline = True
    coord._device_states["binary_sensor.device_removed"] = stale

    await coord._async_save_storage()

    saved = coord._store.async_save.call_args[0][0]
    assert "binary_sensor.device_removed" not in saved["device_states"]


async def test_load_storage_skips_suppression_for_removed_entities(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Suppression entries for entities no longer in config are not restored."""
    hass = mock_hass

    stored_data = {
        "availability": {},
        "suppressed": {
            "binary_sensor.device_a": None,
            "binary_sensor.device_removed": None,
        },
        "device_states": {},
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    assert "binary_sensor.device_a" in coord._suppressed
    assert "binary_sensor.device_removed" not in coord._suppressed


async def test_save_storage_skips_suppression_for_removed_entities(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Suppression entries for entities not in current config are excluded from save."""
    hass = mock_hass

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=None)
    coord._store.async_save = AsyncMock()

    coord._suppressed["binary_sensor.device_removed"] = None

    await coord._async_save_storage()

    saved = coord._store.async_save.call_args[0][0]
    assert "binary_sensor.device_removed" not in saved["suppressed"]


async def test_load_storage_naive_offline_since_gets_utc(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Naive offline_since ISO string is restored with tzinfo=UTC."""
    hass = mock_hass
    naive_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)

    stored_data = {
        "availability": {},
        "suppressed": {},
        "device_states": {
            "binary_sensor.device_a": {
                "is_offline": True,
                "offline_since": naive_ts.isoformat(),
                "cooldown_start": None,
                "recently_offline_at": None,
            }
        },
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    device = coord._device_states["binary_sensor.device_a"]
    assert device.offline_since is not None
    assert device.offline_since.tzinfo is not None


async def test_load_storage_naive_cooldown_start_gets_utc(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Naive cooldown_start ISO string is restored with tzinfo=UTC."""
    hass = mock_hass
    naive_ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(tzinfo=None)

    stored_data = {
        "availability": {},
        "suppressed": {},
        "device_states": {
            "binary_sensor.device_a": {
                "is_offline": False,
                "offline_since": None,
                "cooldown_start": naive_ts.isoformat(),
                "recently_offline_at": None,
            }
        },
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    device = coord._device_states["binary_sensor.device_a"]
    assert device.cooldown_start is not None
    assert device.cooldown_start.tzinfo is not None


# ---------------------------------------------------------------------------
# Battery detection — device_class=battery entity reads own state
# ---------------------------------------------------------------------------


async def test_battery_detection_from_own_state_device_class(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Entity with device_class=battery uses its own state as battery level."""
    hass = mock_hass
    hass.states.async_set(
        "binary_sensor.device_a",
        "72",
        {"friendly_name": "Phone Battery", "device_class": "battery"},
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.battery_level == 72


async def test_battery_detection_device_class_takes_priority_over_map(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """device_class=battery own-state detection takes priority over battery map."""
    hass = mock_hass
    hass.states.async_set(
        "binary_sensor.device_a",
        "55",
        {"friendly_name": "Phone Battery", "device_class": "battery"},
    )
    hass.states.async_set("sensor.some_other_battery", "10")

    config = dict(mock_config_data)
    config[CONF_BATTERY_ENTITY_MAP] = {
        "binary_sensor.device_a": "sensor.some_other_battery"
    }
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=config,
        entry_id="test_entry_dc_priority",
        unique_id=f"{DOMAIN}_test_dc_priority",
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
        coord._last_update = None
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.battery_level == 55


# ---------------------------------------------------------------------------
# Offline duration — cooldown_start uses state.last_changed
# ---------------------------------------------------------------------------


async def test_cooldown_start_uses_last_changed_when_earlier(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """cooldown_start is set to state.last_changed when entity was already offline before coordinator first saw it."""
    hass = mock_hass
    earlier = datetime.now(timezone.utc) - timedelta(hours=3)

    # Simulate entity that went unavailable 3 hours ago
    state = State(
        "binary_sensor.device_a",
        STATE_UNAVAILABLE,
        attributes={"friendly_name": "Device A"},
        last_changed=earlier,
        last_updated=earlier,
    )
    hass.states.async_set(
        "binary_sensor.device_a",
        STATE_UNAVAILABLE,
        {"friendly_name": "Device A"},
    )
    # Directly inject the state with backdated last_changed
    hass.states._states["binary_sensor.device_a"] = state

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._startup_time = datetime.now(timezone.utc) - timedelta(
            seconds=STARTUP_GRACE_PERIOD + 10
        )
        coord._last_update = None
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.cooldown_start is not None
    assert device_a.cooldown_start == earlier


async def test_staleness_detection_tz_naive_last_changed(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """Staleness detection handles tz-naive last_changed without TypeError."""
    hass = mock_hass
    config = dict(mock_config_data)
    config[CONF_STALENESS_THRESHOLD] = 10

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=config,
        entry_id="test_entry_tz_naive",
        unique_id=f"{DOMAIN}_test_tz_naive",
    )

    # Inject a State with tz-naive last_changed (no tzinfo)
    old_time_naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        minutes=15
    )
    state = State(
        "binary_sensor.device_a",
        "on",
        {"friendly_name": "Device A"},
        last_changed=old_time_naive,
        last_updated=old_time_naive,
    )
    hass.states.async_set("binary_sensor.device_a", "on", {"friendly_name": "Device A"})
    hass.states._states["binary_sensor.device_a"] = state

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
        coord._last_update = None
        # Should not raise TypeError even with tz-naive last_changed
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.is_degraded is True


async def test_cooldown_start_tz_naive_last_changed(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """cooldown_start is set correctly when state.last_changed is tz-naive."""
    hass = mock_hass
    naive_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)

    state = State(
        "binary_sensor.device_a",
        "unavailable",
        {"friendly_name": "Device A"},
        last_changed=naive_time,
        last_updated=naive_time,
    )
    hass.states.async_set("binary_sensor.device_a", "unavailable")
    hass.states._states["binary_sensor.device_a"] = state

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._startup_time = datetime.now(timezone.utc) - timedelta(
            seconds=STARTUP_GRACE_PERIOD + 10
        )
        coord._last_update = None
        # Should not raise TypeError
        await coord._async_update_data()

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.cooldown_start is not None


# ---------------------------------------------------------------------------
# async_config_entry_first_refresh (lines 135-148)
# ---------------------------------------------------------------------------


async def test_async_config_entry_first_refresh_calls_setup(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """async_config_entry_first_refresh loads storage and sets up state listeners."""
    hass = mock_hass
    mock_config_entry.add_to_hass(hass)

    with (
        patch.object(
            EntityAvailabilityCoordinator,
            "_async_load_storage",
            new_callable=AsyncMock,
        ) as mock_load,
        patch.object(
            EntityAvailabilityCoordinator,
            "_setup_state_listeners",
        ) as mock_listeners,
        patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        await coord.async_config_entry_first_refresh()

    mock_load.assert_called_once()
    mock_listeners.assert_called_once()
    assert coord._startup_time is not None


# ---------------------------------------------------------------------------
# async_shutdown — cancels unsub_state_change and debounce (lines 154-155, 157-158)
# ---------------------------------------------------------------------------


async def test_shutdown_cancels_state_change_listener(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """async_shutdown cancels the state change listener when it exists."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)

    unsub_called = []

    def unsub():
        unsub_called.append(True)

    coord._unsub_state_change = unsub
    coord._dirty = False
    await coord.async_shutdown()

    assert len(unsub_called) == 1
    assert coord._unsub_state_change is None


async def test_shutdown_cancels_debounce(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """async_shutdown cancels all per-entity debounce timers."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)

    debounce_called = []

    def debounce_cancel():
        debounce_called.append(True)

    coord._debounce_cancel_map["binary_sensor.device_a"] = debounce_cancel
    coord._dirty = False
    await coord.async_shutdown()

    assert len(debounce_called) == 1
    assert len(coord._debounce_cancel_map) == 0


# ---------------------------------------------------------------------------
# _async_load_storage — timed suppression restore (lines 187-198)
# ---------------------------------------------------------------------------


async def test_load_storage_restores_naive_recently_offline_at(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Naive (no tzinfo) recently_offline_at within recovery window is restored."""
    hass = mock_hass
    # Naive isoformat — within default 5-minute window
    naive_ts = (datetime.now(timezone.utc) - timedelta(minutes=2)).replace(tzinfo=None)

    stored_data = {
        "availability": {},
        "suppressed": {},
        "device_states": {
            "binary_sensor.device_a": {
                "is_offline": True,
                "offline_since": None,
                "cooldown_start": None,
                "recently_offline_at": naive_ts.isoformat(),
            }
        },
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    device = coord._device_states["binary_sensor.device_a"]
    assert device.recently_offline_at is not None
    assert device.recently_offline_at.tzinfo is not None


async def test_load_storage_restores_future_timed_suppression(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Timed suppression with a future expiry is restored from storage."""
    hass = mock_hass
    future_ts = datetime.now(timezone.utc) + timedelta(hours=2)

    stored_data = {
        "availability": {},
        "suppressed": {
            "binary_sensor.device_a": future_ts.isoformat(),
        },
        "device_states": {},
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    assert "binary_sensor.device_a" in coord._suppressed
    restored = coord._suppressed["binary_sensor.device_a"]
    assert restored is not None
    # Stored value should be close to future_ts
    diff = abs((restored - future_ts).total_seconds())
    assert diff < 1


async def test_load_storage_discards_expired_timed_suppression(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Timed suppression that has already expired is not restored."""
    hass = mock_hass
    past_ts = datetime.now(timezone.utc) - timedelta(hours=1)

    stored_data = {
        "availability": {},
        "suppressed": {
            "binary_sensor.device_a": past_ts.isoformat(),
        },
        "device_states": {},
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    assert "binary_sensor.device_a" not in coord._suppressed


async def test_load_storage_restores_naive_timed_suppression(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Naive (no tzinfo) suppress_until ISO string is treated as UTC and restored."""
    hass = mock_hass

    stored_data = {
        "availability": {},
        "suppressed": {
            "binary_sensor.device_a": "2099-01-01T12:00:00",  # naive — far future
        },
        "device_states": {},
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    assert "binary_sensor.device_a" in coord._suppressed
    restored = coord._suppressed["binary_sensor.device_a"]
    assert restored is not None
    assert restored.tzinfo is not None


async def test_load_storage_ignores_invalid_suppression_timestamp(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Invalid ISO string for timed suppression is silently ignored."""
    hass = mock_hass

    stored_data = {
        "availability": {},
        "suppressed": {
            "binary_sensor.device_a": "not-a-valid-iso",
        },
        "device_states": {},
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    # Should not raise
    await coord._async_load_storage()
    assert "binary_sensor.device_a" not in coord._suppressed


# ---------------------------------------------------------------------------
# _async_load_storage — invalid offline_since / cooldown_start (lines 209-210, 217-218)
# ---------------------------------------------------------------------------


async def test_load_storage_handles_invalid_offline_since(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Invalid offline_since ISO string defaults to None without raising."""
    hass = mock_hass

    stored_data = {
        "availability": {},
        "suppressed": {},
        "device_states": {
            "binary_sensor.device_a": {
                "is_offline": True,
                "offline_since": "not-a-date",
                "cooldown_start": None,
                "recently_offline_at": None,
            }
        },
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    device = coord._device_states["binary_sensor.device_a"]
    assert device.offline_since is None


async def test_load_storage_handles_invalid_cooldown_start(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Invalid cooldown_start ISO string defaults to None without raising."""
    hass = mock_hass

    stored_data = {
        "availability": {},
        "suppressed": {},
        "device_states": {
            "binary_sensor.device_a": {
                "is_offline": False,
                "offline_since": None,
                "cooldown_start": "bad-date",
                "recently_offline_at": None,
            }
        },
    }

    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=stored_data)
    coord._store.async_save = AsyncMock()

    await coord._async_load_storage()

    device = coord._device_states["binary_sensor.device_a"]
    assert device.cooldown_start is None


# ---------------------------------------------------------------------------
# _async_save_storage — saves offline devices (lines 244-245)
# ---------------------------------------------------------------------------


async def test_save_storage_includes_offline_device(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Offline devices are included in saved device_states."""
    from custom_components.entity_availability.models import DeviceState

    hass = mock_hass
    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=None)
    coord._store.async_save = AsyncMock()

    offline_since = datetime.now(timezone.utc) - timedelta(minutes=10)
    device = DeviceState(entity_id="binary_sensor.device_a")
    device.is_offline = True
    device.offline_since = offline_since
    coord._device_states["binary_sensor.device_a"] = device

    await coord._async_save_storage()

    saved = coord._store.async_save.call_args[0][0]
    assert "binary_sensor.device_a" in saved["device_states"]
    assert saved["device_states"]["binary_sensor.device_a"]["is_offline"] is True


async def test_save_storage_includes_cooldown_device(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Devices with active cooldown_start are included in saved device_states."""
    from custom_components.entity_availability.models import DeviceState

    hass = mock_hass
    coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    coord._store = MagicMock()
    coord._store.async_load = AsyncMock(return_value=None)
    coord._store.async_save = AsyncMock()

    cooldown_start = datetime.now(timezone.utc) - timedelta(seconds=30)
    device = DeviceState(entity_id="binary_sensor.device_a")
    device.is_offline = False
    device.cooldown_start = cooldown_start
    coord._device_states["binary_sensor.device_a"] = device

    await coord._async_save_storage()

    saved = coord._store.async_save.call_args[0][0]
    assert "binary_sensor.device_a" in saved["device_states"]


# ---------------------------------------------------------------------------
# _setup_state_listeners — re-registration cancels previous (lines 279-287)
# ---------------------------------------------------------------------------


def test_setup_state_listeners_cancels_existing(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """_setup_state_listeners cancels an existing listener before re-registering."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)

    cancel_calls = []

    def existing_unsub():
        cancel_calls.append(True)

    coord._unsub_state_change = existing_unsub

    with patch(
        "custom_components.entity_availability.coordinator.async_track_state_change_event",
        return_value=lambda: None,
    ):
        coord._setup_state_listeners()

    # The previous listener should have been cancelled
    assert len(cancel_calls) == 1


# ---------------------------------------------------------------------------
# _handle_state_change debounced_refresh callback (lines 311-312)
# ---------------------------------------------------------------------------


async def test_debounced_refresh_callback_creates_task(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """The debounced refresh callback clears _debounce_cancel and schedules a refresh."""
    hass = mock_hass

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)

    # Capture the debounced callback
    captured_callbacks = []

    def fake_call_later(h, delay, callback):
        captured_callbacks.append(callback)
        return lambda: None

    with patch(
        "custom_components.entity_availability.coordinator.async_call_later",
        side_effect=fake_call_later,
    ):
        coord._handle_state_change(MagicMock())

    assert len(captured_callbacks) == 1

    # Now invoke the callback: entry removed from map and refresh scheduled
    with patch.object(coord, "async_request_refresh", new_callable=AsyncMock):
        with patch.object(
            hass, "async_create_task", side_effect=lambda coro: coro.close()
        ) as mock_task:
            captured_callbacks[0](None)  # simulate the timer firing

    assert "unknown" not in coord._debounce_cancel_map
    mock_task.assert_called_once()


# ---------------------------------------------------------------------------
# _get_battery_level — battery map entity present with valid state (lines 509-520)
# ---------------------------------------------------------------------------


async def test_battery_via_map_valid_state(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """Battery level is read from the mapped entity when its state is valid."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", "on", {"friendly_name": "Device A"})
    hass.states.async_set("sensor.custom_battery", "55")

    from custom_components.entity_availability.const import CONF_BATTERY_ENTITY_MAP

    config = dict(mock_config_data)
    config[CONF_BATTERY_ENTITY_MAP] = {
        "binary_sensor.device_a": "sensor.custom_battery"
    }
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=config,
        entry_id="bat_map_valid_entry",
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
        coord._last_update = None
        await coord._async_update_data()

    assert coord.device_states["binary_sensor.device_a"].battery_level == 55


async def test_battery_via_map_entity_unavailable_returns_none(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """Battery level returns None when mapped entity is unavailable."""
    from homeassistant.const import STATE_UNAVAILABLE

    from custom_components.entity_availability.const import CONF_BATTERY_ENTITY_MAP

    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", "on", {"friendly_name": "Device A"})
    hass.states.async_set("sensor.custom_battery", STATE_UNAVAILABLE)

    config = dict(mock_config_data)
    config[CONF_BATTERY_ENTITY_MAP] = {
        "binary_sensor.device_a": "sensor.custom_battery"
    }
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=config,
        entry_id="bat_map_unavail_entry",
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
        coord._last_update = None
        await coord._async_update_data()

    assert coord.device_states["binary_sensor.device_a"].battery_level is None


# ---------------------------------------------------------------------------
# _get_battery_level — guessed battery entity (line 586)
# ---------------------------------------------------------------------------


async def test_battery_via_guessed_entity(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Battery level falls back to sensor.<entity_name>_battery when no map/attr."""
    hass = mock_hass
    # Entity with no battery attribute and no map
    hass.states.async_set("binary_sensor.device_a", "on", {"friendly_name": "Device A"})
    # Guessed entity: sensor.device_a_battery
    hass.states.async_set("sensor.device_a_battery", "72")

    # Ensure no battery_entity_map is set (empty dict is the default in conftest)
    # and no battery attribute on the entity itself
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

    assert coord.device_states["binary_sensor.device_a"].battery_level == 72


# ---------------------------------------------------------------------------
# _get_battery_from_device_registry — returns None when no match (line 600)
# ---------------------------------------------------------------------------


async def test_battery_from_device_registry_no_battery_entity(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """_get_battery_from_device_registry returns None when device has no battery entity."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", "on", {"friendly_name": "Device A"})

    mock_ent_entry = MagicMock()
    mock_ent_entry.device_id = "device_no_bat"

    # Device has one non-battery entity
    mock_non_bat_entry = MagicMock()
    mock_non_bat_entry.entity_id = "sensor.device_a_temperature"
    mock_non_bat_entry.original_device_class = "temperature"
    mock_non_bat_entry.device_class = "temperature"

    with (
        patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ),
        patch(
            "custom_components.entity_availability.coordinator.er.async_get"
        ) as mock_er,
        patch(
            "custom_components.entity_availability.coordinator.er.async_entries_for_device"
        ) as mock_entries,
    ):
        mock_ent_reg = MagicMock()
        mock_ent_reg.async_get.return_value = mock_ent_entry
        mock_er.return_value = mock_ent_reg
        mock_entries.return_value = [mock_non_bat_entry]

        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        result = coord._get_battery_from_device_registry("binary_sensor.device_a")

    assert result is None


# ---------------------------------------------------------------------------
# _get_battery_from_device_registry — skips self (line 586)
# ---------------------------------------------------------------------------


async def test_battery_from_device_registry_skips_self_entity(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """_get_battery_from_device_registry skips the entity itself when iterating."""
    hass = mock_hass
    hass.states.async_set("binary_sensor.device_a", "on", {"friendly_name": "Device A"})
    hass.states.async_set("sensor.device_a_bat_level", "65")

    mock_ent_entry = MagicMock()
    mock_ent_entry.device_id = "device_with_self"

    # The entity registry returns the monitored entity itself first, then a battery entity
    mock_self_entry = MagicMock()
    mock_self_entry.entity_id = (
        "binary_sensor.device_a"  # same entity — should be skipped
    )
    mock_self_entry.original_device_class = None
    mock_self_entry.device_class = None

    mock_bat_entry = MagicMock()
    mock_bat_entry.entity_id = "sensor.device_a_bat_level"
    mock_bat_entry.original_device_class = "battery"
    mock_bat_entry.device_class = "battery"

    with (
        patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ),
        patch(
            "custom_components.entity_availability.coordinator.er.async_get"
        ) as mock_er,
        patch(
            "custom_components.entity_availability.coordinator.er.async_entries_for_device"
        ) as mock_entries,
    ):
        mock_ent_reg = MagicMock()
        mock_ent_reg.async_get.return_value = mock_ent_entry
        mock_er.return_value = mock_ent_reg
        # Returns both: the entity itself (should be skipped) and a battery entity
        mock_entries.return_value = [mock_self_entry, mock_bat_entry]

        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        result = coord._get_battery_from_device_registry("binary_sensor.device_a")

    # Despite the self-entry being in the list, the battery is still found
    assert result == 65
