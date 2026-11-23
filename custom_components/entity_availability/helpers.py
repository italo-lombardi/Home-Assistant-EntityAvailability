"""Shared helpers for Entity Availability."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass
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


# Suffix → network type inference. Only unambiguous mappings (LQI = Zigbee).
_SIGNAL_SUFFIX_NETWORK_TYPE: dict[str, str] = {
    "_linkquality": "zigbee_lqi",
    "_lqi": "zigbee_lqi",
}


def resolve_label_members(hass: HomeAssistant, label_ids: list[str]) -> list[str]:
    """Resolve HA label_ids to a deduped, ordered list of monitored entity_ids.

    Membership = union of entities carrying a label DIRECTLY and every entity of a
    DEVICE carrying a label. Order is deterministic: direct entities first (in
    registry order per label), then device-derived entities, dedup preserving first
    sight.

    DISABLED entities are excluded. On the DIRECT path ``async_entries_for_label``
    returns disabled entities, so we filter ``disabled_by is None`` explicitly; on the
    DEVICE path ``async_entries_for_device`` already excludes them by default. A
    disabled entity has no state object, so monitoring it would count it offline
    forever and poison the group's availability %, offline count and MTBF/MTTR; the
    explicit filter also removes the asymmetry that would otherwise keep a disabled
    entity when labeled directly but drop it when its device is labeled. HIDDEN
    entities are kept — they still have a live state and are legitimately monitorable;
    the user only hid them from dashboards.

    Returns [] when label_ids is empty (a label group with no labels monitors
    nothing). Note the empty-but-labels-set guard lives in the CALLER: an empty
    result for a non-empty label set can be a transient registry hiccup and must not
    trigger a history-pruning membership shrink.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    seen: dict[str, None] = {}  # insertion-ordered set
    for label_id in label_ids:
        for ent in er.async_entries_for_label(ent_reg, label_id):
            # async_entries_for_label returns disabled entities; filter them here.
            if ent.disabled_by is None:
                seen.setdefault(ent.entity_id, None)
        for device in dr.async_entries_for_label(dev_reg, label_id):
            # async_entries_for_device defaults include_disabled_entities=False, so
            # the device path already excludes disabled entities — no filter needed
            # (adding one would be dead code and symmetrically redundant).
            for ent in er.async_entries_for_device(ent_reg, device.id):
                seen.setdefault(ent.entity_id, None)
    return list(seen)


def infer_network_type(signal_entity_id: str) -> str:
    """Infer network type from signal sensor entity_id suffix. 'generic' if unknown."""
    for suffix, nt in _SIGNAL_SUFFIX_NETWORK_TYPE.items():
        if signal_entity_id.endswith(suffix):
            return nt
    return "generic"


def detect_signal_entity(hass: HomeAssistant, entity_id: str) -> str:
    """Auto-detect signal sensor for an entity. Returns entity_id or empty string.

    Detection order:
    1. Device registry sibling with device_class=SIGNAL_STRENGTH
    2. Naming convention on slug: *_linkquality, *_signal_strength, *_rssi, *_lqi, *_signal
    3. Same as 2 but with known HA suffixes (_last_seen, _last_updated) stripped first
    """
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if entry and entry.device_id:
        for ent in er.async_entries_for_device(ent_reg, entry.device_id):
            if ent.entity_id == entity_id:
                continue
            if (
                ent.original_device_class == SensorDeviceClass.SIGNAL_STRENGTH
                or ent.device_class == SensorDeviceClass.SIGNAL_STRENGTH
            ):
                return ent.entity_id

    parts = entity_id.split(".", 1)
    if len(parts) == 2:  # pragma: no branch
        slug = parts[1]
        signal_suffixes = (
            "_linkquality",
            "_signal_strength",
            "_rssi",
            "_lqi",
            "_signal",
        )
        slugs_to_try = [slug]
        for strip_suffix in ("_last_seen", "_last_updated"):
            if slug.endswith(strip_suffix):
                slugs_to_try.append(slug[: -len(strip_suffix)])
                break
        for base in slugs_to_try:
            for suffix in signal_suffixes:
                candidate = f"sensor.{base}{suffix}"
                if hass.states.get(candidate):
                    return candidate

    return ""


