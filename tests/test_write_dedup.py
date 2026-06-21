"""Tests for write-dedup mixin and integration into sensor platforms."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.binary_sensor import AnyOfflineBinarySensor
from custom_components.entity_availability.combined_binary_sensor import (
    CombinedGroupAnyOfflineBinarySensor,
)
from custom_components.entity_availability.combined_sensor import (
    CombinedGroupSensor,
    CombinedLowBatteryCountSensor,
    CombinedLowBatterySensor,
    CombinedOfflineEntitiesSensor,
    CombinedRecentlyOfflineSensor,
    CombinedRecentlyRecoveredSensor,
)
from custom_components.entity_availability.const import DOMAIN
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)
from custom_components.entity_availability.models import DeviceState
from custom_components.entity_availability.sensor import (
    AvailabilitySensor,
    DegradedDevicesSensor,
    GroupSummarySensor,
    LowBatteryCountSensor,
    OfflineCountSensor,
    OfflineDevicesSensor,
    RecentlyOfflineSensor,
    RecentlyRecoveredSensor,
)
from custom_components.entity_availability.write_dedup import (
    DedupCoordinatorBinarySensor,
    DedupCoordinatorSensor,
    WriteDedupMixin,
    _UNSET,
)


@pytest.fixture
def mock_coordinator(mock_hass: HomeAssistant, mock_config_entry):
    """Coordinator with one offline device."""
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
        }
        yield coord


def test_mixin_first_call_writes() -> None:
    """First call always returns True because cache is _UNSET."""

    class _S(WriteDedupMixin):
        def __init__(self) -> None:
            self.value = 1
            self.extra_state_attributes: dict = {"a": 1}

        def _ea_current_value(self):
            return self.value

    s = _S()
    assert s._ea_should_write() is True
    # cache populated
    assert s._ea_should_write() is False


def test_mixin_value_change_writes() -> None:
    """Different value triggers write; same value does not."""

    class _S(WriteDedupMixin):
        def __init__(self) -> None:
            self.value = 1
            self.extra_state_attributes: dict = {}

        def _ea_current_value(self):
            return self.value

    s = _S()
    assert s._ea_should_write() is True
    assert s._ea_should_write() is False
    s.value = 2
    assert s._ea_should_write() is True
    assert s._ea_should_write() is False


def test_mixin_attrs_change_writes() -> None:
    """Same value but changed attrs still triggers write."""

    class _S(WriteDedupMixin):
        def __init__(self) -> None:
            self.value = 1
            self.extra_state_attributes: dict = {"a": 1}

        def _ea_current_value(self):
            return self.value

    s = _S()
    assert s._ea_should_write() is True
    assert s._ea_should_write() is False
    s.extra_state_attributes = {"a": 2}
    assert s._ea_should_write() is True


def test_mixin_no_attrs_property() -> None:
    """Subclass without extra_state_attributes uses None and still dedups."""

    class _S(WriteDedupMixin):
        def __init__(self) -> None:
            self.value = 1

        def _ea_current_value(self):
            return self.value

    s = _S()
    assert s._ea_should_write() is True
    assert s._ea_should_write() is False


def test_mixin_default_value_raises() -> None:
    """Base mixin's _ea_current_value is abstract."""
    with pytest.raises(NotImplementedError):
        WriteDedupMixin()._ea_current_value()


def test_dedup_coordinator_sensor_skips_unchanged(mock_coordinator) -> None:
    """`OfflineCountSensor._handle_coordinator_update` writes once, then dedups."""
    sensor = OfflineCountSensor(mock_coordinator, "Test Group", "test_group", "eid")
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_coordinator_update()
        sensor._handle_coordinator_update()
        sensor._handle_coordinator_update()
    assert write.call_count == 1


def test_dedup_coordinator_sensor_writes_on_change(mock_coordinator) -> None:
    """Bringing a new device offline triggers a fresh write."""
    sensor = OfflineCountSensor(mock_coordinator, "Test Group", "test_group", "eid")
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_coordinator_update()
        # Take device_a offline; native_value goes 1 -> 2
        mock_coordinator.device_states["binary_sensor.device_a"].is_offline = True
        mock_coordinator.device_states[
            "binary_sensor.device_a"
        ].offline_since = datetime.now(timezone.utc)
        sensor._handle_coordinator_update()
    assert write.call_count == 2


