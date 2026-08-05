"""Binary sensor platform for Entity Availability."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    CONF_SIGNAL_ENABLED,
    DEFAULT_SIGNAL_ENABLED,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
)
from .coordinator import EntityAvailabilityCoordinator
from .write_dedup import DedupCoordinatorBinarySensor


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Entity Availability binary sensors."""
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_COMBINED:
        from .combined_binary_sensor import async_setup_entry as _combined

        await _combined(hass, entry, async_add_entities)
        return

    coordinator: EntityAvailabilityCoordinator = hass.data[DOMAIN][entry.entry_id]
    group_name = entry.data[CONF_GROUP_NAME]
    group_slug = re.sub(r"[^a-z0-9_]+", "_", group_name.lower()).strip("_")
    if not group_slug:
        group_slug = entry.entry_id[:8].lower()

    entities = [
        AnyOfflineBinarySensor(coordinator, group_name, group_slug, entry.entry_id),
        AnyLowBatteryBinarySensor(coordinator, group_name, group_slug, entry.entry_id),
        AnyStaleBinarySensor(coordinator, group_name, group_slug, entry.entry_id),
        NonEssentialAnyOfflineBinarySensor(
            coordinator, group_name, group_slug, entry.entry_id
        ),
        AnyLowBatteryNonEssentialBinarySensor(
            coordinator, group_name, group_slug, entry.entry_id
        ),
    ]
    if entry.data.get(CONF_SIGNAL_ENABLED, DEFAULT_SIGNAL_ENABLED):
        entities.append(
            AnyPoorSignalBinarySensor(
                coordinator, group_name, group_slug, entry.entry_id
            )
        )
        entities.append(
            AnyPoorSignalNonEssentialBinarySensor(
                coordinator, group_name, group_slug, entry.entry_id
            )
        )
    async_add_entities(entities)


class AnyOfflineBinarySensor(DedupCoordinatorBinarySensor):
    """Binary sensor: ON when at least one entity is offline (problem detected)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_any_offline"
        self.entity_id = f"binary_sensor.entity_availability_{group_slug}_any_offline"
        self._attr_translation_key = "any_offline"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"Entity Availability - {group_name}",
            manufacturer="Entity Availability",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._offline_entities: list[str] = []

    def _refresh_offline(self) -> list[str]:
        """Compute and return the current list of offline, non-suppressed, non-essential entity IDs."""
        self._offline_entities = [
            d.entity_id
            for d in self.coordinator.device_states.values()
            if d.is_offline and not d.is_suppressed and not d.is_non_essential
        ]
        return self._offline_entities

    @property
    def is_on(self) -> bool:
        """Return True if any non-suppressed entity is offline."""
        return len(self._refresh_offline()) > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return offline entity details."""
        offline_entities = self._refresh_offline()
        return {
            "offline_entities": offline_entities,
            "offline_count": len(offline_entities),
        }


class NonEssentialAnyOfflineBinarySensor(DedupCoordinatorBinarySensor):
    """Binary sensor: ON when at least one non-essential entity is offline."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert-outline"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_any_offline_non_essential"
        self.entity_id = (
            f"binary_sensor.entity_availability_{group_slug}_any_offline_non_essential"
        )
        self._attr_translation_key = "any_offline_non_essential"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"Entity Availability - {group_name}",
            manufacturer="Entity Availability",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        return any(
            d.is_offline and not d.is_suppressed and d.is_non_essential
            for d in self.coordinator.device_states.values()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entities = [
            d.entity_id
            for d in self.coordinator.device_states.values()
            if d.is_offline and not d.is_suppressed and d.is_non_essential
        ]
        return {"offline_entities": entities, "offline_count": len(entities)}


class AnyLowBatteryBinarySensor(DedupCoordinatorBinarySensor):
    """Binary sensor: ON when at least one essential entity has low battery."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_icon = "mdi:battery-alert-variant-outline"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_any_low_battery"
        self.entity_id = (
            f"binary_sensor.entity_availability_{group_slug}_any_low_battery"
        )
        self._attr_translation_key = "any_low_battery"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"Entity Availability - {group_name}",
            manufacturer="Entity Availability",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        """Return True if any essential entity has low battery."""
        return any(
            d.is_low_battery and not d.is_suppressed and not d.is_non_essential
            for d in self.coordinator.device_states.values()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return low battery entity details."""
        entities = [
            d.entity_id
            for d in self.coordinator.device_states.values()
            if d.is_low_battery and not d.is_suppressed and not d.is_non_essential
        ]
        return {"low_battery_entities": entities, "low_battery_count": len(entities)}


class AnyStaleBinarySensor(DedupCoordinatorBinarySensor):
    """Binary sensor: ON when at least one essential entity is stale."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:clock-alert-outline"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_any_stale"
        self.entity_id = f"binary_sensor.entity_availability_{group_slug}_any_stale"
        self._attr_translation_key = "any_stale"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"Entity Availability - {group_name}",
            manufacturer="Entity Availability",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        """Return True if any essential entity is stale."""
        return any(
            d.is_stale and not d.is_suppressed and not d.is_non_essential
            for d in self.coordinator.device_states.values()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return stale entity details."""
        entities = [
            d.entity_id
            for d in self.coordinator.device_states.values()
            if d.is_stale and not d.is_suppressed and not d.is_non_essential
        ]
        return {"stale_entities": entities, "stale_count": len(entities)}


class AnyPoorSignalBinarySensor(DedupCoordinatorBinarySensor):
    """Binary sensor: ON when at least one essential entity has poor signal strength."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:signal-off"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_any_poor_signal"
        self.entity_id = (
            f"binary_sensor.entity_availability_{group_slug}_any_poor_signal"
        )
        self._attr_translation_key = "any_poor_signal"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"Entity Availability - {group_name}",
            manufacturer="Entity Availability",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator._poor_signal_entity_ids())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entities = self.coordinator._poor_signal_entity_ids()
        return {"poor_signal_entities": entities, "poor_signal_count": len(entities)}


class AnyPoorSignalNonEssentialBinarySensor(DedupCoordinatorBinarySensor):
    """Binary sensor: ON when at least one non-essential entity has poor signal."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:signal-off"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_any_poor_signal_non_essential"
        self.entity_id = f"binary_sensor.entity_availability_{group_slug}_any_poor_signal_non_essential"
        self._attr_translation_key = "any_poor_signal_non_essential"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"Entity Availability - {group_name}",
            manufacturer="Entity Availability",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator._poor_signal_ne_entity_ids())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entities = self.coordinator._poor_signal_ne_entity_ids()
        return {"poor_signal_entities": entities, "poor_signal_count": len(entities)}


class AnyLowBatteryNonEssentialBinarySensor(DedupCoordinatorBinarySensor):
    """Binary sensor: ON when at least one non-essential entity has low battery."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_icon = "mdi:battery-alert-variant-outline"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EntityAvailabilityCoordinator,
        group_name: str,
        group_slug: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_any_low_battery_non_essential"
        self.entity_id = f"binary_sensor.entity_availability_{group_slug}_any_low_battery_non_essential"
        self._attr_translation_key = "any_low_battery_non_essential"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"Entity Availability - {group_name}",
            manufacturer="Entity Availability",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        return any(
            d.is_low_battery and not d.is_suppressed and d.is_non_essential
            for d in self.coordinator.device_states.values()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entities = [
            d.entity_id
            for d in self.coordinator.device_states.values()
            if d.is_low_battery and not d.is_suppressed and d.is_non_essential
        ]
        return {"low_battery_entities": entities, "low_battery_count": len(entities)}
