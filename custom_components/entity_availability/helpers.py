"""Shared helpers for Entity Availability."""

from __future__ import annotations

from typing import TYPE_CHECKING

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

if TYPE_CHECKING:  # pragma: no cover
    from .models import DeviceState


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


def collapse_severity(d: DeviceState) -> int:
    """Worst-case severity rank for representative selection (red>yellow>grey>green)."""
    if d.is_offline:
        return 3  # red
    if d.is_low_battery or d.signal_quality == "poor":
        return 2  # yellow
    if d.is_stale:
        return 1  # grey
    return 0  # green


def _representative_rank(d: DeviceState) -> tuple[bool, int]:
    """Rank for picking a device's representative.

    An UNSUPPRESSED member always outranks a suppressed one — otherwise a suppressed
    entity with high raw severity (e.g. suppressed+offline) could become the
    representative and hide a genuine, unsuppressed problem on a sibling (stale/low
    battery/poor signal), dropping the whole device from every active-problem count.
    Within the same suppression status, worst severity wins.
    """
    return (not d.is_suppressed, collapse_severity(d))


def collapse_key(hass: HomeAssistant, d: DeviceState) -> str | None:
    """Return the composite device-collapse key for a DeviceState, or None.

    Key = device_id::battery::signal::unit::non_essential. Entities with no
    device_id return None (never collapse — each stays its own row/count).
    Only value fields drive the key, so entities that differ on battery or
    signal never merge — the same conservative behavior the card used. The
    non-essential flag is included so essential and non-essential entities on the
    same device never merge (they render in separate tiers).
    device_id alone guarantees same-device grouping; display_name is excluded so
    a mid-session device rename cannot split a merged device into two rows.
    """
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(d.entity_id)
    device_id = entry.device_id if entry else None
    if not device_id:
        return None
    # Coerce numeric fields to a stable int (or None) so a flaky sensor emitting a
    # float/NaN can't produce an inconsistent key that silently breaks grouping.
    battery = _stable_int(d.battery_level)
    signal = _stable_int(d.signal_level)
    # Signal unit is meaningless without a level — drop it when level is None.
    unit = d.signal_unit if signal is not None else None
    return f"{device_id}::{battery}::{signal}::{unit}::{d.is_non_essential}"


def _stable_int(value: object) -> int | None:
    """Return int(value), or None if value is None/NaN/non-numeric (stable key part)."""
    if value is None:
        return None
    try:
        ivalue = round(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return ivalue


def collapse_representatives(
    hass: HomeAssistant,
    states: dict[str, DeviceState],
    collapsible: set[str] | None = None,
) -> dict[str, str]:
    """Return {entity_id -> representative entity_id} collapsing same-device entities.

    Groups states by composite key; each key's representative is its worst-severity
    member, with unsuppressed members always preferred over suppressed ones (ties:
    first in insertion order). Entities with no device_id (key None) map to
    themselves. Callers gate on whether collapse is active — this always collapses
    whatever it is given.

    ``collapsible`` optionally restricts which entities may merge: an entity not in
    the set always maps to itself and never becomes another entity's representative.
    Used by combined groups so entities from a group with collapse OFF stay their
    own rows even when a sibling on the same device comes from a collapse-ON group.
    ``None`` means every entity is collapsible (single-group behavior).
    """
    by_key: dict[str, str] = {}
    rep_of: dict[str, str] = {}
    for eid, d in states.items():
        # Entities from non-collapse groups never merge — own row, own count.
        if (collapsible is not None and eid not in collapsible) or (
            key := collapse_key(hass, d)
        ) is None:
            rep_of[eid] = eid
            continue
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = eid
            rep_of[eid] = eid
        elif _representative_rank(d) > _representative_rank(states[cur]):
            # New best-ranked member (unsuppressed first, then worst severity)
            # becomes the key's representative.
            by_key[key] = eid
            # ponytail: O(n) scan per reassignment → O(n²) per key; fine for typical
            # HA group sizes (<500 entities); use two-pass if that ceiling is hit.
            for other, r in rep_of.items():
                if r == cur:
                    rep_of[other] = eid
            rep_of[eid] = eid
        else:
            rep_of[eid] = by_key[key]
    return rep_of
