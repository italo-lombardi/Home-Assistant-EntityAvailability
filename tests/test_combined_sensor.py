"""Tests for combined group sensor entities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.combined_sensor import (
    MAX_STATE_LENGTH,
    CombinedGroupSensor,
    CombinedLowBatteryCountSensor,
    CombinedLowBatterySensor,
    CombinedOfflineEntitiesSensor,
    CombinedRecentlyOfflineSensor,
    CombinedRecentlyRecoveredSensor,
    async_setup_entry,
)
from custom_components.entity_availability.const import (
    CONF_AVAILABILITY_WINDOWS,
    CONF_BATTERY_ENTITY_MAP,
    CONF_COMBINED_GROUPS,
    CONF_ENTITIES,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    DEFAULT_AVAILABILITY_WINDOWS,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
    ENTRY_TYPE_GROUP,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)
from custom_components.entity_availability.models import DeviceState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_group_entry(entry_id: str, name: str, entities: list[str]) -> MockConfigEntry:
    """Return a MockConfigEntry representing a monitor group."""
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title=name,
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_GROUP,
            CONF_GROUP_NAME: name,
            CONF_ENTITIES: entities,
            CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
        },
        entry_id=entry_id,
        unique_id=f"{DOMAIN}_{name.lower().replace(' ', '_')}",
    )


def _make_combined_entry(
    entry_id: str, name: str, combined_ids: list[str]
) -> MockConfigEntry:
    """Return a MockConfigEntry representing a combined group."""
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
    """Coordinator for group A with pre-populated device states."""
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(mock_hass, group_entry_a)
        coord._device_states = {
            "binary_sensor.a1": DeviceState(
                entity_id="binary_sensor.a1",
                is_offline=False,
            ),
            "binary_sensor.a2": DeviceState(
                entity_id="binary_sensor.a2",
                is_offline=True,
            ),
        }
    return coord


@pytest.fixture
def coordinator_b(mock_hass: HomeAssistant, group_entry_b: MockConfigEntry):
    """Coordinator for group B with pre-populated device states."""
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(mock_hass, group_entry_b)
        coord._device_states = {
            "binary_sensor.b1": DeviceState(
                entity_id="binary_sensor.b1",
                is_offline=False,
            ),
        }
    return coord


@pytest.fixture
def coordinators(coordinator_a, coordinator_b):
    return [coordinator_a, coordinator_b]


# ---------------------------------------------------------------------------
# CombinedSensorBase: subscription lifecycle
# ---------------------------------------------------------------------------


class TestCombinedSensorBase:
    """Tests for the base subscription/unsubscription mechanism."""

    def _make_sensor(self, hass, entry, coordinators):
        """Instantiate a minimal concrete subclass (CombinedGroupSensor)."""
        return CombinedGroupSensor(
            hass,
            entry,
            "Combined",
            "combined",
            coordinators,
            [c.entry.entry_id for c in coordinators],
        )

    async def test_async_added_subscribes_all_coordinators(
        self, mock_hass, combined_entry, coordinators
    ):
        """async_added_to_hass registers a listener on every coordinator."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._make_sensor(mock_hass, combined_entry, coordinators)

        fired = []

        def _fake_add_listener(callback):
            fired.append(True)
            # Return an unsub callable
            return lambda: None

        for coord in coordinators:
            coord.async_add_listener = _fake_add_listener

        await sensor.async_added_to_hass()
        assert len(fired) == len(coordinators)

    async def test_async_will_remove_calls_unsubs(
        self, mock_hass, combined_entry, coordinators
    ):
        """async_will_remove_from_hass calls every unsub and clears the list."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._make_sensor(mock_hass, combined_entry, coordinators)

        unsub_called = []

        def _unsub():
            unsub_called.append(True)

        # Manually inject unsub callbacks
        sensor._unsub_listeners = [_unsub, _unsub]
        await sensor.async_will_remove_from_hass()

        assert len(unsub_called) == 2
        assert sensor._unsub_listeners == []

    def test_active_coordinators_filters_unloaded(
        self, mock_hass, combined_entry, coordinators
    ):
        """_active_coordinators returns only coordinators still in hass.data."""
        # Only entry_a is loaded
        mock_hass.data[DOMAIN] = {"entry_a": coordinators[0]}
        sensor = self._make_sensor(mock_hass, combined_entry, coordinators)
        active = sensor._active_coordinators()
        assert len(active) == 1
        assert active[0] is coordinators[0]

    def test_active_coordinators_returns_all_when_all_loaded(
        self, mock_hass, combined_entry, coordinators
    ):
        """All coordinators returned when all are loaded."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._make_sensor(mock_hass, combined_entry, coordinators)
        assert len(sensor._active_coordinators()) == 2

    def test_available_true_when_at_least_one_coordinator_loaded(
        self, mock_hass, combined_entry, coordinators
    ):
        """available returns True when at least one coordinator is active."""
        mock_hass.data[DOMAIN] = {"entry_a": coordinators[0]}
        sensor = self._make_sensor(mock_hass, combined_entry, coordinators)
        assert sensor.available is True

    def test_available_false_when_all_coordinators_unloaded(
        self, mock_hass, combined_entry, coordinators
    ):
        """available returns False when no coordinator is in hass.data."""
        mock_hass.data[DOMAIN] = {}
        sensor = self._make_sensor(mock_hass, combined_entry, coordinators)
        assert sensor.available is False

    def test_available_false_when_hass_data_has_non_coordinator_value(
        self, mock_hass, combined_entry, coordinators
    ):
        """available returns False when hass.data entry is not an EntityAvailabilityCoordinator."""
        mock_hass.data[DOMAIN] = {"entry_a": "not_a_coordinator", "entry_b": object()}
        sensor = self._make_sensor(mock_hass, combined_entry, coordinators)
        assert sensor.available is False

    def test_missing_groups_uses_isinstance_check(
        self, mock_hass, combined_entry, coordinators
    ):
        """missing_groups flags entries whose hass.data value is not a coordinator."""
        # entry_a has a non-coordinator value — should be flagged as missing
        mock_hass.data[DOMAIN] = {
            "entry_a": "not_a_coordinator",
            "entry_b": coordinators[1],
        }
        sensor = CombinedGroupSensor(
            mock_hass,
            combined_entry,
            "Combined",
            "combined",
            coordinators,
            ["entry_a", "entry_b"],
        )
        attrs = sensor.extra_state_attributes
        assert "missing_groups" in attrs
        assert "entry_a" in attrs["missing_groups"]
        assert "entry_b" not in attrs["missing_groups"]