def test_dedup_coordinator_binary_sensor_skips_unchanged(mock_coordinator) -> None:
    """Binary sensor variant dedups too."""
    sensor = AnyOfflineBinarySensor(mock_coordinator, "Test Group", "test_group", "eid")
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_coordinator_update()
        sensor._handle_coordinator_update()
    assert write.call_count == 1


def test_dedup_coordinator_binary_sensor_writes_on_change(mock_coordinator) -> None:
    """Flipping every device online flips is_on False -> True path."""
    sensor = AnyOfflineBinarySensor(mock_coordinator, "Test Group", "test_group", "eid")
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_coordinator_update()
        # Bring device_b back online
        mock_coordinator.device_states["binary_sensor.device_b"].is_offline = False
        sensor._handle_coordinator_update()
    assert write.call_count == 2


def test_dedup_subclasses_provide_current_value(mock_coordinator) -> None:
    """Sensor subclass returns native_value, binary subclass returns is_on."""
    s = OfflineCountSensor(mock_coordinator, "Test Group", "test_group", "eid")
    assert isinstance(s, DedupCoordinatorSensor)
    assert s._ea_current_value() == s.native_value

    b = AnyOfflineBinarySensor(mock_coordinator, "Test Group", "test_group", "eid")
    assert isinstance(b, DedupCoordinatorBinarySensor)
    assert b._ea_current_value() == b.is_on


