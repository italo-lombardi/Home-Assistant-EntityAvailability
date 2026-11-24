"""Device-map hoist: shared per-combined-entry cache correctness.

Guards the refactor that moved the collapsed device-map cache from per-sensor
instance state to a per-combined-entry shared store (combined_sensor._dm_store /
_build_device_map, __init__ store slot). Every test names an invariant from the
plan; each guard is mutation-verified (disable it → the test reds).
"""

from __future__ import annotations

import dataclasses
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
    CONF_COLLAPSE_DEVICES,
    CONF_COMBINED_GROUPS,
    CONF_COMBINED_GROUPS_OVERRIDES,
    CONF_ENTITIES,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    CONF_NON_ESSENTIAL_ENTITIES,
    CONF_USE_DEVICE_NAMES,
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
from tests.test_collapse import _register_entity

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _group(
    entry_id: str,
    name: str,
    entities: list[str],
    ne: list[str] | None = None,
    collapse: bool = False,
):
    data = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_GROUP,
        CONF_GROUP_NAME: name,
        CONF_ENTITIES: entities,
        CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
    }
    if ne is not None:
        data[CONF_NON_ESSENTIAL_ENTITIES] = ne
    if collapse:
        data[CONF_USE_DEVICE_NAMES] = True
        data[CONF_COLLAPSE_DEVICES] = True
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
    post-bump offline count stays 1 instead of dropping to 0 → reds. That is the
    #151 stale-HIT revival. The recovered DeviceState is REBOUND (a fresh object,
    not an in-place flag flip) so a stale map keeps the old offline object and
    the value assertion reds on its own — see the rebind note below.
    """
    hass, combined, coords = two_group_combined
    s = _sensor(hass, combined, coords)
    # Baseline: a2 is offline → the combined reports 1 offline.
    before_offline = s.extra_state_attributes["offline"]
    store = hass.data[DOMAIN][f"{DM_CACHE_PREFIX}c_1"]
    key_before = next(iter(store))

    # A device recovers + the coordinator bumps its generation (what every tick
    # does at the end of _async_update_data / _invalidate_collapse). Replace the
    # DeviceState with a NEW object (not an in-place flag flip): the KEEP path of
    # _apply_ne_override returns the SAME object, which the cached map stores by
    # reference — an in-place mutation would be visible THROUGH a stale map, so
    # after_offline would drop to 0 even if the map were NOT rebuilt, making the
    # value assertion below inert. Rebinding leaves any stale map holding the old
    # (offline) object, so after_offline sticks at 1 unless a real rebuild runs.
    old = coords[0]._device_states["binary_sensor.a2"]
    coords[0]._device_states["binary_sensor.a2"] = dataclasses.replace(
        old, is_offline=False, offline_since=None
    )
    coords[0]._collapse_generation += 1

    after_offline = s.extra_state_attributes["offline"]
    key_after = next(iter(store))
    assert key_after != key_before, "generation bump must change the cache key"
    assert len(store) == 1, "stale-generation key must be evicted (single-entry)"
    # VALUE assertion (the real stale-content guard): the offline count must
    # DROP after the recovery+bump. If generation were dropped from the key, the
    # stale map would be served on hit and offline would stick at its old value.
    assert before_offline == 1, f"expected 1 offline at baseline, got {before_offline}"
    assert after_offline == 0, (
        f"offline must drop to 0 after recovery+gen-bump; got {after_offline} "
        "(stuck = stale map served = generation missing from the key)"
    )


# --- C: shared store dies with the entry (unload teardown, #155-analog) ------


async def test_store_slot_created_on_setup_and_dropped_on_unload(
    mock_hass: HomeAssistant,
):
    """C (#155-analog): the per-combined-entry store slot must be CREATED by the
    real async_setup_entry and REMOVED by the real on_unload callback — so a
    reload cannot serve a stale map under a recurring (entry_ids, gen=0) key.

    Drives the ACTUAL product teardown (entry._async_process_on_unload runs the
    lambda __init__ registered), not a hand-rolled dict.pop — so deleting the
    on_unload registration in __init__.py reds this test. (The earlier version
    popped the slot itself and only proved dict.pop works.)
    """
    from custom_components.entity_availability import (
        async_setup_entry as ea_async_setup_entry,
    )

    combined = _combined("c_unload", "Unload Combined", ["g_a", "g_b"])
    combined.add_to_hass(mock_hass)
    slot = f"{DM_CACHE_PREFIX}c_unload"

    # Real setup: the combined branch creates the slot + registers the on_unload
    # pop. Stub the platform forward + card install (not under test here).
    with (
        patch.object(
            mock_hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.entity_availability._async_install_card",
            new_callable=AsyncMock,
        ),
    ):
        ok = await ea_async_setup_entry(mock_hass, combined)
    assert ok is True
    assert slot in mock_hass.data[DOMAIN], "setup must create the store slot"
    # Prove it would otherwise persist: seed a stale map.
    mock_hass.data[DOMAIN][slot][("stale",)] = "old-map"

    # Real unload: run the callbacks the entry registered (the __init__ lambda).
    await combined._async_process_on_unload(mock_hass)
    assert slot not in mock_hass.data[DOMAIN], (
        "the on_unload hook must POP the slot (gone, not emptied) so a reload "
        "starts fresh — guards the #155 survive-unload class"
    )


def test_missing_store_slot_degrades_safely(two_group_combined):
    """Defensive: if the store slot is gone (unload race), _dm_store falls back
    to a private dict and the sensor still returns a correct map — never
    resurrects stale shared state."""
    hass, combined, coords = two_group_combined
    hass.data[DOMAIN].pop(f"{DM_CACHE_PREFIX}c_1", None)  # slot removed
    s = _sensor(hass, combined, coords)
    # Still computes a correct value without raising.
    assert s.native_value == 3  # a1 + a2 + b1 representatives


# --- Parity on the NON-TRIVIAL map configs (collapse-active, split-row) ------
# The battery fixtures above exercise classic/collapse-off/divergent-NE only.
# These cover the two configs where the stored tuple's collapsed / udn_map /
# rep_of / rk_groups slots are NON-trivial — exactly where a shared-store slot
# swap would corrupt output and the trivial fixtures can't catch it.


def test_parity_collapse_active_shared_hit(mock_hass: HomeAssistant):
    """COLLAPSE-ACTIVE parity: two entities on ONE device in a collapse-active
    source group collapse to a single representative row (non-identity rep_of +
    udn_map). A second sensor served from the shared store must publish the
    IDENTICAL full attrs as the sensor that built the map — proving the restored
    collapsed/udn_map/rk_groups tuple is byte-identical across the shared hit,
    not a slot-swapped or rebuilt-divergent map.

    Mutation: drop the rep_of slot from the stored tuple / don't restore it on
    hit → the second sensor's entities_collapsed / row_members / total diverge
    from the first → deep-equal reds. (The udn_map slot only feeds friendly-name
    display, which is identical for this single-named-device fixture, so a
    udn_map swap alone would NOT red here; rep_of is the discriminating guard.)
    """
    _register_entity(mock_hass, "binary_sensor.dev1_a", "dev1")
    _register_entity(mock_hass, "binary_sensor.dev1_b", "dev1")
    _register_entity(mock_hass, "binary_sensor.solo", "dev2")
    ga = _group(
        "g_col",
        "Collapse Grp",
        ["binary_sensor.dev1_a", "binary_sensor.dev1_b", "binary_sensor.solo"],
        collapse=True,
    )
    gb = _group("g_other", "Other", ["binary_sensor.other1"])
    combined = _combined("c_col", "Collapse Combined", ["g_col", "g_other"])
    ca = _coord(
        mock_hass,
        ga,
        {
            "binary_sensor.dev1_a": DeviceState(
                entity_id="binary_sensor.dev1_a", is_offline=True, offline_since=T0
            ),
            "binary_sensor.dev1_b": DeviceState(
                entity_id="binary_sensor.dev1_b", is_offline=False
            ),
            "binary_sensor.solo": DeviceState(
                entity_id="binary_sensor.solo", is_offline=False
            ),
        },
    )
    cb = _coord(
        mock_hass,
        gb,
        {
            "binary_sensor.other1": DeviceState(
                entity_id="binary_sensor.other1", is_offline=False
            ),
        },
    )
    mock_hass.data[DOMAIN] = {"g_col": ca, "g_other": cb}
    _store(mock_hass, "c_col")

    s1 = _sensor(mock_hass, combined, [ca, cb])
    s2 = _sensor(mock_hass, combined, [ca, cb])
    s1.native_value  # prime the shared store (collapse build happens here)
    # Full-attrs byte-identical across build-sensor vs hit-sensor.
    assert s1.native_value == s2.native_value
    assert s1.extra_state_attributes == s2.extra_state_attributes
    # And collapse genuinely happened (non-trivial slots exercised): dev1_a+b
    # collapse to one row → 3 reps (dev1, solo, other1), not 4 raw entities.
    assert s1.native_value == 3


def test_parity_divergent_fingerprint_shared_hit(mock_hass: HomeAssistant):
    """DIVERGENT-FINGERPRINT parity: the SAME entity in two source groups with
    DIFFERENT config (one collapse-active, one not) produces a split
    ``eid::entry_id`` row — the exact case rk_groups' split-vs-union logic
    exists for. Second sensor served from the store must match the first's full
    attrs (incl. row_groups), proving the rk_groups side-output in the stored
    tuple is restored correctly, not a stale/foreign per-sensor value.

    Mutation: skip the rk_groups restore on a store hit → row_groups differs
    between the two sensors → deep-equal reds (this is the side-output the
    hoist newly shares, the genuinely-new risk).
    """
    _register_entity(mock_hass, "binary_sensor.shared", "devX")
    # Group A: collapse-active, includes the shared entity.
    ga = _group("g_dva", "Grp A", ["binary_sensor.shared"], collapse=True)
    # Group B: collapse-OFF, includes the SAME entity → divergent fingerprint
    # (udn differs) → the combined splits it into two rows keyed eid::entry_id.
    gb = _group("g_dvb", "Grp B", ["binary_sensor.shared"])
    combined = _combined("c_dv", "Divergent Combined", ["g_dva", "g_dvb"])
    ca = _coord(
        mock_hass,
        ga,
        {
            "binary_sensor.shared": DeviceState(
                entity_id="binary_sensor.shared", is_offline=False
            ),
        },
    )
    cb = _coord(
        mock_hass,
        gb,
        {
            "binary_sensor.shared": DeviceState(
                entity_id="binary_sensor.shared", is_offline=False
            ),
        },
    )
    mock_hass.data[DOMAIN] = {"g_dva": ca, "g_dvb": cb}
    _store(mock_hass, "c_dv")

    s1 = _sensor(mock_hass, combined, [ca, cb])
    s2 = _sensor(mock_hass, combined, [ca, cb])
    s1.native_value  # prime
    assert s1.native_value == s2.native_value
    # row_groups is the rk_groups-backed attr — must be identical across the
    # build-sensor and the store-hit sensor.
    rg1 = s1.extra_state_attributes.get("row_groups")
    rg2 = s2.extra_state_attributes.get("row_groups")
    assert rg1 == rg2
    # Full-attrs parity (catches any other slot swap on the divergent config).
    assert s1.extra_state_attributes == s2.extra_state_attributes
