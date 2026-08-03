"""Diagnostics support for Entity Availability."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AVAILABILITY_WINDOWS,
    CONF_BAD_STATES,
    CONF_BATTERY_ENTITY_MAP,
    CONF_BATTERY_THRESHOLD,
    CONF_COMBINED_GROUPS,
    CONF_COOLDOWN,
    CONF_ENTITIES,
    CONF_ENTRY_TYPE,
    CONF_NON_ESSENTIAL_ENTITIES,
    CONF_RECOVERY_WINDOW,
    CONF_SIGNAL_ENABLED,
    CONF_SIGNAL_ENTITY_MAP,
    CONF_STALENESS_THRESHOLD,
    CONF_STALENESS_USE_LAST_UPDATED,
    CONF_USE_DEVICE_NAMES,
    DEFAULT_AVAILABILITY_WINDOWS,
    DEFAULT_BAD_STATES,
    DEFAULT_BATTERY_THRESHOLD,
    DEFAULT_COOLDOWN,
    DEFAULT_RECOVERY_WINDOW,
    DEFAULT_SIGNAL_ENABLED,
    DEFAULT_STALENESS_THRESHOLD,
    DEFAULT_STALENESS_USE_LAST_UPDATED,
    DEFAULT_USE_DEVICE_NAMES,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
)
from .coordinator import EntityAvailabilityCoordinator

TO_REDACT: set[str] = set()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_COMBINED:
        return async_redact_data(
            {
                "entry_type": "combined",
                "title": entry.title,
                "combined_groups": entry.data.get(CONF_COMBINED_GROUPS, []),
            },
            TO_REDACT,
        )

    coordinator: EntityAvailabilityCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if not coordinator:
        return {"error": "coordinator not loaded"}

    data = entry.data
    states = coordinator.device_states
    essential = [
        e
        for e in data.get(CONF_ENTITIES, [])
        if e not in data.get(CONF_NON_ESSENTIAL_ENTITIES, [])
    ]
    non_essential = data.get(CONF_NON_ESSENTIAL_ENTITIES, [])

    return async_redact_data(
        {
            "entry_type": "group",
            "title": entry.title,
            "config": {
                "cooldown_seconds": data.get(CONF_COOLDOWN, DEFAULT_COOLDOWN),
                "staleness_threshold_minutes": data.get(
                    CONF_STALENESS_THRESHOLD, DEFAULT_STALENESS_THRESHOLD
                ),
                "staleness_use_last_updated": data.get(
                    CONF_STALENESS_USE_LAST_UPDATED, DEFAULT_STALENESS_USE_LAST_UPDATED
                ),
                "battery_threshold_pct": data.get(
                    CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD
                ),
                "signal_enabled": data.get(CONF_SIGNAL_ENABLED, DEFAULT_SIGNAL_ENABLED),
                "recovery_window_minutes": data.get(
                    CONF_RECOVERY_WINDOW, DEFAULT_RECOVERY_WINDOW
                ),
                "bad_states": data.get(CONF_BAD_STATES, DEFAULT_BAD_STATES),
                "use_device_names": data.get(
                    CONF_USE_DEVICE_NAMES, DEFAULT_USE_DEVICE_NAMES
                ),
                "availability_windows": data.get(
                    CONF_AVAILABILITY_WINDOWS, DEFAULT_AVAILABILITY_WINDOWS
                ),
            },
            "entities": {
                "essential": essential,
                "non_essential": non_essential,
                "battery_entity_map": data.get(CONF_BATTERY_ENTITY_MAP, {}),
                "signal_entity_map": data.get(CONF_SIGNAL_ENTITY_MAP, {}),
            },
            "counts": {
                "total": len(states),
                "essential": sum(1 for d in states.values() if not d.is_non_essential),
                "non_essential": sum(1 for d in states.values() if d.is_non_essential),
                "offline": sum(
                    1
                    for d in states.values()
                    if d.is_offline and not d.is_suppressed and not d.is_non_essential
                ),
                "offline_non_essential": sum(
                    1
                    for d in states.values()
                    if d.is_offline and not d.is_suppressed and d.is_non_essential
                ),
                "suppressed": sum(1 for d in states.values() if d.is_suppressed),
                "low_battery": sum(
                    1
                    for d in states.values()
                    if d.is_low_battery and not d.is_non_essential
                ),
                "stale": sum(
                    1 for d in states.values() if d.is_stale and not d.is_non_essential
                ),
            },
        },
        TO_REDACT,
    )