@pytest.mark.asyncio
async def test_combined_sensor_dedup(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Combined sensor's added-to-hass listener dedups using the mixin."""
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(mock_hass, mock_config_entry)
        coord._device_states = {
            "binary_sensor.device_a": DeviceState(
                entity_id="binary_sensor.device_a",
                is_offline=True,
                offline_since=datetime.now(timezone.utc),
            ),
        }

    mock_hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coord

    combined_entry = MockConfigEntry(
        version=1, domain=DOMAIN, title="Combined", data={}, entry_id="combined_eid"
    )
    sensor = CombinedGroupSensor(
        mock_hass,
        combined_entry,
        "Combined",
        "combined",
        [coord],
        [mock_config_entry.entry_id],
    )

    captured: list = []

    def _fake_add_listener(cb):
        captured.append(cb)

        def _unsub():
            return None

        return _unsub

    coord.async_add_listener = MagicMock(side_effect=_fake_add_listener)
    with patch.object(sensor, "async_write_ha_state") as write:
        await sensor.async_added_to_hass()
        assert captured, "listener registered"
        callback_fn = captured[0]
        callback_fn()  # first invocation -> write
        callback_fn()  # unchanged -> skip
        # Mutate value
        coord.device_states["binary_sensor.device_a"].is_offline = False
        callback_fn()  # changed -> write
    assert write.call_count == 2

    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_combined_binary_sensor_dedup(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Combined binary sensor's added-to-hass listener dedups."""
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(mock_hass, mock_config_entry)
        coord._device_states = {
            "binary_sensor.device_a": DeviceState(
                entity_id="binary_sensor.device_a",
                is_offline=True,
                offline_since=datetime.now(timezone.utc),
            ),
        }

    mock_hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coord

    combined_entry = MockConfigEntry(
        version=1, domain=DOMAIN, title="Combined", data={}, entry_id="combined_eid_b"
    )
    sensor = CombinedGroupAnyOfflineBinarySensor(
        mock_hass,
        combined_entry,
        "Combined",
        "combined",
        [coord],
        [mock_config_entry.entry_id],
    )

    captured: list = []

    def _fake_add_listener(cb):
        captured.append(cb)
        return lambda: None

    coord.async_add_listener = MagicMock(side_effect=_fake_add_listener)
    with patch.object(sensor, "async_write_ha_state") as write:
        await sensor.async_added_to_hass()
        callback_fn = captured[0]
        callback_fn()
        callback_fn()
        coord.device_states["binary_sensor.device_a"].is_offline = False
        callback_fn()
    assert write.call_count == 2

    await sensor.async_will_remove_from_hass()


# ----------------------------------------------------------------------------
# Edge cases: None transitions, attrs {} ↔ None, nested mutables, multi-revert
# ----------------------------------------------------------------------------


def test_mixin_none_to_value_writes() -> None:
    """Value flipping None -> int triggers a write after the first publish."""

    class _S(WriteDedupMixin):
        def __init__(self) -> None:
            self.value: object = None
            self.extra_state_attributes: dict = {}

        def _ea_current_value(self):
            return self.value

    s = _S()
    assert s._ea_should_write() is True  # first publish (None)
    assert s._ea_should_write() is False  # still None, dedup
    s.value = 42
    assert s._ea_should_write() is True  # None -> 42
    s.value = None
    assert s._ea_should_write() is True  # 42 -> None


def test_mixin_attrs_empty_to_none_and_back() -> None:
    """Attrs flipping {} -> None and None -> {} both trigger writes."""

    class _S(WriteDedupMixin):
        def __init__(self) -> None:
            self.value = 1
            self.extra_state_attributes: dict | None = {}

        def _ea_current_value(self):
            return self.value

    s = _S()
    assert s._ea_should_write() is True
    s.extra_state_attributes = None
    assert s._ea_should_write() is True
    s.extra_state_attributes = {}
    assert s._ea_should_write() is True


def test_mixin_nested_mutable_attrs_dedup() -> None:
    """Identical-content nested lists/dicts dedup even when the wrapper is fresh."""

    class _S(WriteDedupMixin):
        def __init__(self) -> None:
            self.value = 1
            self.extra_state_attributes = {"entities": ["a", "b"], "count": 2}

        def _ea_current_value(self):
            return self.value

    s = _S()
    assert s._ea_should_write() is True
    # Replace with a new dict carrying the same content — dict == is value-based.
    s.extra_state_attributes = {"entities": ["a", "b"], "count": 2}
    assert s._ea_should_write() is False
    s.extra_state_attributes = {"entities": ["a", "b", "c"], "count": 3}
    assert s._ea_should_write() is True


def test_mixin_multi_step_revert_writes_each_change() -> None:
    """Reverting to the original value still counts as a change vs the cached pair."""

    class _S(WriteDedupMixin):
        def __init__(self) -> None:
            self.value = 1
            self.extra_state_attributes: dict = {}

        def _ea_current_value(self):
            return self.value

    s = _S()
    assert s._ea_should_write() is True  # initial
    s.value = 2
    assert s._ea_should_write() is True
    s.value = 1  # revert
    assert s._ea_should_write() is True
    assert s._ea_should_write() is False  # stable


def test_mixin_reset_cache_forces_next_write() -> None:
    """`_ea_reset_cache` makes the next `_ea_should_write` write through."""

    class _S(WriteDedupMixin):
        def __init__(self) -> None:
            self.value = 1
            self.extra_state_attributes: dict = {}

        def _ea_current_value(self):
            return self.value

    s = _S()
    assert s._ea_should_write() is True
    assert s._ea_should_write() is False
    s._ea_reset_cache()
    assert s._ea_last_value is _UNSET
    assert s._ea_last_attrs is _UNSET
    assert s._ea_last_available is _UNSET
    assert s._ea_should_write() is True  # cache cleared -> write


def test_mixin_available_flip_writes() -> None:
    """`available` flipping True -> False triggers a write even with same value."""

    class _S(WriteDedupMixin):
        def __init__(self) -> None:
            self.value = 1
            self.extra_state_attributes: dict = {}
            self.available = True

        def _ea_current_value(self):
            return self.value

    s = _S()
    assert s._ea_should_write() is True
    assert s._ea_should_write() is False
    s.available = False
    assert s._ea_should_write() is True
    assert s._ea_should_write() is False
    s.available = True
    assert s._ea_should_write() is True


# ----------------------------------------------------------------------------
# Per-subclass dedup smoke tests
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sensor_cls",
    [
        OfflineCountSensor,
        OfflineDevicesSensor,
        DegradedDevicesSensor,
        LowBatteryCountSensor,
        GroupSummarySensor,
        RecentlyOfflineSensor,
        RecentlyRecoveredSensor,
    ],
)
def test_each_dedup_sensor_subclass_skips_unchanged(
    mock_coordinator, mock_hass, sensor_cls
) -> None:
    """Every concrete `DedupCoordinatorSensor` subclass dedups identical ticks."""
    sensor = sensor_cls(mock_coordinator, "Test Group", "test_group", "eid")
    sensor.hass = mock_hass
    assert isinstance(sensor, DedupCoordinatorSensor)
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_coordinator_update()
        sensor._handle_coordinator_update()
        sensor._handle_coordinator_update()
    assert write.call_count == 1


def test_availability_sensor_dedup_skips_unchanged(mock_coordinator, mock_hass) -> None:
    """`AvailabilitySensor` (constructor signature differs) also dedups."""
    sensor = AvailabilitySensor(
        mock_coordinator, "Test Group", "test_group", "today", "eid"
    )
    sensor.hass = mock_hass
    assert isinstance(sensor, DedupCoordinatorSensor)
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_coordinator_update()
        sensor._handle_coordinator_update()
    assert write.call_count == 1


def test_availability_sensor_dedup_catches_sub_percent_drift(
    mock_coordinator, mock_hass
) -> None:
    """v5.5 F-EA-1: sub-percent rolling-window drift must not defeat dedup.

    Without quantization, ``native_value`` and the ``per_device`` attribute
    floats drift on every coordinator tick (the in-progress 5-min bucket grows
    by ``SCAN_INTERVAL`` seconds per tick), defeating ``WriteDedupMixin`` and
    producing one recorder row per tick (~2880/day per ``_today`` sensor).

    Quantizing both to whole percent collapses the per-tick noise so dedup
    wins on virtually every tick.
    """
    sensor = AvailabilitySensor(
        mock_coordinator, "Test Group", "test_group", "today", "eid"
    )
    sensor.hass = mock_hass
    storage = mock_coordinator.availability_storage
    tick = {"n": 0}

    def _drifting(_eid, _window, _now):
        return 99.5 + (tick["n"] * 30 / 86400)

    with (
        patch.object(storage, "get_availability", side_effect=_drifting),
        patch.object(sensor, "async_write_ha_state") as write,
    ):
        for i in range(1000):
            tick["n"] = i
            sensor._handle_coordinator_update()

    assert write.call_count < 10, (
        f"sub-percent drift defeated dedup: {write.call_count}/1000 writes"
    )


def test_dedup_sensor_first_write_after_construction(mock_coordinator) -> None:
    """A freshly constructed concrete sensor writes on its first refresh."""
    sensor = OfflineCountSensor(mock_coordinator, "Test Group", "test_group", "eid")
    # Cache must start unset.
    assert sensor._ea_last_value is _UNSET
    assert sensor._ea_last_attrs is _UNSET
    assert sensor._ea_last_available is _UNSET
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_coordinator_update()
    assert write.call_count == 1


def test_dedup_binary_sensor_first_write_after_construction(mock_coordinator) -> None:
    """A freshly constructed binary sensor writes on its first refresh."""
    sensor = AnyOfflineBinarySensor(mock_coordinator, "Test Group", "test_group", "eid")
    assert sensor._ea_last_value is _UNSET
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_coordinator_update()
    assert write.call_count == 1


@pytest.mark.asyncio
async def test_dedup_sensor_remove_resets_cache(mock_coordinator) -> None:
    """`async_will_remove_from_hass` clears the cache for the per-group sensor."""
    sensor = OfflineCountSensor(mock_coordinator, "Test Group", "test_group", "eid")
    sensor._ea_last_value = 5
    sensor._ea_last_attrs = {"a": 1}
    sensor._ea_last_available = False
    with patch.object(
        CoordinatorEntity, "async_will_remove_from_hass", new_callable=AsyncMock
    ):
        await sensor.async_will_remove_from_hass()
    assert sensor._ea_last_value is _UNSET
    assert sensor._ea_last_attrs is _UNSET
    assert sensor._ea_last_available is _UNSET


@pytest.mark.asyncio
async def test_dedup_binary_sensor_remove_resets_cache(mock_coordinator) -> None:
    """`async_will_remove_from_hass` clears the cache for the binary sensor."""
    sensor = AnyOfflineBinarySensor(mock_coordinator, "Test Group", "test_group", "eid")
    sensor._ea_last_value = True
    sensor._ea_last_attrs = {"x": 1}
    sensor._ea_last_available = True
    with patch.object(
        CoordinatorEntity, "async_will_remove_from_hass", new_callable=AsyncMock
    ):
        await sensor.async_will_remove_from_hass()
    assert sensor._ea_last_value is _UNSET
    assert sensor._ea_last_attrs is _UNSET
    assert sensor._ea_last_available is _UNSET


def test_dedup_sensor_available_flip_writes(mock_coordinator) -> None:
    """Coordinator failure flipping `available` triggers a write even when value sticks."""
    sensor = OfflineCountSensor(mock_coordinator, "Test Group", "test_group", "eid")
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_coordinator_update()
        # Same value/attrs -> dedup.
        sensor._handle_coordinator_update()
        # Coordinator's update fails: `available` flips True -> False but
        # device_states (and so native_value/extra_state_attributes) stays the same.
        mock_coordinator.last_update_success = False
        sensor._handle_coordinator_update()
    assert write.call_count == 2


# ----------------------------------------------------------------------------
# Combined sensor coverage: every concrete subclass exercises the listener path
# ----------------------------------------------------------------------------


def _build_combined_coord(mock_hass, mock_config_entry):
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(mock_hass, mock_config_entry)
    coord._device_states = {
        "binary_sensor.device_a": DeviceState(
            entity_id="binary_sensor.device_a",
            is_offline=True,
            offline_since=datetime.now(timezone.utc),
            recently_offline_at=datetime.now(timezone.utc),
        ),
    }
    mock_hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coord
    return coord


@pytest.mark.parametrize(
    "sensor_cls",
    [
        CombinedGroupSensor,
        CombinedOfflineEntitiesSensor,
        CombinedLowBatterySensor,
        CombinedLowBatteryCountSensor,
        CombinedRecentlyOfflineSensor,
        CombinedRecentlyRecoveredSensor,
    ],
)
@pytest.mark.asyncio
async def test_each_combined_sensor_subclass_dedups(
    mock_hass: HomeAssistant, mock_config_entry, sensor_cls
) -> None:
    """Every combined sensor subclass dedups identical ticks via the mixin."""
    coord = _build_combined_coord(mock_hass, mock_config_entry)
    combined_entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Combined",
        data={},
        entry_id=f"combined_{sensor_cls.__name__}",
    )
    sensor = sensor_cls(
        mock_hass,
        combined_entry,
        "Combined",
        "combined",
        [coord],
        [mock_config_entry.entry_id],
    )

    captured: list = []

    def _fake_add_listener(cb):
        captured.append(cb)
        return lambda: None

    coord.async_add_listener = MagicMock(side_effect=_fake_add_listener)
    with patch.object(sensor, "async_write_ha_state") as write:
        await sensor.async_added_to_hass()
        callback_fn = captured[0]
        callback_fn()  # first
        callback_fn()  # unchanged
        callback_fn()  # unchanged
    assert write.call_count == 1

    await sensor.async_will_remove_from_hass()
    # Cache reset on remove → next listener invocation publishes again.
    assert sensor._ea_last_value is _UNSET
    assert sensor._ea_last_attrs is _UNSET
    assert sensor._ea_last_available is _UNSET


