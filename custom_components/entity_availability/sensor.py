"""Sensor platform for Entity Availability."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import (
    CONF_AVAILABILITY_WINDOWS,
    CONF_BATTERY_ENTITY_MAP,
    CONF_BATTERY_THRESHOLD,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    CONF_USE_DEVICE_NAMES,
    DEFAULT_AVAILABILITY_WINDOWS,
    DEFAULT_BATTERY_THRESHOLD,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
    NO_AREA_SENTINEL,
)
from .coordinator import EntityAvailabilityCoordinator
from .helpers import resolve_area_name, resolve_display_name
from .write_dedup import DedupCoordinatorSensor

_LOGGER = logging.getLogger(__name__)

MAX_STATE_LENGTH = 255


def _resolve_display_name(
    hass: HomeAssistant, entity_id: str, use_device_names: bool = False
) -> str:
    """Return a display name for entity_id."""
    return resolve_display_name(hass, entity_id, use_device_names)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Entity Availability sensors."""
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_COMBINED:
        from .combined_sensor import async_setup_entry as _combined

        await _combined(hass, entry, async_add_entities)
        return

    coordinator: EntityAvailabilityCoordinator = hass.data[DOMAIN][entry.entry_id]
    group_name = entry.data[CONF_GROUP_NAME]
    group_slug = re.sub(r"[^a-z0-9_]+", "_", group_name.lower()).strip("_")
    if not group_slug:
        group_slug = entry.entry_id[:8].lower()
    windows = entry.data.get(CONF_AVAILABILITY_WINDOWS, DEFAULT_AVAILABILITY_WINDOWS)

    _LOGGER.debug(
        "Setting up sensors for group '%s': windows=%s, entities=%d",
        group_name,
        windows,
        len(coordinator.monitored_entities),
    )

    entities: list[Entity] = [
        OfflineCountSensor(coordinator, group_name, group_slug, entry.entry_id),
        OfflineDevicesSensor(coordinator, group_name, group_slug, entry.entry_id),
        GroupSummarySensor(coordinator, group_name, group_slug, entry.entry_id),
        RecentlyOfflineSensor(coordinator, group_name, group_slug, entry.entry_id),
        RecentlyRecoveredSensor(coordinator, group_name, group_slug, entry.entry_id),
        MTBFSensor(coordinator, group_name, group_slug, entry.entry_id),
        MTTRSensor(coordinator, group_name, group_slug, entry.entry_id),
    ]

    entities.extend(
        [
            AffectedAreasCountSensor(
                coordinator, group_name, group_slug, entry.entry_id
            ),
            AffectedAreasSensor(coordinator, group_name, group_slug, entry.entry_id),
            AffectedAreasRecentlyOfflineSensor(
                coordinator, group_name, group_slug, entry.entry_id
            ),
            AffectedAreasRecentlyRecoveredSensor(
                coordinator, group_name, group_slug, entry.entry_id
            ),
        ]
    )

    battery_threshold = entry.data.get(
        CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD
    )
    if battery_threshold > 0:
        _LOGGER.debug(
            "Battery monitoring enabled for group '%s': threshold=%d%%",
            group_name,
            battery_threshold,
        )
        entities.append(
            DegradedDevicesSensor(coordinator, group_name, group_slug, entry.entry_id)
        )
        entities.append(
            LowBatteryCountSensor(coordinator, group_name, group_slug, entry.entry_id)
        )

    for window in windows:
        entities.append(
            AvailabilitySensor(
                coordinator, group_name, group_slug, window, entry.entry_id
            )
        )

    async_add_entities(entities)


