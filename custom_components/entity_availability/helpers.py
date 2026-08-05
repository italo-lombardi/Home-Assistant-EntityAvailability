"""Shared helpers for Entity Availability."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)


def resolve_area_name(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the area name for entity_id, or None if unassigned.

    Priority: entity area_id → device area_id → None.
    """
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if not entry:
        return None
    area_id = entry.area_id
    if not area_id and entry.device_id:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(entry.device_id)
        area_id = device.area_id if device else None
    if not area_id:
        return None
    area_reg = ar.async_get(hass)
    area = area_reg.async_get_area(area_id)
    return area.name if area else None


def resolve_display_name(
    hass: HomeAssistant, entity_id: str, use_device_names: bool = False
) -> str:
    """Return a display name for entity_id.

    If use_device_names is True, prefer the device name from the device registry.
    Falls back to friendly_name state attribute, then to an entity_id slug.
    """
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