# ---------------------------------------------------------------------------
# CombinedGroupSensor
# ---------------------------------------------------------------------------


class TestCombinedGroupSensor:
    """Tests for CombinedGroupSensor."""

    def _sensor(self, hass, entry, coordinators):
        return CombinedGroupSensor(
            hass,
            entry,
            "Combined",
            "combined",
            coordinators,
            [c.entry.entry_id for c in coordinators],
        )

    def test_native_value_sums_offline(self, mock_hass, combined_entry, coordinators):
        """native_value is the total count of unsuppressed offline devices across groups."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        # a2 is offline, all others online
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.native_value == 1

    def test_native_value_suppressed_excluded(
        self, mock_hass, combined_entry, coordinators
    ):
        """Suppressed offline devices are not counted."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a2"].is_suppressed = True
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.native_value == 0

    def test_native_value_multiple_groups(
        self, mock_hass, combined_entry, coordinators
    ):
        """Offline devices from multiple groups are summed."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[1]._device_states["binary_sensor.b1"].is_offline = True
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.native_value == 2

    def test_attributes_breakdown(self, mock_hass, combined_entry, coordinators):
        """extra_state_attributes has groups, totals and offline_entities."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        attrs = sensor.extra_state_attributes

        assert attrs["total_entities"] == 3  # a1, a2, b1
        assert attrs["offline"] == 1  # a2
        assert attrs["online"] == 2
        assert "binary_sensor.a2" in attrs["offline_entities"]
        assert "groups" in attrs
        assert "entry_a" in attrs["groups"]
        assert "entry_b" in attrs["groups"]
        assert attrs["groups"]["entry_a"]["name"] == "Group A"
        assert "entities" in attrs
        assert set(attrs["entities"]) == {
            "binary_sensor.a1",
            "binary_sensor.a2",
            "binary_sensor.b1",
        }
        assert len(attrs["entities"]) == len(set(attrs["entities"])), (
            "entities must be deduplicated"
        )

    def test_attributes_entities_deduplicated(self, mock_hass, group_entry_a):
        """entities list has no duplicates when the same entity appears in two source groups."""
        shared_entry = _make_group_entry(
            "entry_shared", "Shared Group", ["binary_sensor.a1", "binary_sensor.shared"]
        )
        combined = _make_combined_entry(
            "combined_dup", "Dup", ["entry_a", "entry_shared"]
        )

        with patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ):
            coord_a = EntityAvailabilityCoordinator(mock_hass, group_entry_a)
            coord_a._device_states = {
                "binary_sensor.a1": DeviceState(entity_id="binary_sensor.a1"),
                "binary_sensor.a2": DeviceState(entity_id="binary_sensor.a2"),
            }
            coord_shared = EntityAvailabilityCoordinator(mock_hass, shared_entry)
            coord_shared._device_states = {
                "binary_sensor.a1": DeviceState(entity_id="binary_sensor.a1"),
                "binary_sensor.shared": DeviceState(entity_id="binary_sensor.shared"),
            }

        mock_hass.data[DOMAIN] = {"entry_a": coord_a, "entry_shared": coord_shared}
        sensor = CombinedGroupSensor(
            mock_hass,
            combined,
            "Dup",
            "dup",
            [coord_a, coord_shared],
            ["entry_a", "entry_shared"],
        )
        entities = sensor.extra_state_attributes["entities"]
        assert entities.count("binary_sensor.a1") == 1, (
            "duplicate entity_id in entities list"
        )
        assert set(entities) == {
            "binary_sensor.a1",
            "binary_sensor.a2",
            "binary_sensor.shared",
        }

    def test_attributes_battery_powered_via_device_states(
        self, mock_hass, combined_entry, coordinators
    ):
        """battery_powered counts devices with battery_level set when no battery map."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].battery_level = 80
        coordinators[1]._device_states["binary_sensor.b1"].battery_level = 55
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        attrs = sensor.extra_state_attributes
        assert attrs["battery_powered"] == 2
        assert attrs["groups"]["entry_a"]["battery_powered"] == 1
        assert attrs["groups"]["entry_b"]["battery_powered"] == 1

    def test_attributes_battery_powered_via_battery_map(
        self, mock_hass, group_entry_a, group_entry_b, coordinators
    ):
        """battery_powered uses CONF_BATTERY_ENTITY_MAP when present."""

        group_entry_a_with_map = MockConfigEntry(
            version=1,
            domain=DOMAIN,
            title="Group A",
            data={
                CONF_ENTRY_TYPE: ENTRY_TYPE_GROUP,
                CONF_GROUP_NAME: "Group A",
                CONF_ENTITIES: ["binary_sensor.a1", "binary_sensor.a2"],
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
                CONF_BATTERY_ENTITY_MAP: {
                    "binary_sensor.a1": "sensor.a1_battery",
                    "binary_sensor.a2": None,
                },
            },
            entry_id="entry_a",
        )
        combined_entry = MockConfigEntry(
            version=1,
            domain=DOMAIN,
            title="Combined",
            data={
                CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
                CONF_GROUP_NAME: "Combined",
                CONF_COMBINED_GROUPS: ["entry_a", "entry_b"],
            },
            entry_id="combined_1",
        )
        with patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ):
            coord_a = EntityAvailabilityCoordinator(mock_hass, group_entry_a_with_map)
            coord_a._device_states = {
                "binary_sensor.a1": DeviceState(entity_id="binary_sensor.a1"),
                "binary_sensor.a2": DeviceState(entity_id="binary_sensor.a2"),
            }
        mock_hass.data[DOMAIN] = {
            "entry_a": coord_a,
            "entry_b": coordinators[1],
        }
        sensor = CombinedGroupSensor(
            mock_hass,
            combined_entry,
            "Combined",
            "combined",
            [coord_a, coordinators[1]],
            ["entry_a", "entry_b"],
        )
        attrs = sensor.extra_state_attributes
        # Group A: 1 non-None value in map; Group B: no map → 0
        assert attrs["groups"]["entry_a"]["battery_powered"] == 1
        assert attrs["groups"]["entry_b"]["battery_powered"] == 0
        assert attrs["battery_powered"] == 1

    def test_attributes_battery_powered_zero_when_no_battery(
        self, mock_hass, combined_entry, coordinators
    ):
        """battery_powered is 0 when no devices have battery_level set."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.extra_state_attributes["battery_powered"] == 0

    def test_attributes_missing_groups(self, mock_hass, combined_entry, coordinators):
        """missing_groups attribute present when a source group is not in hass.data."""
        # Only load entry_a
        mock_hass.data[DOMAIN] = {"entry_a": coordinators[0]}
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        attrs = sensor.extra_state_attributes
        assert "missing_groups" in attrs
        assert "entry_b" in attrs["missing_groups"]

    def test_unique_id(self, mock_hass, combined_entry, coordinators):
        """unique_id uses entry_id + suffix."""
        mock_hass.data[DOMAIN] = {}
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.unique_id == "combined_1_combined_summary"


