"""Tests for Entity Availability binary sensor."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.binary_sensor import (
    AnyOfflineBinarySensor,
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

    assert len(added) == 1
    assert isinstance(added[0], AnyOfflineBinarySensor)


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

    assert len(added) == 1
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
