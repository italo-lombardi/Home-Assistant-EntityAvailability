"""Tests for device-collapse (config option collapse_devices).

Collapse merges multiple entities of the same physical device into one across
every count/list/event, gated on both collapse_devices AND use_device_names.

Collapse is SOURCE-based, not value-based. The coarse bucket key is
``device_id::non_essential`` (see ``collapse_key``); within a bucket entities
merge by the compatibility of their ``(battery_source, signal_source)`` pairs —
i.e. WHICH battery/signal SENSOR feeds each entity, not the live reading. Per
axis two entities are compatible when the sources are equal OR at least one is
None (None = wildcard, merges). Two DISTINCT concrete sources on one axis (e.g.
two different battery sensors on one device) conflict and never merge. A merged
cluster's representative TIGHTENS (a meet): it absorbs a member's concrete source
on any axis it left None, so a both-None sibling can join a bound cluster via a
real concrete match rather than a wildcard bridge, deterministically and
independent of insertion order. Availability/MTBF/MTTR and area sensors are
intentionally NOT collapsed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.combined_sensor import (
    CombinedGroupSensor,
    CombinedOfflineCountSensor,
    CombinedOfflineEntitiesSensor,
    CombinedRecentlyOfflineSensor,
)
from custom_components.entity_availability.const import (
    CONF_BAD_STATES,
    CONF_COLLAPSE_DEVICES,
    CONF_ENTITIES,
    CONF_GROUP_NAME,
    CONF_USE_DEVICE_NAMES,
    DEFAULT_BAD_STATES,
    DOMAIN,
    EVENT_OFFLINE,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)
from custom_components.entity_availability.helpers import (
    _sources_compatible,
    _tighten,
    collapse_key,
    collapse_representatives,
    dedup_display_names,
    render_name_list,
)
from custom_components.entity_availability.models import DeviceState
from custom_components.entity_availability.sensor import (
    GroupSummarySensor,
    LowBatteryCountSensor,
    OfflineCountSensor,
    OfflineDevicesSensor,
    RecentlyOfflineSensor,
    RecentlyRecoveredSensor,
    StaleCountSensor,
)

_REG_ENTRY_ID = "collapse_test_entry"


def _ensure_reg_entry(hass: HomeAssistant) -> None:
    """Add the config entry the device registry links devices to (idempotent)."""
    if hass.config_entries.async_get_entry(_REG_ENTRY_ID) is None:
        MockConfigEntry(
            domain=DOMAIN, entry_id=_REG_ENTRY_ID, title="Collapse Group"
        ).add_to_hass(hass)


def _register_entity(
    hass: HomeAssistant, entity_id: str, device_id: str | None
) -> None:
    """Register entity_id in the registry, optionally linked to a device."""
    _ensure_reg_entry(hass)
    ent_reg = er.async_get(hass)
    domain, object_id = entity_id.split(".", 1)
    entry = ent_reg.async_get_or_create(
        domain, "test", object_id, suggested_object_id=object_id
    )
    if device_id is not None:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=_REG_ENTRY_ID,
            identifiers={(DOMAIN, device_id)},
            name=f"Device {device_id}",
        )
        ent_reg.async_update_entity(entry.entity_id, device_id=device.id)


def _get_device(hass: HomeAssistant, device_id: str):
    """Return the registry device for a DOMAIN identifier (version-agnostic).

    ``async_get_device(identifiers=...)`` is deprecated and raises on newer HA;
    ``async_get_device_by_identifier`` is absent on older HA. ``async_get_or_create``
    is idempotent on both and returns the existing device (created by
    ``_register_entity``) without mutating it when only identifiers are passed.
    """
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=_REG_ENTRY_ID, identifiers={(DOMAIN, device_id)}
    )


def _make_coordinator(
    hass: HomeAssistant,
    *,
    collapse: bool,
    use_device_names: bool,
    states: dict[str, DeviceState],
) -> EntityAvailabilityCoordinator:
    _ensure_reg_entry(hass)
    entry = hass.config_entries.async_get_entry(_REG_ENTRY_ID)
    hass.config_entries.async_update_entry(
        entry,
        data={
            CONF_GROUP_NAME: "Collapse Group",
            CONF_ENTITIES: list(states),
            CONF_BAD_STATES: DEFAULT_BAD_STATES,
            CONF_COLLAPSE_DEVICES: collapse,
            CONF_USE_DEVICE_NAMES: use_device_names,
        },
    )
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
    coord._device_states = states
    coord._entities = list(states)
    return coord


@pytest.fixture
def two_offline_one_device(mock_hass: HomeAssistant):
    """Two offline entities on the same device (identical key) + one on another device."""
    # Same device, same name (device name), same battery/signal -> collapse to 1.
    _register_entity(mock_hass, "binary_sensor.dev1_a", "dev1")
    _register_entity(mock_hass, "binary_sensor.dev1_b", "dev1")
    _register_entity(mock_hass, "binary_sensor.dev2", "dev2")
    return {
        "binary_sensor.dev1_a": DeviceState(
            entity_id="binary_sensor.dev1_a", is_offline=True
        ),
        "binary_sensor.dev1_b": DeviceState(
            entity_id="binary_sensor.dev1_b", is_offline=True
        ),
        "binary_sensor.dev2": DeviceState(
            entity_id="binary_sensor.dev2", is_offline=True
        ),
    }


class TestGate:
    """Collapse must be a no-op unless BOTH toggles are on."""

    def test_collapse_off_counts_per_entity(self, mock_hass, two_offline_one_device):
        coord = _make_coordinator(
            mock_hass,
            collapse=False,
            use_device_names=True,
            states=two_offline_one_device,
        )
        assert coord.collapse_active is False
        sensor = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        assert sensor.native_value == 3

    def test_use_device_names_off_disables_collapse(
        self, mock_hass, two_offline_one_device
    ):
        coord = _make_coordinator(
            mock_hass,
            collapse=True,
            use_device_names=False,
            states=two_offline_one_device,
        )
        assert coord.collapse_active is False
        sensor = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        assert sensor.native_value == 3


class TestCollapseCounts:
    def test_offline_count_collapses_same_device(
        self, mock_hass, two_offline_one_device
    ):
        coord = _make_coordinator(
            mock_hass,
            collapse=True,
            use_device_names=True,
            states=two_offline_one_device,
        )
        assert coord.collapse_active is True
        sensor = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        # dev1_a + dev1_b collapse -> 1 device; dev2 -> 1. Total 2, not 3.
        assert sensor.native_value == 2

    def test_group_summary_entities_collapsed_and_counts(
        self, mock_hass, two_offline_one_device
    ):
        coord = _make_coordinator(
            mock_hass,
            collapse=True,
            use_device_names=True,
            states=two_offline_one_device,
        )
        sensor = GroupSummarySensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        # entities_collapsed drops one of the dev1 pair; card renders it 1:1.
        assert len(attrs["entities_collapsed"]) == 2
        assert attrs["offline"] == 2
        assert len(attrs["offline_entities"]) == 2
        # Raw membership preserved for backward compat.
        assert len(attrs["entities"]) == 3
        # Count == rows the card renders.
        assert attrs["offline"] == len(attrs["entities_collapsed"])

    def test_row_members_emitted_for_collapsed_device(
        self, mock_hass, two_offline_one_device
    ):
        coord = _make_coordinator(
            mock_hass,
            collapse=True,
            use_device_names=True,
            states=two_offline_one_device,
        )
        sensor = GroupSummarySensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        row_members = attrs["row_members"]
        # dev1_a and dev1_b collapse to one rep; that rep has 2 members.
        reps_with_multiple = {k: v for k, v in row_members.items() if len(v) > 1}
        assert len(reps_with_multiple) == 1
        rep, members = next(iter(reps_with_multiple.items()))
        assert rep in ("binary_sensor.dev1_a", "binary_sensor.dev1_b")
        assert set(members) == {"binary_sensor.dev1_a", "binary_sensor.dev1_b"}
        # dev2 is a single-entity device — absent from row_members.
        assert "binary_sensor.dev2" not in row_members

    def test_row_members_representative_is_first(
        self, mock_hass, two_offline_one_device
    ):
        coord = _make_coordinator(
            mock_hass,
            collapse=True,
            use_device_names=True,
            states=two_offline_one_device,
        )
        sensor = GroupSummarySensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        row_members = attrs["row_members"]
        collapsed = attrs["entities_collapsed"]
        for rep in collapsed:
            if rep in row_members:
                assert row_members[rep][0] == rep, (
                    f"rep {rep} not first in its member list"
                )

    def test_row_members_empty_when_collapse_off(
        self, mock_hass, two_offline_one_device
    ):
        coord = _make_coordinator(
            mock_hass,
            collapse=False,
            use_device_names=True,
            states=two_offline_one_device,
        )
        sensor = GroupSummarySensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert attrs["row_members"] == {}

    def test_row_members_empty_when_use_device_names_off(
        self, mock_hass, two_offline_one_device
    ):
        coord = _make_coordinator(
            mock_hass,
            collapse=True,
            use_device_names=False,
            states=two_offline_one_device,
        )
        sensor = GroupSummarySensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert attrs["row_members"] == {}

    def test_row_members_in_unrecorded_attributes(
        self, mock_hass, two_offline_one_device
    ):
        coord = _make_coordinator(
            mock_hass,
            collapse=True,
            use_device_names=True,
            states=two_offline_one_device,
        )
        sensor = GroupSummarySensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        assert "row_members" in sensor._unrecorded_attributes

    def test_group_summary_native_value_collapses(
        self, mock_hass, two_offline_one_device
    ):
        coord = _make_coordinator(
            mock_hass,
            collapse=True,
            use_device_names=True,
            states=two_offline_one_device,
        )
        sensor = GroupSummarySensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        # Headline total collapses: dev1 pair -> 1, dev2 -> 1 = 2 (not raw 3).
        assert sensor.native_value == 2

    def test_group_summary_native_value_raw_when_off(
        self, mock_hass, two_offline_one_device
    ):
        coord = _make_coordinator(
            mock_hass,
            collapse=False,
            use_device_names=True,
            states=two_offline_one_device,
        )
        sensor = GroupSummarySensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        assert sensor.native_value == 3


class TestCrossCategoryCollapse:
    def test_device_with_offline_and_stale_siblings_counts_in_both(self, mock_hass):
        # Same device: one entity offline, another stale (both unsuppressed). The
        # device collapses to ONE row, but must appear in BOTH the offline and stale
        # categories — a real problem on a sibling is never hidden behind another.
        _register_entity(mock_hass, "binary_sensor.d_off", "shared")
        _register_entity(mock_hass, "binary_sensor.d_stale", "shared")
        states = {
            "binary_sensor.d_stale": DeviceState(
                entity_id="binary_sensor.d_stale", is_stale=True
            ),
            "binary_sensor.d_off": DeviceState(
                entity_id="binary_sensor.d_off", is_offline=True
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        off = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        off.hass = mock_hass
        stale = StaleCountSensor(coord, "G", "g", "collapse_test_entry")
        stale.hass = mock_hass
        # One physical device -> one collapsed row (worst-severity rep = offline)...
        assert coord.collapsed_entities() == ["binary_sensor.d_off"]
        # ...but it counts in BOTH categories (offline sibling + stale sibling).
        assert off.native_value == 1
        assert stale.native_value == 1

    def test_unsuppressed_member_wins_over_suppressed(self, mock_hass):
        # Same device-key: a SUPPRESSED offline entity (higher raw severity) must NOT
        # become the representative and mask an unsuppressed stale sibling. The
        # unsuppressed member is chosen so the real problem still surfaces.
        _register_entity(mock_hass, "binary_sensor.s_off", "supdev")
        _register_entity(mock_hass, "binary_sensor.s_stale", "supdev")
        states = {
            "binary_sensor.s_off": DeviceState(
                entity_id="binary_sensor.s_off", is_offline=True, is_suppressed=True
            ),
            "binary_sensor.s_stale": DeviceState(
                entity_id="binary_sensor.s_stale", is_stale=True
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        # Representative is the unsuppressed stale entity, not the suppressed offline.
        assert coord.collapsed_entities() == ["binary_sensor.s_stale"]
        stale = StaleCountSensor(coord, "G", "g", "collapse_test_entry")
        stale.hass = mock_hass
        off = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        off.hass = mock_hass
        # The genuine (unsuppressed) stale problem is still reported.
        assert stale.native_value == 1
        # The suppressed offline is not counted (suppressed never counts).
        assert off.native_value == 0


class TestNoDeviceId:
    def test_entities_without_device_never_collapse(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.nodev_a", None)
        _register_entity(mock_hass, "binary_sensor.nodev_b", None)
        states = {
            "binary_sensor.nodev_a": DeviceState(
                entity_id="binary_sensor.nodev_a", is_offline=True
            ),
            "binary_sensor.nodev_b": DeviceState(
                entity_id="binary_sensor.nodev_b", is_offline=True
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        sensor = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        # No device_id -> each stays its own row/count.
        assert sensor.native_value == 2


class TestDifferentBatteryNoCollapse:
    def test_same_device_distinct_battery_source_stays_separate(self, mock_hass):
        # Two DISTINCT concrete battery sources on one device conflict on the battery
        # axis -> never merge -> 2 rows. (Value-based splitting is gone; provenance
        # is what splits now.)
        _register_entity(mock_hass, "binary_sensor.bat_a", "batdev")
        _register_entity(mock_hass, "binary_sensor.bat_b", "batdev")
        states = {
            "binary_sensor.bat_a": DeviceState(
                entity_id="binary_sensor.bat_a",
                is_offline=True,
                battery_source="sensor.bat_a",
            ),
            "binary_sensor.bat_b": DeviceState(
                entity_id="binary_sensor.bat_b",
                is_offline=True,
                battery_source="sensor.bat_b",
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        sensor = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        # Distinct battery sources -> conflict on battery axis -> no merge.
        assert sensor.native_value == 2

    def test_same_device_same_battery_source_merges(self, mock_hass):
        # Same concrete battery source on both -> compatible -> one row.
        _register_entity(mock_hass, "binary_sensor.sb_a", "sbdev")
        _register_entity(mock_hass, "binary_sensor.sb_b", "sbdev")
        states = {
            "binary_sensor.sb_a": DeviceState(
                entity_id="binary_sensor.sb_a",
                is_offline=True,
                battery_source="sensor.shared_bat",
            ),
            "binary_sensor.sb_b": DeviceState(
                entity_id="binary_sensor.sb_b",
                is_offline=True,
                battery_source="sensor.shared_bat",
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        sensor = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        assert sensor.native_value == 1

    def test_same_device_one_battery_source_none_merges(self, mock_hass):
        # One concrete battery source + one None (wildcard) -> compatible -> one row.
        _register_entity(mock_hass, "binary_sensor.wb_a", "wbdev")
        _register_entity(mock_hass, "binary_sensor.wb_b", "wbdev")
        states = {
            "binary_sensor.wb_a": DeviceState(
                entity_id="binary_sensor.wb_a",
                is_offline=True,
                battery_source="sensor.wb_bat",
            ),
            "binary_sensor.wb_b": DeviceState(
                entity_id="binary_sensor.wb_b",
                is_offline=True,
                battery_source=None,
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        sensor = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        assert sensor.native_value == 1


class TestNonEssentialSeparation:
    def test_essential_and_ne_never_merge(self, mock_hass):
        # Same device, but one essential and one non-essential -> must stay separate
        # so an essential offline entity is never hidden behind an NE representative.
        _register_entity(mock_hass, "binary_sensor.ne_ess", "nedev")
        _register_entity(mock_hass, "binary_sensor.ne_non", "nedev")
        states = {
            "binary_sensor.ne_ess": DeviceState(
                entity_id="binary_sensor.ne_ess", is_offline=True
            ),
            "binary_sensor.ne_non": DeviceState(
                entity_id="binary_sensor.ne_non",
                is_offline=True,
                is_non_essential=True,
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        sensor = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        # Essential offline count stays 1 (NE not merged into it).
        assert sensor.native_value == 1


class TestLowBatteryCollapse:
    def test_low_battery_count_collapses(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.lb_a", "lbdev")
        _register_entity(mock_hass, "binary_sensor.lb_b", "lbdev")
        states = {
            "binary_sensor.lb_a": DeviceState(
                entity_id="binary_sensor.lb_a", is_low_battery=True, battery_level=5
            ),
            "binary_sensor.lb_b": DeviceState(
                entity_id="binary_sensor.lb_b", is_low_battery=True, battery_level=5
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        sensor = LowBatteryCountSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        assert sensor.native_value == 1


class TestAllHealthyCollapse:
    def test_all_online_device_collapses_to_one_total(self, mock_hass):
        # Two healthy (green) entities on one device -> total collapses to 1.
        # Exercises the green-severity branch and the identity-representative path.
        _register_entity(mock_hass, "binary_sensor.ok_a", "okdev")
        _register_entity(mock_hass, "binary_sensor.ok_b", "okdev")
        states = {
            "binary_sensor.ok_a": DeviceState(entity_id="binary_sensor.ok_a"),
            "binary_sensor.ok_b": DeviceState(entity_id="binary_sensor.ok_b"),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        sensor = GroupSummarySensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        assert len(attrs["entities_collapsed"]) == 1
        assert attrs["total_entities"] == 1
        assert attrs["online"] == 1
        assert attrs["offline"] == 0


class TestRepresentativeReassignment:
    def test_later_worse_member_becomes_representative(self, mock_hass):
        # Insertion order: other-device green, same-device green, same-device offline.
        # The offline entity (worst), seen last, reassigns its key's representative;
        # the reassignment loop must skip the unrelated other-device rep (false branch).
        _register_entity(mock_hass, "binary_sensor.re_other", "otherdev")
        for suffix in ("g1", "off"):
            _register_entity(mock_hass, f"binary_sensor.re_{suffix}", "redev")
        states = {
            "binary_sensor.re_other": DeviceState(entity_id="binary_sensor.re_other"),
            "binary_sensor.re_g1": DeviceState(entity_id="binary_sensor.re_g1"),
            "binary_sensor.re_off": DeviceState(
                entity_id="binary_sensor.re_off", is_offline=True
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        # redev collapses to its offline representative; otherdev stays its own row.
        collapsed = coord.collapsed_entities()
        assert "binary_sensor.re_off" in collapsed
        assert "binary_sensor.re_other" in collapsed
        assert "binary_sensor.re_g1" not in collapsed
        assert len(collapsed) == 2
        off = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        off.hass = mock_hass
        assert off.native_value == 1


def _make_group_coord(
    hass: HomeAssistant,
    entry_id: str,
    states: dict[str, DeviceState],
    *,
    collapse: bool = True,
    use_device_names: bool = True,
) -> EntityAvailabilityCoordinator:
    """Build a group coordinator under its own config entry (collapse-on by default)."""
    if hass.config_entries.async_get_entry(entry_id) is None:
        MockConfigEntry(
            domain=DOMAIN,
            entry_id=entry_id,
            title=entry_id,
            data={
                CONF_GROUP_NAME: entry_id,
                CONF_ENTITIES: list(states),
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COLLAPSE_DEVICES: collapse,
                CONF_USE_DEVICE_NAMES: use_device_names,
            },
        ).add_to_hass(hass)
    entry = hass.config_entries.async_get_entry(entry_id)
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
    coord._device_states = states
    coord._entities = list(states)
    return coord


class TestCombinedReCollapse:
    def test_device_split_across_two_groups_counted_once(self, mock_hass):
        # One physical device "shared" has an entity in each of two groups. The
        # combined view must re-collapse across groups so the device counts once.
        _register_entity(mock_hass, "binary_sensor.shared_g1", "shared")
        _register_entity(mock_hass, "binary_sensor.shared_g2", "shared")
        states_a = {
            "binary_sensor.shared_g1": DeviceState(
                entity_id="binary_sensor.shared_g1", is_offline=True
            )
        }
        states_b = {
            "binary_sensor.shared_g2": DeviceState(
                entity_id="binary_sensor.shared_g2", is_offline=True
            )
        }
        coord_a = _make_group_coord(mock_hass, "grp_a", states_a)
        coord_b = _make_group_coord(mock_hass, "grp_b", states_b)
        mock_hass.data[DOMAIN] = {"grp_a": coord_a, "grp_b": coord_b}

        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="combined_x", title="Combined X"
        )
        count = CombinedOfflineCountSensor(
            mock_hass, combined_entry, "Combined X", "combined_x", ["grp_a", "grp_b"]
        )
        # Same device across both groups -> one offline device, not two.
        assert count.native_value == 1

        summary = CombinedGroupSensor(
            mock_hass, combined_entry, "Combined X", "combined_x", ["grp_a", "grp_b"]
        )
        attrs = summary.extra_state_attributes
        assert len(attrs["entities_collapsed"]) == 1
        assert attrs["offline"] == 1
        # native_value (total) also collapsed across groups.
        assert summary.native_value == 1
        # Second read within the same tick hits the per-tick memo (cache hit path).
        assert summary.native_value == 1

    def test_combined_cross_category_counts_in_both(self, mock_hass):
        # One device split across two groups: offline member in A, stale member in B.
        # Combined must count the device in BOTH offline and stale, not just one.
        _register_entity(mock_hass, "binary_sensor.x_off", "xdev")
        _register_entity(mock_hass, "binary_sensor.x_stale", "xdev")
        coord_a = _make_group_coord(
            mock_hass,
            "grp_ca",
            {
                "binary_sensor.x_off": DeviceState(
                    entity_id="binary_sensor.x_off", is_offline=True
                )
            },
        )
        coord_b = _make_group_coord(
            mock_hass,
            "grp_cb",
            {
                "binary_sensor.x_stale": DeviceState(
                    entity_id="binary_sensor.x_stale", is_stale=True
                )
            },
        )
        mock_hass.data[DOMAIN] = {"grp_ca": coord_a, "grp_cb": coord_b}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="combined_cc", title="Combined CC"
        )
        summary = CombinedGroupSensor(
            mock_hass,
            combined_entry,
            "Combined CC",
            "combined_cc",
            ["grp_ca", "grp_cb"],
        )
        attrs = summary.extra_state_attributes
        assert len(attrs["entities_collapsed"]) == 1  # one physical device
        assert attrs["offline"] == 1
        assert attrs["stale"] == 1

    def test_combined_row_members_emitted_for_cross_group_device(self, mock_hass):
        # Same device split across two groups -> combined collapses to one row;
        # row_members must list both entity_ids under the representative.
        _register_entity(mock_hass, "binary_sensor.rm_off", "rmdev")
        _register_entity(mock_hass, "binary_sensor.rm_stale", "rmdev")
        coord_a = _make_group_coord(
            mock_hass,
            "grp_rm_a",
            {
                "binary_sensor.rm_off": DeviceState(
                    entity_id="binary_sensor.rm_off", is_offline=True
                )
            },
        )
        coord_b = _make_group_coord(
            mock_hass,
            "grp_rm_b",
            {
                "binary_sensor.rm_stale": DeviceState(
                    entity_id="binary_sensor.rm_stale", is_stale=True
                )
            },
        )
        mock_hass.data[DOMAIN] = {"grp_rm_a": coord_a, "grp_rm_b": coord_b}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="combined_rm", title="Combined RM"
        )
        summary = CombinedGroupSensor(
            mock_hass,
            combined_entry,
            "Combined RM",
            "combined_rm",
            ["grp_rm_a", "grp_rm_b"],
        )
        attrs = summary.extra_state_attributes
        row_members = attrs["row_members"]
        assert len(row_members) == 1
        members = next(iter(row_members.values()))
        assert set(members) == {"binary_sensor.rm_off", "binary_sensor.rm_stale"}
        # rm_off is offline (worst severity) so it must be the representative → first.
        assert members[0] == "binary_sensor.rm_off"
        assert "row_members" in summary._unrecorded_attributes

    def test_combined_suppression_unsuppressed_wins(self, mock_hass):
        # Entity in two groups: suppressed in A, unsuppressed in B.
        # Combined must show it as active (unsuppressed-wins) not order-dependent.
        _register_entity(mock_hass, "binary_sensor.sup_shared", "supdev")

        suppressed_state = DeviceState(
            entity_id="binary_sensor.sup_shared",
            is_offline=True,
            is_suppressed=True,
        )
        active_state = DeviceState(
            entity_id="binary_sensor.sup_shared",
            is_offline=True,
            is_suppressed=False,
        )
        # Group A: suppressed first, Group B: active — combined must be active.
        coord_a = _make_group_coord(
            mock_hass, "grp_sup_a", {"binary_sensor.sup_shared": suppressed_state}
        )
        coord_b = _make_group_coord(
            mock_hass, "grp_sup_b", {"binary_sensor.sup_shared": active_state}
        )
        mock_hass.data[DOMAIN] = {"grp_sup_a": coord_a, "grp_sup_b": coord_b}
        entry = MockConfigEntry(domain=DOMAIN, entry_id="combined_sup", title="Sup")
        summary = CombinedGroupSensor(
            mock_hass, entry, "Sup", "sup", ["grp_sup_a", "grp_sup_b"]
        )
        attrs = summary.extra_state_attributes
        # Entity is offline (not suppressed) in combined — should count in offline.
        assert attrs["offline"] == 1
        assert attrs["suppressed"] == 0

        # Reverse order: active first, suppressed second — same result.
        mock_hass.data[DOMAIN] = {"grp_sup_b": coord_b, "grp_sup_a": coord_a}
        entry2 = MockConfigEntry(domain=DOMAIN, entry_id="combined_sup2", title="Sup2")
        summary2 = CombinedGroupSensor(
            mock_hass, entry2, "Sup2", "sup2", ["grp_sup_b", "grp_sup_a"]
        )
        attrs2 = summary2.extra_state_attributes
        assert attrs2["offline"] == 1
        assert attrs2["suppressed"] == 0

    def test_combined_suppression_expiry_longer_wins(self, mock_hass):
        # Both groups have entity suppressed but with different expiries.
        # The longer (later) suppress_until must win regardless of group order.
        _register_entity(mock_hass, "binary_sensor.exp_shared", "expdev")
        t_short = datetime(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc)
        t_long = datetime(2026, 8, 25, 18, 0, 0, tzinfo=timezone.utc)
        state_short = DeviceState(
            entity_id="binary_sensor.exp_shared",
            is_suppressed=True,
            suppress_until=t_short,
        )
        state_long = DeviceState(
            entity_id="binary_sensor.exp_shared",
            is_suppressed=True,
            suppress_until=t_long,
        )
        coord_a = _make_group_coord(
            mock_hass,
            "grp_exp_a",
            {"binary_sensor.exp_shared": state_short},
            use_device_names=False,
        )
        coord_b = _make_group_coord(
            mock_hass,
            "grp_exp_b",
            {"binary_sensor.exp_shared": state_long},
            use_device_names=False,
        )
        mock_hass.data[DOMAIN] = {"grp_exp_a": coord_a, "grp_exp_b": coord_b}
        entry = MockConfigEntry(
            domain=DOMAIN, entry_id="combined_exp", title="Combined Exp"
        )
        summary = CombinedGroupSensor(
            mock_hass, entry, "Combined Exp", "combined_exp", ["grp_exp_a", "grp_exp_b"]
        )
        attrs = summary.extra_state_attributes
        # Longer expiry wins — suppressed_until should reflect t_long.
        assert attrs["suppressed"] == 1
        assert (
            attrs["suppressed_until"].get("binary_sensor.exp_shared")
            == t_long.isoformat()
        )

        # Reverse order — same result.
        summary2 = CombinedGroupSensor(
            mock_hass, entry, "Combined Exp", "combined_exp", ["grp_exp_b", "grp_exp_a"]
        )
        attrs2 = summary2.extra_state_attributes
        assert (
            attrs2["suppressed_until"].get("binary_sensor.exp_shared")
            == t_long.isoformat()
        )

    def test_mixed_toggle_partial_collapse(self, mock_hass):
        # Combined shows 3 rows total (1 from A + 2 from B).
        for suffix in ("a1", "a2", "b1", "b2"):
            _register_entity(mock_hass, f"binary_sensor.mt_{suffix}", "mtdev")
        coord_a = _make_group_coord(
            mock_hass,
            "grp_ma",
            {
                "binary_sensor.mt_a1": DeviceState(
                    entity_id="binary_sensor.mt_a1", is_offline=True
                ),
                "binary_sensor.mt_a2": DeviceState(
                    entity_id="binary_sensor.mt_a2", is_offline=True
                ),
            },
            use_device_names=True,
        )
        coord_b = _make_group_coord(
            mock_hass,
            "grp_mb",
            {
                "binary_sensor.mt_b1": DeviceState(
                    entity_id="binary_sensor.mt_b1", is_offline=True
                ),
                "binary_sensor.mt_b2": DeviceState(
                    entity_id="binary_sensor.mt_b2", is_offline=True
                ),
            },
            use_device_names=False,
        )
        mock_hass.data[DOMAIN] = {"grp_ma": coord_a, "grp_mb": coord_b}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="combined_mt", title="Combined MT"
        )
        summary = CombinedGroupSensor(
            mock_hass,
            combined_entry,
            "Combined MT",
            "combined_mt",
            ["grp_ma", "grp_mb"],
        )
        attrs = summary.extra_state_attributes
        # A's 2 collapse to 1 (use_device_names=True); B's 2 stay separate -> 3 rows.
        assert attrs["offline"] == 3
        assert len(attrs["entities_collapsed"]) == 3
        assert summary.native_value == 3


class TestCombinedSmartDedup:
    """Same entity_id in multiple groups: merge when config identical, separate rows when different."""

    def test_same_entity_same_config_merges(self, mock_hass):
        # entity E in both groups with identical config -> one row, counted once.
        _register_entity(mock_hass, "binary_sensor.shared", None)
        states = {
            "binary_sensor.shared": DeviceState(
                entity_id="binary_sensor.shared", is_offline=True
            )
        }
        coord_a = _make_group_coord(mock_hass, "grp_sd_a", states)
        coord_b = _make_group_coord(mock_hass, "grp_sd_b", states)
        mock_hass.data[DOMAIN] = {"grp_sd_a": coord_a, "grp_sd_b": coord_b}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="combined_sd", title="Combined SD"
        )
        count = CombinedOfflineCountSensor(
            mock_hass,
            combined_entry,
            "Combined SD",
            "combined_sd",
            ["grp_sd_a", "grp_sd_b"],
        )
        assert count.native_value == 1

    def test_same_entity_different_bad_states_keeps_separate(self, mock_hass):
        # Same entity_id but group A treats "unavailable" as offline, group B doesn't.
        # Different bad_states -> separate rows -> counted twice.
        _register_entity(mock_hass, "binary_sensor.diff_bs", None)
        d = DeviceState(entity_id="binary_sensor.diff_bs", is_offline=True)
        if mock_hass.config_entries.async_get_entry("grp_bs_a") is None:
            MockConfigEntry(
                domain=DOMAIN,
                entry_id="grp_bs_a",
                title="grp_bs_a",
                data={
                    CONF_GROUP_NAME: "grp_bs_a",
                    CONF_ENTITIES: ["binary_sensor.diff_bs"],
                    CONF_BAD_STATES: ["unavailable", "unknown"],
                    CONF_COLLAPSE_DEVICES: False,
                    CONF_USE_DEVICE_NAMES: False,
                },
            ).add_to_hass(mock_hass)
        if mock_hass.config_entries.async_get_entry("grp_bs_b") is None:
            MockConfigEntry(
                domain=DOMAIN,
                entry_id="grp_bs_b",
                title="grp_bs_b",
                data={
                    CONF_GROUP_NAME: "grp_bs_b",
                    CONF_ENTITIES: ["binary_sensor.diff_bs"],
                    CONF_BAD_STATES: ["unavailable"],  # different
                    CONF_COLLAPSE_DEVICES: False,
                    CONF_USE_DEVICE_NAMES: False,
                },
            ).add_to_hass(mock_hass)
        with patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ):
            coord_a = EntityAvailabilityCoordinator(
                mock_hass, mock_hass.config_entries.async_get_entry("grp_bs_a")
            )
            coord_b = EntityAvailabilityCoordinator(
                mock_hass, mock_hass.config_entries.async_get_entry("grp_bs_b")
            )
        coord_a._device_states = {"binary_sensor.diff_bs": d}
        coord_a._entities = ["binary_sensor.diff_bs"]
        coord_b._device_states = {"binary_sensor.diff_bs": d}
        coord_b._entities = ["binary_sensor.diff_bs"]
        mock_hass.data[DOMAIN] = {"grp_bs_a": coord_a, "grp_bs_b": coord_b}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="combined_bs", title="Combined BS"
        )
        count = CombinedOfflineCountSensor(
            mock_hass,
            combined_entry,
            "Combined BS",
            "combined_bs",
            ["grp_bs_a", "grp_bs_b"],
        )
        assert count.native_value == 2

    def test_same_entity_different_use_device_names_keeps_separate(self, mock_hass):
        # Same entity_id: group A has use_device_names=True, group B has False.
        # Different config -> 2 separate rows in combined view.
        _register_entity(mock_hass, "binary_sensor.udn_diff", None)
        d = DeviceState(entity_id="binary_sensor.udn_diff", is_offline=True)
        coord_a = _make_group_coord(
            mock_hass, "grp_udn_a", {"binary_sensor.udn_diff": d}, use_device_names=True
        )
        coord_b = _make_group_coord(
            mock_hass,
            "grp_udn_b",
            {"binary_sensor.udn_diff": d},
            use_device_names=False,
        )
        mock_hass.data[DOMAIN] = {"grp_udn_a": coord_a, "grp_udn_b": coord_b}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="combined_udn", title="Combined UDN"
        )
        count = CombinedOfflineCountSensor(
            mock_hass,
            combined_entry,
            "Combined UDN",
            "combined_udn",
            ["grp_udn_a", "grp_udn_b"],
        )
        assert count.native_value == 2


class TestOnlineWithSuppressedSibling:
    def test_online_not_undercounted_by_suppressed_sibling(self, mock_hass):
        # Same device-key: sibling A online (unsuppressed), sibling C suppressed.
        # online must NOT be computed by subtraction (total - suppressed - ...),
        # which would wrongly yield 0; the device is online via its rep A.
        _register_entity(mock_hass, "binary_sensor.ol_a", "oldev")
        _register_entity(mock_hass, "binary_sensor.ol_c", "oldev")
        states = {
            "binary_sensor.ol_a": DeviceState(entity_id="binary_sensor.ol_a"),
            "binary_sensor.ol_c": DeviceState(
                entity_id="binary_sensor.ol_c", is_suppressed=True
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        sensor = GroupSummarySensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        attrs = sensor.extra_state_attributes
        # Representative is the unsuppressed online A; device is online, not 0.
        assert attrs["online"] == 1
        assert attrs["total_entities"] == 1


class TestSuppressInvalidatesCollapse:
    def test_suppress_updates_collapse_immediately(self, mock_hass):
        # Suppress/unsuppress mutate state without a coordinator tick; the collapse
        # memo must invalidate so counts reflect the new suppression at once.
        _register_entity(mock_hass, "binary_sensor.si_off", "sidev")
        _register_entity(mock_hass, "binary_sensor.si_stale", "sidev")
        states = {
            "binary_sensor.si_off": DeviceState(
                entity_id="binary_sensor.si_off", is_offline=True
            ),
            "binary_sensor.si_stale": DeviceState(
                entity_id="binary_sensor.si_stale", is_stale=True
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        off = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        off.hass = mock_hass
        gen0 = coord.collapse_generation
        assert off.native_value == 1  # primes the memo
        # Suppress the offline member -> memo must rebuild, offline drops to 0.
        coord.suppress_entity("binary_sensor.si_off")
        assert coord.collapse_generation > gen0
        assert off.native_value == 0
        # Unsuppress -> offline back to 1.
        coord.unsuppress_entity("binary_sensor.si_off")
        assert off.native_value == 1


class TestEventCollapse:
    """Event payloads (*_count / *_entities) are built from the collapse-aware
    helpers, so a multi-entity device fires ONE collapsed count/list per event."""

    def _coord(self, mock_hass, states):
        return _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )

    def test_offline_event_payload_collapses(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.ev_o1", "evdev")
        _register_entity(mock_hass, "binary_sensor.ev_o2", "evdev")
        coord = self._coord(
            mock_hass,
            {
                "binary_sensor.ev_o1": DeviceState(
                    entity_id="binary_sensor.ev_o1", is_offline=True
                ),
                "binary_sensor.ev_o2": DeviceState(
                    entity_id="binary_sensor.ev_o2", is_offline=True
                ),
            },
        )
        # _offline_entity_ids feeds offline_count / offline_entities on EVENT_OFFLINE.
        ids = coord._offline_entity_ids()
        assert len(ids) == 1

    def test_low_battery_event_payload_collapses(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.ev_b1", "evbdev")
        _register_entity(mock_hass, "binary_sensor.ev_b2", "evbdev")
        coord = self._coord(
            mock_hass,
            {
                "binary_sensor.ev_b1": DeviceState(
                    entity_id="binary_sensor.ev_b1",
                    is_low_battery=True,
                    battery_level=5,
                ),
                "binary_sensor.ev_b2": DeviceState(
                    entity_id="binary_sensor.ev_b2",
                    is_low_battery=True,
                    battery_level=5,
                ),
            },
        )
        ids = coord._low_battery_entity_ids()
        assert len(ids) == 1

    def test_stale_event_payload_collapses(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.ev_s1", "evsdev")
        _register_entity(mock_hass, "binary_sensor.ev_s2", "evsdev")
        coord = self._coord(
            mock_hass,
            {
                "binary_sensor.ev_s1": DeviceState(
                    entity_id="binary_sensor.ev_s1", is_stale=True
                ),
                "binary_sensor.ev_s2": DeviceState(
                    entity_id="binary_sensor.ev_s2", is_stale=True
                ),
            },
        )
        ids = coord._stale_entity_ids()
        assert len(ids) == 1

    def test_poor_signal_event_payload_collapses(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.ev_p1", "evpdev")
        _register_entity(mock_hass, "binary_sensor.ev_p2", "evpdev")
        coord = self._coord(
            mock_hass,
            {
                "binary_sensor.ev_p1": DeviceState(
                    entity_id="binary_sensor.ev_p1",
                    signal_quality="poor",
                    signal_level=-95,
                    signal_unit="dBm",
                ),
                "binary_sensor.ev_p2": DeviceState(
                    entity_id="binary_sensor.ev_p2",
                    signal_quality="poor",
                    signal_level=-95,
                    signal_unit="dBm",
                ),
            },
        )
        ids = coord._poor_signal_entity_ids()
        assert len(ids) == 1

    def test_offline_event_not_collapsed_when_gate_off(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.ev_g1", "evgdev")
        _register_entity(mock_hass, "binary_sensor.ev_g2", "evgdev")
        coord = _make_coordinator(
            mock_hass,
            collapse=False,
            use_device_names=True,
            states={
                "binary_sensor.ev_g1": DeviceState(
                    entity_id="binary_sensor.ev_g1", is_offline=True
                ),
                "binary_sensor.ev_g2": DeviceState(
                    entity_id="binary_sensor.ev_g2", is_offline=True
                ),
            },
        )
        # Collapse off -> event payload stays per-entity.
        assert len(coord._offline_entity_ids()) == 2


class TestEventFirePayload:
    async def test_offline_event_trigger_id_with_collapsed_list(self, mock_hass):
        """EVENT_OFFLINE fires with the TRIGGER entity_id (documented), while its
        offline_entities payload is device-collapsed to one representative."""
        _ensure_reg_entry(mock_hass)
        _register_entity(mock_hass, "binary_sensor.fe_a", "fedev")
        _register_entity(mock_hass, "binary_sensor.fe_b", "fedev")
        mock_hass.states.async_set("binary_sensor.fe_a", STATE_UNAVAILABLE)
        mock_hass.states.async_set("binary_sensor.fe_b", STATE_UNAVAILABLE)
        entry = mock_hass.config_entries.async_get_entry(_REG_ENTRY_ID)
        mock_hass.config_entries.async_update_entry(
            entry,
            data={
                CONF_GROUP_NAME: "Collapse Group",
                CONF_ENTITIES: ["binary_sensor.fe_a", "binary_sensor.fe_b"],
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COLLAPSE_DEVICES: True,
                CONF_USE_DEVICE_NAMES: True,
            },
        )
        with patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ):
            coord = EntityAvailabilityCoordinator(mock_hass, entry)
            coord._last_update = None

            events = []
            mock_hass.bus.async_listen(EVENT_OFFLINE, lambda e: events.append(e))

            # First tick starts cooldown; force it past the window and tick again.
            await coord._async_update_data()
            for d in coord.device_states.values():
                d.cooldown_start = datetime.now(timezone.utc) - timedelta(seconds=61)
            await coord._async_update_data()
            await mock_hass.async_block_till_done()

        # Both entities transitioned -> two fires, each with its own trigger id.
        fired_ids = {e.data["entity_id"] for e in events}
        assert fired_ids == {"binary_sensor.fe_a", "binary_sensor.fe_b"}
        # ...but every payload's offline_entities is collapsed to ONE device row.
        for e in events:
            assert len(e.data["offline_entities"]) == 1
            assert e.data["offline_count"] == 1


class TestCollapseKeyShape:
    """collapse_key is the coarse device_id::non_essential bucket (source-based
    merge decided later by the meet); it deliberately omits live values/sources."""

    def test_key_is_device_id_and_non_essential(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.k_ess", "kdev")
        _register_entity(mock_hass, "binary_sensor.k_ne", "kdev")
        d_ess = DeviceState(entity_id="binary_sensor.k_ess", is_non_essential=False)
        d_ne = DeviceState(entity_id="binary_sensor.k_ne", is_non_essential=True)
        key_ess = collapse_key(mock_hass, d_ess)
        key_ne = collapse_key(mock_hass, d_ne)
        # Two parts only: <device_id>::<non_essential>.
        assert key_ess.endswith("::False")
        assert key_ne.endswith("::True")
        # Same device, different tier -> different key (never merge across tiers).
        assert key_ess.split("::")[0] == key_ne.split("::")[0]
        assert key_ess != key_ne

    def test_key_ignores_battery_and_signal_values(self, mock_hass):
        # Live battery/signal values must NOT enter the key (no wobble-driven churn).
        _register_entity(mock_hass, "binary_sensor.k_v1", "kvdev")
        _register_entity(mock_hass, "binary_sensor.k_v2", "kvdev")
        d1 = DeviceState(
            entity_id="binary_sensor.k_v1", battery_level=10, signal_level=-70
        )
        d2 = DeviceState(
            entity_id="binary_sensor.k_v2", battery_level=90, signal_level=-40
        )
        # Different values, same device+tier -> identical bucket key.
        assert collapse_key(mock_hass, d1).endswith("::False")
        assert (
            collapse_key(mock_hass, d1).split("::")[0]
            == collapse_key(mock_hass, d2).split("::")[0]
        )

    def test_key_none_when_no_device(self, mock_hass):
        # Device-less entity -> None (never collapses, own row/count).
        _register_entity(mock_hass, "binary_sensor.k_nodev", None)
        d = DeviceState(entity_id="binary_sensor.k_nodev")
        assert collapse_key(mock_hass, d) is None

    def test_key_none_when_not_registered(self, mock_hass):
        # Entity not in the registry -> no entry -> None.
        d = DeviceState(entity_id="binary_sensor.k_unregistered")
        assert collapse_key(mock_hass, d) is None


_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestRecentlyOfflineCollapse:
    """RecentlyOfflineSensor must device-collapse and sort like the offline sensor.

    Regression guard for the duplicate-display-name bug: a device exposing two
    monitored entities (or shown under two names) previously appeared twice.
    """

    def _sensor(self, mock_hass, states, *, collapse, use_device_names):
        coord = _make_coordinator(
            mock_hass,
            collapse=collapse,
            use_device_names=use_device_names,
            states=states,
        )
        sensor = RecentlyOfflineSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        return sensor

    def _two_entities_one_device(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.ro_a", "rodev")
        _register_entity(mock_hass, "binary_sensor.ro_b", "rodev")
        return {
            "binary_sensor.ro_a": DeviceState(
                entity_id="binary_sensor.ro_a",
                is_offline=True,
                recently_offline_at=_NOW - timedelta(minutes=1),
            ),
            "binary_sensor.ro_b": DeviceState(
                entity_id="binary_sensor.ro_b",
                is_offline=True,
                recently_offline_at=_NOW - timedelta(minutes=1),
            ),
        }

    def test_collapse_on_one_row(self, mock_hass):
        sensor = self._sensor(
            mock_hass,
            self._two_entities_one_device(mock_hass),
            collapse=True,
            use_device_names=True,
        )
        with patch("custom_components.entity_availability.sensor.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            sensor._refresh_cache()
            attrs = sensor.extra_state_attributes
        assert attrs["count"] == 1

    def test_collapse_off_two_rows(self, mock_hass):
        sensor = self._sensor(
            mock_hass,
            self._two_entities_one_device(mock_hass),
            collapse=False,
            use_device_names=True,
        )
        with patch("custom_components.entity_availability.sensor.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            sensor._refresh_cache()
            attrs = sensor.extra_state_attributes
        assert attrs["count"] == 2

    def test_sorted_by_display_name(self, mock_hass):
        # Insertion order (z, a) must NOT drive output; name-sort → a before z.
        _register_entity(mock_hass, "binary_sensor.zzz", "zdev")
        _register_entity(mock_hass, "binary_sensor.aaa", "adev")
        mock_hass.states.async_set(
            "binary_sensor.zzz", STATE_UNAVAILABLE, {"friendly_name": "Zulu"}
        )
        mock_hass.states.async_set(
            "binary_sensor.aaa", STATE_UNAVAILABLE, {"friendly_name": "Alpha"}
        )
        states = {
            "binary_sensor.zzz": DeviceState(
                entity_id="binary_sensor.zzz",
                is_offline=True,
                recently_offline_at=_NOW - timedelta(minutes=1),
            ),
            "binary_sensor.aaa": DeviceState(
                entity_id="binary_sensor.aaa",
                is_offline=True,
                recently_offline_at=_NOW - timedelta(minutes=1),
            ),
        }
        # use_device_names=False → friendly_name drives the sort key.
        sensor = self._sensor(mock_hass, states, collapse=False, use_device_names=False)
        with patch("custom_components.entity_availability.sensor.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            value = sensor.native_value
        assert value == "Alpha, Zulu"


class TestRecentlyRecoveredCollapse:
    """RecentlyRecoveredSensor must device-collapse the recovered list too."""

    def test_collapse_on_one_row(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.rr_a", "rrdev")
        _register_entity(mock_hass, "binary_sensor.rr_b", "rrdev")
        states = {
            "binary_sensor.rr_a": DeviceState(
                entity_id="binary_sensor.rr_a",
                is_offline=False,
                last_recovery=_NOW - timedelta(minutes=1),
            ),
            "binary_sensor.rr_b": DeviceState(
                entity_id="binary_sensor.rr_b",
                is_offline=False,
                last_recovery=_NOW - timedelta(minutes=1),
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        sensor = RecentlyRecoveredSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        with patch("custom_components.entity_availability.sensor.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            sensor._refresh_cache()
            attrs = sensor.extra_state_attributes
        assert attrs["count"] == 1


class TestSourcesCompatible:
    """_sources_compatible: per-axis equal or wildcard-None; distinct concrete conflict."""

    def test_both_none_compatible(self):
        assert _sources_compatible((None, None), (None, None)) is True

    def test_equal_concrete_compatible(self):
        assert _sources_compatible(("bat", "rssi"), ("bat", "rssi")) is True

    def test_wildcard_one_none_compatible(self):
        assert _sources_compatible(("bat", None), (None, "rssi")) is True
        assert _sources_compatible((None, "rssi"), ("bat", "rssi")) is True

    def test_distinct_concrete_battery_conflicts(self):
        assert _sources_compatible(("bat_a", None), ("bat_b", None)) is False

    def test_distinct_concrete_signal_conflicts(self):
        assert _sources_compatible((None, "rssi_a"), (None, "rssi_b")) is False


class TestTighten:
    """_tighten: rep absorbs a member's concrete source on axes it left None (meet)."""

    def test_none_axis_absorbs_concrete(self):
        assert _tighten(("bat_a", None), (None, "rssi")) == ("bat_a", "rssi")

    def test_concrete_axis_is_kept(self):
        # Rep's concrete source is never overwritten by a member's other concrete.
        assert _tighten(("bat_a", "rssi"), ("bat_b", "rssi")) == ("bat_a", "rssi")

    def test_idempotent_and_wildcard_member(self):
        assert _tighten(("bat_a", "rssi"), (None, None)) == ("bat_a", "rssi")