def detect_battery_entity(hass: HomeAssistant, entity_id: str) -> str:
    """Auto-detect battery entity for a monitored entity. Returns entity_id or ''."""
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if entry and entry.device_id:
        for ent in er.async_entries_for_device(ent_reg, entry.device_id):
            if ent.entity_id == entity_id:
                continue
            if ent.original_device_class == SensorDeviceClass.BATTERY or (
                ent.device_class == SensorDeviceClass.BATTERY
            ):
                return ent.entity_id

    parts = entity_id.split(".", 1)
    if len(parts) == 2:  # pragma: no branch
        battery_entity = f"sensor.{parts[1]}_battery"
        if hass.states.get(battery_entity):
            return battery_entity

    return ""


def build_signal_map(
    hass: HomeAssistant, entities: list[str]
) -> dict[str, dict[str, str]]:
    """Auto-build a signal entity map for entities with a detectable signal sensor.

    Used at runtime for label-group members (no per-entity wizard input available).
    Only entities whose signal sensor auto-detects get an entry; others are omitted
    (no signal monitoring, same as an unmapped static-group entity).
    """
    signal_map: dict[str, dict[str, str]] = {}
    for entity_id in entities:
        sensor = detect_signal_entity(hass, entity_id)
        if sensor:
            signal_map[entity_id] = {
                "sensor": sensor,
                "network_type": infer_network_type(sensor),
            }
    return signal_map


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


def dedup_display_names(names: list[str]) -> list[str]:
    """Collapse identical display strings to ``"<name> {N}"`` and sort by name.

    After device-collapse and name resolution, two rows can still render the
    same string (e.g. two entities on a device shown by device name, or two
    device-less entities sharing a friendly_name). Rendering them twice reads
    as a bug and, for recovery lists, flaps the recorded state string. Group
    identical strings: count==1 → ``"<name>"``; count>1 → ``"<name> {N}"``.
    N is the post-collapse row count, so it agrees with the numeric count
    sensors. Sorted by the bare display string so severity and recovery lists
    share one deterministic order.
    """
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return [
        name if n == 1 else f"{name} {{{n}}}"
        for name, n in sorted(counts.items(), key=lambda kv: (kv[0].casefold(), kv[0]))
    ]