# ---------------------------------------------------------------------------
# CombinedOfflineEntitiesSensor
# ---------------------------------------------------------------------------


class TestCombinedOfflineEntitiesSensor:
    """Tests for CombinedOfflineEntitiesSensor."""

    def _sensor(self, hass, entry, coordinators):
        return CombinedOfflineEntitiesSensor(
            hass,
            entry,
            "Combined",
            "combined",
            coordinators,
            [c.entry.entry_id for c in coordinators],
        )

    def test_native_value_none_when_all_online(
        self, mock_hass, combined_entry, coordinators
    ):
        """Returns 'None' string when no devices are offline."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a2"].is_offline = False
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.native_value == "None"

    def test_native_value_uses_friendly_name(
        self, mock_hass, combined_entry, coordinators
    ):
        """Offline device shown with its friendly name."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        mock_hass.states.async_set(
            "binary_sensor.a2", STATE_UNAVAILABLE, {"friendly_name": "Sensor A2"}
        )
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert "Sensor A2" in sensor.native_value

    def test_native_value_truncates_at_255(
        self, mock_hass, combined_entry, coordinators
    ):
        """native_value is truncated to MAX_STATE_LENGTH chars."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        # Add many offline devices with long names
        for i in range(30):
            eid = f"binary_sensor.long_{i:03d}"
            coordinators[0]._device_states[eid] = DeviceState(
                entity_id=eid, is_offline=True
            )
            mock_hass.states.async_set(
                eid,
                STATE_UNAVAILABLE,
                {"friendly_name": f"Very Long Device Name Number {i:03d}"},
            )
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        value = sensor.native_value
        assert len(value) <= MAX_STATE_LENGTH
        assert value.endswith("...")

    def test_native_value_suppressed_excluded(
        self, mock_hass, combined_entry, coordinators
    ):
        """Suppressed devices not shown in offline list."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a2"].is_suppressed = True
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.native_value == "None"

    def test_extra_state_attributes(self, mock_hass, combined_entry, coordinators):
        """extra_state_attributes has entities list and count."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        attrs = sensor.extra_state_attributes
        assert "entities" in attrs
        assert "count" in attrs
        assert attrs["count"] == 1
        assert "binary_sensor.a2" in attrs["entities"]


# ---------------------------------------------------------------------------
# CombinedLowBatterySensor
# ---------------------------------------------------------------------------


class TestCombinedLowBatterySensor:
    """Tests for CombinedLowBatterySensor."""

    def _sensor(self, hass, entry, coordinators):
        return CombinedLowBatterySensor(
            hass,
            entry,
            "Combined",
            "combined",
            coordinators,
            [c.entry.entry_id for c in coordinators],
        )

    def test_native_value_none_when_no_low_battery(
        self, mock_hass, combined_entry, coordinators
    ):
        """Returns 'None' when no devices have low battery."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.native_value == "None"

    def test_native_value_shows_battery_level(
        self, mock_hass, combined_entry, coordinators
    ):
        """Low-battery devices shown with percentage."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].is_degraded = True
        coordinators[0]._device_states["binary_sensor.a1"].battery_level = 12
        mock_hass.states.async_set(
            "binary_sensor.a1", STATE_ON, {"friendly_name": "Sensor A1"}
        )
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert "Sensor A1 (12%)" in sensor.native_value

    def test_extra_state_attributes(self, mock_hass, combined_entry, coordinators):
        """extra_state_attributes has devices map and count."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].is_degraded = True
        coordinators[0]._device_states["binary_sensor.a1"].battery_level = 8
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        attrs = sensor.extra_state_attributes
        assert attrs["count"] == 1
        assert attrs["devices"]["binary_sensor.a1"] == "8%"


