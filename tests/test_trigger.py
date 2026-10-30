"""Smoke test for the Entity Availability automation-trigger relay.

Requires the HA Trigger platform (HA Core >= 2025.11), which the integration's
hacs.json floor now mandates, so these run unconditionally in CI.
"""

from __future__ import annotations

from homeassistant.core import Context, HomeAssistant

from custom_components.entity_availability import trigger as ea_trigger
from custom_components.entity_availability.const import EVENT_OFFLINE

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


async def test_trigger_relays_event_data(hass: HomeAssistant) -> None:
    """Attaching the offline trigger forwards the raw event.data to the action."""
    trig = ea_trigger._TRIGGERS["offline"](hass, {})
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
