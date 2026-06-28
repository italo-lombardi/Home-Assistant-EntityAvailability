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
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_BATTERY_ENTITY_MAP,
    CONF_COMBINED_GROUPS,
    CONF_GROUP_NAME,
    CONF_USE_DEVICE_NAMES,
    DOMAIN,
)
from .coordinator import EntityAvailabilityCoordinator
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

    coordinators: list[EntityAvailabilityCoordinator] = [
        hass.data[DOMAIN][eid] for eid in combined_entry_ids if eid in hass.data[DOMAIN]
    ]

    async_add_entities(
        [
            CombinedGroupSensor(
                hass, entry, group_name, group_slug, coordinators, combined_entry_ids
            ),
            CombinedOfflineCountSensor(
                hass, entry, group_name, group_slug, coordinators, combined_entry_ids
            ),
            CombinedOfflineEntitiesSensor(
                hass, entry, group_name, group_slug, coordinators, combined_entry_ids
            ),
            CombinedLowBatterySensor(
                hass, entry, group_name, group_slug, coordinators, combined_entry_ids
            ),
            CombinedLowBatteryCountSensor(
                hass, entry, group_name, group_slug, coordinators, combined_entry_ids
            ),
            CombinedRecentlyOfflineSensor(
                hass, entry, group_name, group_slug, coordinators, combined_entry_ids
            ),
            CombinedRecentlyRecoveredSensor(
                hass, entry, group_name, group_slug, coordinators, combined_entry_ids
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
    # ponytail: duplicate of _resolve_display_name in sensor.py — module-level
    # circular import prevents sharing (sensor.py imports combined_sensor.py).
    if use_device_names:
        ent_reg = er.async_get(hass)
        entry = ent_reg.async_get(entity_id)
        if entry and entry.device_id:
            dev_reg = dr.async_get(hass)
            device = dev_reg.async_get(entry.device_id)
            if device and (device.name_by_user or device.name):
                return device.name_by_user or device.name
    state = hass.states.get(entity_id)
    if state and state.attributes.get("friendly_name"):
        return state.attributes["friendly_name"]
    return entity_id.split(".")[-1].replace("_", " ").title()


class CombinedSensorBase(WriteDedupMixin, SensorEntity):
    """Base class for combined group sensors — handles coordinator subscriptions."""

    _attr_has_entity_name = True

    def _ea_current_value(self) -> Any:
        return self.native_value

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        group_name: str,
        group_slug: str,
        coordinators: list[EntityAvailabilityCoordinator],
        combined_entry_ids: list[str],
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._group_slug = group_slug
        self._coordinators = coordinators
        self._combined_entry_ids = combined_entry_ids
        self._attr_device_info = _device_info(entry.entry_id, group_name)
        self._unsub_listeners: list[Callable[[], None]] = []

    async def async_added_to_hass(self) -> None:
        """Subscribe to all included coordinators."""
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            if self._ea_should_write():
                self.async_write_ha_state()

        for coordinator in self._coordinators:
            self._unsub_listeners.append(
                coordinator.async_add_listener(_on_coordinator_update)
            )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from all coordinators."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()
        self._ea_reset_cache()
        await super().async_will_remove_from_hass()

    def _active_coordinators(self) -> list[EntityAvailabilityCoordinator]:
        domain_data = self.hass.data.get(DOMAIN, {})
        active = [
            c
            for c in self._coordinators
            if isinstance(
                domain_data.get(c.entry.entry_id), EntityAvailabilityCoordinator
            )
        ]
        if len(active) != len(self._coordinators):
            _LOGGER.debug(
                "[%s] _active_coordinators: %d/%d active",
                self.entity_id,
                len(active),
                len(self._coordinators),
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
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, hass, entry, group_name, group_slug, coordinators, combined_entry_ids
    ):
        super().__init__(
            hass, entry, group_name, group_slug, coordinators, combined_entry_ids
        )
        self._attr_unique_id = f"{entry.entry_id}_combined_summary"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_combined_summary"
        )
        self._attr_name = "Combined Summary"

    @property
    def native_value(self) -> int:
        return sum(
            len(coord.monitored_entities) for coord in self._active_coordinators()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        total = online = offline = stale = low_battery = suppressed = (
            battery_powered
        ) = 0
        offline_entities: list[str] = []
        low_battery_entities: list[str] = []
        groups: dict[str, Any] = {}

        for coord in self._active_coordinators():
            states = coord.device_states
            g_total = len(coord.monitored_entities)
            g_offline = sum(
                1 for d in states.values() if d.is_offline and not d.is_suppressed
            )
            g_suppressed = sum(1 for d in states.values() if d.is_suppressed)
            g_online = g_total - g_offline - g_suppressed
            g_stale = sum(
                1 for d in states.values() if d.is_stale and not d.is_suppressed
            )
            g_low_battery = sum(
                1
                for d in states.values()
                if d.is_degraded and not d.is_suppressed and d.battery_level is not None
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
            total += g_total
            online += g_online
            offline += g_offline
            stale += g_stale
            low_battery += g_low_battery
            suppressed += g_suppressed
            battery_powered += g_battery_powered
            offline_entities += [
                d.entity_id
                for d in states.values()
                if d.is_offline and not d.is_suppressed
            ]
            low_battery_entities += [
                d.entity_id
                for d in states.values()
                if d.is_degraded and not d.is_suppressed and d.battery_level is not None
            ]
            gname = coord.group_name
            groups[coord.entry.entry_id] = {
                "name": gname,
                "total": g_total,
                "online": g_online,
                "offline": g_offline,
                "stale": g_stale,
                "low_battery": g_low_battery,
                "suppressed": g_suppressed,
                "battery_powered": g_battery_powered,
            }

        all_entities = list(
            dict.fromkeys(
                eid
                for coord in self._active_coordinators()
                for eid in coord.monitored_entities
            )
        )
        display_names: dict[str, str] = {}
        for coord in self._active_coordinators():
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
            "battery_powered": battery_powered,
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

    def __init__(
        self, hass, entry, group_name, group_slug, coordinators, combined_entry_ids
    ):
        super().__init__(
            hass, entry, group_name, group_slug, coordinators, combined_entry_ids
        )
        self._attr_unique_id = f"{entry.entry_id}_combined_offline_count"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_offline_count"
        )
        self._attr_name = "Offline Count"

    @property
    def native_value(self) -> int:
        return sum(
            1
            for coord in self._active_coordinators()
            for d in coord.device_states.values()
            if d.is_offline and not d.is_suppressed
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        offline = [
            d.entity_id
            for coord in self._active_coordinators()
            for d in coord.device_states.values()
            if d.is_offline and not d.is_suppressed
        ]
        return {"entities": offline, "count": len(offline)}


class CombinedOfflineEntitiesSensor(CombinedSensorBase):
    """Sensor showing comma-separated list of offline entities across all included groups."""

    _attr_icon = "mdi:devices"

    def __init__(
        self, hass, entry, group_name, group_slug, coordinators, combined_entry_ids
    ):
        super().__init__(
            hass, entry, group_name, group_slug, coordinators, combined_entry_ids
        )
        self._attr_unique_id = f"{entry.entry_id}_combined_offline_entities"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_offline_entities"
        )
        self._attr_name = "Offline Entities"

    @property
    def native_value(self) -> str:
        coords = self._active_coordinators()
        offline = [
            _friendly_name(
                self.hass,
                d.entity_id,
                coord.entry.data.get(CONF_USE_DEVICE_NAMES, False),
            )
            for coord in coords
            for d in coord.device_states.values()
            if d.is_offline and not d.is_suppressed
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
        offline = [
            d.entity_id
            for coord in self._active_coordinators()
            for d in coord.device_states.values()
            if d.is_offline and not d.is_suppressed
        ]
        return {"entities": offline, "count": len(offline)}


class CombinedLowBatterySensor(CombinedSensorBase):
    """Sensor showing comma-separated list of low battery entities across all included groups."""

    _attr_icon = "mdi:battery-alert"

    def __init__(
        self, hass, entry, group_name, group_slug, coordinators, combined_entry_ids
    ):
        super().__init__(
            hass, entry, group_name, group_slug, coordinators, combined_entry_ids
        )
        self._attr_unique_id = f"{entry.entry_id}_combined_low_battery"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_low_battery"
        )
        self._attr_name = "Low Battery"

    @property
    def native_value(self) -> str:
        coords = self._active_coordinators()
        low = [
            f"{_friendly_name(self.hass, d.entity_id, coord.entry.data.get(CONF_USE_DEVICE_NAMES, False))} ({d.battery_level}%)"
            for coord in coords
            for d in coord.device_states.values()
            if d.is_degraded and not d.is_suppressed and d.battery_level is not None
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
        devices: dict[str, Any] = {
            d.entity_id: f"{d.battery_level}%"
            for coord in self._active_coordinators()
            for d in coord.device_states.values()
            if d.is_degraded and not d.is_suppressed and d.battery_level is not None
        }
        return {"devices": devices, "count": len(devices)}


class CombinedLowBatteryCountSensor(CombinedSensorBase):
    """Sensor showing total low battery count across all included groups."""

    _attr_icon = "mdi:battery-alert-variant-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, hass, entry, group_name, group_slug, coordinators, combined_entry_ids
    ):
        super().__init__(
            hass, entry, group_name, group_slug, coordinators, combined_entry_ids
        )
        self._attr_unique_id = f"{entry.entry_id}_combined_low_battery_count"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_low_battery_count"
        )
        self._attr_name = "Low Battery Count"

    @property
    def native_value(self) -> int:
        return sum(
            1
            for coord in self._active_coordinators()
            for d in coord.device_states.values()
            if d.is_degraded and not d.is_suppressed and d.battery_level is not None
        )


class CombinedRecentlyOfflineSensor(CombinedSensorBase):
    """Sensor showing entities that recently went offline across all included groups."""

    _attr_icon = "mdi:lan-disconnect"
    _attr_has_entity_name = True

    def __init__(
        self, hass, entry, group_name, group_slug, coordinators, combined_entry_ids
    ):
        super().__init__(
            hass, entry, group_name, group_slug, coordinators, combined_entry_ids
        )
        self._attr_unique_id = f"{entry.entry_id}_combined_recently_offline"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_recently_offline"
        )
        self._attr_name = "Recently Offline"

    def _matching_devices(self):
        now = datetime.now(timezone.utc)
        result = []
        for coord in self._active_coordinators():
            cutoff = coord.recovery_window_minutes * 60
            result += [
                (coord, d)
                for d in coord.device_states.values()
                if d.is_offline
                and not d.is_suppressed
                and d.recently_offline_at is not None
                and (now - d.recently_offline_at).total_seconds() <= cutoff
            ]
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

    def __init__(
        self, hass, entry, group_name, group_slug, coordinators, combined_entry_ids
    ):
        super().__init__(
            hass, entry, group_name, group_slug, coordinators, combined_entry_ids
        )
        self._attr_unique_id = f"{entry.entry_id}_combined_recently_recovered"
        self.entity_id = (
            f"sensor.entity_availability_combined_{self._group_slug}_recently_recovered"
        )
        self._attr_name = "Recently Recovered"

    def _matching_devices(self):
        now = datetime.now(timezone.utc)
        result = []
        for coord in self._active_coordinators():
            cutoff = coord.recovery_window_minutes * 60
            result += [
                (coord, d)
                for d in coord.device_states.values()
                if not d.is_offline
                and not d.is_suppressed
                and d.last_recovery is not None
                and (now - d.last_recovery).total_seconds() <= cutoff
            ]
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
