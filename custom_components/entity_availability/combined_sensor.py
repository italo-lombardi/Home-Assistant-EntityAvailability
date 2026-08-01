"""Combined group sensor for Entity Availability."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_BATTERY_ENTITY_MAP,
    CONF_BATTERY_THRESHOLD,
    CONF_COMBINED_GROUPS,
    CONF_GROUP_NAME,
    CONF_STALENESS_THRESHOLD,
    CONF_USE_DEVICE_NAMES,
    DOMAIN,
    EVENT_BATTERY_OK,
    EVENT_LOW_BATTERY,
    EVENT_OFFLINE,
    EVENT_RECOVERED,
    NO_AREA_SENTINEL,
)
from .coordinator import EntityAvailabilityCoordinator
from .models import DeviceState
from .helpers import resolve_area_name, resolve_display_name
from .write_dedup import WriteDedupMixin

_LOGGER = logging.getLogger(__name__)

MAX_STATE_LENGTH = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up combined group sensors."""
    group_name = entry.data[CONF_GROUP_NAME]
    group_slug = re.sub(r"[^a-z0-9_]+", "_", group_name.lower()).strip("_")
    if not group_slug:
        group_slug = entry.entry_id[:8].lower()
    combined_entry_ids: list[str] = entry.data.get(CONF_COMBINED_GROUPS, [])

    async_add_entities(
        [
            CombinedGroupSensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
            CombinedOfflineCountSensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
            CombinedOfflineEntitiesSensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
            CombinedLowBatterySensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
            CombinedLowBatteryCountSensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
            CombinedRecentlyOfflineSensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
            CombinedRecentlyRecoveredSensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
            CombinedAffectedAreasCountSensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
            CombinedAffectedAreasSensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
            CombinedAffectedAreasRecentlyOfflineSensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
            CombinedAffectedAreasRecentlyRecoveredSensor(
                hass, entry, group_name, group_slug, combined_entry_ids
            ),
        ]
    )


def _device_info(entry_id: str, group_name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=f"Entity Availability - [Combined] {group_name}",
        manufacturer="Entity Availability",
        entry_type=DeviceEntryType.SERVICE,
    )


def _friendly_name(
    hass: HomeAssistant, entity_id: str, use_device_names: bool = False
) -> str:
    return resolve_display_name(hass, entity_id, use_device_names)


class CombinedSensorBase(WriteDedupMixin, SensorEntity):
    """Base class for combined group sensors — handles coordinator subscriptions."""

    _attr_has_entity_name = True
    _on_coordinator_update: Callable[[], None] | None = None

    def _ea_current_value(self) -> Any:
        return self.native_value

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        group_name: str,
        group_slug: str,
        combined_entry_ids: list[str],
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._group_slug = group_slug
        self._subscribed_entry_ids: set[str] = set()
        self._combined_entry_ids = combined_entry_ids
        self._attr_device_info = _device_info(entry.entry_id, group_name)
        self._unsub_listeners: list[Callable[[], None]] = []

    async def async_added_to_hass(self) -> None:
        """Subscribe to all included coordinators."""
        await super().async_added_to_hass()
        self._on_coordinator_update = self._make_update_callback()
        domain_data = self.hass.data.get(DOMAIN, {})
        for eid in self._combined_entry_ids:
            coord = domain_data.get(eid)
            if isinstance(coord, EntityAvailabilityCoordinator):
                self._subscribe(eid, coord)

    def _make_update_callback(self) -> Callable[[], None]:
        """Return the coordinator update callback. Subclasses may override."""

        @callback
        def _on_coordinator_update() -> None:
            if self._ea_should_write():
                self.async_write_ha_state()

        return _on_coordinator_update

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from all coordinators."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()
        self._ea_reset_cache()
        await super().async_will_remove_from_hass()

    def _subscribe(self, eid: str, coord: EntityAvailabilityCoordinator) -> None:
        """Subscribe to a coordinator and evict eid on unsub (handles reload)."""

        def _unsub_and_evict(raw_unsub: Callable[[], None]) -> Callable[[], None]:
            def _unsub() -> None:
                raw_unsub()
                self._subscribed_entry_ids.discard(eid)

            return _unsub

        raw = coord.async_add_listener(self._on_coordinator_update)
        self._unsub_listeners.append(_unsub_and_evict(raw))
        self._subscribed_entry_ids.add(eid)

    def _active_coordinators(self) -> list[EntityAvailabilityCoordinator]:
        domain_data = self.hass.data.get(DOMAIN, {})
        active = [
            c
            for eid in self._combined_entry_ids
            if isinstance(c := domain_data.get(eid), EntityAvailabilityCoordinator)
        ]
        for coord in active:
            eid = coord.entry.entry_id
            if (
                eid not in self._subscribed_entry_ids
                and self._on_coordinator_update is not None
            ):
                self._subscribe(eid, coord)
                _LOGGER.debug(
                    "[%s] late-subscribed to coordinator %s", self.entity_id, eid
                )
        if len(active) != len(self._combined_entry_ids):
            _LOGGER.debug(
                "[%s] _active_coordinators: %d/%d active",
                self.entity_id,
                len(active),
                len(self._combined_entry_ids),
            )
        return active

    @property
    def available(self) -> bool:
        """Return False when all source coordinators have been unloaded."""
        is_available = len(self._active_coordinators()) > 0
        if not is_available:
            _LOGGER.debug(
                "[%s] unavailable: all source coordinators unloaded",
                self.entity_id,
            )
        return is_available


