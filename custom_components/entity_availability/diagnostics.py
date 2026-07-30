"""Diagnostics support for Entity Availability."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_COMBINED
from .coordinator import EntityAvailabilityCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_COMBINED:
        return {
            "entry_type": "combined",
            "title": entry.title,
            "combined_groups": len(entry.data.get("combined_groups", [])),
        }

    coordinator: EntityAvailabilityCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if not coordinator:
        return {"error": "coordinator not loaded"}

    states = coordinator.device_states
    data = entry.data
    return {
        "entry_type": "group",
        "title": entry.title,
        "config": {
            "cooldown_seconds": data.get("cooldown", 0),
            "staleness_threshold_minutes": data.get("staleness_threshold", 0),
            "battery_threshold_pct": data.get("battery_threshold", 0),
            "recovery_window_minutes": coordinator.recovery_window_minutes,
            "availability_windows": data.get("availability_windows", []),
        },
        "entity_count": len(states),
        "essential_count": sum(1 for d in states.values() if not d.is_non_essential),
        "non_essential_count": sum(1 for d in states.values() if d.is_non_essential),
        "offline_count": sum(
            1
            for d in states.values()
            if d.is_offline and not d.is_suppressed and not d.is_non_essential
        ),
        "offline_count_non_essential": sum(
            1
            for d in states.values()
            if d.is_offline and not d.is_suppressed and d.is_non_essential
        ),
        "suppressed_count": sum(1 for d in states.values() if d.is_suppressed),
        "low_battery_count": sum(
            1 for d in states.values() if d.is_low_battery and not d.is_non_essential
        ),
        "stale_count": sum(
            1 for d in states.values() if d.is_stale and not d.is_non_essential
        ),
    }