# ---------------------------------------------------------------------------
# CombinedLowBatteryCountSensor
# ---------------------------------------------------------------------------


class TestCombinedLowBatteryCountSensor:
    """Tests for CombinedLowBatteryCountSensor."""

    def _sensor(self, hass, entry, coordinators):
        return CombinedLowBatteryCountSensor(
            hass,
            entry,
            "Combined",
            "combined",
            coordinators,
            [c.entry.entry_id for c in coordinators],
        )

    def test_native_value_zero_when_none(self, mock_hass, combined_entry, coordinators):
        """Returns 0 when no low-battery devices."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.native_value == 0

    def test_native_value_counts_across_groups(
        self, mock_hass, combined_entry, coordinators
    ):
        """Counts low-battery devices across all groups."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].is_degraded = True
        coordinators[0]._device_states["binary_sensor.a1"].battery_level = 5
        coordinators[1]._device_states["binary_sensor.b1"].is_degraded = True
        coordinators[1]._device_states["binary_sensor.b1"].battery_level = 10
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.native_value == 2

    def test_native_value_excludes_suppressed(
        self, mock_hass, combined_entry, coordinators
    ):
        """Suppressed low-battery devices not counted."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].is_degraded = True
        coordinators[0]._device_states["binary_sensor.a1"].battery_level = 5
        coordinators[0]._device_states["binary_sensor.a1"].is_suppressed = True
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.native_value == 0

    def test_unique_id(self, mock_hass, combined_entry, coordinators):
        """unique_id uses entry_id + suffix."""
        mock_hass.data[DOMAIN] = {}
        sensor = self._sensor(mock_hass, combined_entry, coordinators)
        assert sensor.unique_id == "combined_1_combined_low_battery_count"


# ---------------------------------------------------------------------------
# async_setup_entry: coordinator list
# ---------------------------------------------------------------------------


class TestAsyncSetupEntry:
    """Tests for the combined_sensor.async_setup_entry function."""

    async def test_setup_builds_coordinator_list_from_hass_data(
        self, mock_hass: HomeAssistant, group_entry_a, group_entry_b, combined_entry
    ):
        """Only coordinators present in hass.data[DOMAIN] are used."""
        coord_a = MagicMock(spec=EntityAvailabilityCoordinator)
        coord_a.entry = group_entry_a
        # entry_b not loaded — should be skipped
        mock_hass.data[DOMAIN] = {"entry_a": coord_a}

        group_entry_a.add_to_hass(mock_hass)
        group_entry_b.add_to_hass(mock_hass)
        combined_entry.add_to_hass(mock_hass)

        added = []

        def _fake_add(entities):
            added.extend(entities)

        await async_setup_entry(mock_hass, combined_entry, _fake_add)
        # Sensors were registered
        assert len(added) > 0

    async def test_setup_registers_six_sensors(
        self, mock_hass: HomeAssistant, group_entry_a, group_entry_b, combined_entry
    ):
        """async_setup_entry registers exactly 6 sensors (including recently_offline/recovered)."""
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
        assert len(added) == 6
        types = {type(s) for s in added}
        assert CombinedRecentlyOfflineSensor in types
        assert CombinedRecentlyRecoveredSensor in types


# ---------------------------------------------------------------------------
# CombinedRecentlyOfflineSensor
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


def _make_recently_offline_sensor(hass, entry, coordinators):
    return CombinedRecentlyOfflineSensor(
        hass,
        entry,
        "Combined",
        "combined",
        coordinators,
        [c.entry.entry_id for c in coordinators],
    )


def _make_recently_recovered_sensor(hass, entry, coordinators):
    return CombinedRecentlyRecoveredSensor(
        hass,
        entry,
        "Combined",
        "combined",
        coordinators,
        [c.entry.entry_id for c in coordinators],
    )


class TestCombinedRecentlyOfflineSensor:
    """Tests for CombinedRecentlyOfflineSensor."""

    def test_native_value_none_when_no_recent_offline(
        self, mock_hass, combined_entry, coordinators
    ):
        """Returns 'None' when no devices went offline recently."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = _make_recently_offline_sensor(mock_hass, combined_entry, coordinators)
        assert sensor.native_value == "None"

    def test_native_value_within_window(self, mock_hass, combined_entry, coordinators):
        """Returns friendly name of device that went offline within window."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a2"].recently_offline_at = (
            _NOW - timedelta(minutes=2)
        )
        mock_hass.states.async_set(
            "binary_sensor.a2", STATE_UNAVAILABLE, {"friendly_name": "Sensor A2"}
        )
        sensor = _make_recently_offline_sensor(mock_hass, combined_entry, coordinators)
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            value = sensor.native_value
        assert "Sensor A2" in value

    def test_native_value_outside_window_excluded(
        self, mock_hass, combined_entry, coordinators
    ):
        """Device that went offline beyond the window is excluded."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        # Default recovery_window_minutes = 5; set offline 10 min ago
        coordinators[0]._device_states["binary_sensor.a2"].recently_offline_at = (
            _NOW - timedelta(minutes=10)
        )
        sensor = _make_recently_offline_sensor(mock_hass, combined_entry, coordinators)
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            value = sensor.native_value
        assert value == "None"

    def test_aggregates_across_groups(self, mock_hass, combined_entry, coordinators):
        """Devices from multiple groups all appear in native_value."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a2"].recently_offline_at = (
            _NOW - timedelta(minutes=1)
        )
        coordinators[1]._device_states["binary_sensor.b1"].is_offline = True
        coordinators[1]._device_states["binary_sensor.b1"].recently_offline_at = (
            _NOW - timedelta(minutes=1)
        )
        mock_hass.states.async_set(
            "binary_sensor.a2", STATE_UNAVAILABLE, {"friendly_name": "Sensor A2"}
        )
        mock_hass.states.async_set(
            "binary_sensor.b1", STATE_UNAVAILABLE, {"friendly_name": "Sensor B1"}
        )
        sensor = _make_recently_offline_sensor(mock_hass, combined_entry, coordinators)
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            attrs = sensor.extra_state_attributes
        assert attrs["count"] == 2
        assert "binary_sensor.a2" in attrs["entities"]
        assert "binary_sensor.b1" in attrs["entities"]

    def test_suppressed_excluded(self, mock_hass, combined_entry, coordinators):
        """Suppressed devices not included even if recently_offline_at is set."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        d = coordinators[0]._device_states["binary_sensor.a2"]
        d.recently_offline_at = _NOW - timedelta(minutes=1)
        d.is_suppressed = True
        sensor = _make_recently_offline_sensor(mock_hass, combined_entry, coordinators)
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            value = sensor.native_value
        assert value == "None"

    def test_truncates_long_value(self, mock_hass, combined_entry, coordinators):
        """native_value truncated to MAX_STATE_LENGTH."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        for i in range(30):
            eid = f"binary_sensor.recent_{i:03d}"
            coordinators[0]._device_states[eid] = DeviceState(
                entity_id=eid,
                is_offline=True,
                recently_offline_at=_NOW - timedelta(minutes=1),
            )
            mock_hass.states.async_set(
                eid,
                STATE_UNAVAILABLE,
                {"friendly_name": f"Very Long Device Name Number {i:03d}"},
            )
        sensor = _make_recently_offline_sensor(mock_hass, combined_entry, coordinators)
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            value = sensor.native_value
        assert len(value) <= MAX_STATE_LENGTH
        assert value.endswith("...")

    def test_uses_per_group_window(self, mock_hass, combined_entry, coordinators):
        """Each group's own recovery_window_minutes is used for filtering."""
        from custom_components.entity_availability.const import CONF_RECOVERY_WINDOW

        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        # Give coord_a a 5-min window and coord_b a 1-min window via entry data
        coordinators[0].entry = MockConfigEntry(
            version=1,
            domain=DOMAIN,
            title="Group A",
            data={**coordinators[0].entry.data, CONF_RECOVERY_WINDOW: 5},
            entry_id="entry_a",
        )
        coordinators[1].entry = MockConfigEntry(
            version=1,
            domain=DOMAIN,
            title="Group B",
            data={**coordinators[1].entry.data, CONF_RECOVERY_WINDOW: 1},
            entry_id="entry_b",
        )
        # a2 went offline 3 min ago — within coord_a 5-min window
        coordinators[0]._device_states["binary_sensor.a2"].recently_offline_at = (
            _NOW - timedelta(minutes=3)
        )
        # b1 went offline 3 min ago — outside coord_b 1-min window
        coordinators[1]._device_states["binary_sensor.b1"].is_offline = True
        coordinators[1]._device_states["binary_sensor.b1"].recently_offline_at = (
            _NOW - timedelta(minutes=3)
        )
        mock_hass.states.async_set(
            "binary_sensor.a2", STATE_UNAVAILABLE, {"friendly_name": "Sensor A2"}
        )
        sensor = _make_recently_offline_sensor(mock_hass, combined_entry, coordinators)
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            attrs = sensor.extra_state_attributes
        assert attrs["count"] == 1
        assert "binary_sensor.a2" in attrs["entities"]
        assert "binary_sensor.b1" not in attrs["entities"]

    def test_unique_id(self, mock_hass, combined_entry, coordinators):
        mock_hass.data[DOMAIN] = {}
        sensor = _make_recently_offline_sensor(mock_hass, combined_entry, coordinators)
        assert sensor.unique_id == "combined_1_combined_recently_offline"


