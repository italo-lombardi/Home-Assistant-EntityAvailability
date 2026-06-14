"""Tests for combined group binary sensor entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.combined_binary_sensor import (
    CombinedGroupAnyOfflineBinarySensor,
    async_setup_entry,
)
from custom_components.entity_availability.const import (
    CONF_COMBINED_GROUPS,
    CONF_ENTITIES,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
    ENTRY_TYPE_GROUP,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)
from custom_components.entity_availability.models import DeviceState


# ---------------------------------------------------------------------------
# Fixtures (mirror those in test_combined_sensor.py)
# ---------------------------------------------------------------------------


def _make_group_entry(entry_id: str, name: str, entities: list[str]) -> MockConfigEntry:
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title=name,
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_GROUP,
            CONF_GROUP_NAME: name,
            CONF_ENTITIES: entities,
        },
        entry_id=entry_id,
        unique_id=f"{DOMAIN}_{name.lower().replace(' ', '_')}",
    )


def _make_combined_entry(
    entry_id: str, name: str, combined_ids: list[str]
) -> MockConfigEntry:
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title=name,
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
            CONF_GROUP_NAME: name,
            CONF_COMBINED_GROUPS: combined_ids,
        },
        entry_id=entry_id,
        unique_id=f"{DOMAIN}_combined_{name.lower().replace(' ', '_')}",
    )


@pytest.fixture
def group_entry_a() -> MockConfigEntry:
    return _make_group_entry(
        "entry_a", "Group A", ["binary_sensor.a1", "binary_sensor.a2"]
    )


@pytest.fixture
def group_entry_b() -> MockConfigEntry:
    return _make_group_entry("entry_b", "Group B", ["binary_sensor.b1"])


@pytest.fixture
def combined_entry(group_entry_a, group_entry_b) -> MockConfigEntry:
    return _make_combined_entry("combined_1", "Combined", ["entry_a", "entry_b"])


@pytest.fixture
def coordinator_a(mock_hass: HomeAssistant, group_entry_a: MockConfigEntry):
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(mock_hass, group_entry_a)
        coord._device_states = {
            "binary_sensor.a1": DeviceState(
                entity_id="binary_sensor.a1", is_offline=False
            ),
            "binary_sensor.a2": DeviceState(
                entity_id="binary_sensor.a2", is_offline=False
            ),
        }
    return coord


@pytest.fixture
def coordinator_b(mock_hass: HomeAssistant, group_entry_b: MockConfigEntry):
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(mock_hass, group_entry_b)
        coord._device_states = {
            "binary_sensor.b1": DeviceState(
                entity_id="binary_sensor.b1", is_offline=False
            ),
        }
    return coord


@pytest.fixture
def coordinators(coordinator_a, coordinator_b):
    return [coordinator_a, coordinator_b]


# ---------------------------------------------------------------------------
# CombinedGroupAnyOfflineBinarySensor
# ---------------------------------------------------------------------------


class TestCombinedGroupAnyOfflineBinarySensor:
    """Tests for CombinedGroupAnyOfflineBinarySensor."""

    def _sensor(self, hass, entry, coordinators):
        return CombinedGroupAnyOfflineBinarySensor(
            hass,
            entry,
            "Combined",
            "combined",
            coordinators,
            [c.entry.entry_id for c in coordinators],
        )

    # -- is_on logic --

    def test_is_off_when_all_online(self, mock_hass, combined_entry, coordinators):
        """is_on is False when all entities across all groups are online."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.is_on is False

    def test_is_on_when_any_offline(self, mock_hass, combined_entry, coordinators):
        """is_on is True when any entity is offline."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].is_offline = True
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.is_on is True

    def test_is_on_when_offline_in_second_group(
        self, mock_hass, combined_entry, coordinators
    ):
        """is_on is True when entity in the second group is offline."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[1]._device_states["binary_sensor.b1"].is_offline = True
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.is_on is True

    def test_suppressed_offline_excluded(self, mock_hass, combined_entry, coordinators):
        """Suppressed offline entities do not trigger is_on."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].is_offline = True
        coordinators[0]._device_states["binary_sensor.a1"].is_suppressed = True
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.is_on is False

    def test_is_off_when_no_coordinators(self, mock_hass, combined_entry):
        """is_on is False when the coordinator list is empty."""
        mock_hass.data[DOMAIN] = {}
        sensor = self._sensor(mock_hass, combined_entry, [])
        assert sensor.is_on is False

    # -- attributes --

    def test_extra_state_attributes_empty_when_all_online(
        self, mock_hass, combined_entry, coordinators
    ):
        """Attributes list 0 offline entities when all are online."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        attrs = sensor.extra_state_attributes
        assert attrs["offline_count"] == 0
        assert attrs["offline_entities"] == []

    def test_extra_state_attributes_lists_offline(
        self, mock_hass, combined_entry, coordinators
    ):
        """Attributes contain offline entity ids from both groups."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].is_offline = True
        coordinators[1]._device_states["binary_sensor.b1"].is_offline = True
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        attrs = sensor.extra_state_attributes
        assert attrs["offline_count"] == 2
        assert "binary_sensor.a1" in attrs["offline_entities"]
        assert "binary_sensor.b1" in attrs["offline_entities"]

    def test_extra_state_attributes_excludes_suppressed(
        self, mock_hass, combined_entry, coordinators
    ):
        """Suppressed entities excluded from offline_entities attribute."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].is_offline = True
        coordinators[0]._device_states["binary_sensor.a1"].is_suppressed = True
        coordinators[0]._device_states["binary_sensor.a2"].is_offline = True
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        attrs = sensor.extra_state_attributes
        assert attrs["offline_count"] == 1
        assert "binary_sensor.a1" not in attrs["offline_entities"]
        assert "binary_sensor.a2" in attrs["offline_entities"]

    # -- metadata --

    def test_device_class_is_problem(self, mock_hass, combined_entry, coordinators):
        """device_class is PROBLEM."""
        mock_hass.data[DOMAIN] = {}
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.device_class == BinarySensorDeviceClass.PROBLEM

    def test_unique_id(self, mock_hass, combined_entry, coordinators):
        """unique_id uses entry_id + suffix."""
        mock_hass.data[DOMAIN] = {}
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.unique_id == "combined_1_combined_any_offline"

    # -- subscription lifecycle --

    async def test_async_added_subscribes_coordinators(
        self, mock_hass, combined_entry, coordinators
    ):
        """async_added_to_hass registers a listener on every coordinator."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._sensor(mock_hass, combined_entry, coordinators)

        fired = []

        def _fake_add_listener(callback):
            fired.append(True)
            return lambda: None

        for coord in coordinators:
            coord.async_add_listener = _fake_add_listener

        await sensor.async_added_to_hass()
        assert len(fired) == len(coordinators)

    async def test_async_will_remove_unsubscribes(
        self, mock_hass, combined_entry, coordinators
    ):
        """async_will_remove_from_hass calls every unsub and clears the list."""
        mock_hass.data[DOMAIN] = {}
        sensor = self._sensor(mock_hass, combined_entry, coordinators)

        unsub_called = []

        def _unsub():
            unsub_called.append(True)

        sensor._unsub_listeners = [_unsub, _unsub]
        await sensor.async_will_remove_from_hass()
        assert len(unsub_called) == 2
        assert sensor._unsub_listeners == []

    # -- active coordinators --

    def test_active_coordinators_filters_unloaded(
        self, mock_hass, combined_entry, coordinators
    ):
        """_active_coordinators skips coordinators not in hass.data."""
        mock_hass.data[DOMAIN] = {"entry_a": coordinators[0]}
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        active = sensor._active_coordinators()
        assert len(active) == 1
        assert active[0] is coordinators[0]

    def test_available_false_when_all_coordinators_unloaded(
        self, mock_hass, combined_entry, coordinators
    ):
        """available is False when all source coordinators have been removed from hass.data."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        # Simulate all group entries being unloaded
        mock_hass.data[DOMAIN].pop("entry_a", None)
        mock_hass.data[DOMAIN].pop("entry_b", None)
        assert sensor.available is False


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


