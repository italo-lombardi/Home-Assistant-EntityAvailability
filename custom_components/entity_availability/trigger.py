"""Automation triggers for Entity Availability.

Surfaces the integration's bus events as named, UI-discoverable triggers in the
automation editor's Triggers tab. The raw ``entity_availability_*`` events keep
firing regardless, so existing ``platform: event`` automations are unaffected.

Requires the HA Trigger platform (``async_attach_runner`` surface), which landed
in HA Core 2025.11 — matching the integration's min-HA floor in ``hacs.json``.
"""

from collections.abc import Callable
from typing import cast, override

import voluptuous as vol
from homeassistant.const import CONF_OPTIONS
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.trigger import Trigger, TriggerActionRunner
from homeassistant.helpers.typing import ConfigType

from .const import (
    EVENT_BATTERY_OK,
    EVENT_LOW_BATTERY,
    EVENT_OFFLINE,
    EVENT_POOR_SIGNAL,
    EVENT_RECOVERED,
    EVENT_SIGNAL_OK,
    EVENT_STALE,
    EVENT_STALE_RECOVERED,
)

# Our events carry no user-selectable options, so the only accepted config is an
# (optional, empty) options block.
TRIGGER_SCHEMA = vol.Schema({vol.Required(CONF_OPTIONS, default={}): {}})


class _EventTrigger(Trigger):
    """Relay a single Entity Availability bus event to an automation."""

    _event_type: str
    _description: str

    @classmethod
    @override
    async def async_validate_config(
        cls, hass: HomeAssistant, config: ConfigType
    ) -> ConfigType:
        """Validate the trigger configuration."""
        return cast(ConfigType, TRIGGER_SCHEMA(config))

    @override
    async def async_attach_runner(
        self,
        run_action: TriggerActionRunner,
        _did_not_trigger: Callable[..., None] | None = None,
    ) -> CALLBACK_TYPE:
        """Listen for the event and forward its data to the action."""

        @callback
        def _handle(event: Event) -> None:
            run_action(dict(event.data), self._description, event.context)

        return self._hass.bus.async_listen(self._event_type, _handle)


class _OfflineTrigger(_EventTrigger):
    _event_type = EVENT_OFFLINE
    _description = "went offline"


class _RecoveredTrigger(_EventTrigger):
    _event_type = EVENT_RECOVERED
    _description = "recovered"


class _LowBatteryTrigger(_EventTrigger):
    _event_type = EVENT_LOW_BATTERY
    _description = "battery low"


class _BatteryOkTrigger(_EventTrigger):
    _event_type = EVENT_BATTERY_OK
    _description = "battery ok"


class _StaleTrigger(_EventTrigger):
    _event_type = EVENT_STALE
    _description = "went stale"


class _StaleRecoveredTrigger(_EventTrigger):
    _event_type = EVENT_STALE_RECOVERED
    _description = "stale recovered"


class _PoorSignalTrigger(_EventTrigger):
    _event_type = EVENT_POOR_SIGNAL
    _description = "poor signal"


class _SignalOkTrigger(_EventTrigger):
    _event_type = EVENT_SIGNAL_OK
    _description = "signal ok"


_TRIGGERS: dict[str, type[Trigger]] = {
    "offline": _OfflineTrigger,
    "recovered": _RecoveredTrigger,
    "low_battery": _LowBatteryTrigger,
    "battery_ok": _BatteryOkTrigger,
    "stale": _StaleTrigger,
    "stale_recovered": _StaleRecoveredTrigger,
    "poor_signal": _PoorSignalTrigger,
    "signal_ok": _SignalOkTrigger,
}


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return the triggers provided by Entity Availability."""
    return dict(_TRIGGERS)
