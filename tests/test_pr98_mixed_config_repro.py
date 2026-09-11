"""Falsifying repro for peer d1's MEDIUM finding on PR#98.

Mixed-config combined group: same entity_id monitored in group A (collapse
active) AND group B (collapse off). collapse_key token (A) != entity_id token
(B) -> both survive the shared `seen` set in _collapsed_recovery_pairs ->
device double-counted. Old code (entity_id-only dedup) collapsed to one row.

Expect count == 1; if the bug is real, current code yields 2.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.entity_availability.const import (
    CONF_COLLAPSE_DEVICES,
    CONF_ENTITIES,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    CONF_USE_DEVICE_NAMES,
    DOMAIN,
    ENTRY_TYPE_GROUP,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)
from custom_components.entity_availability.models import DeviceState

from tests.test_combined_sensor import (
    _make_combined_entry,
    _make_recently_offline_sensor,
)

try:
    from custom_components.entity_availability.const import (
        DEFAULT_AVAILABILITY_WINDOWS,
    )

    _WINDOWS = {"CONF_AVAILABILITY_WINDOWS": DEFAULT_AVAILABILITY_WINDOWS}
except Exception:  # pragma: no cover
    _WINDOWS = {}

from pytest_homeassistant_custom_component.common import MockConfigEntry

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _entry(entry_id, name, entities, *, collapse, udn):
    data = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_GROUP,
        CONF_GROUP_NAME: name,
        CONF_ENTITIES: entities,
        CONF_COLLAPSE_DEVICES: collapse,
        CONF_USE_DEVICE_NAMES: udn,
    }
    return MockConfigEntry(
        version=1, domain=DOMAIN, title=name, data=data, entry_id=entry_id
    )


def _coord(hass, entry, states):
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
    coord._device_states = states
    coord._entities = list(states)
    return coord


def test_mixed_config_same_entity_double_count(mock_hass):
    """Same entity_id E in group A (collapse ON) + group B (collapse OFF)."""
    eid = "binary_sensor.shared_e"
    off = _NOW - timedelta(minutes=1)

    # Group A: collapse active (collapse ON + use_device_names ON).
    entry_a = _entry("m_a", "Group A", [eid], collapse=True, udn=True)
    # Group B: collapse OFF.
    entry_b = _entry("m_b", "Group B", [eid], collapse=False, udn=False)

    coord_a = _coord(
        mock_hass,
        entry_a,
        {eid: DeviceState(entity_id=eid, is_offline=True, recently_offline_at=off)},
    )
    coord_b = _coord(
        mock_hass,
        entry_b,
        {eid: DeviceState(entity_id=eid, is_offline=True, recently_offline_at=off)},
    )
    mock_hass.data[DOMAIN] = {"m_a": coord_a, "m_b": coord_b}
    combined = _make_combined_entry("m_combined", "Combined", ["m_a", "m_b"])

    # E has a device_id -> group A's token is collapse_key (devid::...),
    # group B's token is the entity_id -> tokens differ.
    ent_reg = MagicMock()
    ent_entry = MagicMock()
    ent_entry.device_id = "shared_dev"
    ent_reg.async_get.return_value = ent_entry
    dev_reg = MagicMock()
    device = MagicMock()
    device.name_by_user = None
    device.name = "Shared Device"
    dev_reg.async_get.return_value = device

    sensor = _make_recently_offline_sensor(mock_hass, combined, [coord_a, coord_b])
    with (
        patch(
            "custom_components.entity_availability.helpers.er.async_get",
            return_value=ent_reg,
        ),
        patch(
            "custom_components.entity_availability.helpers.dr.async_get",
            return_value=dev_reg,
        ),
        patch(
            "custom_components.entity_availability.combined_sensor.datetime"
        ) as mock_dt,
    ):
        mock_dt.now.return_value = _NOW
        value = sensor.native_value
        attrs = sensor.extra_state_attributes

    print(f"\nvalue={value!r} count={attrs['count']} entities={attrs['entities']}")
    assert attrs["count"] == 1, f"double-count: {attrs}"
    assert attrs["entities"] == [eid], f"dup entity_id: {attrs}"
