"""Tests for device-collapse (config option collapse_devices).

Collapse merges multiple entities of the same physical device into one across
every count/list/event, gated on both collapse_devices AND use_device_names.
The composite key is device_id::name::battery::signal::unit::non_essential, so
entities that differ on any of those never merge (matching the old card behavior).
Availability/MTBF/MTTR and area sensors are intentionally NOT collapsed.
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
from custom_components.entity_availability.helpers import collapse_key
from custom_components.entity_availability.models import DeviceState
from custom_components.entity_availability.sensor import (
    GroupSummarySensor,
    LowBatteryCountSensor,
    OfflineCountSensor,
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
    def test_same_device_different_battery_stays_separate(self, mock_hass):
        _register_entity(mock_hass, "binary_sensor.bat_a", "batdev")
        _register_entity(mock_hass, "binary_sensor.bat_b", "batdev")
        states = {
            "binary_sensor.bat_a": DeviceState(
                entity_id="binary_sensor.bat_a", is_offline=True, battery_level=10
            ),
            "binary_sensor.bat_b": DeviceState(
                entity_id="binary_sensor.bat_b", is_offline=True, battery_level=90
            ),
        }
        coord = _make_coordinator(
            mock_hass, collapse=True, use_device_names=True, states=states
        )
        sensor = OfflineCountSensor(coord, "G", "g", "collapse_test_entry")
        sensor.hass = mock_hass
        # Different battery levels -> different key -> no merge.
        assert sensor.native_value == 2


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

    def test_mixed_toggle_partial_collapse(self, mock_hass):
        # Group A has use_device_names=True (auto-collapses in combined view).
        # Group B has use_device_names=False (no collapse — each entity its own row).
        # Same physical device: A has two entities (collapse to 1), B has two (stay 2).
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


class TestCollapseKeyStableNumerics:
    def test_nan_battery_coerced_to_none_in_key(self, mock_hass):
        # A flaky sensor emitting NaN must not produce a wobbly key: NaN coerces to
        # None so the entity keys the same as a genuine None-battery sibling.
        _register_entity(mock_hass, "binary_sensor.nan_bat", "nandev")
        d_nan = DeviceState(
            entity_id="binary_sensor.nan_bat", battery_level=float("nan")
        )
        _register_entity(mock_hass, "binary_sensor.none_bat", "nandev")
        d_none = DeviceState(entity_id="binary_sensor.none_bat", battery_level=None)
        key_nan = collapse_key(mock_hass, d_nan)
        key_none = collapse_key(mock_hass, d_none)
        # Both resolve the NaN/None battery to the same "None" key segment.
        assert "::None::" in key_nan
        assert key_nan.split("::")[2] == key_none.split("::")[2]
