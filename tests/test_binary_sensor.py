"""Tests for Entity Availability binary sensor."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.binary_sensor import (
    AnyLowBatteryBinarySensor,
    AnyOfflineBinarySensor,
    AnyStaleBinarySensor,
    NonEssentialAnyOfflineBinarySensor,
    async_setup_entry,
)
from custom_components.entity_availability.const import (
    CONF_COMBINED_GROUPS,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)
from custom_components.entity_availability.models import DeviceState


@pytest.fixture
def mock_coordinator(mock_hass: HomeAssistant, mock_config_entry):
    """Create coordinator with device states for binary sensor tests."""
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
                is_offline=False,
            ),
            "binary_sensor.device_c": DeviceState(
                entity_id="binary_sensor.device_c",
                is_offline=False,
            ),
        }
    return coord


class TestAnyOfflineBinarySensor:
    """Tests for AnyOfflineBinarySensor."""

    def test_is_off_when_all_online(self, mock_coordinator, mock_hass):
        """Test is_on is False when all entities are online."""
        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.is_on is False

    def test_is_on_when_any_offline(self, mock_coordinator, mock_hass):
        """Test is_on is True when any entity is offline."""
        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = True
        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.is_on is True

    def test_is_on_when_multiple_offline(self, mock_coordinator, mock_hass):
        """Test is_on is True when multiple entities are offline."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_offline = True
        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = True
        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.is_on is True

    def test_suppressed_excluded(self, mock_coordinator, mock_hass):
        """Test suppressed offline entities don't trigger."""
        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = True
        mock_coordinator._device_states["binary_sensor.device_b"].is_suppressed = True
        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.is_on is False

    def test_is_off_with_no_devices(self, mock_coordinator, mock_hass):
        """Test is_on is False when no device states exist."""
        mock_coordinator._device_states = {}
        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        assert sensor.is_on is False

    def test_unique_id(self, mock_coordinator, mock_hass):
        """Test unique_id format."""
        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.unique_id == "test_entry_id_any_offline"

    def test_device_class_is_problem(self, mock_coordinator, mock_hass):
        """Test device class is PROBLEM."""
        from homeassistant.components.binary_sensor import BinarySensorDeviceClass

        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.device_class == BinarySensorDeviceClass.PROBLEM

    def test_extra_state_attributes(self, mock_coordinator, mock_hass):
        """Test extra attributes list offline entities."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_offline = True
        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = True
        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        sensor.is_on
        attrs = sensor.extra_state_attributes
        assert attrs["offline_count"] == 2
        assert "binary_sensor.device_a" in attrs["offline_entities"]
        assert "binary_sensor.device_b" in attrs["offline_entities"]

    def test_extra_state_attributes_excludes_suppressed(
        self, mock_coordinator, mock_hass
    ):
        """Test suppressed entities excluded from attributes."""
        mock_coordinator._device_states["binary_sensor.device_a"].is_offline = True
        mock_coordinator._device_states["binary_sensor.device_a"].is_suppressed = True
        mock_coordinator._device_states["binary_sensor.device_b"].is_offline = True
        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        sensor.hass = mock_hass
        sensor.is_on
        attrs = sensor.extra_state_attributes
        assert attrs["offline_count"] == 1
        assert "binary_sensor.device_a" not in attrs["offline_entities"]
        assert "binary_sensor.device_b" in attrs["offline_entities"]


# ---------------------------------------------------------------------------
# async_setup_entry — group path (lines 27-41)
# ---------------------------------------------------------------------------


async def test_binary_sensor_setup_entry_group_path(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """async_setup_entry for a regular (group) entry creates AnyOfflineBinarySensor."""
    hass = mock_hass
    mock_config_entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    hass.data[DOMAIN][mock_config_entry.entry_id] = coord

    added = []

    def capture(entities):
        added.extend(entities)

    await async_setup_entry(hass, mock_config_entry, capture)

    assert len(added) == 5
    assert isinstance(added[0], AnyOfflineBinarySensor)
    assert isinstance(added[1], AnyLowBatteryBinarySensor)
    assert isinstance(added[2], AnyStaleBinarySensor)
    assert isinstance(added[3], NonEssentialAnyOfflineBinarySensor)
    assert added[4].__class__.__name__ == "AnyLowBatteryNonEssentialBinarySensor"


async def test_binary_sensor_setup_entry_slug_fallback(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """When group name produces empty slug, entry_id[:8] is used instead."""
    hass = mock_hass
    config = dict(mock_config_data)
    config[CONF_GROUP_NAME] = "!!!"

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="!!!",
        data=config,
        entry_id="abcdef1234567890",
        unique_id=f"{DOMAIN}_fallback_slug",
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

    assert len(added) == 5
    assert "abcdef12" in added[0].entity_id


async def test_binary_sensor_setup_entry_combined_path(
    mock_hass: HomeAssistant,
) -> None:
    """async_setup_entry for a combined entry delegates to combined_binary_sensor."""
    hass = mock_hass
    combined_entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="My Combined",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
            CONF_GROUP_NAME: "My Combined",
            CONF_COMBINED_GROUPS: [],
        },
        entry_id="combined_bs_id",
        unique_id=f"{DOMAIN}_combined_my_combined_bs",
    )
    combined_entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with patch(
        "custom_components.entity_availability.combined_binary_sensor.async_setup_entry",
        new_callable=AsyncMock,
    ) as mock_combined:
        await async_setup_entry(hass, combined_entry, [].append)
        mock_combined.assert_called_once()


# ---------------------------------------------------------------------------
# group_slug sanitization — forward slash and special chars (GH issue)
# ---------------------------------------------------------------------------


async def test_binary_sensor_setup_entry_slug_sanitizes_slash_in_group_name(
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
        entry_id="slash_bs_entry",
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


class TestNonEssentialBinarySensor:
    """Binary sensor ignores non-essential offline entities."""

    def test_binary_sensor_ignores_non_essential_offline(
        self, mock_coordinator, mock_hass
    ):
        """is_on is False when only non-essential entity is offline."""
        mock_coordinator.device_states["binary_sensor.device_b"].is_non_essential = True
        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is False

    def test_binary_sensor_non_essential_not_in_attrs(
        self, mock_coordinator, mock_hass
    ):
        """extra_state_attributes omits non-essential offline entity."""
        mock_coordinator.device_states["binary_sensor.device_b"].is_non_essential = True
        sensor = AnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert (
            "binary_sensor.device_b"
            not in sensor.extra_state_attributes["offline_entities"]
        )

    def test_non_essential_any_offline_on(self, mock_coordinator, mock_hass):
        """NonEssentialAnyOfflineBinarySensor is_on True when non-essential offline."""
        d = mock_coordinator.device_states["binary_sensor.device_b"]
        d.is_offline = True
        d.is_non_essential = True
        sensor = NonEssentialAnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is True
        attrs = sensor.extra_state_attributes
        assert attrs["offline_count"] == 1
        assert "binary_sensor.device_b" in attrs["offline_entities"]

    def test_non_essential_any_offline_off(self, mock_coordinator, mock_hass):
        """NonEssentialAnyOfflineBinarySensor is_on False when no non-essential offline."""
        sensor = NonEssentialAnyOfflineBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is False
        assert sensor.extra_state_attributes["offline_count"] == 0


class TestAnyLowBatteryBinarySensor:
    """Tests for AnyLowBatteryBinarySensor."""

    def test_is_off_when_no_low_battery(self, mock_coordinator, mock_hass):
        sensor = AnyLowBatteryBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is False

    def test_is_on_when_low_battery(self, mock_coordinator, mock_hass):
        mock_coordinator.device_states["binary_sensor.device_a"].is_low_battery = True
        sensor = AnyLowBatteryBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is True

    def test_excludes_suppressed(self, mock_coordinator, mock_hass):
        d = mock_coordinator.device_states["binary_sensor.device_a"]
        d.is_low_battery = True
        d.is_suppressed = True
        sensor = AnyLowBatteryBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is False

    def test_excludes_non_essential(self, mock_coordinator, mock_hass):
        d = mock_coordinator.device_states["binary_sensor.device_a"]
        d.is_low_battery = True
        d.is_non_essential = True
        sensor = AnyLowBatteryBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is False

    def test_extra_state_attributes(self, mock_coordinator, mock_hass):
        mock_coordinator.device_states["binary_sensor.device_a"].is_low_battery = True
        sensor = AnyLowBatteryBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        attrs = sensor.extra_state_attributes
        assert attrs["low_battery_count"] == 1
        assert "binary_sensor.device_a" in attrs["low_battery_entities"]


class TestAnyStaleBinarySensor:
    """Tests for AnyStaleBinarySensor."""

    def test_is_off_when_no_stale(self, mock_coordinator, mock_hass):
        sensor = AnyStaleBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is False

    def test_is_on_when_stale(self, mock_coordinator, mock_hass):
        mock_coordinator.device_states["binary_sensor.device_a"].is_stale = True
        sensor = AnyStaleBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is True

    def test_excludes_suppressed(self, mock_coordinator, mock_hass):
        d = mock_coordinator.device_states["binary_sensor.device_a"]
        d.is_stale = True
        d.is_suppressed = True
        sensor = AnyStaleBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is False

    def test_excludes_non_essential(self, mock_coordinator, mock_hass):
        d = mock_coordinator.device_states["binary_sensor.device_a"]
        d.is_stale = True
        d.is_non_essential = True
        sensor = AnyStaleBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        assert sensor.is_on is False

    def test_extra_state_attributes(self, mock_coordinator, mock_hass):
        mock_coordinator.device_states["binary_sensor.device_a"].is_stale = True
        sensor = AnyStaleBinarySensor(
            mock_coordinator, "Test Group", "test_group", "test_entry_id"
        )
        attrs = sensor.extra_state_attributes
        assert attrs["stale_count"] == 1
        assert "binary_sensor.device_a" in attrs["stale_entities"]


# ---------------------------------------------------------------------------
# AnyPoorSignalBinarySensor tests
# ---------------------------------------------------------------------------


async def test_any_poor_signal_binary_sensor_on_when_essential_poor(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """AnyPoorSignalBinarySensor is ON when an essential entity has poor signal."""
    from unittest.mock import AsyncMock, patch
    from custom_components.entity_availability.const import (
        CONF_SIGNAL_ENABLED,
        CONF_SIGNAL_ENTITY_MAP,
    )
    from custom_components.entity_availability.binary_sensor import (
        AnyPoorSignalBinarySensor,
    )
    from custom_components.entity_availability.coordinator import (
        EntityAvailabilityCoordinator,
    )

    hass.states.async_set("binary_sensor.device_a", "on")
    hass.states.async_set("sensor.a_rssi", "-80")  # poor wifi

    data = dict(mock_config_entry.data)
    data[CONF_SIGNAL_ENABLED] = True
    data[CONF_SIGNAL_ENTITY_MAP] = {
        "binary_sensor.device_a": {"sensor": "sensor.a_rssi", "network_type": "wifi"},
    }
    mock_config_entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=data,
        entry_id="test_entry_id",
        unique_id=f"{DOMAIN}_test_group",
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

    sensor = AnyPoorSignalBinarySensor(coord, "Test", "test", "entry1")
    assert sensor.is_on is True
    assert (
        "binary_sensor.device_a"
        in sensor.extra_state_attributes["poor_signal_entities"]
    )


async def test_any_poor_signal_binary_sensor_off_when_signal_good(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """AnyPoorSignalBinarySensor is OFF when all essential entities have good signal."""
    from unittest.mock import AsyncMock, patch
    from custom_components.entity_availability.const import (
        CONF_SIGNAL_ENABLED,
        CONF_SIGNAL_ENTITY_MAP,
    )
    from custom_components.entity_availability.binary_sensor import (
        AnyPoorSignalBinarySensor,
    )
    from custom_components.entity_availability.coordinator import (
        EntityAvailabilityCoordinator,
    )

    hass.states.async_set("binary_sensor.device_a", "on")
    hass.states.async_set("sensor.a_rssi", "-40")  # good wifi

    data = dict(mock_config_entry.data)
    data[CONF_SIGNAL_ENABLED] = True
    data[CONF_SIGNAL_ENTITY_MAP] = {
        "binary_sensor.device_a": {"sensor": "sensor.a_rssi", "network_type": "wifi"},
    }
    mock_config_entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=data,
        entry_id="test_entry_id",
        unique_id=f"{DOMAIN}_test_group",
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

    sensor = AnyPoorSignalBinarySensor(coord, "Test", "test", "entry1")
    assert sensor.is_on is False


async def test_any_poor_signal_binary_sensor_off_when_only_ne_is_poor(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """AnyPoorSignalBinarySensor stays OFF when only a NE entity has poor signal."""
    from unittest.mock import AsyncMock, patch
    from custom_components.entity_availability.const import (
        CONF_NON_ESSENTIAL_ENTITIES,
        CONF_SIGNAL_ENABLED,
        CONF_SIGNAL_ENTITY_MAP,
    )
    from custom_components.entity_availability.binary_sensor import (
        AnyPoorSignalBinarySensor,
    )
    from custom_components.entity_availability.coordinator import (
        EntityAvailabilityCoordinator,
    )

    hass.states.async_set("binary_sensor.device_c", "on")
    hass.states.async_set("sensor.c_rssi", "-80")

    data = dict(mock_config_entry.data)
    data[CONF_SIGNAL_ENABLED] = True
    data[CONF_NON_ESSENTIAL_ENTITIES] = ["binary_sensor.device_c"]
    data[CONF_SIGNAL_ENTITY_MAP] = {
        "binary_sensor.device_c": {"sensor": "sensor.c_rssi", "network_type": "wifi"},
    }
    mock_config_entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=data,
        entry_id="test_entry_id",
        unique_id=f"{DOMAIN}_test_group",
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._last_update = None
        await coord._async_update_data()

    sensor = AnyPoorSignalBinarySensor(coord, "Test", "test", "entry1")
    assert sensor.is_on is False


async def test_binary_sensor_setup_entry_with_signal_enabled(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """async_setup_entry adds AnyPoorSignalBinarySensor when signal_enabled=True."""
    from custom_components.entity_availability.const import (
        CONF_SIGNAL_ENABLED,
        CONF_SIGNAL_ENTITY_MAP,
    )
    from custom_components.entity_availability.binary_sensor import (
        AnyPoorSignalBinarySensor,
    )

    hass = mock_hass
    config = dict(mock_config_data)
    config[CONF_SIGNAL_ENABLED] = True
    config[CONF_SIGNAL_ENTITY_MAP] = {}

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Signal Group",
        data=config,
        entry_id="sig_entry",
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coord

    added = []
    await async_setup_entry(hass, entry, added.extend)

    types = [type(e).__name__ for e in added]
    assert "AnyPoorSignalBinarySensor" in types
    # Should be the 5th entity (index 4)
    poor_signal_sensors = [e for e in added if isinstance(e, AnyPoorSignalBinarySensor)]
    assert len(poor_signal_sensors) == 1


async def test_any_low_battery_non_essential_binary_sensor_on(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """AnyLowBatteryNonEssentialBinarySensor is ON when a NE entity has low battery."""
    from unittest.mock import AsyncMock, patch
    from custom_components.entity_availability.binary_sensor import (
        AnyLowBatteryNonEssentialBinarySensor,
    )
    from custom_components.entity_availability.coordinator import (
        EntityAvailabilityCoordinator,
    )
    from custom_components.entity_availability.models import DeviceState

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)

    coord._device_states["binary_sensor.ne"] = DeviceState(
        entity_id="binary_sensor.ne", is_non_essential=True, is_low_battery=True
    )
    coord._device_states["binary_sensor.ess"] = DeviceState(
        entity_id="binary_sensor.ess", is_non_essential=False, is_low_battery=False
    )

    sensor = AnyLowBatteryNonEssentialBinarySensor(coord, "Test", "test", "eid1")
    assert sensor.is_on is True
    assert "binary_sensor.ne" in sensor.extra_state_attributes["low_battery_entities"]


async def test_any_low_battery_non_essential_binary_sensor_off_when_essential_low(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """AnyLowBatteryNonEssentialBinarySensor stays OFF when only essential has low battery."""
    from unittest.mock import AsyncMock, patch
    from custom_components.entity_availability.binary_sensor import (
        AnyLowBatteryNonEssentialBinarySensor,
    )
    from custom_components.entity_availability.coordinator import (
        EntityAvailabilityCoordinator,
    )
    from custom_components.entity_availability.models import DeviceState

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)

    coord._device_states["binary_sensor.ess"] = DeviceState(
        entity_id="binary_sensor.ess", is_non_essential=False, is_low_battery=True
    )

    sensor = AnyLowBatteryNonEssentialBinarySensor(coord, "Test", "test", "eid1")
    assert sensor.is_on is False


async def test_any_poor_signal_non_essential_binary_sensor_on(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """AnyPoorSignalNonEssentialBinarySensor is ON when a NE entity has poor signal."""
    from unittest.mock import AsyncMock, patch
    from custom_components.entity_availability.binary_sensor import (
        AnyPoorSignalNonEssentialBinarySensor,
    )
    from custom_components.entity_availability.coordinator import (
        EntityAvailabilityCoordinator,
    )
    from custom_components.entity_availability.models import DeviceState

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)

    coord._device_states["binary_sensor.ne"] = DeviceState(
        entity_id="binary_sensor.ne", is_non_essential=True, signal_quality="poor"
    )

    sensor = AnyPoorSignalNonEssentialBinarySensor(coord, "Test", "test", "eid1")
    assert sensor.is_on is True
    assert "binary_sensor.ne" in sensor.extra_state_attributes["poor_signal_entities"]


async def test_any_poor_signal_non_essential_off_when_essential_poor(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """AnyPoorSignalNonEssentialBinarySensor OFF when only essential has poor signal."""
    from unittest.mock import AsyncMock, patch
    from custom_components.entity_availability.binary_sensor import (
        AnyPoorSignalNonEssentialBinarySensor,
    )
    from custom_components.entity_availability.coordinator import (
        EntityAvailabilityCoordinator,
    )
    from custom_components.entity_availability.models import DeviceState

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)

    coord._device_states["binary_sensor.ess"] = DeviceState(
        entity_id="binary_sensor.ess", is_non_essential=False, signal_quality="poor"
    )

    sensor = AnyPoorSignalNonEssentialBinarySensor(coord, "Test", "test", "eid1")
    assert sensor.is_on is False


async def test_binary_sensor_setup_includes_ne_low_battery(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """async_setup_entry always creates AnyLowBatteryNonEssentialBinarySensor."""

    mock_config_entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
    hass.data[DOMAIN][mock_config_entry.entry_id] = coord

    added = []
    await async_setup_entry(hass, mock_config_entry, added.extend)

    types = [type(e).__name__ for e in added]
    assert "AnyLowBatteryNonEssentialBinarySensor" in types
