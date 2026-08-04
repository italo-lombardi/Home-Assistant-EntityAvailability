"""Config flow for Entity Availability integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    AVAILABLE_WINDOWS,
    CONF_AVAILABILITY_WINDOWS,
    CONF_BAD_STATES,
    CONF_BATTERY_ENTITY_MAP,
    CONF_BATTERY_THRESHOLD,
    CONF_COMBINED_GROUPS,
    CONF_COOLDOWN,
    CONF_ENTITIES,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    CONF_NON_ESSENTIAL_ENTITIES,
    CONF_RECOVERY_WINDOW,
    CONF_SIGNAL_ENABLED,
    CONF_SIGNAL_ENTITY_MAP,
    CONF_STALENESS_THRESHOLD,
    CONF_STALENESS_USE_LAST_UPDATED,
    CONF_USE_DEVICE_NAMES,
    DEFAULT_AVAILABILITY_WINDOWS,
    DEFAULT_BAD_STATES,
    DEFAULT_BATTERY_THRESHOLD,
    DEFAULT_COOLDOWN,
    DEFAULT_RECOVERY_WINDOW,
    DEFAULT_SIGNAL_ENABLED,
    DEFAULT_STALENESS_THRESHOLD,
    DEFAULT_STALENESS_USE_LAST_UPDATED,
    DEFAULT_USE_DEVICE_NAMES,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
    ENTRY_TYPE_GROUP,
    SIGNAL_NETWORK_TYPES,
)

_LOGGER = logging.getLogger(__name__)


_SIGNAL_NETWORK_TYPE_OPTIONS = [
    selector.SelectOptionDict(value=k, label=v["label"])
    for k, v in sorted(SIGNAL_NETWORK_TYPES.items(), key=lambda x: x[1]["label"])
]


# Suffix → network type inference. Only unambiguous mappings (LQI = Zigbee).
_SIGNAL_SUFFIX_NETWORK_TYPE: dict[str, str] = {
    "_linkquality": "zigbee_lqi",
    "_lqi": "zigbee_lqi",
}


def _infer_network_type(signal_entity_id: str) -> str:
    """Infer network type from signal sensor entity_id suffix. Returns 'generic' if unknown."""
    for suffix, nt in _SIGNAL_SUFFIX_NETWORK_TYPE.items():
        if signal_entity_id.endswith(suffix):
            return nt
    return "generic"


def _detect_signal_entity(hass: Any, entity_id: str) -> str:
    """Auto-detect signal sensor for an entity. Returns entity_id or empty string.

    Detection order:
    1. Device registry sibling with device_class=SIGNAL_STRENGTH
    2. Naming convention on slug: *_linkquality, *_signal_strength, *_rssi, *_lqi, *_signal
    3. Same as 2 but with known HA suffixes (_last_seen, _last_updated) stripped first
    """
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if entry and entry.device_id:
        for ent in er.async_entries_for_device(ent_reg, entry.device_id):
            if ent.entity_id == entity_id:
                continue
            if (
                ent.original_device_class == SensorDeviceClass.SIGNAL_STRENGTH
                or ent.device_class == SensorDeviceClass.SIGNAL_STRENGTH
            ):
                return ent.entity_id

    parts = entity_id.split(".", 1)
    if len(parts) == 2:  # pragma: no branch
        slug = parts[1]
        signal_suffixes = (
            "_linkquality",
            "_signal_strength",
            "_rssi",
            "_lqi",
            "_signal",
        )
        slugs_to_try = [slug]
        for strip_suffix in ("_last_seen", "_last_updated"):
            if slug.endswith(strip_suffix):
                slugs_to_try.append(slug[: -len(strip_suffix)])
                break
        for base in slugs_to_try:
            for suffix in signal_suffixes:
                candidate = f"sensor.{base}{suffix}"
                if hass.states.get(candidate):
                    return candidate

    return ""


def _build_signal_map_from_input(
    entities: list[str], user_input: dict[str, Any]
) -> dict[str, dict[str, str]]:
    """Build signal entity map from wizard step user_input."""
    signal_map: dict[str, dict[str, str]] = {}
    for entity_id in entities:
        sensor = user_input.get(entity_id, "")
        if sensor:
            nt = user_input.get(f"{entity_id}#network", "") or "generic"
            signal_map[entity_id] = {
                "sensor": sensor,
                "network_type": nt,
            }
    return signal_map


class EntityAvailabilityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Entity Availability."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Choose entry type — monitor entities or combine groups."""
        if user_input is not None:
            if user_input["entry_type"] == ENTRY_TYPE_COMBINED:
                return await self.async_step_combined()
            return await self.async_step_group()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "entry_type", default=ENTRY_TYPE_GROUP
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[ENTRY_TYPE_GROUP, ENTRY_TYPE_COMBINED],
                            translation_key="entry_type",
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1b: Group name and entity selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            group_name = user_input[CONF_GROUP_NAME].strip()
            monitored = user_input.get(CONF_ENTITIES, [])
            non_essential = user_input.get(CONF_NON_ESSENTIAL_ENTITIES, [])

            if not group_name:
                errors[CONF_GROUP_NAME] = "empty_group_name"
            elif not monitored and not non_essential:
                errors[CONF_ENTITIES] = "no_entities"
            elif set(monitored) & set(non_essential):
                errors[CONF_ENTITIES] = "duplicate_entities"
            else:
                await self.async_set_unique_id(
                    f"{DOMAIN}_{group_name.lower().replace(' ', '_')}"
                )
                self._abort_if_unique_id_configured()

                non_essential = list(dict.fromkeys(non_essential))
                self._data[CONF_ENTRY_TYPE] = ENTRY_TYPE_GROUP
                self._data[CONF_GROUP_NAME] = group_name
                self._data[CONF_ENTITIES] = list(
                    dict.fromkeys(monitored + non_essential)
                )
                self._data[CONF_NON_ESSENTIAL_ENTITIES] = non_essential
                return await self.async_step_monitoring()

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_GROUP_NAME,
                    default=(user_input or {}).get(CONF_GROUP_NAME, ""),
                ): str,
                vol.Optional(
                    CONF_ENTITIES,
                    default=(user_input or {}).get(CONF_ENTITIES, []),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(multiple=True)
                ),
                vol.Optional(
                    CONF_NON_ESSENTIAL_ENTITIES,
                    default=(user_input or {}).get(CONF_NON_ESSENTIAL_ENTITIES, []),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(multiple=True)
                ),
            }
        )

        return self.async_show_form(
            step_id="group",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_combined(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step for creating a combined group."""
        errors: dict[str, str] = {}

        existing_groups = [
            e
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_GROUP) == ENTRY_TYPE_GROUP
            and e.state == ConfigEntryState.LOADED
        ]

        if len(existing_groups) < 2:
            return self.async_abort(reason="not_enough_groups")

        if user_input is not None:
            group_name = user_input[CONF_GROUP_NAME].strip()
            combined_groups = user_input[CONF_COMBINED_GROUPS]

            if not group_name:
                errors[CONF_GROUP_NAME] = "empty_group_name"
            elif len(combined_groups) < 2:
                errors[CONF_COMBINED_GROUPS] = "not_enough_groups_selected"
            else:
                await self.async_set_unique_id(
                    f"{DOMAIN}_combined_{group_name.lower().replace(' ', '_')}"
                )
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=group_name,
                    data={
                        CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
                        CONF_GROUP_NAME: group_name,
                        CONF_COMBINED_GROUPS: combined_groups,
                    },
                )

        group_options = [
            selector.SelectOptionDict(value=e.entry_id, label=e.title)
            for e in existing_groups
        ]

        return self.async_show_form(
            step_id="combined",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GROUP_NAME): str,
                    vol.Required(
                        CONF_COMBINED_GROUPS, default=[]
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=group_options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_monitoring(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Monitoring settings."""
        if user_input is not None:
            self._data[CONF_BAD_STATES] = user_input[CONF_BAD_STATES]
            self._data[CONF_COOLDOWN] = user_input[CONF_COOLDOWN]
            self._data[CONF_STALENESS_THRESHOLD] = user_input[CONF_STALENESS_THRESHOLD]
            self._data[CONF_STALENESS_USE_LAST_UPDATED] = user_input.get(
                CONF_STALENESS_USE_LAST_UPDATED, DEFAULT_STALENESS_USE_LAST_UPDATED
            )
            return await self.async_step_advanced()

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_BAD_STATES, default=DEFAULT_BAD_STATES
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["unavailable", "unknown"],
                        multiple=True,
                        custom_value=True,
                    )
                ),
                vol.Required(
                    CONF_COOLDOWN, default=DEFAULT_COOLDOWN
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=3600, step=1, unit_of_measurement="seconds"
                    )
                ),
                vol.Required(
                    CONF_STALENESS_THRESHOLD, default=DEFAULT_STALENESS_THRESHOLD
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=1440, step=1, unit_of_measurement="minutes"
                    )
                ),
                vol.Optional(
                    CONF_STALENESS_USE_LAST_UPDATED,
                    default=DEFAULT_STALENESS_USE_LAST_UPDATED,
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="monitoring",
            data_schema=data_schema,
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: Advanced settings."""
        if user_input is not None:
            self._data[CONF_BATTERY_THRESHOLD] = user_input[CONF_BATTERY_THRESHOLD]
            self._data[CONF_AVAILABILITY_WINDOWS] = user_input[
                CONF_AVAILABILITY_WINDOWS
            ]
            self._data[CONF_RECOVERY_WINDOW] = user_input[CONF_RECOVERY_WINDOW]
            self._data[CONF_USE_DEVICE_NAMES] = user_input.get(
                CONF_USE_DEVICE_NAMES, DEFAULT_USE_DEVICE_NAMES
            )
            self._data[CONF_SIGNAL_ENABLED] = user_input.get(
                CONF_SIGNAL_ENABLED, DEFAULT_SIGNAL_ENABLED
            )

            if self._data[CONF_BATTERY_THRESHOLD] > 0:
                return await self.async_step_battery_mapping()

            if self._data[CONF_SIGNAL_ENABLED]:
                return await self.async_step_signal_mapping()

            self._data[CONF_BATTERY_ENTITY_MAP] = {}
            self._data[CONF_SIGNAL_ENTITY_MAP] = {}
            return self.async_create_entry(
                title=self._data[CONF_GROUP_NAME],
                data=self._data,
            )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_BATTERY_THRESHOLD, default=DEFAULT_BATTERY_THRESHOLD
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, step=1, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_SIGNAL_ENABLED, default=DEFAULT_SIGNAL_ENABLED
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_AVAILABILITY_WINDOWS, default=DEFAULT_AVAILABILITY_WINDOWS
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=AVAILABLE_WINDOWS,
                        multiple=True,
                    )
                ),
                vol.Required(
                    CONF_RECOVERY_WINDOW, default=DEFAULT_RECOVERY_WINDOW
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=60, step=1, unit_of_measurement="minutes"
                    )
                ),
                vol.Optional(
                    CONF_USE_DEVICE_NAMES, default=DEFAULT_USE_DEVICE_NAMES
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="advanced",
            data_schema=data_schema,
        )

    async def async_step_battery_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: Battery entity mapping."""
        if user_input is not None:
            # Build full map: entities in user_input have a battery, others don't
            battery_map = {}
            for entity_id in self._data[CONF_ENTITIES]:
                battery_map[entity_id] = user_input.get(entity_id, "")
            self._data[CONF_BATTERY_ENTITY_MAP] = battery_map

            if self._data.get(CONF_SIGNAL_ENABLED):
                return await self.async_step_signal_mapping()

            self._data[CONF_SIGNAL_ENTITY_MAP] = {}
            return self.async_create_entry(
                title=self._data[CONF_GROUP_NAME],
                data=self._data,
            )

        schema_dict: dict[Any, Any] = {}
        for entity_id in self._data[CONF_ENTITIES]:
            detected = self._detect_battery_entity(entity_id)
            schema_dict[
                vol.Optional(
                    entity_id,
                    description={"suggested_value": detected} if detected else None,
                )
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor"])
            )

        return self.async_show_form(
            step_id="battery_mapping",
            data_schema=vol.Schema(schema_dict),
        )

    def _detect_battery_entity(self, entity_id: str) -> str:
        """Auto-detect battery entity for a monitored entity."""
        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(entity_id)
        if entry and entry.device_id:
            for ent in er.async_entries_for_device(ent_reg, entry.device_id):
                if ent.entity_id == entity_id:
                    continue
                if ent.original_device_class == SensorDeviceClass.BATTERY or (
                    ent.device_class == SensorDeviceClass.BATTERY
                ):
                    return ent.entity_id

        parts = entity_id.split(".", 1)
        if len(parts) == 2:  # pragma: no branch
            battery_entity = f"sensor.{parts[1]}_battery"
            if self.hass.states.get(battery_entity):
                return battery_entity

        return ""

    async def async_step_signal_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 5: Signal entity mapping."""
        if user_input is not None:
            self._data[CONF_SIGNAL_ENTITY_MAP] = _build_signal_map_from_input(
                self._data[CONF_ENTITIES], user_input
            )
            return self.async_create_entry(
                title=self._data[CONF_GROUP_NAME],
                data=self._data,
            )

        schema_dict: dict[Any, Any] = {}
        for entity_id in self._data[CONF_ENTITIES]:
            detected = _detect_signal_entity(self.hass, entity_id)
            schema_dict[
                vol.Optional(
                    entity_id,
                    description={"suggested_value": detected} if detected else None,
                )
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["sensor", "input_number", "number"]
                )
            )
            schema_dict[
                vol.Optional(
                    f"{entity_id}#network",
                    default=_infer_network_type(detected) if detected else "generic",
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=_SIGNAL_NETWORK_TYPE_OPTIONS)
            )

        return self.async_show_form(
            step_id="signal_mapping",
            data_schema=vol.Schema(schema_dict),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> EntityAvailabilityOptionsFlow | CombinedGroupOptionsFlow:
        """Get the options flow for this handler."""
        if config_entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_COMBINED:
            return CombinedGroupOptionsFlow()
        return EntityAvailabilityOptionsFlow()


class EntityAvailabilityOptionsFlow(OptionsFlow):
    """Handle options flow for Entity Availability."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._data: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            monitored_raw = user_input.get(CONF_ENTITIES, [])
            non_essential_raw = user_input.get(CONF_NON_ESSENTIAL_ENTITIES, [])

            if set(monitored_raw) & set(non_essential_raw):
                errors[CONF_ENTITIES] = "duplicate_entities"
            else:
                self._data = {**self.config_entry.data, **user_input}
                non_essential = list(dict.fromkeys(non_essential_raw))
                self._data[CONF_ENTITIES] = list(
                    dict.fromkeys(monitored_raw + non_essential)
                )
                self._data[CONF_NON_ESSENTIAL_ENTITIES] = non_essential

                if self._data.get(CONF_BATTERY_THRESHOLD, 0) > 0:
                    return await self.async_step_battery_mapping()

                if self._data.get(CONF_SIGNAL_ENABLED):
                    return await self.async_step_signal_mapping()

                self._data[CONF_BATTERY_ENTITY_MAP] = {}
                # Preserve signal map even when disabled so re-enabling restores bindings.
                self._data.setdefault(
                    CONF_SIGNAL_ENTITY_MAP,
                    self.config_entry.data.get(CONF_SIGNAL_ENTITY_MAP, {}),
                )
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=self._data
                )
                return self.async_create_entry(title="", data={})

        current = self.config_entry.data

        current_non_essential = current.get(CONF_NON_ESSENTIAL_ENTITIES, [])
        current_monitored = [
            e for e in current.get(CONF_ENTITIES, []) if e not in current_non_essential
        ]

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ENTITIES, default=current_monitored
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(multiple=True)
                ),
                vol.Optional(
                    CONF_NON_ESSENTIAL_ENTITIES,
                    default=current_non_essential,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(multiple=True)
                ),
                vol.Required(
                    CONF_BAD_STATES,
                    default=current.get(CONF_BAD_STATES, DEFAULT_BAD_STATES),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["unavailable", "unknown"],
                        multiple=True,
                        custom_value=True,
                    )
                ),
                vol.Required(
                    CONF_COOLDOWN, default=current.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=3600, step=1, unit_of_measurement="seconds"
                    )
                ),
                vol.Required(
                    CONF_STALENESS_THRESHOLD,
                    default=current.get(
                        CONF_STALENESS_THRESHOLD, DEFAULT_STALENESS_THRESHOLD
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=1440, step=1, unit_of_measurement="minutes"
                    )
                ),
                vol.Optional(
                    CONF_STALENESS_USE_LAST_UPDATED,
                    default=current.get(
                        CONF_STALENESS_USE_LAST_UPDATED,
                        DEFAULT_STALENESS_USE_LAST_UPDATED,
                    ),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_BATTERY_THRESHOLD,
                    default=current.get(
                        CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, step=1, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_SIGNAL_ENABLED,
                    default=current.get(CONF_SIGNAL_ENABLED, DEFAULT_SIGNAL_ENABLED),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_AVAILABILITY_WINDOWS,
                    default=current.get(
                        CONF_AVAILABILITY_WINDOWS, DEFAULT_AVAILABILITY_WINDOWS
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=AVAILABLE_WINDOWS,
                        multiple=True,
                    )
                ),
                vol.Required(
                    CONF_RECOVERY_WINDOW,
                    default=current.get(CONF_RECOVERY_WINDOW, DEFAULT_RECOVERY_WINDOW),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=60, step=1, unit_of_measurement="minutes"
                    )
                ),
                vol.Optional(
                    CONF_USE_DEVICE_NAMES,
                    default=current.get(
                        CONF_USE_DEVICE_NAMES, DEFAULT_USE_DEVICE_NAMES
                    ),
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_battery_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Battery entity mapping step in options flow."""
        if user_input is not None:
            entities = self._data.get(
                CONF_ENTITIES, self.config_entry.data.get(CONF_ENTITIES, [])
            )
            battery_map = {}
            for entity_id in entities:
                battery_map[entity_id] = user_input.get(entity_id, "")
            self._data[CONF_BATTERY_ENTITY_MAP] = battery_map

            if self._data.get(CONF_SIGNAL_ENABLED):
                return await self.async_step_signal_mapping()

            # Preserve signal map even when disabled so re-enabling restores bindings.
            self._data.setdefault(
                CONF_SIGNAL_ENTITY_MAP,
                self.config_entry.data.get(CONF_SIGNAL_ENTITY_MAP, {}),
            )
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=self._data
            )
            return self.async_create_entry(title="", data={})

        existing_map = self.config_entry.data.get(CONF_BATTERY_ENTITY_MAP, {})
        entities = self._data.get(
            CONF_ENTITIES, self.config_entry.data.get(CONF_ENTITIES, [])
        )

        schema_dict: dict[Any, Any] = {}
        for entity_id in entities:
            if entity_id in existing_map:
                default = existing_map[entity_id]
            else:
                default = self._detect_battery_entity(entity_id)
            schema_dict[
                vol.Optional(
                    entity_id,
                    description={"suggested_value": default} if default else None,
                )
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor"])
            )

        return self.async_show_form(
            step_id="battery_mapping",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_signal_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Signal entity mapping step in options flow."""
        entities = self._data.get(
            CONF_ENTITIES, self.config_entry.data.get(CONF_ENTITIES, [])
        )
        existing_map = self.config_entry.data.get(CONF_SIGNAL_ENTITY_MAP, {})

        if user_input is not None:
            # Merge into existing map: submitted sensors update/add; empty selector
            # explicitly removes the mapping so stale entries are cleaned up.
            merged = dict(existing_map)
            for entity_id in entities:
                sensor = user_input.get(entity_id, "")
                if sensor:
                    nt = user_input.get(f"{entity_id}#network", "") or "generic"
                    merged[entity_id] = {"sensor": sensor, "network_type": nt}
                else:
                    merged.pop(entity_id, None)
            self._data[CONF_SIGNAL_ENTITY_MAP] = merged
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=self._data
            )
            return self.async_create_entry(title="", data={})

        schema_dict: dict[Any, Any] = {}
        for entity_id in entities:
            existing = existing_map.get(entity_id, {})
            suggested = existing.get("sensor") or _detect_signal_entity(
                self.hass, entity_id
            )
            schema_dict[
                vol.Optional(
                    entity_id,
                    description={"suggested_value": suggested} if suggested else None,
                )
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["sensor", "input_number", "number"]
                )
            )
            schema_dict[
                vol.Optional(
                    f"{entity_id}#network",
                    default=existing.get("network_type")
                    or (_infer_network_type(suggested) if suggested else "generic"),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=_SIGNAL_NETWORK_TYPE_OPTIONS)
            )

        return self.async_show_form(
            step_id="signal_mapping",
            data_schema=vol.Schema(schema_dict),
        )

    def _detect_battery_entity(self, entity_id: str) -> str:
        """Auto-detect battery entity for a monitored entity."""
        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(entity_id)
        if entry and entry.device_id:
            for ent in er.async_entries_for_device(ent_reg, entry.device_id):
                if ent.entity_id == entity_id:
                    continue
                if ent.original_device_class == SensorDeviceClass.BATTERY or (
                    ent.device_class == SensorDeviceClass.BATTERY
                ):
                    return ent.entity_id

        parts = entity_id.split(".", 1)
        if len(parts) == 2:  # pragma: no branch
            battery_entity = f"sensor.{parts[1]}_battery"
            if self.hass.states.get(battery_entity):
                return battery_entity

        return ""