class CombinedGroupSensor(CombinedSensorBase):
    """Sensor showing total entity count across multiple groups."""

    _attr_icon = "mdi:format-list-group"
    # No state_class: see GroupSummarySensor.

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = f"{entry.entry_id}_combined_summary"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_combined_summary"
        )
        self._attr_translation_key = "combined_summary"
        self._prev_offline_set: frozenset[str] = frozenset()
        self._prev_low_battery_set: frozenset[str] = frozenset()
        self._prev_source_group_map: dict[str, list[str]] = {}

    def _make_update_callback(self) -> Callable[[], None]:
        """Return event-aware coordinator update callback."""

        @callback
        def _on_update() -> None:
            coords = self._active_coordinators()
            # Late-joining coordinators are auto-subscribed by _active_coordinators;
            # if one joins between priming and this first tick its entities appear in
            # current but not prev, which may fire a spurious OFFLINE event.
            current = self._current_offline_set(coords)
            prev = self._prev_offline_set
            self._prev_offline_set = current

            current_lb = self._current_low_battery_set(coords)
            prev_lb = self._prev_low_battery_set
            self._prev_low_battery_set = current_lb

            if current != prev or current_lb != prev_lb:
                device_map = self._build_device_map(coords)
                # Build source_group_map: eid → sorted list of group names.
                # Sorted for deterministic order regardless of coordinator subscription order.
                current_source_group_map: dict[str, list[str]] = {}
                for coord in coords:
                    for eid in coord.device_states:
                        current_source_group_map.setdefault(eid, []).append(
                            coord.group_name
                        )
                for eid, groups in current_source_group_map.items():
                    current_source_group_map[eid] = sorted(groups)
                # Merge prev → current so RECOVERED/BATTERY_OK events for entities
                # whose coordinator was removed between ticks still carry group names.
                # Prune to entities in any active or transitioning set to prevent
                # unbounded growth — entities gone from all sets on this tick are dropped.
                still_relevant = current | prev | current_lb | prev_lb
                source_group_map = {
                    **{
                        k: v
                        for k, v in self._prev_source_group_map.items()
                        if k in still_relevant
                    },
                    **current_source_group_map,
                }
                self._prev_source_group_map = {
                    k: v
                    for k, v in source_group_map.items()
                    if k in (current | current_lb | prev | prev_lb)
                }
                group_name = self._entry.data.get(CONF_GROUP_NAME, "")
                entry_id = self._entry.entry_id

                if current != prev:
                    offline_list = sorted(current)
                    offline_count = len(current)
                    for eid in sorted(current - prev):
                        d = device_map.get(eid)
                        self.hass.bus.async_fire(
                            EVENT_OFFLINE,
                            {
                                "entity_id": eid,
                                "group": group_name,
                                "entry_id": entry_id,
                                "offline_since": d.offline_since.isoformat()
                                if d and d.offline_since
                                else None,
                                "offline_count": offline_count,
                                "offline_entities": offline_list,
                                "source_groups": source_group_map.get(eid, []),
                            },
                        )
                    for eid in sorted(prev - current):
                        # d may be None if the coordinator that owned this entity was
                        # removed between ticks; fire the event with null downtime rather
                        # than silently dropping it.
                        d = device_map.get(eid)
                        self.hass.bus.async_fire(
                            EVENT_RECOVERED,
                            {
                                "entity_id": eid,
                                "group": group_name,
                                "entry_id": entry_id,
                                "downtime_seconds": d.last_downtime_seconds
                                if d
                                else None,
                                "offline_count": offline_count,
                                "offline_entities": offline_list,
                                "source_groups": source_group_map.get(eid, []),
                            },
                        )

                if current_lb != prev_lb:
                    low_battery_list = sorted(current_lb)
                    low_battery_count = len(current_lb)
                    for eid in sorted(current_lb - prev_lb):
                        d = device_map.get(eid)
                        self.hass.bus.async_fire(
                            EVENT_LOW_BATTERY,
                            {
                                "entity_id": eid,
                                "group": group_name,
                                "entry_id": entry_id,
                                "battery_level": d.battery_level if d else None,
                                "low_battery_count": low_battery_count,
                                "low_battery_entities": low_battery_list,
                                "source_groups": source_group_map.get(eid, []),
                            },
                        )
                    for eid in sorted(prev_lb - current_lb):
                        d = device_map.get(eid)
                        self.hass.bus.async_fire(
                            EVENT_BATTERY_OK,
                            {
                                "entity_id": eid,
                                "group": group_name,
                                "entry_id": entry_id,
                                "battery_level": d.battery_level if d else None,
                                "low_battery_count": low_battery_count,
                                "low_battery_entities": low_battery_list,
                                "source_groups": source_group_map.get(eid, []),
                            },
                        )

            if self._ea_should_write():
                self.async_write_ha_state()

        return _on_update

    @staticmethod
    def _build_device_map(
        coords: list[EntityAvailabilityCoordinator],
    ) -> dict[str, Any]:
        """Return first-wins dedup map of entity_id → DeviceState across coordinators."""
        device_map: dict[str, Any] = {}
        for coord in coords:
            for d in coord.device_states.values():
                if d.entity_id not in device_map:
                    device_map[d.entity_id] = d
        return device_map

    def _current_offline_set(
        self, coords: list[EntityAvailabilityCoordinator]
    ) -> frozenset[str]:
        """Return deduplicated set of offline, non-suppressed, non-essential entity IDs."""
        dm = self._build_device_map(coords)
        return frozenset(
            eid
            for eid, d in dm.items()
            if d.is_offline and not d.is_suppressed and not d.is_non_essential
        )

    def _current_low_battery_set(
        self, coords: list[EntityAvailabilityCoordinator]
    ) -> frozenset[str]:
        """Return deduplicated set of low-battery, non-suppressed, non-essential entity IDs."""
        dm = self._build_device_map(coords)
        return frozenset(
            eid
            for eid, d in dm.items()
            if d.is_low_battery and not d.is_suppressed and not d.is_non_essential
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Prime both prev sets after super() subscribes so _active_coordinators()
        # returns the full list — prevents spurious events on first tick.
        coords = self._active_coordinators()
        self._prev_offline_set = self._current_offline_set(coords)
        self._prev_low_battery_set = self._current_low_battery_set(coords)

    @property
    def native_value(self) -> int:
        return sum(
            len(coord.monitored_entities) for coord in self._active_coordinators()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        groups: dict[str, Any] = {}

        active = self._active_coordinators()
        registry = er.async_get(self.hass)
        for coord in active:
            states = coord.device_states
            g_non_essential = sum(1 for d in states.values() if d.is_non_essential)
            g_total = len(coord.monitored_entities) - g_non_essential
            g_offline = sum(
                1
                for d in states.values()
                if d.is_offline and not d.is_suppressed and not d.is_non_essential
            )
            g_suppressed = sum(
                1 for d in states.values() if d.is_suppressed and not d.is_non_essential
            )
            g_non_essential_suppressed = sum(
                1 for d in states.values() if d.is_non_essential and d.is_suppressed
            )
            g_online = g_total - g_offline - g_suppressed
            g_stale = sum(
                1
                for d in states.values()
                if d.is_stale and not d.is_suppressed and not d.is_non_essential
            )
            g_low_battery = sum(
                1
                for d in states.values()
                if d.is_low_battery
                and not d.is_suppressed
                and not d.is_offline
                and not d.is_non_essential
            )
            battery_map = coord.entry.data.get(CONF_BATTERY_ENTITY_MAP, {})
            if battery_map:
                g_battery_powered = sum(1 for v in battery_map.values() if v)
            else:
                g_battery_powered = sum(
                    1
                    for d in states.values()
                    if d.battery_level is not None and not d.is_suppressed
                )
            gname = coord.group_name
            gsummary = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{coord.entry.entry_id}_group_summary"
            )
            if gsummary is None:
                _LOGGER.warning(
                    "Could not find group summary entity for %s", coord.entry.entry_id
                )
            g_non_essential_entities = [
                d.entity_id
                for d in states.values()
                if d.is_non_essential and not d.is_suppressed
            ]
            g_non_essential_offline = sum(
                1
                for d in states.values()
                if d.is_non_essential and not d.is_suppressed and d.is_offline
            )
            g_non_essential_online = sum(
                1
                for d in states.values()
                if d.is_non_essential and not d.is_suppressed and not d.is_offline
            )
            g_non_essential_stale = sum(
                1
                for d in states.values()
                if d.is_non_essential and not d.is_suppressed and d.is_stale
            )
            g_non_essential_low_battery = sum(
                1
                for d in states.values()
                if d.is_non_essential and not d.is_suppressed and d.is_low_battery
            )
            groups[coord.entry.entry_id] = {
                "name": gname,
                "entity_id": gsummary,
                "total": g_total,
                "online": g_online,
                "offline": g_offline,
                "stale": g_stale,
                "low_battery": g_low_battery,
                "suppressed": g_suppressed,
                "non_essential": g_non_essential,
                "non_essential_suppressed": g_non_essential_suppressed,
                "non_essential_entities": g_non_essential_entities,
                "non_essential_offline": g_non_essential_offline,
                "non_essential_online": g_non_essential_online,
                "non_essential_stale": g_non_essential_stale,
                "non_essential_low_battery": g_non_essential_low_battery,
                "battery_enabled": coord.entry.data.get(CONF_BATTERY_THRESHOLD, 0) > 0,
                "staleness_enabled": coord.entry.data.get(CONF_STALENESS_THRESHOLD, 0)
                > 0,
                "battery_powered": g_battery_powered,
            }

        # Build merged state map (first-wins for shared entities) to dedup all counts.
        merged_states: dict[str, DeviceState] = {}
        for coord in active:
            for eid, d in coord.device_states.items():
                if eid not in merged_states:
                    merged_states[eid] = d

        all_entities = list(
            dict.fromkeys(eid for coord in active for eid in coord.monitored_entities)
        )
        offline_entities = [
            d.entity_id
            for d in merged_states.values()
            if d.is_offline and not d.is_suppressed and not d.is_non_essential
        ]
        low_battery_entities = [
            d.entity_id
            for d in merged_states.values()
            if d.is_low_battery
            and not d.is_suppressed
            and not d.is_offline
            and not d.is_non_essential
        ]
        total = len(all_entities)
        offline = len(offline_entities)
        low_battery = len(low_battery_entities)
        non_essential_entities = [
            d.entity_id
            for d in merged_states.values()
            if d.is_non_essential and not d.is_suppressed
        ]
        non_essential_suppressed = sum(
            1 for d in merged_states.values() if d.is_non_essential and d.is_suppressed
        )
        non_essential = len(non_essential_entities) + non_essential_suppressed
        suppressed = sum(
            1
            for d in merged_states.values()
            if d.is_suppressed and not d.is_non_essential
        )
        online = sum(
            1
            for d in merged_states.values()
            if not d.is_offline and not d.is_suppressed and not d.is_non_essential
        )
        stale = sum(
            1
            for d in merged_states.values()
            if d.is_stale and not d.is_suppressed and not d.is_non_essential
        )
        # Dedup battery_powered by collecting device entity_ids (not battery sensor ids).
        # battery_map keys are device entity_ids; battery_level path is already keyed by eid.
        # Using one set handles mixed-config groups without double-counting.
        battery_powered_eids: set[str] = set()
        for coord in active:
            battery_map = coord.entry.data.get(CONF_BATTERY_ENTITY_MAP, {})
            if battery_map:
                battery_powered_eids.update(
                    eid for eid, sid in battery_map.items() if sid
                )
            else:
                for eid, d in coord.device_states.items():
                    if d.battery_level is not None and not d.is_suppressed:
                        battery_powered_eids.add(eid)
        battery_powered = len(battery_powered_eids)
        # Feature-enabled flags: battery/staleness columns must NEVER appear when the
        # feature is disabled (threshold=0), even if counts happen to be non-zero.
        battery_enabled = any(
            coord.entry.data.get(CONF_BATTERY_THRESHOLD, 0) > 0 for coord in active
        )
        staleness_enabled = any(
            coord.entry.data.get(CONF_STALENESS_THRESHOLD, 0) > 0 for coord in active
        )
        display_names: dict[str, str] = {}
        for coord in active:
            use_device_names = coord.entry.data.get(CONF_USE_DEVICE_NAMES, False)
            for eid in coord.monitored_entities:
                if eid not in display_names:
                    display_names[eid] = _friendly_name(
                        self.hass, eid, use_device_names
                    )
        attrs: dict[str, Any] = {
            "total_entities": total,
            "online": online,
            "offline": offline,
            "stale": stale,
            "low_battery": low_battery,
            "suppressed": suppressed,
            "non_essential": non_essential,
            "non_essential_entities": non_essential_entities,
            "battery_powered": battery_powered,
            "battery_enabled": battery_enabled,
            "staleness_enabled": staleness_enabled,
            "groups": groups,
            "entities": all_entities,
            "display_names": display_names,
            "offline_entities": offline_entities,
            "low_battery_entities": low_battery_entities,
        }
        domain_data = self.hass.data.get(DOMAIN, {})
        missing = [
            eid
            for eid in self._combined_entry_ids
            if not isinstance(domain_data.get(eid), EntityAvailabilityCoordinator)
        ]
        if missing:
            _LOGGER.debug(
                "[%s] missing_groups detected: %s",
                self.entity_id,
                missing,
            )
            attrs["missing_groups"] = missing
        return attrs


class CombinedOfflineCountSensor(CombinedSensorBase):
    """Sensor showing count of offline devices across all included groups."""

    _attr_icon = "mdi:alert-circle"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = f"{entry.entry_id}_combined_offline_count"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_offline_count"
        )
        self._attr_translation_key = "offline_count"

    @property
    def native_value(self) -> int:
        return len(
            {
                d.entity_id
                for coord in self._active_coordinators()
                for d in coord.device_states.values()
                if d.is_offline and not d.is_suppressed and not d.is_non_essential
            }
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        offline = list(
            dict.fromkeys(
                d.entity_id
                for coord in self._active_coordinators()
                for d in coord.device_states.values()
                if d.is_offline and not d.is_suppressed and not d.is_non_essential
            )
        )
        return {"entities": offline, "count": len(offline)}


class CombinedOfflineEntitiesSensor(CombinedSensorBase):
    """Sensor showing comma-separated list of offline entities across all included groups."""

    _attr_icon = "mdi:devices"

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = f"{entry.entry_id}_combined_offline_entities"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_offline_entities"
        )
        self._attr_translation_key = "offline_entities"

    @property
    def native_value(self) -> str:
        coords = self._active_coordinators()
        offline = list(
            dict.fromkeys(
                _friendly_name(
                    self.hass,
                    d.entity_id,
                    coord.entry.data.get(CONF_USE_DEVICE_NAMES, False),
                )
                for coord in coords
                for d in coord.device_states.values()
                if d.is_offline and not d.is_suppressed and not d.is_non_essential
            )
        )
        if not offline:
            return "None"
        result = ", ".join(offline)
        return (
            result[: MAX_STATE_LENGTH - 3] + "..."
            if len(result) > MAX_STATE_LENGTH - 3
            else result
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        offline = list(
            dict.fromkeys(
                d.entity_id
                for coord in self._active_coordinators()
                for d in coord.device_states.values()
                if d.is_offline and not d.is_suppressed and not d.is_non_essential
            )
        )
        return {"entities": offline, "count": len(offline)}


class CombinedLowBatterySensor(CombinedSensorBase):
    """Sensor showing comma-separated list of low battery entities across all included groups."""

    _attr_icon = "mdi:battery-alert"

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = f"{entry.entry_id}_combined_low_battery"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_low_battery"
        )
        self._attr_translation_key = "low_battery"

    @property
    def native_value(self) -> str:
        coords = self._active_coordinators()
        low = list(
            dict.fromkeys(
                f"{_friendly_name(self.hass, d.entity_id, coord.entry.data.get(CONF_USE_DEVICE_NAMES, False))} ({d.battery_level}%)"
                for coord in coords
                for d in coord.device_states.values()
                if d.is_low_battery
                and not d.is_suppressed
                and not d.is_offline
                and not d.is_non_essential
            )
        )
        if not low:
            return "None"
        result = ", ".join(low)
        return (
            result[: MAX_STATE_LENGTH - 3] + "..."
            if len(result) > MAX_STATE_LENGTH - 3
            else result
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        devices: dict[str, Any] = {
            d.entity_id: f"{d.battery_level}%"
            for coord in self._active_coordinators()
            for d in coord.device_states.values()
            if d.is_low_battery
            and not d.is_suppressed
            and not d.is_offline
            and not d.is_non_essential
        }
        return {"devices": devices, "count": len(devices)}


class CombinedLowBatteryCountSensor(CombinedSensorBase):
    """Sensor showing total low battery count across all included groups."""

    _attr_icon = "mdi:battery-alert-variant-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = f"{entry.entry_id}_combined_low_battery_count"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_low_battery_count"
        )
        self._attr_translation_key = "low_battery_count"

    @property
    def native_value(self) -> int:
        return len(
            {
                d.entity_id
                for coord in self._active_coordinators()
                for d in coord.device_states.values()
                if d.is_low_battery
                and not d.is_suppressed
                and not d.is_offline
                and not d.is_non_essential
            }
        )