# ---------------------------------------------------------------------------
# CombinedRecentlyRecoveredSensor
# ---------------------------------------------------------------------------


class TestCombinedRecentlyRecoveredSensor:
    """Tests for CombinedRecentlyRecoveredSensor."""

    def test_native_value_none_when_no_recent_recovery(
        self, mock_hass, combined_entry, coordinators
    ):
        """Returns 'None' when no devices recovered recently."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        sensor = _make_recently_recovered_sensor(
            mock_hass, combined_entry, coordinators
        )
        assert sensor.native_value == "None"

    def test_native_value_within_window(self, mock_hass, combined_entry, coordinators):
        """Returns friendly name of device that recovered within window."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].last_recovery = (
            _NOW - timedelta(minutes=2)
        )
        mock_hass.states.async_set(
            "binary_sensor.a1", STATE_ON, {"friendly_name": "Sensor A1"}
        )
        sensor = _make_recently_recovered_sensor(
            mock_hass, combined_entry, coordinators
        )
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            value = sensor.native_value
        assert "Sensor A1" in value

    def test_offline_devices_excluded(self, mock_hass, combined_entry, coordinators):
        """Devices still offline are not included even if last_recovery is set."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        d = coordinators[0]._device_states["binary_sensor.a2"]
        # a2 is_offline=True; set last_recovery anyway
        d.last_recovery = _NOW - timedelta(minutes=1)
        sensor = _make_recently_recovered_sensor(
            mock_hass, combined_entry, coordinators
        )
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            value = sensor.native_value
        assert value == "None"

    def test_aggregates_across_groups(self, mock_hass, combined_entry, coordinators):
        """Recovered devices from multiple groups all appear."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0]._device_states["binary_sensor.a1"].last_recovery = (
            _NOW - timedelta(minutes=1)
        )
        coordinators[1]._device_states["binary_sensor.b1"].last_recovery = (
            _NOW - timedelta(minutes=1)
        )
        mock_hass.states.async_set(
            "binary_sensor.a1", STATE_ON, {"friendly_name": "Sensor A1"}
        )
        mock_hass.states.async_set(
            "binary_sensor.b1", STATE_ON, {"friendly_name": "Sensor B1"}
        )
        sensor = _make_recently_recovered_sensor(
            mock_hass, combined_entry, coordinators
        )
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            attrs = sensor.extra_state_attributes
        assert attrs["count"] == 2
        assert "binary_sensor.a1" in attrs["entities"]
        assert "binary_sensor.b1" in attrs["entities"]

    def test_uses_per_group_window(self, mock_hass, combined_entry, coordinators):
        """Each group's own recovery_window_minutes is used."""
        from custom_components.entity_availability.const import CONF_RECOVERY_WINDOW

        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }
        coordinators[0].entry = MockConfigEntry(
            version=1,
            domain=DOMAIN,
            title="Group A",
            data={**coordinators[0].entry.data, CONF_RECOVERY_WINDOW: 5},
            entry_id="entry_a",
        )
        coordinators[1].entry = MockConfigEntry(
            version=1,
            domain=DOMAIN,
            title="Group B",
            data={**coordinators[1].entry.data, CONF_RECOVERY_WINDOW: 1},
            entry_id="entry_b",
        )
        # a1 recovered 3 min ago — within coord_a 5-min window
        coordinators[0]._device_states["binary_sensor.a1"].last_recovery = (
            _NOW - timedelta(minutes=3)
        )
        # b1 recovered 3 min ago — outside coord_b 1-min window
        coordinators[1]._device_states["binary_sensor.b1"].last_recovery = (
            _NOW - timedelta(minutes=3)
        )
        mock_hass.states.async_set(
            "binary_sensor.a1", STATE_ON, {"friendly_name": "Sensor A1"}
        )
        sensor = _make_recently_recovered_sensor(
            mock_hass, combined_entry, coordinators
        )
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            attrs = sensor.extra_state_attributes
        assert attrs["count"] == 1
        assert "binary_sensor.a1" in attrs["entities"]
        assert "binary_sensor.b1" not in attrs["entities"]

    def test_unique_id(self, mock_hass, combined_entry, coordinators):
        mock_hass.data[DOMAIN] = {}
        sensor = _make_recently_recovered_sensor(
            mock_hass, combined_entry, coordinators
        )
        assert sensor.unique_id == "combined_1_combined_recently_recovered"


