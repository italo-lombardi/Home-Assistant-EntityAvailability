"""Combined group binary sensor for Entity Availability."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_COMBINED_GROUPS, CONF_GROUP_NAME, DOMAIN
from .coordinator import EntityAvailabilityCoordinator
from .write_dedup import WriteDedupMixin


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up combined group binary sensors."""
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
            CombinedGroupAnyOfflineBinarySensor(
                hass, entry, group_name, group_slug, coordinators, combined_entry_ids
            )
        ]
    )


class CombinedGroupAnyOfflineBinarySensor(WriteDedupMixin, BinarySensorEntity):
    """Binary sensor: ON when any entity across included groups is offline."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert"
    _attr_has_entity_name = True
    _attr_should_poll = False

    def _ea_current_value(self) -> Any:
        return self.is_on

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
        self._coordinators = coordinators
        self._combined_entry_ids = combined_entry_ids
        self._attr_unique_id = f"{entry.entry_id}_combined_any_offline"
        self.entity_id = (
            f"binary_sensor.entity_availability_combined_{group_slug}_any_offline"
        )
        self._attr_translation_key = "combined_any_offline"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Entity Availability - [Combined] {group_name}",
            manufacturer="Entity Availability",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._unsub_listeners: list[Callable[[], None]] = []

    async def async_added_to_hass(self) -> None:
        """Subscribe to all included coordinators."""

        @callback
        def _on_coordinator_update() -> None:
            if self._ea_should_write():
                self.async_write_ha_state()

        for coordinator in self._coordinators:
            self._unsub_listeners.append(
                coordinator.async_add_listener(_on_coordinator_update)
            )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from coordinators."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()
        self._ea_reset_cache()

    def _active_coordinators(self) -> list[EntityAvailabilityCoordinator]:
        domain_data = self.hass.data.get(DOMAIN, {})
        return [
            c
            for c in self._coordinators
            if isinstance(
                domain_data.get(c.entry.entry_id), EntityAvailabilityCoordinator
            )
        ]

    @property
    def available(self) -> bool:
        """Return False when all source coordinators have been unloaded."""
        return len(self._active_coordinators()) > 0

    @property
    def is_on(self) -> bool:
        """Return True if any non-suppressed entity across all groups is offline."""
        for coord in self._active_coordinators():
            for device in coord.device_states.values():
                if not device.is_suppressed and device.is_offline:
                    return True
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return offline entity details across all groups."""
        offline_entities = [
            d.entity_id
            for coord in self._active_coordinators()
            for d in coord.device_states.values()
            if d.is_offline and not d.is_suppressed
        ]
        return {
            "offline_entities": offline_entities,
            "offline_count": len(offline_entities),
        }
