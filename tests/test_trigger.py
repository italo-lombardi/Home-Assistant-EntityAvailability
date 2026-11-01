"""Smoke test for the Entity Availability automation-trigger relay.

Requires the HA Trigger platform (HA Core >= 2025.11), which the integration's
hacs.json floor now mandates, so these run unconditionally in CI.
"""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.const import CONF_OPTIONS
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.trigger import TriggerConfig
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability import trigger as ea_trigger
from custom_components.entity_availability.const import (
    CONF_CONFIG_ENTRY_ID,
    DOMAIN,
    EVENT_OFFLINE,
)

_SLUGS = {
    "offline",
    "recovered",
    "low_battery",
    "battery_ok",
    "stale",
    "stale_recovered",
    "poor_signal",
    "signal_ok",
}


async def test_async_get_triggers_exposes_all_events(hass: HomeAssistant) -> None:
    """All 8 bus events are surfaced as named triggers."""
    triggers = await ea_trigger.async_get_triggers(hass)
    assert set(triggers) == _SLUGS


async def test_async_validate_config_drops_empty_options(hass: HomeAssistant) -> None:
    """Unset scoping leaves no options block, keeping the stored YAML clean."""
    validated = await ea_trigger._OfflineTrigger.async_validate_config(hass, {})
    assert validated == {}


def _group_entry(hass: HomeAssistant, entry_id: str = "grp-1") -> MockConfigEntry:
    """Register a group config entry so validation finds it."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Group", entry_id=entry_id)
    entry.add_to_hass(hass)
    return entry


async def test_async_validate_config_round_trips_scope(hass: HomeAssistant) -> None:
    """A configured, existing config_entry_id survives validation inside options."""
    entry = _group_entry(hass)
    validated = await ea_trigger._OfflineTrigger.async_validate_config(
        hass, {CONF_OPTIONS: {CONF_CONFIG_ENTRY_ID: entry.entry_id}}
    )
    assert validated == {CONF_OPTIONS: {CONF_CONFIG_ENTRY_ID: entry.entry_id}}


async def test_async_validate_config_rejects_unknown_entry(hass: HomeAssistant) -> None:
    """A typo'd/removed config_entry_id is rejected loudly, not silently dead."""
    with pytest.raises(vol.Invalid):
        await ea_trigger._OfflineTrigger.async_validate_config(
            hass, {CONF_OPTIONS: {CONF_CONFIG_ENTRY_ID: "does-not-exist"}}
        )


async def test_async_validate_config_rejects_foreign_entry(hass: HomeAssistant) -> None:
    """An entry from another integration is rejected — its events never match."""
    foreign = MockConfigEntry(domain="other", data={}, entry_id="foreign-1")
    foreign.add_to_hass(hass)
    with pytest.raises(vol.Invalid):
        await ea_trigger._OfflineTrigger.async_validate_config(
            hass, {CONF_OPTIONS: {CONF_CONFIG_ENTRY_ID: foreign.entry_id}}
        )


async def test_complete_config_migrates_top_level_scope(hass: HomeAssistant) -> None:
    """A top-level config_entry_id (how the UI/YAML write it) lands in options.

    Without the async_validate_complete_config override the base schema rejects
    the top-level key and the automation fails to set up — the regression this
    guards against.
    """
    entry = _group_entry(hass)
    validated = await ea_trigger._OfflineTrigger.async_validate_complete_config(
        hass,
        {
            "platform": "entity_availability.offline",
            CONF_CONFIG_ENTRY_ID: entry.entry_id,
        },
    )
    assert validated[CONF_OPTIONS] == {CONF_CONFIG_ENTRY_ID: entry.entry_id}
    assert CONF_CONFIG_ENTRY_ID not in validated  # moved, not duplicated


async def test_trigger_relays_event_data(hass: HomeAssistant) -> None:
    """Attaching the unscoped offline trigger forwards raw event.data."""
    trig = ea_trigger._TRIGGERS["offline"](hass, TriggerConfig(key="offline"))
    captured: list = []

    def run_action(run_vars, description, context):
        captured.append((run_vars, description, context))

    unsub = await trig.async_attach_runner(run_action)
    try:
        ctx = Context()
        payload = {"entity_id": "binary_sensor.x", "group": "G", "offline_count": 1}
        hass.bus.async_fire(EVENT_OFFLINE, payload, context=ctx)
        await hass.async_block_till_done()
    finally:
        unsub()

    assert len(captured) == 1
    run_vars, description, context = captured[0]
    assert run_vars == payload
    assert description == "went offline"
    assert context is ctx


async def test_trigger_scopes_to_config_entry(hass: HomeAssistant) -> None:
    """A scoped trigger fires only for its own config entry, skips others."""
    trig = ea_trigger._TRIGGERS["offline"](
        hass, TriggerConfig(key="offline", options={CONF_CONFIG_ENTRY_ID: "abc"})
    )
    captured: list = []

    unsub = await trig.async_attach_runner(lambda rv, d, c: captured.append(rv))
    try:
        hass.bus.async_fire(EVENT_OFFLINE, {"entry_id": "other"})
        hass.bus.async_fire(EVENT_OFFLINE, {"entry_id": "abc"})
        await hass.async_block_till_done()
    finally:
        unsub()

    assert len(captured) == 1
    assert captured[0]["entry_id"] == "abc"