def _device_info(entry_id: str, group_slug: str, group_name: str) -> DeviceInfo:
    """Return shared device info for all sensors of a group."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=f"Entity Availability - {group_name}",
        manufacturer="Entity Availability",
        entry_type=DeviceEntryType.SERVICE,
    )


class OfflineCountSensor(DedupCoordinatorSensor):
    """Sensor showing count of offline devices in the group."""

    _attr_icon = "mdi:alert-circle"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_offline_count"
        self.entity_id = f"sensor.entity_availability_{group_slug}_offline_count"
        self._attr_translation_key = "offline_count"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> int:
        """Return count of offline devices."""
        return sum(
            1
            for d in self.coordinator.device_states.values()
            if d.is_offline and not d.is_suppressed
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return per-device offline status."""
        return {
            d.entity_id: {
                "offline": d.is_offline,
                "since": d.offline_since.isoformat() if d.offline_since else None,
                "last_recovery": d.last_recovery.isoformat()
                if d.last_recovery
                else None,
                "last_downtime_seconds": d.last_downtime_seconds,
            }
            for d in self.coordinator.device_states.values()
            if d.is_offline and not d.is_suppressed
        }


class OfflineDevicesSensor(DedupCoordinatorSensor):
    """Sensor showing comma-separated list of offline entity names."""

    _attr_icon = "mdi:devices"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_offline_entities"
        self.entity_id = f"sensor.entity_availability_{group_slug}_offline_entities"
        self._attr_translation_key = "offline_entities"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> str:
        """Return comma-separated offline device names."""
        offline = [
            _resolve_display_name(
                self.hass,
                d.entity_id,
                self.coordinator.entry.data.get(CONF_USE_DEVICE_NAMES, False),
            )
            for d in self.coordinator.device_states.values()
            if d.is_offline and not d.is_suppressed
        ]
        if not offline:
            return "None"
        result = ", ".join(offline)
        if len(result) > MAX_STATE_LENGTH - 3:
            result = result[: MAX_STATE_LENGTH - 3] + "..."
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return full list in attributes (no truncation)."""
        offline = [
            d.entity_id
            for d in self.coordinator.device_states.values()
            if d.is_offline and not d.is_suppressed
        ]
        return {"entities": offline, "count": len(offline)}


class DegradedDevicesSensor(DedupCoordinatorSensor):
    """Sensor showing devices with low battery."""

    _attr_icon = "mdi:battery-alert"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_low_battery"
        self.entity_id = f"sensor.entity_availability_{group_slug}_low_battery"
        self._attr_translation_key = "low_battery"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> str:
        """Return comma-separated list of low battery device names."""
        low_bat = [
            self._format_device(d)
            for d in self.coordinator.device_states.values()
            if d.is_degraded and not d.is_suppressed and d.battery_level is not None
        ]
        if not low_bat:
            return "None"
        result = ", ".join(low_bat)
        if len(result) > MAX_STATE_LENGTH - 3:
            result = result[: MAX_STATE_LENGTH - 3] + "..."
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return per-device battery details."""
        devices: dict[str, Any] = {}
        for d in self.coordinator.device_states.values():
            if d.is_degraded and not d.is_suppressed and d.battery_level is not None:
                devices[d.entity_id] = f"{d.battery_level}%"
        return {"devices": devices, "count": len(devices)}

    def _format_device(self, device) -> str:
        """Format device name with battery level."""
        name = _resolve_display_name(
            self.hass,
            device.entity_id,
            self.coordinator.entry.data.get(CONF_USE_DEVICE_NAMES, False),
        )
        return f"{name} ({device.battery_level}%)"


class LowBatteryCountSensor(DedupCoordinatorSensor):
    """Sensor showing count of devices with low battery."""

    _attr_icon = "mdi:battery-alert-variant-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_low_battery_count"
        self.entity_id = f"sensor.entity_availability_{group_slug}_low_battery_count"
        self._attr_translation_key = "low_battery_count"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> int:
        """Return count of devices with low battery."""
        return sum(
            1
            for d in self.coordinator.device_states.values()
            if d.is_degraded and not d.is_suppressed and d.battery_level is not None
        )