class TestCombinedBinarySensorSetupEntry:
    """Tests for combined_binary_sensor.async_setup_entry."""

    async def test_setup_registers_one_binary_sensor(
        self, mock_hass: HomeAssistant, group_entry_a, group_entry_b, combined_entry
    ):
        """async_setup_entry registers exactly one binary sensor."""
        coord_a = MagicMock(spec=EntityAvailabilityCoordinator)
        coord_a.entry = group_entry_a
        coord_b = MagicMock(spec=EntityAvailabilityCoordinator)
        coord_b.entry = group_entry_b
        mock_hass.data[DOMAIN] = {"entry_a": coord_a, "entry_b": coord_b}

        group_entry_a.add_to_hass(mock_hass)
        group_entry_b.add_to_hass(mock_hass)
        combined_entry.add_to_hass(mock_hass)

        added = []

        def _fake_add(entities):
            added.extend(entities)

        await async_setup_entry(mock_hass, combined_entry, _fake_add)
        assert len(added) == 1
        assert isinstance(added[0], CombinedGroupAnyOfflineBinarySensor)

    async def test_setup_skips_missing_source_groups(
        self, mock_hass: HomeAssistant, group_entry_a, group_entry_b, combined_entry
    ):
        """Source groups not in hass.data are simply skipped (no error)."""
        # Only entry_a is loaded
        coord_a = MagicMock(spec=EntityAvailabilityCoordinator)
        coord_a.entry = group_entry_a
        mock_hass.data[DOMAIN] = {"entry_a": coord_a}

        group_entry_a.add_to_hass(mock_hass)
        group_entry_b.add_to_hass(mock_hass)
        combined_entry.add_to_hass(mock_hass)

        added = []

        def _fake_add(entities):
            added.extend(entities)

        await async_setup_entry(mock_hass, combined_entry, _fake_add)
        # Still registers the sensor, just with fewer coordinators
        assert len(added) == 1