class CombinedRecentlyOfflineSensor(CombinedSensorBase):
    """Sensor showing entities that recently went offline across all included groups."""

    _attr_icon = "mdi:lan-disconnect"
    _attr_has_entity_name = True

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = f"{entry.entry_id}_combined_recently_offline"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_recently_offline"
        )
        self._attr_translation_key = "recently_offline"

    def _matching_devices(self):
        now = datetime.now(timezone.utc)
        seen: set[str] = set()
        result = []
        for coord in self._active_coordinators():
            cutoff = coord.recovery_window_minutes * 60
            for d in coord.device_states.values():
                if (
                    d.is_offline
                    and not d.is_suppressed
                    and not d.is_non_essential
                    and d.recently_offline_at is not None
                    and (now - d.recently_offline_at).total_seconds() <= cutoff
                    and d.entity_id not in seen
                ):
                    seen.add(d.entity_id)
                    result.append((coord, d))
        return result

    @property
    def native_value(self) -> str:
        pairs = self._matching_devices()
        if not pairs:
            return "None"
        result = ", ".join(
            _friendly_name(
                self.hass,
                d.entity_id,
                coord.entry.data.get(CONF_USE_DEVICE_NAMES, False),
            )
            for coord, d in pairs
        )
        return (
            result[: MAX_STATE_LENGTH - 3] + "..."
            if len(result) > MAX_STATE_LENGTH - 3
            else result
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pairs = self._matching_devices()
        return {"entities": [d.entity_id for _, d in pairs], "count": len(pairs)}


class CombinedRecentlyRecoveredSensor(CombinedSensorBase):
    """Sensor showing entities that recently recovered across all included groups."""

    _attr_icon = "mdi:lan-connect"
    _attr_has_entity_name = True

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = f"{entry.entry_id}_combined_recently_recovered"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_recently_recovered"
        )
        self._attr_translation_key = "recently_recovered"

    def _matching_devices(self):
        now = datetime.now(timezone.utc)
        seen: set[str] = set()
        result = []
        for coord in self._active_coordinators():
            cutoff = coord.recovery_window_minutes * 60
            for d in coord.device_states.values():
                if (
                    not d.is_offline
                    and not d.is_suppressed
                    and not d.is_non_essential
                    and d.last_recovery is not None
                    and (now - d.last_recovery).total_seconds() <= cutoff
                    and d.entity_id not in seen
                ):
                    seen.add(d.entity_id)
                    result.append((coord, d))
        return result

    @property
    def native_value(self) -> str:
        pairs = self._matching_devices()
        if not pairs:
            return "None"
        result = ", ".join(
            _friendly_name(
                self.hass,
                d.entity_id,
                coord.entry.data.get(CONF_USE_DEVICE_NAMES, False),
            )
            for coord, d in pairs
        )
        return (
            result[: MAX_STATE_LENGTH - 3] + "..."
            if len(result) > MAX_STATE_LENGTH - 3
            else result
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pairs = self._matching_devices()
        return {"entities": [d.entity_id for _, d in pairs], "count": len(pairs)}


class CombinedAffectedAreasCountSensor(CombinedSensorBase):
    """Sensor showing count of unique areas with offline entities across all groups."""

    _attr_icon = "mdi:home-alert"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = f"{entry.entry_id}_combined_affected_areas_count"
        self.entity_id = f"sensor.entity_availability_combined_{self._group_slug}_affected_areas_count"
        self._attr_translation_key = "affected_areas_count"

    @property
    def native_value(self) -> int:
        areas: set[str] = set()
        for coord in self._active_coordinators():
            for d in coord.device_states.values():
                if d.is_offline and not d.is_suppressed and not d.is_non_essential:
                    area = resolve_area_name(self.hass, d.entity_id)
                    areas.add(area if area else NO_AREA_SENTINEL)
        return len(areas)


class CombinedAffectedAreasSensor(CombinedSensorBase):
    """Sensor showing sorted comma-separated areas with offline entities across all groups."""

    _attr_icon = "mdi:home-group"
    _attr_has_entity_name = True

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = f"{entry.entry_id}_combined_affected_areas"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_affected_areas"
        )
        self._attr_translation_key = "affected_areas"
        self._cached_areas: list[str] = []
        self._cached_unassigned: list[str] = []

    def _refresh_cache(self) -> list[str]:
        areas: set[str] = set()
        unassigned: list[str] = []
        for coord in self._active_coordinators():
            for d in coord.device_states.values():
                if d.is_offline and not d.is_suppressed and not d.is_non_essential:
                    area = resolve_area_name(self.hass, d.entity_id)
                    if area:
                        areas.add(area)
                    else:
                        areas.add(NO_AREA_SENTINEL)
                        unassigned.append(d.entity_id)
        self._cached_areas = sorted(areas)
        self._cached_unassigned = unassigned
        return self._cached_areas

    @property
    def native_value(self) -> str:
        areas = self._refresh_cache()
        if not areas:
            return "None"
        result = ", ".join(areas)
        return (
            result[: MAX_STATE_LENGTH - 3] + "..."
            if len(result) > MAX_STATE_LENGTH - 3
            else result
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "areas": self._cached_areas,
            "count": len(self._cached_areas),
            "unassigned_entities": self._cached_unassigned,
        }