class AvailabilitySensor(DedupCoordinatorSensor):
    """Sensor showing group availability % for a time window."""

    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        window: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._window = window
        self._attr_unique_id = f"{entry_id}_availability_{window}"
        self.entity_id = (
            f"sensor.entity_availability_{group_slug}_availability_{window}"
        )
        self._attr_translation_key = f"availability_{window}"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> float | None:
        """Return group availability % (1-decimal precision)."""
        now = datetime.now(timezone.utc)
        storage = self.coordinator.availability_storage

        values: list[float] = []
        for entity_id in self.coordinator.monitored_entities:
            # Skip suppressed devices
            device = self.coordinator.device_states.get(entity_id)
            if device and device.is_suppressed:
                continue
            avail = storage.get_availability(entity_id, self._window, now)
            if avail is not None:
                values.append(avail)

        if not values:
            return None

        return round(sum(values) / len(values), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return per-device availability breakdown (1-decimal precision).

        Per-device values are rounded to 1 decimal to match ``native_value``.
        Previously they were unrounded floats that drifted by ~0.035% on every
        coordinator tick, defeating ``WriteDedupMixin`` even when the
        group-level rounded value was stable (v5.5 audit finding F-EA-1).
        """
        now = datetime.now(timezone.utc)
        storage = self.coordinator.availability_storage
        breakdown: dict[str, float | None] = {}
        for entity_id in self.coordinator.monitored_entities:
            device = self.coordinator.device_states.get(entity_id)
            if device and device.is_suppressed:
                continue
            avail = storage.get_availability(entity_id, self._window, now)
            breakdown[entity_id] = round(avail, 1) if avail is not None else None
        return {"per_device": breakdown}


class MTBFSensor(DedupCoordinatorSensor):
    """Sensor showing group mean MTBF (hours) with per-device breakdown.

    MTBF (mean time between failures) answers "how often do devices break".
    Companion :class:`MTTRSensor` answers "how long is each outage".
    """

    _attr_icon = "mdi:chart-timeline-variant"
    _attr_native_unit_of_measurement = "h"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # No state_class: MTBF changes only on offline/recovery events, not on a
    # fixed cadence — see GroupSummarySensor.
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_mtbf"
        self.entity_id = f"sensor.entity_availability_{group_slug}_mtbf"
        self._attr_translation_key = "mtbf"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> float | None:
        """Return group mean MTBF in hours (avg over entities with data)."""
        now = datetime.now(timezone.utc)
        values = [
            stats["mtbf_hours"]
            for entity_id in self.coordinator.monitored_entities
            if not (
                (d := self.coordinator.device_states.get(entity_id)) and d.is_suppressed
            )
            and (stats := self.coordinator.reliability_stats(entity_id, now))[
                "mtbf_hours"
            ]
            is not None
        ]
        if not values:
            return None
        return round(sum(values) / len(values), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return per-device MTBF breakdown and total event count."""
        now = datetime.now(timezone.utc)
        per_device: dict[str, dict[str, Any]] = {}
        total_events = 0
        for entity_id in self.coordinator.monitored_entities:
            device = self.coordinator.device_states.get(entity_id)
            if device and device.is_suppressed:
                continue
            stats = self.coordinator.reliability_stats(entity_id, now)
            per_device[entity_id] = {
                "mtbf_hours": stats["mtbf_hours"],
                "offline_events": stats["offline_events"],
            }
            total_events += stats["offline_events"]
        return {
            "total_offline_events": total_events,
            "per_device": per_device,
        }


class MTTRSensor(DedupCoordinatorSensor):
    """Sensor showing group mean MTTR (minutes) — average outage length."""

    _attr_icon = "mdi:timer-alert-outline"
    _attr_native_unit_of_measurement = "min"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # No state_class: changes only on recovery events — see GroupSummarySensor.
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_mttr"
        self.entity_id = f"sensor.entity_availability_{group_slug}_mttr"
        self._attr_translation_key = "mttr"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> float | None:
        """Return group mean MTTR in minutes (avg over entities with data)."""
        now = datetime.now(timezone.utc)
        values = [
            stats["mttr_minutes"]
            for entity_id in self.coordinator.monitored_entities
            if not (
                (d := self.coordinator.device_states.get(entity_id)) and d.is_suppressed
            )
            and (stats := self.coordinator.reliability_stats(entity_id, now))[
                "mttr_minutes"
            ]
            is not None
        ]
        if not values:
            return None
        return round(sum(values) / len(values), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return per-device MTTR breakdown and total event count."""
        now = datetime.now(timezone.utc)
        per_device: dict[str, dict[str, Any]] = {}
        total_events = 0
        for entity_id in self.coordinator.monitored_entities:
            device = self.coordinator.device_states.get(entity_id)
            if device and device.is_suppressed:
                continue
            stats = self.coordinator.reliability_stats(entity_id, now)
            per_device[entity_id] = {
                "mttr_minutes": stats["mttr_minutes"],
                "offline_events": stats["offline_events"],
            }
            total_events += stats["offline_events"]
        return {
            "total_offline_events": total_events,
            "per_device": per_device,
        }


class GroupSummarySensor(DedupCoordinatorSensor):
    """Sensor showing total entity count with detailed breakdown in attributes."""

    _attr_icon = "mdi:format-list-group"
    # No state_class: entity count only changes on group edit, so long-term
    # statistics would be constant rows sampled every 5 min. Stays a valid state sensor.
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_group_summary"
        self.entity_id = f"sensor.entity_availability_{group_slug}_group_summary"
        self._attr_translation_key = "group_summary"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> int:
        """Return total entity count in the group."""
        return len(self.coordinator.monitored_entities)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed group breakdown."""
        states = self.coordinator.device_states
        total = len(self.coordinator.monitored_entities)
        offline = sum(
            1
            for eid in self.coordinator.monitored_entities
            if states.get(eid)
            and states[eid].is_offline
            and not states[eid].is_suppressed
        )
        suppressed = sum(
            1
            for eid in self.coordinator.monitored_entities
            if states.get(eid) and states[eid].is_suppressed
        )
        online = total - offline - suppressed

        battery_map = self.coordinator.entry.data.get(CONF_BATTERY_ENTITY_MAP, {})
        if battery_map:
            battery_powered = sum(1 for v in battery_map.values() if v)
        else:
            battery_powered = sum(
                1
                for eid in self.coordinator.monitored_entities
                if states.get(eid)
                and states[eid].battery_level is not None
                and not states[eid].is_suppressed
            )

        low_battery_entities = [
            eid
            for eid in self.coordinator.monitored_entities
            if states.get(eid)
            and states[eid].is_degraded
            and not states[eid].is_suppressed
            and states[eid].battery_level is not None
        ]
        low_battery = len(low_battery_entities)

        use_device_names = self.coordinator.entry.data.get(CONF_USE_DEVICE_NAMES, False)
        return {
            "total_entities": total,
            "online": online,
            "offline": offline,
            "suppressed": suppressed,
            "battery_powered": battery_powered,
            "low_battery": low_battery,
            "low_battery_entities": low_battery_entities,
            "entities": list(self.coordinator.monitored_entities),
            "display_names": {
                eid: _resolve_display_name(self.hass, eid, use_device_names)
                for eid in self.coordinator.monitored_entities
            },
            "battery_levels": {
                eid: d.battery_level
                for eid, d in states.items()
                if d.battery_level is not None
            },
            "suppressed_until": {
                eid: d.suppress_until.isoformat()
                for eid, d in states.items()
                if d.is_suppressed and d.suppress_until is not None
            },
            "stale_entities": [
                eid for eid, d in states.items() if d.is_stale and not d.is_suppressed
            ],
            "offline_since": {
                eid: d.offline_since.isoformat()
                for eid, d in states.items()
                if d.offline_since is not None
            },
        }


class RecentlyOfflineSensor(DedupCoordinatorSensor):
    """Sensor showing entities that went offline within the recovery window."""

    _attr_icon = "mdi:lan-disconnect"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_recently_offline"
        self.entity_id = f"sensor.entity_availability_{group_slug}_recently_offline"
        self._attr_translation_key = "recently_offline"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)
        self._cached_devices: list = []

    def _window_seconds(self) -> float:
        return self.coordinator.recovery_window_minutes * 60

    def _refresh_cache(self) -> list:
        """Compute and return offline devices whose offline event is within the recovery window."""
        now = datetime.now(timezone.utc)
        cutoff = self._window_seconds()
        self._cached_devices = [
            d
            for d in self.coordinator.device_states.values()
            if d.is_offline
            and not d.is_suppressed
            and d.recently_offline_at is not None
            and (now - d.recently_offline_at).total_seconds() <= cutoff
        ]
        _LOGGER.debug(
            "[%s] RecentlyOfflineSensor cache refreshed: %d device(s) within %ss window",
            self.entity_id,
            len(self._cached_devices),
            cutoff,
        )
        return self._cached_devices

    @property
    def native_value(self) -> str:
        """Return comma-separated friendly names of recently offline entities."""
        devices = self._refresh_cache()
        if not devices:
            return "None"
        use_device_names = self.coordinator.entry.data.get(CONF_USE_DEVICE_NAMES, False)
        result = ", ".join(
            _resolve_display_name(self.hass, d.entity_id, use_device_names)
            for d in devices
        )
        if len(result) > MAX_STATE_LENGTH - 3:
            result = result[: MAX_STATE_LENGTH - 3] + "..."
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return list of entity IDs that recently went offline."""
        devices = self._cached_devices
        return {
            "entities": [d.entity_id for d in devices],
            "count": len(devices),
            "window_minutes": self.coordinator.recovery_window_minutes,
        }


class RecentlyRecoveredSensor(DedupCoordinatorSensor):
    """Sensor showing entities that recovered from offline within the recovery window."""

    _attr_icon = "mdi:lan-connect"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_recently_recovered"
        self.entity_id = f"sensor.entity_availability_{group_slug}_recently_recovered"
        self._attr_translation_key = "recently_recovered"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)
        self._cached_devices: list = []

    def _window_seconds(self) -> float:
        return self.coordinator.recovery_window_minutes * 60

    def _refresh_cache(self) -> list:
        """Compute and return online devices whose recovery event is within the recovery window."""
        now = datetime.now(timezone.utc)
        cutoff = self._window_seconds()
        self._cached_devices = [
            d
            for d in self.coordinator.device_states.values()
            if not d.is_offline
            and not d.is_suppressed
            and d.last_recovery is not None
            and (now - d.last_recovery).total_seconds() <= cutoff
        ]
        _LOGGER.debug(
            "[%s] RecentlyRecoveredSensor cache refreshed: %d device(s) within %ss window",
            self.entity_id,
            len(self._cached_devices),
            cutoff,
        )
        return self._cached_devices

    @property
    def native_value(self) -> str:
        """Return comma-separated friendly names of recently recovered entities."""
        devices = self._refresh_cache()
        if not devices:
            return "None"
        use_device_names = self.coordinator.entry.data.get(CONF_USE_DEVICE_NAMES, False)
        result = ", ".join(
            _resolve_display_name(self.hass, d.entity_id, use_device_names)
            for d in devices
        )
        if len(result) > MAX_STATE_LENGTH - 3:
            result = result[: MAX_STATE_LENGTH - 3] + "..."
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return list of entity IDs that recently recovered."""
        devices = self._cached_devices
        return {
            "entities": [d.entity_id for d in devices],
            "count": len(devices),
            "window_minutes": self.coordinator.recovery_window_minutes,
        }


class AffectedAreasCountSensor(DedupCoordinatorSensor):
    """Sensor showing count of unique areas with offline entities."""

    _attr_icon = "mdi:home-alert"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_affected_areas_count"
        self.entity_id = f"sensor.entity_availability_{group_slug}_affected_areas_count"
        self._attr_translation_key = "affected_areas_count"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> int:
        areas = {
            resolve_area_name(self.hass, d.entity_id) or NO_AREA_SENTINEL
            for d in self.coordinator.device_states.values()
            if d.is_offline and not d.is_suppressed
        }
        return len(areas)


class AffectedAreasSensor(DedupCoordinatorSensor):
    """Sensor showing sorted comma-separated list of areas with offline entities."""

    _attr_icon = "mdi:home-group"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_affected_areas"
        self.entity_id = f"sensor.entity_availability_{group_slug}_affected_areas"
        self._attr_translation_key = "affected_areas"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)
        self._cached_areas: list[str] = []
        self._cached_unassigned: list[str] = []

    def _refresh_cache(self) -> list[str]:
        areas: set[str] = set()
        unassigned: list[str] = []
        for d in self.coordinator.device_states.values():
            if d.is_offline and not d.is_suppressed:
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
        if len(result) > MAX_STATE_LENGTH - 3:
            result = result[: MAX_STATE_LENGTH - 3] + "..."
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "areas": self._cached_areas,
            "count": len(self._cached_areas),
            "unassigned_entities": self._cached_unassigned,
        }