# ---------------------------------------------------------------------------
# CombinedSensorBase — coordinator callback fires async_write_ha_state (line 108)
# ---------------------------------------------------------------------------


class TestCombinedSensorBaseCallbackFires:
    """Test that the coordinator update callback invokes async_write_ha_state."""

    async def test_coordinator_callback_calls_write_ha_state(
        self, mock_hass, combined_entry, coordinators
    ):
        """The _on_coordinator_update callback invokes async_write_ha_state."""
        mock_hass.data[DOMAIN] = {
            "entry_a": coordinators[0],
            "entry_b": coordinators[1],
        }

        sensor = CombinedGroupSensor(
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

        assert len(captured_callbacks) >= 1
        captured_callbacks[0]()

        assert len(write_state_calls) == 1


# ---------------------------------------------------------------------------
# group_slug sanitization — forward slash and special chars (GH issue)
# ---------------------------------------------------------------------------


async def test_combined_sensor_setup_entry_slug_sanitizes_slash_in_group_name(
    mock_hass: HomeAssistant, group_entry_a, group_entry_b
) -> None:
    """Combined group names with slashes produce valid entity IDs (no slash in slug)."""
    combined_entry = _make_combined_entry(
        "slash_combined_entry",
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


async def test_combined_sensor_slug_fallback(
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

    assert len(added) > 0
    for entity in added:
        assert "abcdef12" in entity.entity_id