def render_name_list(names: list[str], max_len: int) -> str:
    """Sort + {N}-dedup names, join with ", ", truncate to max_len.

    Empty input renders ``"None"`` (the sentinel every list sensor uses).
    Truncation drops whole trailing names on the ", " boundary so a ``{N}``
    marker is never sliced mid-token (e.g. ``"...Motion {1"``). If even the
    first name overflows, that single name is byte-truncated with a trailing
    ``"..."`` as a last resort.
    """
    deduped = dedup_display_names(names)
    if not deduped:
        return "None"
    result = ", ".join(deduped)
    if len(result) <= max_len:
        return result
    # Overflow: keep whole names on the ", " boundary until the next would exceed
    # the budget (room left for "..."). Guaranteed to drop at least one name, so
    # the loop always breaks — it can never run to natural completion here (that
    # would mean every name fit, contradicting the len(result) > max_len guard).
    budget = max_len - 3
    if len(deduped[0]) > budget:  # even the first name alone overflows
        return deduped[0][:budget] + "..."
    kept = [deduped[0]]
    used = len(deduped[0])
    for name in deduped[1:]:  # pragma: no branch - guard guarantees a break
        if used + 2 + len(name) > budget:
            break
        kept.append(name)
        used += 2 + len(name)
    return ", ".join(kept) + "..."


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

    Entities with no device_id return None (never collapse — each stays its own
    row/count). This key is only a COARSE same-device+same-tier bucket; the real
    same-vs-different-source decision (with None-as-wildcard) is made by the meet in
    ``collapse_representatives``, which a flat string key cannot express. So the key
    deliberately omits the source fields and keys ONLY on device_id + non_essential.

    Values (battery_level, signal_level) are intentionally NOT in the key: a live
    reading drifting across a boundary (e.g. -70/-71 dBm) would otherwise reshuffle
    rows every poll and amplify recorder writes. Provenance (which sensor) lives on
    the DeviceState and is compared by the meet, not baked into this string.
    """
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(d.entity_id)
    device_id = entry.device_id if entry else None
    if not device_id:
        return None
    return f"{device_id}::{d.is_non_essential}"


def _sources_compatible(
    a: tuple[str | None, str | None], b: tuple[str | None, str | None]
) -> bool:
    """Two (battery_source, signal_source) pairs may share a row.

    Per axis: equal, or at least one None (None = wildcard, merges). Two DISTINCT
    concrete sources on the same axis conflict → never merge (e.g. two battery
    sensors on one device stay separate rows).
    """
    return all((x == y) or (x is None) or (y is None) for x, y in zip(a, b))


def _tighten(
    rep: tuple[str | None, str | None], member: tuple[str | None, str | None]
) -> tuple[str | None, str | None]:
    """Meet: rep absorbs a member's CONCRETE source on any axis the rep left None.

    Monotone (None→concrete only, never concrete→other) and idempotent. The binary
    op itself is NOT commutative on conflicting concretes (``_tighten((b1,_),(b2,_))``
    keeps b1), but conflicting concretes never reach it — ``_sources_compatible``
    gates the merge first — so a cluster's tightened pair converges to the same value
    regardless of the order compatible members join. This is what lets a both-None
    sibling merge into a bound rep via a REAL concrete match against the tightened
    rep, not a wildcard bridge.
    """
    return tuple(r if r is not None else m for r, m in zip(rep, member))  # type: ignore[return-value]


def collapse_representatives(
    hass: HomeAssistant,
    states: dict[str, DeviceState],
    collapsible: set[str] | None = None,
) -> dict[str, str]:
    """Return {entity_id -> representative entity_id} collapsing same-device entities.

    Buckets entities by coarse key (device_id + non-essential tier), then within each
    bucket merges by SOURCE compatibility: two entities share a row iff their
    (battery_source, signal_source) pairs are compatible (equal or wildcard-None per
    axis). Distinct concrete sources on an axis (e.g. two battery sensors on one
    device) stay separate rows. Entities with no device_id (key None) map to
    themselves.

    Within a bucket, clusters are grown against a REPRESENTATIVE whose source pair
    TIGHTENS as members join (a meet: absorbs concrete sources on axes the rep left
    None). Candidates are processed in a deterministic total order — unsuppressed
    first, then worst severity, then entity_id descending (``reverse=True``) — so a
    source-less wildcard member attaches to the same concrete cluster regardless of
    dict/registration order. The resulting row COUNT (set of representatives) is fully
    stable across restarts and severity changes.

    Caveat: the rank component reads LIVE severity, so WHICH sibling is the chosen
    representative can shift between two equally-valid members when their severity
    flips (e.g. one goes stale). This only moves the representative *entity_id* within
    a row, never the row count, and the rep-id attrs (``row_entity_ids`` /
    ``row_members``) are unrecorded — so it is a cosmetic display shift, not a recorder
    write. The same class of shift applies to WHICH cluster a source-less wildcard
    member joins when a device bucket holds two distinct concrete sources (e.g.
    ``{(bat1, None), (None, sig1), (bat2, sig1)}``): the row count is invariant, only
    the wildcard's parent row differs by order, and both are unrecorded. A
    source-stable tiebreak could pin either if a card ever needs that.

    ``collapsible`` optionally restricts which entities may merge: an entity not in
    the set always maps to itself and never becomes another entity's representative.
    Used by combined groups so entities from a group with collapse OFF stay their
    own rows even when a sibling on the same device comes from a collapse-ON group.
    ``None`` means every entity is collapsible (single-group behavior).
    """
    rep_of: dict[str, str] = {}
    buckets: dict[str, list[str]] = {}
    for eid, d in states.items():
        # Entities from non-collapse groups never merge — own row, own count.
        if (collapsible is not None and eid not in collapsible) or (
            key := collapse_key(hass, d)
        ) is None:
            rep_of[eid] = eid
            continue
        buckets.setdefault(key, []).append(eid)

    for members in buckets.values():
        # Deterministic processing order: best-ranked first (unsuppressed, worst
        # severity), entity_id tiebreak. Governs which concrete cluster a shared
        # wildcard member meets first → stable assignment across runs.
        members.sort(key=lambda e: (_representative_rank(states[e]), e), reverse=True)
        # Each cluster: representative entity_id -> its (tightened) source pair.
        reps: dict[str, tuple[str | None, str | None]] = {}
        for eid in members:
            src = (states[eid].battery_source, states[eid].signal_source)
            for rep_eid, rep_src in reps.items():
                if _sources_compatible(rep_src, src):
                    reps[rep_eid] = _tighten(rep_src, src)
                    rep_of[eid] = rep_eid
                    break
            else:
                reps[eid] = src
                rep_of[eid] = eid
    return rep_of