class AffectedAreasRecentlyOfflineSensor(DedupCoordinatorSensor):
    """Sensor showing areas where an entity went offline within the recovery window."""

    _attr_icon = "mdi:home-clock"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_affected_areas_recently_offline"
        self.entity_id = (
            f"sensor.entity_availability_{group_slug}_affected_areas_recently_offline"
        )
        self._attr_translation_key = "affected_areas_recently_offline"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)
        self._cached_areas: list[str] = []

    def _refresh_cache(self) -> list[str]:
        now = datetime.now(timezone.utc)
        cutoff = self.coordinator.recovery_window_minutes * 60
        areas: set[str] = set()
        for d in self.coordinator.device_states.values():
            if (
                d.is_offline
                and not d.is_suppressed
                and d.recently_offline_at is not None
                and (now - d.recently_offline_at).total_seconds() <= cutoff
            ):
                area = resolve_area_name(self.hass, d.entity_id)
                areas.add(area if area else NO_AREA_SENTINEL)
        self._cached_areas = sorted(areas)
        return self._cached_areas

    @property
    def native_value(self) -> str:
        areas = self._refresh_cache()
        if not areas:
            return "None"
        result = ", ".join(areas)
        if len(result) > MAX_STATE_LENGTH - 3:
            result = result[: MAX_STATE_LENGTH - 3] + "..."
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        areas = self._refresh_cache()
        return {
            "areas": areas,
            "count": len(areas),
            "window_minutes": self.coordinator.recovery_window_minutes,
        }


