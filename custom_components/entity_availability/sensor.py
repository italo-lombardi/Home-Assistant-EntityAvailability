"""Sensor platform for Entity Availability."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
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
    DEFAULT_AVAILABILITY_WINDOWS,
    DEFAULT_BATTERY_THRESHOLD,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
)
from .coordinator import EntityAvailabilityCoordinator
from .write_dedup import DedupCoordinatorSensor

_LOGGER = logging.getLogger(__name__)

MAX_STATE_LENGTH = 255


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
    ]

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
        self._attr_name = "Offline Count"
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
        self._attr_name = "Offline Entities"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> str:
        """Return comma-separated offline device names."""
        offline = [
            self._friendly_name(d.entity_id)
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

    def _friendly_name(self, entity_id: str) -> str:
        """Get friendly name for an entity."""
        state = self.hass.states.get(entity_id)
        if state and state.attributes.get("friendly_name"):
            return state.attributes["friendly_name"]
        return entity_id.split(".")[-1].replace("_", " ").title()


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
        self._attr_name = "Low Battery"
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
        state = self.hass.states.get(device.entity_id)
        if state and state.attributes.get("friendly_name"):
            name = state.attributes["friendly_name"]
        else:
            name = device.entity_id.split(".")[-1].replace("_", " ").title()
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
        self._attr_name = "Low Battery Count"
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
        self._attr_name = f"Availability ({window})"
        self._attr_device_info = _device_info(entry_id, group_slug, group_name)

    @property
    def native_value(self) -> float | None:
        """Return group availability %."""
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
        """Return per-device availability breakdown."""
        now = datetime.now(timezone.utc)
        storage = self.coordinator.availability_storage
        breakdown: dict[str, float | None] = {}
        for entity_id in self.coordinator.monitored_entities:
            device = self.coordinator.device_states.get(entity_id)
            if device and device.is_suppressed:
                continue
            avail = storage.get_availability(entity_id, self._window, now)
            breakdown[entity_id] = avail
        return {"per_device": breakdown}


class GroupSummarySensor(DedupCoordinatorSensor):
    """Sensor showing total entity count with detailed breakdown in attributes."""

    _attr_icon = "mdi:format-list-group"
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
        self._attr_unique_id = f"{entry_id}_group_summary"
        self.entity_id = f"sensor.entity_availability_{group_slug}_group_summary"
        self._attr_name = "Group Summary"
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

        return {
            "total_entities": total,
            "online": online,
            "offline": offline,
            "suppressed": suppressed,
            "battery_powered": battery_powered,
            "low_battery": low_battery,
            "low_battery_entities": low_battery_entities,
            "entities": list(self.coordinator.monitored_entities),
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
        self._attr_name = "Recently Offline"
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
        return self._cached_devices

    def _friendly_name(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        if state and state.attributes.get("friendly_name"):
            return state.attributes["friendly_name"]
        return entity_id.split(".")[-1].replace("_", " ").title()

    @property
    def native_value(self) -> str:
        """Return comma-separated friendly names of recently offline entities."""
        devices = self._refresh_cache()
        if not devices:
            return "None"
        result = ", ".join(self._friendly_name(d.entity_id) for d in devices)
        if len(result) > MAX_STATE_LENGTH - 3:
            result = result[: MAX_STATE_LENGTH - 3] + "..."
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return list of entity IDs that recently went offline."""
        devices = self._refresh_cache()
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
        self._attr_name = "Recently Recovered"
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
        return self._cached_devices

    def _friendly_name(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        if state and state.attributes.get("friendly_name"):
            return state.attributes["friendly_name"]
        return entity_id.split(".")[-1].replace("_", " ").title()

    @property
    def native_value(self) -> str:
        """Return comma-separated friendly names of recently recovered entities."""
        devices = self._refresh_cache()
        if not devices:
            return "None"
        result = ", ".join(self._friendly_name(d.entity_id) for d in devices)
        if len(result) > MAX_STATE_LENGTH - 3:
            result = result[: MAX_STATE_LENGTH - 3] + "..."
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return list of entity IDs that recently recovered."""
        devices = self._refresh_cache()
        return {
            "entities": [d.entity_id for d in devices],
            "count": len(devices),
            "window_minutes": self.coordinator.recovery_window_minutes,
        }
