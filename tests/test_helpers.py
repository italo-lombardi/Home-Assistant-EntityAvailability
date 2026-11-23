"""Tests for label-group resolution helpers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.const import DOMAIN
from custom_components.entity_availability.helpers import (
    build_signal_map,
    infer_network_type,
    resolve_label_members,
)


async def test_resolve_label_members_union_dedup(hass: HomeAssistant) -> None:
    """Union of direct-entity labels and device labels, deduped and order-preserving.

    An entity that is BOTH directly labeled and on a labeled device appears once.
    """
    lbl = lr.async_get(hass).async_create("L").label_id
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    reg = MockConfigEntry(domain=DOMAIN, entry_id="h_reg", title="R")
    reg.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id="h_reg", identifiers={(DOMAIN, "d")}, name="D"
    )
    dev_reg.async_update_device(device.id, labels={lbl})
    # on the labeled device
    on_dev = ent_reg.async_get_or_create(
        "binary_sensor", "t", "on_dev", suggested_object_id="on_dev"
    )
    ent_reg.async_update_entity(on_dev.entity_id, device_id=device.id)
    # on the device AND directly labeled → must not duplicate
    both = ent_reg.async_get_or_create(
        "binary_sensor", "t", "both", suggested_object_id="both"
    )
    ent_reg.async_update_entity(both.entity_id, device_id=device.id, labels={lbl})
    # directly labeled, no device
    direct = ent_reg.async_get_or_create(
        "binary_sensor", "t", "direct", suggested_object_id="direct"
    )
    ent_reg.async_update_entity(direct.entity_id, labels={lbl})

    members = resolve_label_members(hass, [lbl])
    assert sorted(members) == [
        "binary_sensor.both",
        "binary_sensor.direct",
        "binary_sensor.on_dev",
    ]
    assert len(members) == len(set(members))  # no dupes


async def test_resolve_label_members_empty_label_list(hass: HomeAssistant) -> None:
    """No labels → no members (a genuinely empty label group monitors nothing)."""
    assert resolve_label_members(hass, []) == []


def test_infer_network_type() -> None:
    """LQI/linkquality suffixes infer zigbee; everything else is generic."""
    assert infer_network_type("sensor.x_lqi") == "zigbee_lqi"
    assert infer_network_type("sensor.x_linkquality") == "zigbee_lqi"
    assert infer_network_type("sensor.x_rssi") == "generic"
    assert infer_network_type("sensor.x") == "generic"


async def test_build_signal_map_detects_sibling(hass: HomeAssistant) -> None:
    """An entity with a detectable signal sensor gets a mapped entry; others omitted."""
    # Naming-convention signal sensor the detector picks up for binary_sensor.foo.
    hass.states.async_set("sensor.foo_rssi", "-70", {"device_class": "signal_strength"})

    result = build_signal_map(hass, ["binary_sensor.foo", "binary_sensor.bar"])

    # foo resolves to its rssi sibling; bar has no detectable signal sensor.
    assert result == {
        "binary_sensor.foo": {
            "sensor": "sensor.foo_rssi",
            "network_type": "generic",
        }
    }


async def test_build_signal_map_empty(hass: HomeAssistant) -> None:
    """No detectable signal sensors → empty map."""
    assert build_signal_map(hass, ["binary_sensor.nothing"]) == {}