class AffectedAreasRecentlyRecoveredSensor(DedupCoordinatorSensor):
    """Sensor showing areas where all entities recovered within the recovery window."""

    _attr_icon = "mdi:home-heart"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_affected_areas_recently_recovered"
        self.entity_id = (
            f"sensor.entity_availability_{group_slug}_affected_areas_recently_recovered"
        )
        self._attr_translation_key = "affected_areas_recently_recovered"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)
        self._cached_areas: list[str] = []

    def _refresh_cache(self) -> list[str]:
        now = datetime.now(timezone.utc)
        cutoff = self.coordinator.recovery_window_minutes * 60

        # Build area → list[DeviceState] for non-suppressed devices
        area_devices: dict[str, list] = {}
        for d in self.coordinator.device_states.values():
            if d.is_suppressed:
                continue
            area = resolve_area_name(self.hass, d.entity_id) or NO_AREA_SENTINEL
            area_devices.setdefault(area, []).append(d)

        recovered: list[str] = []
        for area, devices in area_devices.items():
            # All devices in this area must be online
            if any(d.is_offline for d in devices):
                continue
            # At least one must have recovered within the window
            if any(
                d.last_recovery is not None
                and (now - d.last_recovery).total_seconds() <= cutoff
                for d in devices
            ):
                recovered.append(area)

        self._cached_areas = sorted(recovered)
        return self._cached_areas

    @property
    def native_value(self) -> str:
        areas = self._refresh_cache()
        if not areas:
            return "None"
        result = ", ".join(areas)
        if len(result) > MAX_STATE_LENGTH - 3:
            result = result[: MAX_STATE_LENGTH - 3] + "..."
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        areas = self._refresh_cache()
        return {
            "areas": areas,
            "count": len(areas),
            "window_minutes": self.coordinator.recovery_window_minutes,
        }