class CombinedGroupOptionsFlow(OptionsFlow):
    """Handle options flow for a combined group entry."""

    init_step = "combined_init"

    async def async_step_combined_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit combined group name and included groups."""
        errors: dict[str, str] = {}

        existing_groups = [
            e
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_GROUP) == ENTRY_TYPE_GROUP
            and e.state == ConfigEntryState.LOADED
        ]

        if user_input is not None:
            group_name = user_input[CONF_GROUP_NAME].strip()
            combined_groups = user_input[CONF_COMBINED_GROUPS]

            if not group_name:
                errors[CONF_GROUP_NAME] = "empty_group_name"
            elif len(combined_groups) < 2:
                errors[CONF_COMBINED_GROUPS] = "not_enough_groups_selected"
            else:
                new_data = {
                    **self.config_entry.data,
                    CONF_GROUP_NAME: group_name,
                    CONF_COMBINED_GROUPS: combined_groups,
                }
                self.hass.config_entries.async_update_entry(
                    self.config_entry, title=group_name, data=new_data
                )
                return self.async_create_entry(title="", data={})

        current = self.config_entry.data
        valid_ids = {e.entry_id for e in existing_groups}
        default_combined = [
            eid for eid in current.get(CONF_COMBINED_GROUPS, []) if eid in valid_ids
        ]
        group_options = [
            selector.SelectOptionDict(value=e.entry_id, label=e.title)
            for e in existing_groups
        ]

        return self.async_show_form(
            step_id="combined_init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GROUP_NAME, default=current.get(CONF_GROUP_NAME, "")
                    ): str,
                    vol.Required(
                        CONF_COMBINED_GROUPS,
                        default=default_combined,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=group_options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )
