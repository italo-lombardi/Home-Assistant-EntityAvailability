"""Config flow for Entity Availability integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector

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
    CONF_RECOVERY_WINDOW,
    CONF_STALENESS_THRESHOLD,
    CONF_USE_DEVICE_NAMES,
    DEFAULT_AVAILABILITY_WINDOWS,
    DEFAULT_BAD_STATES,
    DEFAULT_BATTERY_THRESHOLD,
    DEFAULT_COOLDOWN,
    DEFAULT_RECOVERY_WINDOW,
    DEFAULT_STALENESS_THRESHOLD,
    DEFAULT_USE_DEVICE_NAMES,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
    ENTRY_TYPE_GROUP,
)

_LOGGER = logging.getLogger(__name__)


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
            entities = user_input[CONF_ENTITIES]

            if not group_name:
                errors[CONF_GROUP_NAME] = "empty_group_name"
            elif not entities:
                errors[CONF_ENTITIES] = "no_entities"
            else:
                await self.async_set_unique_id(
                    f"{DOMAIN}_{group_name.lower().replace(' ', '_')}"
                )
                self._abort_if_unique_id_configured()

                self._data[CONF_ENTRY_TYPE] = ENTRY_TYPE_GROUP
                self._data[CONF_GROUP_NAME] = group_name
                self._data[CONF_ENTITIES] = entities
                return await self.async_step_monitoring()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_GROUP_NAME): str,
                vol.Required(CONF_ENTITIES): selector.EntitySelector(
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
                    vol.Required(CONF_COMBINED_GROUPS): selector.SelectSelector(
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

            if self._data[CONF_BATTERY_THRESHOLD] > 0:
                return await self.async_step_battery_mapping()

            self._data[CONF_BATTERY_ENTITY_MAP] = {}
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
            ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

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
        if user_input is not None:
            self._data = {**self.config_entry.data, **user_input}

            if self._data.get(CONF_BATTERY_THRESHOLD, 0) > 0:
                return await self.async_step_battery_mapping()

            self._data[CONF_BATTERY_ENTITY_MAP] = {}
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=self._data
            )
            return self.async_create_entry(title="", data={})

        current = self.config_entry.data

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_ENTITIES, default=current.get(CONF_ENTITIES, [])
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
            default = existing_map.get(entity_id, "")
            if not default:
                default = self._detect_battery_entity(entity_id)
            schema_dict[
                vol.Optional(
                    entity_id,
                    description={"suggested_value": default} if default else None,
                )
            ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

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
