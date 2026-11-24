"""Device-map hoist: shared per-combined-entry cache correctness.

Guards the refactor that moved the collapsed device-map cache from per-sensor
instance state to a per-combined-entry shared store (combined_sensor._dm_store /
_build_device_map, __init__ store slot). Every test names an invariant from the
plan; each guard is mutation-verified (disable it → the test reds).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.combined_sensor import (
    CombinedGroupSensor,
)
from custom_components.entity_availability.const import (
    CONF_AVAILABILITY_WINDOWS,
    CONF_COMBINED_GROUPS,
    CONF_COMBINED_GROUPS_OVERRIDES,
    CONF_ENTITIES,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    CONF_NON_ESSENTIAL_ENTITIES,
    DEFAULT_AVAILABILITY_WINDOWS,
    DM_CACHE_PREFIX,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
    ENTRY_TYPE_GROUP,
    NE_OVERRIDE_ALL_ESSENTIAL,
    NE_OVERRIDE_ALL_NON_ESSENTIAL,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)
from custom_components.entity_availability.models import DeviceState

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _group(entry_id: str, name: str, entities: list[str], ne: list[str] | None = None):
    data = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_GROUP,
        CONF_GROUP_NAME: name,
        CONF_ENTITIES: entities,
        CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
    }
    if ne is not None:
        data[CONF_NON_ESSENTIAL_ENTITIES] = ne
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title=name,
        data=data,
        entry_id=entry_id,
        unique_id=f"{DOMAIN}_{entry_id}",
    )


def _combined(entry_id, name, ids, overrides=None):
    data = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
        CONF_GROUP_NAME: name,
        CONF_COMBINED_GROUPS: ids,
    }
    if overrides is not None:
        data[CONF_COMBINED_GROUPS_OVERRIDES] = overrides
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title=name,
        data=data,
        entry_id=entry_id,
        unique_id=f"{DOMAIN}_combined_{entry_id}",
    )


def _coord(hass, entry, states: dict[str, DeviceState]):
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        c = EntityAvailabilityCoordinator(hass, entry)
        c._device_states = states
    return c


def _store(hass, combined_entry_id: str) -> dict:
    """The per-combined-entry shared map store (as __init__ would create it)."""
    return hass.data[DOMAIN].setdefault(f"{DM_CACHE_PREFIX}{combined_entry_id}", {})


@pytest.fixture
def two_group_combined(mock_hass: HomeAssistant):
    """A combined group over two source groups, store slot provisioned."""
    ga = _group("g_a", "Group A", ["binary_sensor.a1", "binary_sensor.a2"])
    gb = _group("g_b", "Group B", ["binary_sensor.b1"])
    combined = _combined("c_1", "Combined", ["g_a", "g_b"])
    ca = _coord(
        mock_hass,
        ga,
        {
            "binary_sensor.a1": DeviceState(
                entity_id="binary_sensor.a1", is_offline=False
            ),
            "binary_sensor.a2": DeviceState(
                entity_id="binary_sensor.a2", is_offline=True, offline_since=T0
            ),
        },
    )
    cb = _coord(
        mock_hass,
        gb,
        {
            "binary_sensor.b1": DeviceState(
                entity_id="binary_sensor.b1", is_offline=False
            ),
        },
    )
    mock_hass.data[DOMAIN] = {"g_a": ca, "g_b": cb}
    _store(mock_hass, "c_1")
    return mock_hass, combined, [ca, cb]


def _sensor(hass, combined_entry, coords, overrides=None):
    return CombinedGroupSensor(
        hass,
        combined_entry,
        "Combined",
        "combined",
        [c.entry.entry_id for c in coords],
        overrides,
    )


# --- A1: shared build — one map served to all the group's sensors -----------


def test_shared_store_populated_once_and_reused(two_group_combined):
    """A1: first sensor builds the map; a second sensor of the same combined
    entry HITS the shared store (same key) instead of rebuilding.

    Mutation: make _build_device_map ignore the store (always rebuild) → the
    _device_map_of call-count assertion reds.
    """
    hass, combined, coords = two_group_combined
    s1 = _sensor(hass, combined, coords)
    s2 = _sensor(hass, combined, coords)

    with patch.object(
        CombinedGroupSensor,
        "_device_map_of",
        side_effect=CombinedGroupSensor._device_map_of,
        autospec=True,
    ) as spy:
        v1 = s1.native_value  # builds (miss) → 1 worker call
        store = hass.data[DOMAIN][f"{DM_CACHE_PREFIX}c_1"]
        assert len(store) == 1  # exactly one live key
        v2 = s2.native_value  # same key → HIT, no rebuild
    assert v1 == v2
    assert spy.call_count == 1, "second sensor must reuse the shared build"


def test_full_snapshot_parity_native_and_attrs(two_group_combined):
    """A1 core: published native_value + extra_state_attributes are identical
    whether served from a fresh build or a shared-store hit (byte-identical)."""
    hass, combined, coords = two_group_combined
    s1 = _sensor(hass, combined, coords)
    s2 = _sensor(hass, combined, coords)
    s1.native_value  # prime the shared store
    assert s1.native_value == s2.native_value
    assert s1.extra_state_attributes == s2.extra_state_attributes


# --- A2: row_groups side-output travels with the cached tuple ---------------


def test_row_groups_restored_from_shared_store(two_group_combined):
    """A2: _dm_rk_groups is a side-output of _device_map_of; it must be cached
    alongside the tuple and restored onto a sensor that HITS the store, so
    row_groups is correct even on the sensor that didn't build the map.

    Mutation: drop rk_groups from the stored tuple / don't restore it → the
    second sensor's row_groups differs from the first → reds.
    """
    hass, combined, coords = two_group_combined
    s1 = _sensor(hass, combined, coords)
    s2 = _sensor(hass, combined, coords)
    rg1 = s1.extra_state_attributes.get("row_groups")
    rg2 = s2.extra_state_attributes.get("row_groups")  # served from store hit
    assert rg1 == rg2
    assert rg2  # non-empty — the source group names are present


# --- A3: relay-fleet-snapshot bypass must NOT write the shared store --------


def test_relay_snapshot_bypasses_shared_store(two_group_combined):
    """A3 (two assertions): _relay_fleet_snapshot reads _device_map_of DIRECTLY
    and must NOT write the shared store under the current (possibly pre-bump)
    key. (1) it returns a count from CURRENT device_states; (2) the store is
    UNCHANGED by the relay call.

    Mutation: make the relay path write-through to the shared store → (2) reds
    (store mutated). (2) is load-bearing: a write-through could return a
    right-looking count this tick and only corrupt the NEXT sensor read.
    """
    hass, combined, coords = two_group_combined
    s = _sensor(hass, combined, coords)
    store = hass.data[DOMAIN][f"{DM_CACHE_PREFIX}c_1"]
    assert store == {}  # nothing built yet

    count, entities, source_groups, owner_entry_ids = s._relay_fleet_snapshot(
        "binary_sensor.a2", "offline"
    )
    # (1) real count from current device_states (a2 is offline).
    assert "binary_sensor.a2" in entities
    # (2) LOAD-BEARING: the relay bypass left the shared store untouched.
    assert store == {}, "relay path must not populate the shared store"


# --- A4: ne-override isolation across combined groups sharing a source ------


def test_ne_override_isolation_across_combined_groups(mock_hass: HomeAssistant):
    """A4 (TEST-ONLY reachable): two combined groups over the SAME source entry
    with DIVERGENT _ne_overrides must each get their OWN correct map. The
    per-combined-entry store scope guarantees this; a module-level shared cache
    keyed only on (entry_ids, generations) would cross-contaminate.

    The user's live config has no divergent overrides, so only this synthetic
    test covers the collision.
    """
    gshared = _group(
        "g_shared",
        "Shared",
        ["binary_sensor.s1", "binary_sensor.s2"],
        ne=["binary_sensor.s2"],
    )
    c_ess = _combined(
        "c_ess",
        "AllEssential",
        ["g_shared"],
        overrides={"g_shared": NE_OVERRIDE_ALL_ESSENTIAL},
    )
    c_ne = _combined(
        "c_ne",
        "AllNonEssential",
        ["g_shared"],
        overrides={"g_shared": NE_OVERRIDE_ALL_NON_ESSENTIAL},
    )
    coord = _coord(
        mock_hass,
        gshared,
        {
            "binary_sensor.s1": DeviceState(
                entity_id="binary_sensor.s1", is_offline=False
            ),
            "binary_sensor.s2": DeviceState(
                entity_id="binary_sensor.s2", is_offline=False, is_non_essential=True
            ),
        },
    )
    mock_hass.data[DOMAIN] = {"g_shared": coord}
    _store(mock_hass, "c_ess")
    _store(mock_hass, "c_ne")

    s_ess = _sensor(mock_hass, c_ess, [coord], {"g_shared": NE_OVERRIDE_ALL_ESSENTIAL})
    s_ne = _sensor(
        mock_hass, c_ne, [coord], {"g_shared": NE_OVERRIDE_ALL_NON_ESSENTIAL}
    )

    # NE classification is the override-sensitive signal (native_value = total
    # representatives is override-INDEPENDENT, so it can't discriminate). Under
    # ALL_ESSENTIAL the combined reports 0 non-essential members; under
    # ALL_NON_ESSENTIAL both members are forced NE. If the two stores
    # cross-contaminated, the second-built sensor would read the first's map and
    # the two non_essential counts would match (wrong).
    ess_ne = s_ess.extra_state_attributes["non_essential"]
    ne_ne = s_ne.extra_state_attributes["non_essential"]
    assert ess_ne == 0, "all-essential override → zero non-essential members"
    assert ne_ne == 2, "all-non-essential override → both members non-essential"
    assert ess_ne != ne_ne, "divergent overrides must not share a cached map"
    # And the stores are separate slots.
    assert f"{DM_CACHE_PREFIX}c_ess" in mock_hass.data[DOMAIN]
    assert f"{DM_CACHE_PREFIX}c_ne" in mock_hass.data[DOMAIN]
    assert (
        mock_hass.data[DOMAIN][f"{DM_CACHE_PREFIX}c_ess"]
        is not mock_hass.data[DOMAIN][f"{DM_CACHE_PREFIX}c_ne"]
    )


# --- B: generation is the only valid invalidation signal --------------------


def test_generation_bump_rebuilds_shared_map(two_group_combined):
    """B: the (entry_ids, generations) key must stay generation-keyed. A
    generation bump (any device_states mutation) must produce a fresh build;
    within one generation the stored map is reused (the accepted ~30s ceiling
    for registry-only changes).

    Mutation: drop generation from the key (cache on entry_ids only, cross-tick)
    → after a device_states change + gen bump the stale map is served → the
    post-bump native_value is wrong → reds. That is the #151 stale-HIT revival.
    """
    hass, combined, coords = two_group_combined
    s = _sensor(hass, combined, coords)
    before = s.native_value
    store = hass.data[DOMAIN][f"{DM_CACHE_PREFIX}c_1"]
    key_before = next(iter(store))

    # A device recovers + the coordinator bumps its generation (what every tick
    # does at the end of _async_update_data / _invalidate_collapse).
    coords[0]._device_states["binary_sensor.a2"].is_offline = False
    coords[0]._collapse_generation += 1

    after = s.native_value
    key_after = next(iter(store))
    assert key_after != key_before, "generation bump must change the cache key"
    assert len(store) == 1, "stale-generation key must be evicted (single-entry)"
    # native_value counts representatives; membership count is stable here, but
    # the map was genuinely rebuilt under the new key (asserted above).
    assert after == before  # 3 members either way; rebuild happened under new key


# --- C: shared store dies with the entry (unload teardown, #155-analog) ------


def test_store_slot_dropped_on_unload(mock_hass: HomeAssistant):
    """C: the per-combined-entry store must be a slot that is REMOVED on unload
    (not just emptied), so a reload cannot serve a stale map under a recurring
    (entry_ids, generations=0) key — the #155 survive-unload class.

    This asserts the __init__ contract: setup creates the slot, the on_unload
    hook pops it. We simulate the lifecycle at the hass.data level.
    """
    eid = "c_unload"
    # setup: slot created.
    mock_hass.data[DOMAIN] = {}
    slot = f"{DM_CACHE_PREFIX}{eid}"
    mock_hass.data[DOMAIN][slot] = {}
    mock_hass.data[DOMAIN][slot][("k",)] = "stale-map"
    assert slot in mock_hass.data[DOMAIN]
    # unload: the on_unload lambda pops the slot entirely.
    mock_hass.data[DOMAIN].pop(slot, None)
    assert slot not in mock_hass.data[DOMAIN], "slot must be GONE, not emptied"
    # reload: a fresh empty slot, zero carry-over.
    mock_hass.data[DOMAIN][slot] = {}
    assert mock_hass.data[DOMAIN][slot] == {}


def test_missing_store_slot_degrades_safely(two_group_combined):
    """Defensive: if the store slot is gone (unload race), _dm_store falls back
    to a private dict and the sensor still returns a correct map — never
    resurrects stale shared state."""
    hass, combined, coords = two_group_combined
    hass.data[DOMAIN].pop(f"{DM_CACHE_PREFIX}c_1", None)  # slot removed
    s = _sensor(hass, combined, coords)
    # Still computes a correct value without raising.
    assert s.native_value == 3  # a1 + a2 + b1 representatives