def _src_states(
    entity_ids_to_src: dict[str, tuple[str | None, str | None]],
) -> dict[str, DeviceState]:
    """Build a states dict from {entity_id: (battery_source, signal_source)}."""
    return {
        eid: DeviceState(entity_id=eid, battery_source=bs, signal_source=ss)
        for eid, (bs, ss) in entity_ids_to_src.items()
    }


def _row_count(rep_of: dict[str, str]) -> int:
    """Number of distinct rows (representatives)."""
    return len(set(rep_of.values()))


class TestCollapseRepresentativesSources:
    """collapse_representatives merges by (battery_source, signal_source) within a bucket."""

    def test_same_both_sources_one_row(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.cs_a", "csdev")
        _register_entity(mock_hass, "binary_sensor.cs_b", "csdev")
        states = _src_states(
            {
                "binary_sensor.cs_a": ("sensor.bat", "sensor.rssi"),
                "binary_sensor.cs_b": ("sensor.bat", "sensor.rssi"),
            }
        )
        rep = collapse_representatives(mock_hass, states)
        assert _row_count(rep) == 1

    def test_distinct_battery_same_signal_splits(self, mock_hass):
        # Multi-battery device: distinct battery sources split EVEN IF signal matches.
        _register_entity(mock_hass, "binary_sensor.mb_a", "mbdev")
        _register_entity(mock_hass, "binary_sensor.mb_b", "mbdev")
        states = _src_states(
            {
                "binary_sensor.mb_a": ("sensor.bat_a", "sensor.rssi"),
                "binary_sensor.mb_b": ("sensor.bat_b", "sensor.rssi"),
            }
        )
        rep = collapse_representatives(mock_hass, states)
        assert _row_count(rep) == 2

    def test_distinct_signal_same_battery_splits(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.ms_a", "msdev")
        _register_entity(mock_hass, "binary_sensor.ms_b", "msdev")
        states = _src_states(
            {
                "binary_sensor.ms_a": ("sensor.bat", "sensor.rssi_a"),
                "binary_sensor.ms_b": ("sensor.bat", "sensor.rssi_b"),
            }
        )
        rep = collapse_representatives(mock_hass, states)
        assert _row_count(rep) == 2

    def test_none_battery_merges_with_concrete_sibling(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.wc_a", "wcdev")
        _register_entity(mock_hass, "binary_sensor.wc_b", "wcdev")
        states = _src_states(
            {
                "binary_sensor.wc_a": ("sensor.bat", None),
                "binary_sensor.wc_b": (None, None),
            }
        )
        rep = collapse_representatives(mock_hass, states)
        assert _row_count(rep) == 1

    def test_meet_three_members_merge_order_independent(self, mock_hass):
        # A=(bat_a, None), B=(None, rssi), C=(bat_a, rssi) on one device.
        # C tightens the rep to (bat_a, rssi); B joins on rssi==rssi; A on bat_a==bat_a.
        # All 3 collapse to ONE row regardless of insertion order.
        _register_entity(mock_hass, "binary_sensor.meet_a", "meetdev")
        _register_entity(mock_hass, "binary_sensor.meet_b", "meetdev")
        _register_entity(mock_hass, "binary_sensor.meet_c", "meetdev")
        src = {
            "binary_sensor.meet_a": ("sensor.bat_a", None),
            "binary_sensor.meet_b": (None, "sensor.rssi"),
            "binary_sensor.meet_c": ("sensor.bat_a", "sensor.rssi"),
        }
        orders = [
            ["binary_sensor.meet_a", "binary_sensor.meet_b", "binary_sensor.meet_c"],
            ["binary_sensor.meet_c", "binary_sensor.meet_b", "binary_sensor.meet_a"],
            ["binary_sensor.meet_b", "binary_sensor.meet_a", "binary_sensor.meet_c"],
        ]
        for order in orders:
            states = _src_states({eid: src[eid] for eid in order})
            rep = collapse_representatives(mock_hass, states)
            assert _row_count(rep) == 1, f"order {order} did not merge to 1 row"

    def test_conflict_determinism_stable_row_identity(self, mock_hass):
        # A=(bat_a, None), B=(bat_b, None), C=(None, rssi) on one device.
        # A and B conflict on battery -> 2 rows always. C is a wildcard on battery;
        # it must attach to the SAME cluster (same standalone rep) across orders.
        _register_entity(mock_hass, "binary_sensor.cf_a", "cfdev")
        _register_entity(mock_hass, "binary_sensor.cf_b", "cfdev")
        _register_entity(mock_hass, "binary_sensor.cf_c", "cfdev")
        src = {
            "binary_sensor.cf_a": ("sensor.bat_a", None),
            "binary_sensor.cf_b": ("sensor.bat_b", None),
            "binary_sensor.cf_c": (None, "sensor.rssi"),
        }
        orders = [
            ["binary_sensor.cf_a", "binary_sensor.cf_b", "binary_sensor.cf_c"],
            ["binary_sensor.cf_c", "binary_sensor.cf_b", "binary_sensor.cf_a"],
            ["binary_sensor.cf_b", "binary_sensor.cf_c", "binary_sensor.cf_a"],
        ]
        results = []
        for order in orders:
            states = _src_states({eid: src[eid] for eid in order})
            rep = collapse_representatives(mock_hass, states)
            assert _row_count(rep) == 2, f"order {order} not 2 rows"
            results.append(rep["binary_sensor.cf_c"])
        # C attaches to the SAME representative across every insertion order.
        assert len(set(results)) == 1, f"C's row identity drifted: {results}"

    def test_device_less_entity_maps_to_itself(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.dl_x", None)
        states = _src_states({"binary_sensor.dl_x": ("sensor.bat", "sensor.rssi")})
        rep = collapse_representatives(mock_hass, states)
        assert rep == {"binary_sensor.dl_x": "binary_sensor.dl_x"}

    def test_collapsible_excludes_entity_from_merge(self, mock_hass):
        # A collapsible sibling and a NON-collapsible sibling on one device: the
        # excluded one stays its own row even though sources are compatible.
        _register_entity(mock_hass, "binary_sensor.ce_in", "cedev")
        _register_entity(mock_hass, "binary_sensor.ce_out", "cedev")
        states = _src_states(
            {
                "binary_sensor.ce_in": ("sensor.bat", None),
                "binary_sensor.ce_out": ("sensor.bat", None),
            }
        )
        rep = collapse_representatives(
            mock_hass, states, collapsible={"binary_sensor.ce_in"}
        )
        # ce_out excluded -> its own row; ce_in -> its own row (nothing to merge with).
        assert rep["binary_sensor.ce_out"] == "binary_sensor.ce_out"
        assert rep["binary_sensor.ce_in"] == "binary_sensor.ce_in"
        assert _row_count(rep) == 2


class TestDedupDisplayNames:
    """Unit tests for the {N} display-dedup + sort helper (change 3 + 4)."""

    def test_distinct_names_sorted_alphabetically(self):
        # Luminance before Motion — severity list order matches recovery order.
        assert dedup_display_names(["Balcony Motion", "Balcony Luminance"]) == [
            "Balcony Luminance",
            "Balcony Motion",
        ]

    def test_identical_names_fold_to_brace_count(self):
        assert dedup_display_names(["Balcony", "Balcony"]) == ["Balcony {2}"]

    def test_triple_identical(self):
        assert dedup_display_names(["Kitchen", "Kitchen", "Kitchen"]) == ["Kitchen {3}"]

    def test_battery_suffix_kept_distinct(self):
        # Same base name, different % — NOT folded (full display string differs).
        assert dedup_display_names(["Balcony (10%)", "Balcony (90%)"]) == [
            "Balcony (10%)",
            "Balcony (90%)",
        ]

    def test_empty(self):
        assert dedup_display_names([]) == []

    def test_render_none_on_empty(self):
        assert render_name_list([], 255) == "None"

    def test_render_joins_and_dedups(self):
        assert render_name_list(["B", "A", "A"], 255) == "A {2}, B"

    def test_render_truncates(self):
        names = [f"{i}" + "x" * 120 for i in range(5)]
        out = render_name_list(names, 255)
        assert out.endswith("...")
        assert len(out) <= 255

    def test_casefold_tie_is_deterministic(self):
        # GAP 1: casefold alone would leave "abc"/"ABC" in dict/insertion order,
        # reflapping the recorded string. The raw-string secondary key pins order:
        # uppercase codepoints sort before lowercase, so "ABC" precedes "abc".
        assert dedup_display_names(["abc", "ABC"]) == ["ABC", "abc"]
        assert dedup_display_names(["abc", "ABC"]) == dedup_display_names(
            ["ABC", "abc"]
        )

    def test_render_truncates_on_boundary_no_split_marker(self):
        # GAP 2: three distinct 100-char names overflow 255. Truncation drops whole
        # names on the ", " boundary — the kept text never ends mid-name and no
        # "{N}" marker is sliced. Exercises the drop-trailing-name path.
        names = [c * 100 for c in ("a", "b", "c")]
        out = render_name_list(names, 255)
        assert out.endswith("...")
        assert len(out) <= 255
        # only whole names kept before the sentinel; no partial name fragment.
        kept = out[:-3].split(", ")
        assert all(len(k) == 100 for k in kept)

    def test_render_folded_marker_not_split(self):
        # GAP 2/3: a folded "{N}" string that overflows must not truncate to a
        # dangling "... {" / "{1". Long identical name folds to "<name> {3}";
        # combined with a second long name it overflows and the first is dropped
        # whole, so the surviving output never contains a broken marker.
        long_name = "L" * 200
        names = [long_name, long_name, long_name, "M" * 200]
        out = render_name_list(names, 255)
        assert len(out) <= 255
        assert out.endswith("...")
        assert "{" not in out or "}" in out  # never an unclosed brace

    def test_render_first_name_overflows_byte_truncated(self):
        # GAP 2: a single name longer than the whole budget can't be kept whole,
        # so it byte-truncates with a trailing "...". Exercises the kept==[] path.
        out = render_name_list(["Z" * 400], 255)
        assert out.endswith("...")
        assert len(out) == 255


class TestSingleGroupListTable:
    """Single-group offline_entities: the 3-row README table.

    Device 'Balcony' with two entities (Motion + Luminance). use_device_names
    OFF -> entity friendly names, two distinct rows sorted alphabetically.
    use_device_names ON + collapse ON -> one collapsed row (device name).
    """

    def _offline_states(self):
        return {
            "binary_sensor.balcony_motion": DeviceState(
                entity_id="binary_sensor.balcony_motion", is_offline=True
            ),
            "sensor.balcony_luminance": DeviceState(
                entity_id="sensor.balcony_luminance", is_offline=True
            ),
        }

    def test_udn_off_two_rows_alphabetical(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.balcony_motion", "balcony")
        _register_entity(mock_hass, "sensor.balcony_luminance", "balcony")
        mock_hass.states.async_set(
            "binary_sensor.balcony_motion", "off", {"friendly_name": "Balcony Motion"}
        )
        mock_hass.states.async_set(
            "sensor.balcony_luminance", "off", {"friendly_name": "Balcony Luminance"}
        )
        coord = _make_coordinator(
            mock_hass,
            collapse=False,
            use_device_names=False,
            states=self._offline_states(),
        )
        sensor = OfflineDevicesSensor(coord, "G", "g", _REG_ENTRY_ID)
        sensor.hass = mock_hass
        # Luminance before Motion.
        assert sensor.native_value == "Balcony Luminance, Balcony Motion"

    def test_udn_on_collapse_on_one_row(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.balcony_motion", "balcony")
        _register_entity(mock_hass, "sensor.balcony_luminance", "balcony")
        # Device registry name resolves for both entities under use_device_names.
        dev_reg = dr.async_get(mock_hass)
        device = _get_device(mock_hass, "balcony")
        dev_reg.async_update_device(device.id, name_by_user="Balcony")
        coord = _make_coordinator(
            mock_hass,
            collapse=True,
            use_device_names=True,
            states=self._offline_states(),
        )
        sensor = OfflineDevicesSensor(coord, "G", "g", _REG_ENTRY_ID)
        sensor.hass = mock_hass
        # Collapsed to one device row.
        assert sensor.native_value == "Balcony"


class TestCombinedRespectSettings:
    """Combined offline_entities respects each source group's OWN collapse gate.

    Change 1: a source group is collapse-active only when use_device_names AND
    collapse_devices. A udn-ON but collapse-OFF group must NOT collapse in combined.
    """

    def test_udn_on_collapse_off_group_does_not_collapse(self, mock_hass):
        # Two entities on one device, group udn ON but collapse OFF -> stay 2 rows,
        # both render the device name -> "Balcony {2}".
        _register_entity(mock_hass, "binary_sensor.b_motion", "bdev")
        _register_entity(mock_hass, "binary_sensor.b_lux", "bdev")
        dev_reg = dr.async_get(mock_hass)
        device = _get_device(mock_hass, "bdev")
        dev_reg.async_update_device(device.id, name_by_user="Balcony")
        states = {
            "binary_sensor.b_motion": DeviceState(
                entity_id="binary_sensor.b_motion", is_offline=True
            ),
            "binary_sensor.b_lux": DeviceState(
                entity_id="binary_sensor.b_lux", is_offline=True
            ),
        }
        coord = _make_group_coord(
            mock_hass, "g_off", states, collapse=False, use_device_names=True
        )
        assert coord.collapse_active is False
        mock_hass.data[DOMAIN] = {"g_off": coord}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="comb_off", title="Combined"
        )
        count = CombinedOfflineCountSensor(
            mock_hass, combined_entry, "Combined", "comb_off", ["g_off"]
        )
        assert count.native_value == 2
        names = CombinedOfflineEntitiesSensor(
            mock_hass, combined_entry, "Combined", "comb_off", ["g_off"]
        )
        names.hass = mock_hass
        assert names.native_value == "Balcony {2}"

    def test_udn_on_collapse_on_group_collapses(self, mock_hass):
        # Same device, group collapse-active -> one row.
        _register_entity(mock_hass, "binary_sensor.c_motion", "cdev")
        _register_entity(mock_hass, "binary_sensor.c_lux", "cdev")
        dev_reg = dr.async_get(mock_hass)
        device = _get_device(mock_hass, "cdev")
        dev_reg.async_update_device(device.id, name_by_user="Balcony")
        states = {
            "binary_sensor.c_motion": DeviceState(
                entity_id="binary_sensor.c_motion", is_offline=True
            ),
            "binary_sensor.c_lux": DeviceState(
                entity_id="binary_sensor.c_lux", is_offline=True
            ),
        }
        coord = _make_group_coord(
            mock_hass, "g_on", states, collapse=True, use_device_names=True
        )
        assert coord.collapse_active is True
        mock_hass.data[DOMAIN] = {"g_on": coord}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="comb_on", title="Combined"
        )
        count = CombinedOfflineCountSensor(
            mock_hass, combined_entry, "Combined", "comb_on", ["g_on"]
        )
        assert count.native_value == 1
        names = CombinedOfflineEntitiesSensor(
            mock_hass, combined_entry, "Combined", "comb_on", ["g_on"]
        )
        names.hass = mock_hass
        assert names.native_value == "Balcony"

    def test_udn_on_collapse_off_three_entities_fold_to_brace_three(self, mock_hass):
        # GAP 3: three entities on one collapse-OFF (udn-on) device -> 3 rows, all
        # render the device name -> "Balcony {3}", and count agrees at 3.
        for eid in ("binary_sensor.t_a", "binary_sensor.t_b", "binary_sensor.t_c"):
            _register_entity(mock_hass, eid, "tdev")
        dev_reg = dr.async_get(mock_hass)
        device = _get_device(mock_hass, "tdev")
        dev_reg.async_update_device(device.id, name_by_user="Balcony")
        states = {
            eid: DeviceState(entity_id=eid, is_offline=True)
            for eid in ("binary_sensor.t_a", "binary_sensor.t_b", "binary_sensor.t_c")
        }
        coord = _make_group_coord(
            mock_hass, "g_three", states, collapse=False, use_device_names=True
        )
        mock_hass.data[DOMAIN] = {"g_three": coord}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="comb_three", title="Combined"
        )
        count = CombinedOfflineCountSensor(
            mock_hass, combined_entry, "Combined", "comb_three", ["g_three"]
        )
        names = CombinedOfflineEntitiesSensor(
            mock_hass, combined_entry, "Combined", "comb_three", ["g_three"]
        )
        names.hass = mock_hass
        # count and marker N agree — the whole point of change 2.
        assert count.native_value == 3
        assert names.native_value == "Balcony {3}"

    def test_recovery_matches_severity_multi_battery_device(self, mock_hass):
        # GAP 4: one physical device, two entities with DISTINCT battery sources,
        # collapse-ON group. Source-aware collapse keeps them 2 rows. Recovery must
        # yield the SAME row count as offline/severity — both route through the same
        # rep_of now, so both are 2. Guards against the old raw-device_id recovery
        # token that would have merged them to 1 while severity kept 2.
        _register_entity(mock_hass, "binary_sensor.mb2_a", "mb2dev")
        _register_entity(mock_hass, "binary_sensor.mb2_b", "mb2dev")
        dev_reg = dr.async_get(mock_hass)
        device = _get_device(mock_hass, "mb2dev")
        dev_reg.async_update_device(device.id, name_by_user="MultiBat")
        states = {
            "binary_sensor.mb2_a": DeviceState(
                entity_id="binary_sensor.mb2_a",
                is_offline=True,
                battery_source="sensor.bat_a",
                recently_offline_at=_NOW - timedelta(minutes=1),
            ),
            "binary_sensor.mb2_b": DeviceState(
                entity_id="binary_sensor.mb2_b",
                is_offline=True,
                battery_source="sensor.bat_b",
                recently_offline_at=_NOW - timedelta(minutes=1),
            ),
        }
        coord = _make_group_coord(
            mock_hass, "g_mb2", states, collapse=True, use_device_names=True
        )
        assert coord.collapse_active is True
        mock_hass.data[DOMAIN] = {"g_mb2": coord}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="comb_mb2", title="Combined"
        )
        offline_count = CombinedOfflineCountSensor(
            mock_hass, combined_entry, "Combined", "comb_mb2", ["g_mb2"]
        ).native_value
        recovery = CombinedRecentlyOfflineSensor(
            mock_hass, combined_entry, "Combined", "comb_mb2", ["g_mb2"]
        )
        recovery.hass = mock_hass
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            recovery_count = recovery.extra_state_attributes["count"]
        # Source-aware split: both paths agree at 2 rows.
        assert offline_count == 2
        assert recovery_count == 2

    def test_recovery_two_entities_split_across_on_off_groups(self, mock_hass):
        # GAP 5: two DISTINCT entities on ONE device, split so entity1 is in a
        # collapse-ON group and entity2 in a collapse-OFF group. entity2 is not
        # collapsible (its group is collapse-off) so it never merges; entity1 has no
        # same-device sibling within a collapse-active group. Result: 2 rows.
        _register_entity(mock_hass, "binary_sensor.sp_on", "spdev")
        _register_entity(mock_hass, "binary_sensor.sp_off", "spdev")
        dev_reg = dr.async_get(mock_hass)
        device = _get_device(mock_hass, "spdev")
        dev_reg.async_update_device(device.id, name_by_user="Split")
        coord_on = _make_group_coord(
            mock_hass,
            "g_sp_on",
            {
                "binary_sensor.sp_on": DeviceState(
                    entity_id="binary_sensor.sp_on",
                    is_offline=True,
                    recently_offline_at=_NOW - timedelta(minutes=1),
                )
            },
            collapse=True,
            use_device_names=True,
        )
        coord_off = _make_group_coord(
            mock_hass,
            "g_sp_off",
            {
                "binary_sensor.sp_off": DeviceState(
                    entity_id="binary_sensor.sp_off",
                    is_offline=True,
                    recently_offline_at=_NOW - timedelta(minutes=1),
                )
            },
            collapse=False,
            use_device_names=True,
        )
        mock_hass.data[DOMAIN] = {"g_sp_on": coord_on, "g_sp_off": coord_off}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="comb_sp", title="Combined"
        )
        offline_count = CombinedOfflineCountSensor(
            mock_hass, combined_entry, "Combined", "comb_sp", ["g_sp_on", "g_sp_off"]
        ).native_value
        recovery = CombinedRecentlyOfflineSensor(
            mock_hass, combined_entry, "Combined", "comb_sp", ["g_sp_on", "g_sp_off"]
        )
        recovery.hass = mock_hass
        with patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            recovery_count = recovery.extra_state_attributes["count"]
        # collapse-OFF group's entity stays its own row; recovery agrees with offline.
        assert offline_count == 2
        assert recovery_count == 2

    def test_device_less_same_name_fold_to_brace_two(self, mock_hass):
        # GAP 6: two device-less entities sharing an identical friendly_name fold to
        # "{2}" at the sensor level (not just the unit helper). Device-less entities
        # map to themselves in rep_of, so they stay 2 rows, then render-fold.
        mock_hass.states.async_set("sensor.dl_a", "off", {"friendly_name": "Orphan"})
        mock_hass.states.async_set("sensor.dl_b", "off", {"friendly_name": "Orphan"})
        states = {
            "sensor.dl_a": DeviceState(entity_id="sensor.dl_a", is_offline=True),
            "sensor.dl_b": DeviceState(entity_id="sensor.dl_b", is_offline=True),
        }
        coord = _make_group_coord(
            mock_hass, "g_dl", states, collapse=False, use_device_names=False
        )
        mock_hass.data[DOMAIN] = {"g_dl": coord}
        combined_entry = MockConfigEntry(
            domain=DOMAIN, entry_id="comb_dl", title="Combined"
        )
        count = CombinedOfflineCountSensor(
            mock_hass, combined_entry, "Combined", "comb_dl", ["g_dl"]
        )
        names = CombinedOfflineEntitiesSensor(
            mock_hass, combined_entry, "Combined", "comb_dl", ["g_dl"]
        )
        names.hass = mock_hass
        assert count.native_value == 2
        assert names.native_value == "Orphan {2}"
