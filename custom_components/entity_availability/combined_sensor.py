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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_BATTERY_ENTITY_MAP,
    CONF_BATTERY_THRESHOLD,
    CONF_COMBINED_GROUPS,
    CONF_GROUP_NAME,
    CONF_STALENESS_THRESHOLD,
    CONF_STALENESS_USE_LAST_UPDATED,
    CONF_USE_DEVICE_NAMES,
    DOMAIN,
    EVENT_BATTERY_OK,
    EVENT_LOW_BATTERY,
    EVENT_OFFLINE,
    EVENT_RECOVERED,
    NO_AREA_SENTINEL,
)
from .coordinator import EntityAvailabilityCoordinator
from .helpers import (
    collapse_representatives,
    resolve_area_name,
    resolve_display_name,
)
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
        # Per-tick memo of the collapsed merged device map. Keyed by
        # (active entry_ids, summed collapse generations) so the global
        # re-collapse (registry lookups) runs once per tick, not per property read.
        self._dm_cache_key: tuple | None = None
        self._dm_cache: dict[str, Any] | None = None

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

    def _build_device_map(
        self,
        coords: list[EntityAvailabilityCoordinator],
    ) -> dict[str, Any]:
        """Return the representative device map across coordinators (one per row).

        First-wins by entity_id. Entities from collapse-active groups merge by
        device-key to a single representative; entities from collapse-off groups
        each stay their own entry (per-group intent honored). Drives the combined
        total and event downtime lookups. Memoized per tick.
        """
        # Memoize per tick: same active coords + same collapse generations → reuse.
        cache_key = (
            tuple(c.entry.entry_id for c in coords),
            tuple(c.collapse_generation for c in coords),
        )
        if self._dm_cache_key == cache_key and self._dm_cache is not None:
            return self._dm_cache
        merged, rep_of = self._device_map_of(coords)
        reps = dict.fromkeys(rep_of.values())
        collapsed = {eid: merged[eid] for eid in reps}
        self._dm_cache_key = cache_key
        self._dm_cache = collapsed
        return collapsed

    def _device_map_of(
        self, coords: list[EntityAvailabilityCoordinator]
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Return (full merged states by entity_id, entity_id -> device-key map).

        The full map keeps EVERY entity (not just representatives); the second map
        groups entities by physical device. An entity is collapsible if ANY of its
        owning groups has collapse active — if Group A (collapse ON) and Group B
        (collapse OFF) both contain entity E, E collapses in the combined view.
        Shared entities across groups are an edge case; the "any-group" rule is
        intentional so collapse-on groups aren't silently defeated by an unrelated
        group that happens to share an entity. When no group collapses, the map is
        the identity.
        """
        merged: dict[str, Any] = {}
        collapsible: set[str] = set()
        for coord in coords:
            active = coord.collapse_active
            for eid, d in coord.device_states.items():
                if eid not in merged:  # first-wins by entity_id for the state
                    merged[eid] = d
                if active:
                    collapsible.add(eid)
        if not collapsible:
            return merged, {eid: eid for eid in merged}
        return merged, collapse_representatives(self.hass, merged, collapsible)

    @staticmethod
    def _collapsed_match(
        merged: dict[str, Any],
        rep_of: dict[str, str],
        predicate: Callable[[Any], bool],
    ) -> list[str]:
        """Return one entity_id per device with ≥1 member matching predicate."""
        seen_device: set[str] = set()
        result: list[str] = []
        for eid, d in merged.items():
            if not predicate(d):
                continue
            device = rep_of.get(eid, eid)
            if device in seen_device:
                continue
            seen_device.add(device)
            result.append(eid)
        return result

    def _use_device_names_map(
        self, coords: list[EntityAvailabilityCoordinator]
    ) -> dict[str, bool]:
        """Return {entity_id -> use_device_names} first-wins across coordinators."""
        flags: dict[str, bool] = {}
        for coord in coords:
            udn = coord.entry.data.get(CONF_USE_DEVICE_NAMES, False)
            for eid in coord.device_states:
                flags.setdefault(eid, udn)
        return flags


class CombinedGroupSensor(CombinedSensorBase):
    """Sensor showing total entity count across multiple groups."""

    _attr_icon = "mdi:format-list-group"
    # No state_class: see GroupSummarySensor.

    # Per-entity list/dict attrs: large, no historical value.
    _unrecorded_attributes = frozenset(
        {
            "display_names",
            "entities",
            "entities_collapsed",
            "groups",
            "offline_entities",
            "stale_entities",
            "poor_signal_entities",
            "offline_entities_non_essential",
            "stale_entities_non_essential",
            "poor_signal_entities_non_essential",
            "low_battery_entities",
            "low_battery_entities_non_essential",
            "non_essential_entities",
            "battery_levels",
            "signal_levels",
            "signal_units",
            "ok_signal_entities",
            "suppressed_until",
            "offline_since",
            "last_seen",
        }
    )

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

    def _current_offline_set(
        self, coords: list[EntityAvailabilityCoordinator]
    ) -> frozenset[str]:
        """Return deduplicated set of offline, non-suppressed, essential entity IDs."""
        merged, rep_of = self._device_map_of(coords)
        return frozenset(
            self._collapsed_match(
                merged,
                rep_of,
                lambda d: (
                    d.is_offline and not d.is_suppressed and not d.is_non_essential
                ),
            )
        )

    def _current_low_battery_set(
        self, coords: list[EntityAvailabilityCoordinator]
    ) -> frozenset[str]:
        """Return deduplicated set of low-battery, non-suppressed, essential entity IDs."""
        merged, rep_of = self._device_map_of(coords)
        return frozenset(
            self._collapsed_match(
                merged,
                rep_of,
                lambda d: (
                    d.is_low_battery and not d.is_suppressed and not d.is_non_essential
                ),
            )
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
        active = self._active_coordinators()
        if any(coord.collapse_active for coord in active):
            # Collapsed total: unique device representatives (with state) across groups.
            return len(self._build_device_map(active))
        return sum(len(coord.monitored_entities) for coord in active)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        groups: dict[str, Any] = {}

        active = self._active_coordinators()
        registry = er.async_get(self.hass)
        for coord in active:
            # Route per-group counts through the coordinator's collapse-aware helpers
            # so the group-breakdown rows match the group's own (possibly collapsed)
            # sensor values.
            def _g(pred, c=coord) -> list[str]:
                return [d.entity_id for d in c.representative_states_matching(pred)]

            g_collapsed = coord.collapsed_entities()
            g_non_essential = len(_g(lambda d: d.is_non_essential))
            g_total = len(g_collapsed) - g_non_essential
            g_offline_entities = _g(
                lambda d: (
                    d.is_offline and not d.is_suppressed and not d.is_non_essential
                )
            )
            g_offline = len(g_offline_entities)
            g_suppressed = len(_g(lambda d: d.is_suppressed and not d.is_non_essential))
            g_non_essential_suppressed = len(
                _g(lambda d: d.is_non_essential and d.is_suppressed)
            )
            if coord.collapse_active:
                # Explicit online predicate: a collapsed device with a suppressed +
                # an unsuppressed sibling lands in two buckets, so subtraction would
                # undercount online.
                g_online = len(
                    _g(
                        lambda d: (
                            not d.is_offline
                            and not d.is_suppressed
                            and not d.is_non_essential
                        )
                    )
                )
            else:
                g_online = g_total - g_offline - g_suppressed
            g_stale_entities = _g(
                lambda d: d.is_stale and not d.is_suppressed and not d.is_non_essential
            )
            g_stale = len(g_stale_entities)
            g_poor_signal_entities = coord._poor_signal_entity_ids()
            g_poor_signal_entities_ne = coord._poor_signal_ne_entity_ids()
            g_stale_entities_ne = _g(
                lambda d: d.is_stale and not d.is_suppressed and d.is_non_essential
            )
            g_low_battery = len(
                _g(
                    lambda d: (
                        d.is_low_battery
                        and not d.is_suppressed
                        and not d.is_offline
                        and not d.is_non_essential
                    )
                )
            )
            battery_map = coord.entry.data.get(CONF_BATTERY_ENTITY_MAP, {})
            if battery_map:
                g_battery_powered = sum(1 for v in battery_map.values() if v)
            else:
                g_battery_powered = len(
                    _g(lambda d: d.battery_level is not None and not d.is_suppressed)
                )
            gname = coord.group_name
            gsummary = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{coord.entry.entry_id}_group_summary"
            )
            if gsummary is None:
                _LOGGER.warning(
                    "Could not find group summary entity for %s", coord.entry.entry_id
                )
            g_offline_entities_ne = _g(
                lambda d: d.is_non_essential and d.is_offline and not d.is_suppressed
            )
            g_non_essential_offline = len(g_offline_entities_ne)
            g_non_essential_online = len(
                _g(
                    lambda d: (
                        d.is_non_essential and not d.is_suppressed and not d.is_offline
                    )
                )
            )
            g_non_essential_stale = len(
                _g(lambda d: d.is_non_essential and not d.is_suppressed and d.is_stale)
            )
            g_non_essential_low_battery = len(
                _g(
                    lambda d: (
                        d.is_non_essential and not d.is_suppressed and d.is_low_battery
                    )
                )
            )
            groups[coord.entry.entry_id] = {
                "name": gname,
                "entity_id": gsummary,
                "total": g_total,
                "online": g_online,
                "offline": g_offline,
                "offline_entities": g_offline_entities,
                "offline_entities_non_essential": g_offline_entities_ne,
                "stale": g_stale,
                "stale_entities": g_stale_entities,
                "stale_entities_non_essential": g_stale_entities_ne,
                "low_battery": g_low_battery,
                "suppressed": g_suppressed,
                "non_essential": g_non_essential,
                "non_essential_suppressed": g_non_essential_suppressed,
                "non_essential_offline": g_non_essential_offline,
                "non_essential_online": g_non_essential_online,
                "non_essential_stale": g_non_essential_stale,
                "non_essential_low_battery": g_non_essential_low_battery,
                "battery_enabled": coord.entry.data.get(CONF_BATTERY_THRESHOLD, 0) > 0,
                "staleness_enabled": coord.entry.data.get(CONF_STALENESS_THRESHOLD, 0)
                > 0,
                "battery_powered": g_battery_powered,
                "signal_enabled": coord._signal_enabled,
                # Per-group poor_signal is NOT deduped — a shared entity counts in each group's row.
                # The combined total (poor_signal_count below) IS deduped via merged_states.
                "poor_signal": len(g_poor_signal_entities),
                "poor_signal_entities": g_poor_signal_entities,
                "poor_signal_entities_non_essential": g_poor_signal_entities_ne,
                "non_essential_poor_signal": len(g_poor_signal_entities_ne),
            }

        # Full merged states + device-key map: category lists dedupe by DEVICE so a
        # device with siblings in different categories appears in each. total/membership
        # use the representative set (one row per device).
        merged_states, rep_of = self._device_map_of(active)

        def _m(pred) -> list[str]:
            return self._collapsed_match(merged_states, rep_of, pred)

        collapse_on = any(coord.collapse_active for coord in active)
        raw_entities = list(
            dict.fromkeys(eid for coord in active for eid in coord.monitored_entities)
        )
        # entities_collapsed is the row source the card renders: one representative
        # per device when active, else the full deduped membership.
        if collapse_on:
            reps = dict.fromkeys(rep_of.values())
            collapsed_entities = [eid for eid in raw_entities if eid in reps]
        else:
            collapsed_entities = raw_entities
        all_entities = collapsed_entities
        offline_entities = _m(
            lambda d: d.is_offline and not d.is_suppressed and not d.is_non_essential
        )
        stale_entities = _m(
            lambda d: d.is_stale and not d.is_suppressed and not d.is_non_essential
        )
        poor_signal_entities = _m(
            lambda d: (
                d.signal_quality == "poor"
                and not d.is_suppressed
                and not d.is_non_essential
            )
        )
        offline_entities_non_essential = _m(
            lambda d: d.is_non_essential and d.is_offline and not d.is_suppressed
        )
        stale_entities_non_essential = _m(
            lambda d: d.is_non_essential and d.is_stale and not d.is_suppressed
        )
        poor_signal_entities_non_essential = _m(
            lambda d: (
                d.is_non_essential
                and d.signal_quality == "poor"
                and not d.is_suppressed
            )
        )
        low_battery_entities = _m(
            lambda d: (
                d.is_low_battery
                and not d.is_suppressed
                and not d.is_offline
                and not d.is_non_essential
            )
        )
        low_battery_entities_non_essential = _m(
            lambda d: d.is_low_battery and d.is_non_essential and not d.is_suppressed
        )
        total = len(all_entities)
        offline = len(offline_entities)
        low_battery = len(low_battery_entities)
        non_essential_entities = _m(
            lambda d: d.is_non_essential and not d.is_suppressed
        )
        non_essential_suppressed = len(
            _m(lambda d: d.is_non_essential and d.is_suppressed)
        )
        non_essential = len(non_essential_entities) + non_essential_suppressed
        suppressed = len(_m(lambda d: d.is_suppressed and not d.is_non_essential))
        online = len(
            _m(
                lambda d: (
                    not d.is_offline and not d.is_suppressed and not d.is_non_essential
                )
            )
        )
        stale = len(stale_entities)
        non_essential_online = len(
            _m(
                lambda d: (
                    d.is_non_essential and not d.is_offline and not d.is_suppressed
                )
            )
        )
        non_essential_offline = len(offline_entities_non_essential)
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
        signal_enabled = any(coord._signal_enabled for coord in active)
        poor_signal_count = len(poor_signal_entities) if signal_enabled else 0
        display_names: dict[str, str] = {}
        battery_levels: dict[str, Any] = {}
        signal_levels: dict[str, Any] = {}
        signal_units: dict[str, Any] = {}
        suppressed_until: dict[str, Any] = {}
        offline_since: dict[str, Any] = {}
        last_seen: dict[str, Any] = {}
        ok_signal_eids: set[str] = set()
        for coord in active:
            use_device_names = coord.entry.data.get(CONF_USE_DEVICE_NAMES, False)
            use_last_updated = coord.entry.data.get(
                CONF_STALENESS_USE_LAST_UPDATED, False
            )
            for eid, d in coord.device_states.items():
                if eid not in display_names:
                    display_names[eid] = _friendly_name(
                        self.hass, eid, use_device_names
                    )
                if eid not in battery_levels and d.battery_level is not None:
                    battery_levels[eid] = d.battery_level
                if eid not in signal_levels and d.signal_level is not None:
                    signal_levels[eid] = d.signal_level
                if eid not in signal_units and d.signal_unit is not None:
                    signal_units[eid] = d.signal_unit
                if (
                    eid not in suppressed_until
                    and d.is_suppressed
                    and d.suppress_until is not None
                ):
                    suppressed_until[eid] = d.suppress_until.isoformat()
                if eid not in offline_since and d.offline_since is not None:
                    offline_since[eid] = d.offline_since.isoformat()
                if eid not in last_seen:
                    ts = d.last_updated if use_last_updated else d.last_changed
                    if ts is not None:
                        last_seen[eid] = ts.isoformat()
                if (
                    signal_enabled
                    and d.signal_quality == "ok"
                    and not d.is_suppressed
                    and not d.is_non_essential
                ):
                    ok_signal_eids.add(eid)
        status_color = (
            "red"
            if offline > 0
            else "yellow"
            if (low_battery > 0 or stale > 0 or poor_signal_count > 0)
            else "green"
        )
        status = (
            "offline"
            if offline > 0
            else "degraded"
            if status_color == "yellow"
            else "ok"
        )
        attrs: dict[str, Any] = {
            "total_entities": total,
            "online": online,
            "offline": offline,
            "stale": stale,
            "low_battery": low_battery,
            "suppressed": suppressed,
            "non_essential_suppressed": non_essential_suppressed,
            "non_essential": non_essential,
            "non_essential_online": non_essential_online,
            "non_essential_offline": non_essential_offline,
            "non_essential_entities": non_essential_entities,
            "battery_powered": battery_powered,
            "battery_enabled": battery_enabled,
            "staleness_enabled": staleness_enabled,
            "signal_enabled": signal_enabled,
            "poor_signal": poor_signal_count,
            "status": status,
            "status_color": status_color,
            "groups": groups,
            "entities": raw_entities,
            "entities_collapsed": collapsed_entities,
            "display_names": display_names,
            "battery_levels": battery_levels,
            "signal_levels": signal_levels,
            "signal_units": signal_units,
            "suppressed_until": suppressed_until,
            "offline_since": offline_since,
            "last_seen": last_seen,
            "ok_signal_entities": list(ok_signal_eids),
            "offline_entities": offline_entities,
            "stale_entities": stale_entities,
            "poor_signal_entities": poor_signal_entities,
            "offline_entities_non_essential": offline_entities_non_essential,
            "stale_entities_non_essential": stale_entities_non_essential,
            "poor_signal_entities_non_essential": poor_signal_entities_non_essential,
            "low_battery_entities": low_battery_entities,
            "low_battery_entities_non_essential": low_battery_entities_non_essential,
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
        merged, rep_of = self._device_map_of(self._active_coordinators())
        return len(
            self._collapsed_match(
                merged,
                rep_of,
                lambda d: (
                    d.is_offline and not d.is_suppressed and not d.is_non_essential
                ),
            )
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        merged, rep_of = self._device_map_of(self._active_coordinators())
        offline = self._collapsed_match(
            merged,
            rep_of,
            lambda d: d.is_offline and not d.is_suppressed and not d.is_non_essential,
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
        merged, rep_of = self._device_map_of(coords)
        udn = self._use_device_names_map(coords)
        offline = [
            _friendly_name(self.hass, eid, udn.get(eid, False))
            for eid in self._collapsed_match(
                merged,
                rep_of,
                lambda d: (
                    d.is_offline and not d.is_suppressed and not d.is_non_essential
                ),
            )
        ]
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
        merged, rep_of = self._device_map_of(self._active_coordinators())
        offline = self._collapsed_match(
            merged,
            rep_of,
            lambda d: d.is_offline and not d.is_suppressed and not d.is_non_essential,
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
        merged, rep_of = self._device_map_of(coords)
        udn = self._use_device_names_map(coords)
        low = [
            f"{_friendly_name(self.hass, eid, udn.get(eid, False))} ({merged[eid].battery_level}%)"
            for eid in self._collapsed_match(
                merged,
                rep_of,
                lambda d: (
                    d.is_low_battery
                    and not d.is_suppressed
                    and not d.is_offline
                    and not d.is_non_essential
                ),
            )
        ]
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
        merged, rep_of = self._device_map_of(self._active_coordinators())
        devices: dict[str, Any] = {
            eid: f"{merged[eid].battery_level}%"
            for eid in self._collapsed_match(
                merged,
                rep_of,
                lambda d: (
                    d.is_low_battery
                    and not d.is_suppressed
                    and not d.is_offline
                    and not d.is_non_essential
                ),
            )
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
        merged, rep_of = self._device_map_of(self._active_coordinators())
        return len(
            self._collapsed_match(
                merged,
                rep_of,
                lambda d: (
                    d.is_low_battery
                    and not d.is_suppressed
                    and not d.is_offline
                    and not d.is_non_essential
                ),
            )
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
    """Sensor showing count of unique areas with offline entities across all groups.

    NOTE: Affected-areas sensors are intentionally NOT device-collapsed (single or
    combined). Entities of the same device share an area, so collapse can't change
    the affected-area set — it would be a no-op.
    """

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