@pytest.mark.asyncio
async def test_combined_sensor_listener_uses_self_after_revert(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Listener captures `self`, so reverting state then back still publishes each step."""
    coord = _build_combined_coord(mock_hass, mock_config_entry)
    combined_entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Combined",
        data={},
        entry_id="combined_revert_eid",
    )
    sensor = CombinedGroupSensor(
        mock_hass,
        combined_entry,
        "Combined",
        "combined",
        [coord],
        [mock_config_entry.entry_id],
    )

    captured: list = []

    def _fake_add_listener(cb):
        captured.append(cb)
        return lambda: None

    coord.async_add_listener = MagicMock(side_effect=_fake_add_listener)
    with patch.object(sensor, "async_write_ha_state") as write:
        await sensor.async_added_to_hass()
        cb = captured[0]
        cb()  # initial: 1 write
        coord.device_states["binary_sensor.device_a"].is_offline = False
        cb()  # change: 2nd write
        coord.device_states["binary_sensor.device_a"].is_offline = True
        cb()  # revert: 3rd write
        cb()  # stable: skipped
    assert write.call_count == 3

    await sensor.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_combined_binary_sensor_remove_resets_cache(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Combined binary sensor's removal also clears the dedup cache."""
    coord = _build_combined_coord(mock_hass, mock_config_entry)
    combined_entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Combined",
        data={},
        entry_id="combined_b_remove_eid",
    )
    sensor = CombinedGroupAnyOfflineBinarySensor(
        mock_hass,
        combined_entry,
        "Combined",
        "combined",
        [coord],
        [mock_config_entry.entry_id],
    )

    captured: list = []

    def _fake_add_listener(cb):
        captured.append(cb)
        return lambda: None

    coord.async_add_listener = MagicMock(side_effect=_fake_add_listener)
    await sensor.async_added_to_hass()
    captured[0]()
    assert sensor._ea_last_value is not _UNSET
    await sensor.async_will_remove_from_hass()
    assert sensor._ea_last_value is _UNSET
    assert sensor._ea_last_attrs is _UNSET
    assert sensor._ea_last_available is _UNSET
