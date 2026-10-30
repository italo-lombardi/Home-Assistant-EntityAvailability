"""Automation triggers for Entity Availability.

Surfaces the integration's bus events as named, UI-discoverable triggers in the
automation editor's Triggers tab. The raw ``entity_availability_*`` events keep
firing regardless, so existing ``platform: event`` automations are unaffected.

Requires the HA Trigger platform (``async_attach_runner`` surface), which landed
in HA Core 2025.11 — matching the integration's min-HA floor in ``hacs.json``.
"""

from collections.abc import Callable
from typing import override

import voluptuous as vol
from homeassistant.const import CONF_OPTIONS
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.automation import move_top_level_schema_fields_to_options
from homeassistant.helpers.trigger import Trigger, TriggerActionRunner, TriggerConfig
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CONFIG_ENTRY_ID,
    EVENT_BATTERY_OK,
    EVENT_LOW_BATTERY,
    EVENT_OFFLINE,
    EVENT_POOR_SIGNAL,
    EVENT_RECOVERED,
    EVENT_SIGNAL_OK,
    EVENT_STALE,
    EVENT_STALE_RECOVERED,
)

# Plain {marker: type} dict — reused by move_top_level_schema_fields_to_options
# (needs the raw markers) and wrapped in a Schema for validation below.
_OPTIONS_SCHEMA_DICT = {vol.Optional(CONF_CONFIG_ENTRY_ID): str}
_OPTIONS_SCHEMA = vol.Schema(_OPTIONS_SCHEMA_DICT)


class _EventTrigger(Trigger):
    """Relay a single Entity Availability bus event to an automation."""

    _event_type: str
    _description: str

    @classmethod
    @override
    async def async_validate_complete_config(
        cls, hass: HomeAssistant, complete_config: ConfigType
    ) -> ConfigType:
        """Migrate a top-level ``config_entry_id`` into the ``options`` block.

        The automation editor (and hand-written YAML) put trigger fields at the
        top level; the new-style Trigger base expects them under ``options``.
        Mirror the zwave_js precedent: move them before the base validates.
        """
        complete_config = move_top_level_schema_fields_to_options(
            complete_config, _OPTIONS_SCHEMA_DICT
        )
        return await super().async_validate_complete_config(hass, complete_config)

    @classmethod
    @override
    async def async_validate_config(
        cls, hass: HomeAssistant, config: ConfigType
    ) -> ConfigType:
        """Validate the trigger configuration.

        The only user-selectable option is ``config_entry_id`` (group scoping).
        When it is unset we return an empty config so the ``options`` key stays
        out of the stored YAML (the base merges back only keys we return); when
        it is set we return the validated ``options`` block so it round-trips.
        """
        options = _OPTIONS_SCHEMA(config.get(CONF_OPTIONS) or {})
        return {CONF_OPTIONS: options} if options else {}

    @override
    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        """Capture the optional group-scoping config entry id."""
        super().__init__(hass, config)
        self._entry_id = (config.options or {}).get(CONF_CONFIG_ENTRY_ID)

    @override
    async def async_attach_runner(
        self,
        run_action: TriggerActionRunner,
        _did_not_trigger: Callable[..., None] | None = None,
    ) -> CALLBACK_TYPE:
        """Listen for the event and forward its data to the action.

        When scoped to a single config entry, events from other groups are
        skipped.
        """

        @callback
        def _handle(event: Event) -> None:
            # ponytail: direct entry_id compare — every EA event payload already
            # carries entry_id (coordinator + combined_sensor), so no unique_id
            # resolution is needed. Revisit only if a payload stops stamping it.
            if self._entry_id and event.data.get("entry_id") != self._entry_id:
                return
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