# ---------------------------------------------------------------------------
# CombinedGroupAnyOfflineBinarySensor — callback fires async_write_ha_state (line 82)
# ---------------------------------------------------------------------------


class TestCombinedBinarySensorCallbackFires:
    """Test that the coordinator update callback calls async_write_ha_state."""

    async def test_coordinator_callback_calls_write_ha_state(
        self, mock_hass, combined_entry, coordinators
    ):
        """The _on_coordinator_update callback invokes async_write_ha_state."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }

        sensor = CombinedGroupAnyOfflineBinarySensor(
            mock_hass,
            combined_entry,
            "Combined",
            "combined",
            coordinators,
            [c.entry.entry_id for c in coordinators],
        )

        write_state_calls = []
        sensor.async_write_ha_state = lambda: write_state_calls.append(True)

        captured_callbacks = []

        def fake_add_listener(callback):
            captured_callbacks.append(callback)
            return lambda: None

        for coord in coordinators:
            coord.async_add_listener = fake_add_listener

        await sensor.async_added_to_hass()

        # Fire the first registered callback
        assert len(captured_callbacks) >= 1
        captured_callbacks[0]()

        assert len(write_state_calls) == 1


# ---------------------------------------------------------------------------
# group_slug sanitization — forward slash and special chars (GH issue)
# ---------------------------------------------------------------------------


async def test_combined_binary_sensor_setup_entry_slug_sanitizes_slash_in_group_name(
    mock_hass: HomeAssistant, group_entry_a, group_entry_b
) -> None:
    """Combined group names with slashes produce valid entity IDs (no slash in slug)."""
    combined_entry = _make_combined_entry(
        "slash_combined_bs_entry",
        "Motion/Presence Combined",
        ["entry_a", "entry_b"],
    )
    coord_a = MagicMock(spec=EntityAvailabilityCoordinator)
    coord_a.entry = group_entry_a
    coord_b = MagicMock(spec=EntityAvailabilityCoordinator)
    coord_b.entry = group_entry_b
    mock_hass.data[DOMAIN] = {"entry_a": coord_a, "entry_b": coord_b}

    group_entry_a.add_to_hass(mock_hass)
    group_entry_b.add_to_hass(mock_hass)
    combined_entry.add_to_hass(mock_hass)

    added = []

    def _fake_add(entities):
        added.extend(entities)

    await async_setup_entry(mock_hass, combined_entry, _fake_add)

    for entity in added:
        assert "/" not in entity.entity_id, (
            f"entity_id '{entity.entity_id}' contains forward slash"
        )


async def test_combined_binary_sensor_slug_fallback(
    mock_hass: HomeAssistant, group_entry_a, group_entry_b
) -> None:
    """All-special-char group name falls back to entry_id[:8] for the slug."""
    combined_entry = _make_combined_entry(
        "abcdef1234567890",
        "!!!",
        ["entry_a", "entry_b"],
    )
    coord_a = MagicMock(spec=EntityAvailabilityCoordinator)
    coord_a.entry = group_entry_a
    coord_b = MagicMock(spec=EntityAvailabilityCoordinator)
    coord_b.entry = group_entry_b
    mock_hass.data[DOMAIN] = {"entry_a": coord_a, "entry_b": coord_b}

    group_entry_a.add_to_hass(mock_hass)
    group_entry_b.add_to_hass(mock_hass)
    combined_entry.add_to_hass(mock_hass)

    added = []

    def _fake_add(entities):
        added.extend(entities)

    await async_setup_entry(mock_hass, combined_entry, _fake_add)

    assert len(added) == 1
    assert "abcdef12" in added[0].entity_id