class CombinedAffectedAreasRecentlyOfflineSensor(CombinedSensorBase):
    """Sensor showing areas where an entity went offline within the window across all groups."""

    _attr_icon = "mdi:home-clock"
    _attr_has_entity_name = True

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = (
            f"{entry.entry_id}_combined_affected_areas_recently_offline"
        )
        self.entity_id = f"sensor.entity_availability_combined_{self._group_slug}_affected_areas_recently_offline"
        self._attr_translation_key = "affected_areas_recently_offline"

    def _matching_areas(self) -> list[str]:
        now = datetime.now(timezone.utc)
        areas: set[str] = set()
        for coord in self._active_coordinators():
            cutoff = coord.recovery_window_minutes * 60
            for d in coord.device_states.values():
                if (
                    d.is_offline
                    and not d.is_suppressed
                    and not d.is_non_essential
                    and d.recently_offline_at is not None
                    and (now - d.recently_offline_at).total_seconds() <= cutoff
                ):
                    area = resolve_area_name(self.hass, d.entity_id)
                    areas.add(area if area else NO_AREA_SENTINEL)
        return sorted(areas)

    @property
    def native_value(self) -> str:
        areas = self._matching_areas()
        if not areas:
            return "None"
        result = ", ".join(areas)
        return (
            result[: MAX_STATE_LENGTH - 3] + "..."
            if len(result) > MAX_STATE_LENGTH - 3
            else result
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        areas = self._matching_areas()
        return {"areas": areas, "count": len(areas)}


class CombinedAffectedAreasRecentlyRecoveredSensor(CombinedSensorBase):
    """Sensor showing areas fully recovered within the window across all groups."""

    _attr_icon = "mdi:home-heart"
    _attr_has_entity_name = True

    def __init__(self, hass, entry, group_name, group_slug, combined_entry_ids):
        super().__init__(hass, entry, group_name, group_slug, combined_entry_ids)
        self._attr_unique_id = (
            f"{entry.entry_id}_combined_affected_areas_recently_recovered"
        )
        self.entity_id = f"sensor.entity_availability_combined_{self._group_slug}_affected_areas_recently_recovered"
        self._attr_translation_key = "affected_areas_recently_recovered"

    def _matching_areas(self) -> list[str]:
        now = datetime.now(timezone.utc)

        # Build combined area → [(coord, device)] across all active coordinators
        area_pairs: dict[str, list] = {}
        for coord in self._active_coordinators():
            for d in coord.device_states.values():
                if d.is_suppressed or d.is_non_essential:
                    continue
                area = resolve_area_name(self.hass, d.entity_id) or NO_AREA_SENTINEL
                area_pairs.setdefault(area, []).append((coord, d))

        recovered: list[str] = []
        for area, pairs in area_pairs.items():
            if any(d.is_offline for _, d in pairs):
                continue
            if any(
                d.last_recovery is not None
                and (now - d.last_recovery).total_seconds()
                <= coord_p.recovery_window_minutes * 60
                for coord_p, d in pairs
            ):
                recovered.append(area)

        return sorted(recovered)

    @property
    def native_value(self) -> str:
        areas = self._matching_areas()
        if not areas:
            return "None"
        result = ", ".join(areas)
        return (
            result[: MAX_STATE_LENGTH - 3] + "..."
            if len(result) > MAX_STATE_LENGTH - 3
            else result
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        areas = self._matching_areas()
        return {"areas": areas, "count": len(areas)}
