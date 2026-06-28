"""Tests for the Entity Availability config flow."""

from __future__ import annotations

from unittest.mock import patch


from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.const import (
    CONF_AVAILABILITY_WINDOWS,
    CONF_BAD_STATES,
    CONF_BATTERY_ENTITY_MAP,
    CONF_BATTERY_THRESHOLD,
    CONF_COMBINED_GROUPS,
    CONF_COOLDOWN,
    CONF_ENTITIES,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    CONF_STALENESS_THRESHOLD,
    CONF_USE_DEVICE_NAMES,
    DEFAULT_AVAILABILITY_WINDOWS,
    DEFAULT_BAD_STATES,
    DEFAULT_COOLDOWN,
    DEFAULT_STALENESS_THRESHOLD,
    DEFAULT_USE_DEVICE_NAMES,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
    ENTRY_TYPE_GROUP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _init_flow(hass: HomeAssistant) -> dict:
    """Start a new config flow and return the initial result."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _step_group(
    hass: HomeAssistant, flow_id: str, name: str, entities: list
) -> dict:
    """Submit the type-selector and the group step in one go."""
    # First pick "group" entry type
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"entry_type": ENTRY_TYPE_GROUP}
    )
    assert result["step_id"] == "group"
    # Then fill in name + entities
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_GROUP_NAME: name, CONF_ENTITIES: entities}
    )


# ---------------------------------------------------------------------------
# user step
# ---------------------------------------------------------------------------


async def test_step_user_shows_form(hass: HomeAssistant) -> None:
    """Test that the first step shows the type-selector form."""
    result = await _init_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_step_user_group_goes_to_group(hass: HomeAssistant) -> None:
    """Choosing 'group' advances to the group step."""
    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entry_type": ENTRY_TYPE_GROUP}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "group"


async def test_step_user_combined_aborts_without_enough_groups(
    hass: HomeAssistant,
) -> None:
    """Choosing 'combined_group' aborts when < 2 source groups exist."""
    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entry_type": ENTRY_TYPE_COMBINED}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "not_enough_groups"


# ---------------------------------------------------------------------------
# group step
# ---------------------------------------------------------------------------


async def test_step_group_empty_name(hass: HomeAssistant) -> None:
    """Validation error on blank group name."""
    result = await _init_flow(hass)
    # Get to group step
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entry_type": ENTRY_TYPE_GROUP}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_GROUP_NAME: "   ", CONF_ENTITIES: ["binary_sensor.test"]},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_GROUP_NAME: "empty_group_name"}


async def test_step_group_no_entities(hass: HomeAssistant) -> None:
    """Validation error when no entities selected."""
    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entry_type": ENTRY_TYPE_GROUP}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_GROUP_NAME: "Test Group", CONF_ENTITIES: []},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_ENTITIES: "no_entities"}


async def test_step_group_valid_goes_to_monitoring(hass: HomeAssistant) -> None:
    """Valid group input advances to monitoring step."""
    result = await _init_flow(hass)
    result = await _step_group(
        hass, result["flow_id"], "My Devices", ["binary_sensor.test"]
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "monitoring"


# ---------------------------------------------------------------------------
# monitoring / advanced / battery_mapping steps
# ---------------------------------------------------------------------------


async def test_step_monitoring_goes_to_advanced(hass: HomeAssistant) -> None:
    """Monitoring step advances to advanced step."""
    result = await _init_flow(hass)
    result = await _step_group(
        hass, result["flow_id"], "My Devices", ["binary_sensor.test"]
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BAD_STATES: DEFAULT_BAD_STATES,
            CONF_COOLDOWN: DEFAULT_COOLDOWN,
            CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "advanced"


async def test_full_config_flow_with_battery(hass: HomeAssistant) -> None:
    """Test complete flow with battery threshold > 0 shows battery mapping step."""
    result = await _init_flow(hass)

    # Step 1: type-selector + group
    result = await _step_group(
        hass,
        result["flow_id"],
        "Office Devices",
        ["sensor.desk_lamp", "binary_sensor.motion"],
    )
    assert result["step_id"] == "monitoring"

    # Step 2: Monitoring
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BAD_STATES: ["unavailable"],
            CONF_COOLDOWN: 120,
            CONF_STALENESS_THRESHOLD: 30,
        },
    )
    assert result["step_id"] == "advanced"

    # Step 3: Advanced (battery > 0)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BATTERY_THRESHOLD: 10, CONF_AVAILABILITY_WINDOWS: ["today", "7d"]},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"

    # Step 4: Battery mapping
    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"sensor.desk_lamp": "sensor.desk_lamp_battery"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Office Devices"
    assert result["data"][CONF_BATTERY_THRESHOLD] == 10
    assert result["data"][CONF_BATTERY_ENTITY_MAP] == {
        "sensor.desk_lamp": "sensor.desk_lamp_battery",
        "binary_sensor.motion": "",
    }


async def test_full_config_flow_no_battery(hass: HomeAssistant) -> None:
    """Test complete flow with battery threshold = 0 skips battery mapping."""
    result = await _init_flow(hass)

    result = await _step_group(
        hass, result["flow_id"], "Office Devices", ["sensor.desk_lamp"]
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BAD_STATES: ["unavailable"],
            CONF_COOLDOWN: 60,
            CONF_STALENESS_THRESHOLD: 0,
        },
    )

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BATTERY_THRESHOLD: 0, CONF_AVAILABILITY_WINDOWS: ["today", "7d"]},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BATTERY_ENTITY_MAP] == {}


async def test_duplicate_prevention(hass: HomeAssistant) -> None:
    """Test that duplicate unique_id is prevented."""
    result = await _init_flow(hass)
    result = await _step_group(hass, result["flow_id"], "My Group", ["sensor.test"])

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BAD_STATES: DEFAULT_BAD_STATES,
            CONF_COOLDOWN: DEFAULT_COOLDOWN,
            CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
        },
    )
    # battery_threshold = 0 → skip battery mapping
    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BATTERY_THRESHOLD: 0,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    # Second flow with same name should be aborted at group step
    result2 = await _init_flow(hass)
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"], {"entry_type": ENTRY_TYPE_GROUP}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {CONF_GROUP_NAME: "My Group", CONF_ENTITIES: ["sensor.other"]},
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant, mock_config_entry) -> None:
    """Test options flow with battery threshold = 0 skips battery mapping."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENTITIES: ["binary_sensor.device_a", "binary_sensor.device_b"],
            CONF_BAD_STATES: ["unavailable"],
            CONF_COOLDOWN: 90,
            CONF_STALENESS_THRESHOLD: 15,
            CONF_BATTERY_THRESHOLD: 0,
            CONF_AVAILABILITY_WINDOWS: ["today", "3d"],
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    assert mock_config_entry.data[CONF_COOLDOWN] == 90
    assert mock_config_entry.data[CONF_BATTERY_ENTITY_MAP] == {}


async def test_options_flow_with_battery_mapping(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Test options flow with battery threshold > 0 shows battery mapping."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENTITIES: ["binary_sensor.device_a", "binary_sensor.device_b"],
            CONF_BAD_STATES: ["unavailable"],
            CONF_COOLDOWN: 60,
            CONF_STALENESS_THRESHOLD: 0,
            CONF_BATTERY_THRESHOLD: 25,
            CONF_AVAILABILITY_WINDOWS: ["today"],
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"binary_sensor.device_a": "sensor.device_a_battery"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert mock_config_entry.data[CONF_BATTERY_ENTITY_MAP] == {
        "binary_sensor.device_a": "sensor.device_a_battery",
        "binary_sensor.device_b": "",
    }


async def test_battery_auto_detection(hass: HomeAssistant) -> None:
    """Test that battery entity auto-detection works in config flow."""
    # Set up a battery entity state so convention-based detection finds it
    hass.states.async_set("sensor.desk_lamp_battery", "85")

    result = await _init_flow(hass)
    result = await _step_group(
        hass, result["flow_id"], "Detection Test", ["light.desk_lamp"]
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BAD_STATES: DEFAULT_BAD_STATES,
            CONF_COOLDOWN: DEFAULT_COOLDOWN,
            CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BATTERY_THRESHOLD: 20,
            CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
        },
    )
    # Should show battery_mapping with pre-detected value
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


# ---------------------------------------------------------------------------
# Combined group config flow
# ---------------------------------------------------------------------------


async def _create_group_entry(
    hass: HomeAssistant, name: str, entities: list[str]
) -> MockConfigEntry:
    """Helper: create and register a real group config entry."""
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title=name,
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_GROUP,
            CONF_GROUP_NAME: name,
            CONF_ENTITIES: entities,
        },
        entry_id=f"entry_{name.lower().replace(' ', '_')}",
        unique_id=f"{DOMAIN}_{name.lower().replace(' ', '_')}",
    )
    entry.add_to_hass(hass)
    return entry


async def test_step_combined_shows_form(hass: HomeAssistant) -> None:
    """When >=2 group entries exist, combined step shows a form."""
    await _create_group_entry(hass, "Group A", ["binary_sensor.a"])
    await _create_group_entry(hass, "Group B", ["binary_sensor.b"])

    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entry_type": ENTRY_TYPE_COMBINED}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "combined"


async def test_step_combined_empty_name_error(hass: HomeAssistant) -> None:
    """Blank combined group name raises validation error."""
    entry_a = await _create_group_entry(hass, "Group A", ["binary_sensor.a"])
    entry_b = await _create_group_entry(hass, "Group B", ["binary_sensor.b"])

    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entry_type": ENTRY_TYPE_COMBINED}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_GROUP_NAME: "   ",
            CONF_COMBINED_GROUPS: [entry_a.entry_id, entry_b.entry_id],
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_GROUP_NAME: "empty_group_name"}


async def test_step_combined_not_enough_groups_selected_error(
    hass: HomeAssistant,
) -> None:
    """Selecting < 2 groups raises validation error."""
    entry_a = await _create_group_entry(hass, "Group A", ["binary_sensor.a"])
    await _create_group_entry(hass, "Group B", ["binary_sensor.b"])

    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entry_type": ENTRY_TYPE_COMBINED}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_GROUP_NAME: "My Combined", CONF_COMBINED_GROUPS: [entry_a.entry_id]},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_COMBINED_GROUPS: "not_enough_groups_selected"}


async def test_step_combined_creates_entry(hass: HomeAssistant) -> None:
    """Valid combined group input creates a config entry."""
    entry_a = await _create_group_entry(hass, "Group A", ["binary_sensor.a"])
    entry_b = await _create_group_entry(hass, "Group B", ["binary_sensor.b"])

    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entry_type": ENTRY_TYPE_COMBINED}
    )
    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_GROUP_NAME: "My Combined",
                CONF_COMBINED_GROUPS: [entry_a.entry_id, entry_b.entry_id],
            },
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Combined"
    assert result["data"][CONF_ENTRY_TYPE] == ENTRY_TYPE_COMBINED
    assert result["data"][CONF_COMBINED_GROUPS] == [entry_a.entry_id, entry_b.entry_id]


async def test_step_combined_duplicate_prevention(hass: HomeAssistant) -> None:
    """Creating a combined group with the same name is aborted."""
    entry_a = await _create_group_entry(hass, "Group A", ["binary_sensor.a"])
    entry_b = await _create_group_entry(hass, "Group B", ["binary_sensor.b"])

    # First combined entry
    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entry_type": ENTRY_TYPE_COMBINED}
    )
    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_GROUP_NAME: "My Combined",
                CONF_COMBINED_GROUPS: [entry_a.entry_id, entry_b.entry_id],
            },
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    # Second combined entry with the same name
    result2 = await _init_flow(hass)
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"], {"entry_type": ENTRY_TYPE_COMBINED}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {
            CONF_GROUP_NAME: "My Combined",
            CONF_COMBINED_GROUPS: [entry_a.entry_id, entry_b.entry_id],
        },
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_combined_options_flow_shows_form(hass: HomeAssistant) -> None:
    """Options flow for a combined entry shows the init form."""
    entry_a = await _create_group_entry(hass, "Group A", ["binary_sensor.a"])
    entry_b = await _create_group_entry(hass, "Group B", ["binary_sensor.b"])

    combined = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="My Combined",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
            CONF_GROUP_NAME: "My Combined",
            CONF_COMBINED_GROUPS: [entry_a.entry_id, entry_b.entry_id],
        },
        entry_id="combined_entry_id",
        unique_id=f"{DOMAIN}_combined_my_combined",
    )
    combined.add_to_hass(hass)

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(combined.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(combined.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "combined_init"


async def test_combined_options_flow_updates_entry(hass: HomeAssistant) -> None:
    """Options flow for combined entry saves new name and groups."""
    entry_a = await _create_group_entry(hass, "Group A", ["binary_sensor.a"])
    entry_b = await _create_group_entry(hass, "Group B", ["binary_sensor.b"])
    entry_c = await _create_group_entry(hass, "Group C", ["binary_sensor.c"])

    combined = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="My Combined",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
            CONF_GROUP_NAME: "My Combined",
            CONF_COMBINED_GROUPS: [entry_a.entry_id, entry_b.entry_id],
        },
        entry_id="combined_entry_id2",
        unique_id=f"{DOMAIN}_combined_my_combined_v2",
    )
    combined.add_to_hass(hass)

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(combined.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(combined.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_GROUP_NAME: "Renamed Combined",
            CONF_COMBINED_GROUPS: [entry_a.entry_id, entry_c.entry_id],
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert combined.data[CONF_GROUP_NAME] == "Renamed Combined"
    assert combined.data[CONF_COMBINED_GROUPS] == [entry_a.entry_id, entry_c.entry_id]


async def test_combined_options_flow_empty_name_error(hass: HomeAssistant) -> None:
    """Combined options flow returns error when group name is empty."""
    entry_a = await _create_group_entry(hass, "Group A", ["binary_sensor.a"])
    entry_b = await _create_group_entry(hass, "Group B", ["binary_sensor.b"])

    combined = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="My Combined",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
            CONF_GROUP_NAME: "My Combined",
            CONF_COMBINED_GROUPS: [entry_a.entry_id, entry_b.entry_id],
        },
        entry_id="combined_err1",
        unique_id=f"{DOMAIN}_combined_err1",
    )
    combined.add_to_hass(hass)

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(combined.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(combined.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_GROUP_NAME: "   ",
            CONF_COMBINED_GROUPS: [entry_a.entry_id, entry_b.entry_id],
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_GROUP_NAME] == "empty_group_name"


async def test_combined_options_flow_not_enough_groups_error(
    hass: HomeAssistant,
) -> None:
    """Combined options flow returns error when fewer than 2 groups selected."""
    entry_a = await _create_group_entry(hass, "Group A", ["binary_sensor.a"])
    entry_b = await _create_group_entry(hass, "Group B", ["binary_sensor.b"])

    combined = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="My Combined",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
            CONF_GROUP_NAME: "My Combined",
            CONF_COMBINED_GROUPS: [entry_a.entry_id, entry_b.entry_id],
        },
        entry_id="combined_err2",
        unique_id=f"{DOMAIN}_combined_err2",
    )
    combined.add_to_hass(hass)

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(combined.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(combined.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_GROUP_NAME: "Valid Name",
            CONF_COMBINED_GROUPS: [entry_a.entry_id],
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_COMBINED_GROUPS] == "not_enough_groups_selected"


async def test_battery_auto_detection_via_device_registry(
    hass: HomeAssistant,
) -> None:
    """Battery detection finds battery sensor via device registry in config flow.

    Also covers the 'continue' branch that skips the monitored entity itself
    when iterating device registry entries.
    """
    from unittest.mock import MagicMock, patch

    from homeassistant.components.sensor import SensorDeviceClass

    # self-entry: same entity_id as monitored — must be skipped (line 314 continue)
    mock_self_entry = MagicMock()
    mock_self_entry.entity_id = "lock.front_door"
    mock_self_entry.original_device_class = None
    mock_self_entry.device_class = None

    mock_bat_entry = MagicMock()
    mock_bat_entry.entity_id = "sensor.lock_battery"
    mock_bat_entry.original_device_class = SensorDeviceClass.BATTERY
    mock_bat_entry.device_class = SensorDeviceClass.BATTERY

    mock_monitored_entry = MagicMock()
    mock_monitored_entry.device_id = "device_abc"

    mock_ent_reg = MagicMock()
    mock_ent_reg.async_get.return_value = mock_monitored_entry

    hass.states.async_set("sensor.lock_battery", "72")

    with (
        patch(
            "custom_components.entity_availability.config_flow.er.async_get",
            return_value=mock_ent_reg,
        ),
        patch(
            "custom_components.entity_availability.config_flow.er.async_entries_for_device",
            return_value=[mock_self_entry, mock_bat_entry],
        ),
    ):
        result = await _init_flow(hass)
        result = await _step_group(
            hass, result["flow_id"], "Lock Group", ["lock.front_door"]
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BATTERY_THRESHOLD: 20,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


async def test_battery_auto_detection_via_device_registry_options_flow(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Battery detection via device registry in options flow covers lines 479-491.

    Submits options with battery_threshold > 0 so _detect_battery_entity is called.
    Two passes:
    1. self-entry returned first — hits the continue branch (line 480)
    2. a non-battery entry returned — falls through to naming convention (line 491)
    """
    from unittest.mock import MagicMock, patch

    from homeassistant.components.sensor import SensorDeviceClass

    # self-entry: same entity_id as monitored — must be skipped (continue, line 480)
    mock_self_entry = MagicMock()
    mock_self_entry.entity_id = "binary_sensor.device_a"
    mock_self_entry.original_device_class = None
    mock_self_entry.device_class = None

    # Non-battery sibling entry — not skipped but no battery class → falls through
    mock_other_entry = MagicMock()
    mock_other_entry.entity_id = "sensor.device_a_signal"
    mock_other_entry.original_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    mock_other_entry.device_class = None

    mock_monitored_entry = MagicMock()
    mock_monitored_entry.device_id = "device_xyz"

    mock_ent_reg = MagicMock()
    mock_ent_reg.async_get.return_value = mock_monitored_entry

    mock_config_entry.add_to_hass(hass)
    # Set battery state so naming-convention fallback (line 491) is hit
    hass.states.async_set("sensor.device_a_battery", "80")
    # Set battery state so naming-convention fallback (line 491) is also hit
    hass.states.async_set("sensor.device_a_battery", "80")

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    with (
        patch(
            "custom_components.entity_availability.config_flow.er.async_get",
            return_value=mock_ent_reg,
        ),
        patch(
            "custom_components.entity_availability.config_flow.er.async_entries_for_device",
            return_value=[mock_self_entry, mock_other_entry],
        ),
    ):
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_ENTITIES: [
                    "binary_sensor.device_a",
                    "binary_sensor.device_b",
                    "binary_sensor.device_c",
                ],
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
                CONF_BATTERY_THRESHOLD: 20,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )

    # With battery_threshold > 0, should proceed to battery_mapping step
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


async def test_battery_auto_detection_registry_success_options_flow(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Options flow _detect_battery_entity returns registry entity (line 485)."""
    from unittest.mock import MagicMock, patch

    from homeassistant.components.sensor import SensorDeviceClass

    mock_bat_entry = MagicMock()
    mock_bat_entry.entity_id = "sensor.device_a_battery"
    mock_bat_entry.original_device_class = SensorDeviceClass.BATTERY
    mock_bat_entry.device_class = SensorDeviceClass.BATTERY

    mock_monitored_entry = MagicMock()
    mock_monitored_entry.device_id = "device_xyz"

    mock_ent_reg = MagicMock()
    mock_ent_reg.async_get.return_value = mock_monitored_entry

    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    with (
        patch(
            "custom_components.entity_availability.config_flow.er.async_get",
            return_value=mock_ent_reg,
        ),
        patch(
            "custom_components.entity_availability.config_flow.er.async_entries_for_device",
            return_value=[mock_bat_entry],
        ),
    ):
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_ENTITIES: [
                    "binary_sensor.device_a",
                    "binary_sensor.device_b",
                    "binary_sensor.device_c",
                ],
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
                CONF_BATTERY_THRESHOLD: 20,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


# ---------------------------------------------------------------------------
# use_device_names config flow
# ---------------------------------------------------------------------------


async def test_advanced_step_includes_use_device_names_field(
    hass: HomeAssistant,
) -> None:
    """Advanced step schema exposes the use_device_names field."""
    result = await _init_flow(hass)
    result = await _step_group(
        hass, result["flow_id"], "UDN Group", ["binary_sensor.test"]
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BAD_STATES: DEFAULT_BAD_STATES,
            CONF_COOLDOWN: DEFAULT_COOLDOWN,
            CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "advanced"
    schema_keys = {
        k.schema if hasattr(k, "schema") else k for k in result["data_schema"].schema
    }
    assert CONF_USE_DEVICE_NAMES in schema_keys


async def test_advanced_step_stores_use_device_names_true(
    hass: HomeAssistant,
) -> None:
    """Submitting advanced step with use_device_names=True persists the value."""
    result = await _init_flow(hass)
    result = await _step_group(
        hass, result["flow_id"], "UDN True Group", ["binary_sensor.test"]
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BAD_STATES: DEFAULT_BAD_STATES,
            CONF_COOLDOWN: DEFAULT_COOLDOWN,
            CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
        },
    )
    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BATTERY_THRESHOLD: 0,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
                CONF_USE_DEVICE_NAMES: True,
            },
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USE_DEVICE_NAMES] is True


async def test_advanced_step_defaults_use_device_names_false(
    hass: HomeAssistant,
) -> None:
    """Omitting use_device_names from advanced step defaults it to False."""
    result = await _init_flow(hass)
    result = await _step_group(
        hass, result["flow_id"], "UDN Default Group", ["binary_sensor.test"]
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BAD_STATES: DEFAULT_BAD_STATES,
            CONF_COOLDOWN: DEFAULT_COOLDOWN,
            CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
        },
    )
    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BATTERY_THRESHOLD: 0,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
                # CONF_USE_DEVICE_NAMES intentionally omitted
            },
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"].get(CONF_USE_DEVICE_NAMES, DEFAULT_USE_DEVICE_NAMES) is False


async def test_options_flow_includes_use_device_names(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Options flow init step schema includes use_device_names field."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    schema_keys = {
        k.schema if hasattr(k, "schema") else k for k in result["data_schema"].schema
    }
    assert CONF_USE_DEVICE_NAMES in schema_keys


async def test_options_flow_updates_use_device_names(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Submitting options flow with use_device_names=True updates config entry data."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENTITIES: ["binary_sensor.device_a", "binary_sensor.device_b"],
            CONF_BAD_STATES: DEFAULT_BAD_STATES,
            CONF_COOLDOWN: DEFAULT_COOLDOWN,
            CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
            CONF_BATTERY_THRESHOLD: 0,
            CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            CONF_USE_DEVICE_NAMES: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert mock_config_entry.data[CONF_USE_DEVICE_NAMES] is True


class TestUseDeviceNamesConfigFlow:
    """Tests for use_device_names option in config flow."""

    async def test_use_device_names_in_options_flow(self, hass):
        """Test use_device_names flag survives options flow round-trip."""
        from custom_components.entity_availability.const import (
            CONF_USE_DEVICE_NAMES,
            CONF_GROUP_NAME,
            CONF_ENTITIES,
            CONF_BAD_STATES,
            CONF_COOLDOWN,
            CONF_STALENESS_THRESHOLD,
            CONF_BATTERY_THRESHOLD,
            CONF_AVAILABILITY_WINDOWS,
            CONF_BATTERY_ENTITY_MAP,
            CONF_RECOVERY_WINDOW,
            DEFAULT_BAD_STATES,
            DEFAULT_COOLDOWN,
            DEFAULT_STALENESS_THRESHOLD,
            DEFAULT_AVAILABILITY_WINDOWS,
            DEFAULT_RECOVERY_WINDOW,
            DOMAIN,
            ENTRY_TYPE_GROUP,
            CONF_ENTRY_TYPE,
        )
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        entry = MockConfigEntry(
            version=1,
            domain=DOMAIN,
            title="Test",
            data={
                CONF_ENTRY_TYPE: ENTRY_TYPE_GROUP,
                CONF_GROUP_NAME: "Test",
                CONF_ENTITIES: ["binary_sensor.test"],
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
                CONF_BATTERY_THRESHOLD: 0,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
                CONF_BATTERY_ENTITY_MAP: {},
                CONF_RECOVERY_WINDOW: DEFAULT_RECOVERY_WINDOW,
                CONF_USE_DEVICE_NAMES: False,
            },
            entry_id="test_options_entry",
        )
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
        assert any("use_device_names" in k for k in schema_keys)


# ---------------------------------------------------------------------------
# Branch coverage: _detect_battery_entity config flow (lines 320->328, 323->320, 329->334)
# ---------------------------------------------------------------------------


async def test_detect_battery_entity_device_has_no_battery_entity(
    hass: HomeAssistant,
) -> None:
    """Device registry entry exists but has no battery-class entity — falls to guessed name path.

    Covers: 320->328 (loop exhausted with no battery entity),
            323->320 (self-entity skipped via continue).
    Guessed name also absent so _detect_battery_entity returns "".
    """
    from unittest.mock import MagicMock, patch

    mock_self_entry = MagicMock()
    mock_self_entry.entity_id = "binary_sensor.no_bat_device"
    mock_self_entry.original_device_class = None
    mock_self_entry.device_class = None

    mock_sibling = MagicMock()
    mock_sibling.entity_id = "sensor.no_bat_device_temperature"
    mock_sibling.original_device_class = "temperature"
    mock_sibling.device_class = "temperature"

    mock_reg_entry = MagicMock()
    mock_reg_entry.device_id = "dev_no_bat"

    mock_ent_reg = MagicMock()
    mock_ent_reg.async_get.return_value = mock_reg_entry

    # No guessed battery state
    with (
        patch(
            "custom_components.entity_availability.config_flow.er.async_get",
            return_value=mock_ent_reg,
        ),
        patch(
            "custom_components.entity_availability.config_flow.er.async_entries_for_device",
            return_value=[mock_self_entry, mock_sibling],
        ),
    ):
        result = await _init_flow(hass)
        result = await _step_group(
            hass, result["flow_id"], "NoBat Group", ["binary_sensor.no_bat_device"]
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BATTERY_THRESHOLD: 20,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


async def test_detect_battery_entity_guessed_name_found_in_config_flow(
    hass: HomeAssistant,
) -> None:
    """Guessed sensor.<slug>_battery is found in state — returned by _detect_battery_entity.

    Covers: 329->334 branch where guessed entity EXISTS and is returned.
    Uses no registry entry so loop is skipped entirely.
    """
    from unittest.mock import MagicMock, patch

    mock_ent_reg = MagicMock()
    mock_ent_reg.async_get.return_value = None  # no registry entry

    hass.states.async_set("sensor.motion_sensor_battery", "42")

    with patch(
        "custom_components.entity_availability.config_flow.er.async_get",
        return_value=mock_ent_reg,
    ):
        result = await _init_flow(hass)
        result = await _step_group(
            hass, result["flow_id"], "Motion Group", ["binary_sensor.motion_sensor"]
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BATTERY_THRESHOLD: 20,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


# ---------------------------------------------------------------------------
# Branch coverage: options flow _detect_battery_entity (lines 474->476, 502->507)
# ---------------------------------------------------------------------------


async def test_options_flow_detect_battery_entity_no_existing_map(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Options flow calls _detect_battery_entity when no existing map default.

    Covers: 474->476 (empty existing_map triggers _detect_battery_entity call),
            502->507 (guessed battery state found in options flow variant).
    """
    from unittest.mock import MagicMock, patch

    mock_ent_reg = MagicMock()
    mock_ent_reg.async_get.return_value = None  # no registry entry

    mock_config_entry.add_to_hass(hass)
    hass.states.async_set("sensor.device_a_battery", "77")

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    with (
        patch(
            "custom_components.entity_availability.config_flow.er.async_get",
            return_value=mock_ent_reg,
        ),
    ):
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_ENTITIES: ["binary_sensor.device_a"],
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
                CONF_BATTERY_THRESHOLD: 20,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


async def test_detect_battery_entity_no_device_id_no_guessed_state(
    hass: HomeAssistant,
) -> None:
    """No device_id and no guessed battery state — _detect_battery_entity returns ''.

    Covers: 329->334 (guessed entity absent → fall through to return '').
    """
    from unittest.mock import MagicMock, patch

    mock_ent_reg = MagicMock()
    mock_ent_reg.async_get.return_value = None  # no registry entry

    # Do NOT set sensor.no_bat_sensor_battery state

    with patch(
        "custom_components.entity_availability.config_flow.er.async_get",
        return_value=mock_ent_reg,
    ):
        result = await _init_flow(hass)
        result = await _step_group(
            hass, result["flow_id"], "No Bat Group", ["sensor.no_bat_sensor"]
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BATTERY_THRESHOLD: 20,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


async def test_options_flow_detect_battery_no_guessed_state(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Options flow: no existing map, no guessed battery state — returns ''.

    Covers: 474->476 (empty map → detect called) and 502->507 (no guessed state → return '').
    """
    from unittest.mock import MagicMock, patch

    mock_ent_reg = MagicMock()
    mock_ent_reg.async_get.return_value = None  # no registry entry

    mock_config_entry.add_to_hass(hass)
    # Do NOT set sensor.device_a_battery state

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    with patch(
        "custom_components.entity_availability.config_flow.er.async_get",
        return_value=mock_ent_reg,
    ):
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_ENTITIES: ["binary_sensor.device_a"],
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
                CONF_BATTERY_THRESHOLD: 20,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


async def test_detect_battery_entity_guessed_state_absent_returns_empty(
    hass: HomeAssistant,
) -> None:
    """_detect_battery_entity returns '' when no guessed battery state found.

    Covers: 329->334 (guessed entity absent → condition False → return '').
    Entity has device_id but device loop finds no battery class.
    No guessed battery state in hass.states.
    """
    from unittest.mock import MagicMock, patch

    mock_sibling = MagicMock()
    mock_sibling.entity_id = "sensor.door_lock_temperature"
    mock_sibling.original_device_class = "temperature"
    mock_sibling.device_class = "temperature"

    mock_reg_entry = MagicMock()
    mock_reg_entry.device_id = "dev_door"

    mock_ent_reg = MagicMock()
    mock_ent_reg.async_get.return_value = mock_reg_entry

    # No guessed state (sensor.door_lock_battery not set)
    with (
        patch(
            "custom_components.entity_availability.config_flow.er.async_get",
            return_value=mock_ent_reg,
        ),
        patch(
            "custom_components.entity_availability.config_flow.er.async_entries_for_device",
            return_value=[mock_sibling],
        ),
    ):
        result = await _init_flow(hass)
        result = await _step_group(
            hass, result["flow_id"], "Door Group", ["lock.door_lock"]
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BATTERY_THRESHOLD: 20,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


async def test_options_flow_existing_map_default_skips_detect(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Options flow: entity has existing map default — _detect_battery_entity skipped.

    Covers: 474->476 (default exists → if not default is False → skip detect call).
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.entity_availability.const import (
        CONF_RECOVERY_WINDOW,
        DEFAULT_RECOVERY_WINDOW,
    )

    # Create entry with populated CONF_BATTERY_ENTITY_MAP
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_GROUP,
            CONF_GROUP_NAME: "Test",
            CONF_ENTITIES: ["binary_sensor.device_a"],
            CONF_BAD_STATES: DEFAULT_BAD_STATES,
            CONF_COOLDOWN: DEFAULT_COOLDOWN,
            CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
            CONF_BATTERY_THRESHOLD: 20,
            CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            CONF_BATTERY_ENTITY_MAP: {
                "binary_sensor.device_a": "sensor.device_a_battery"
            },
            CONF_RECOVERY_WINDOW: DEFAULT_RECOVERY_WINDOW,
            CONF_USE_DEVICE_NAMES: False,
        },
        entry_id="test_existing_map",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENTITIES: ["binary_sensor.device_a"],
            CONF_BAD_STATES: DEFAULT_BAD_STATES,
            CONF_COOLDOWN: DEFAULT_COOLDOWN,
            CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
            CONF_BATTERY_THRESHOLD: 20,
            CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
        },
    )

    # Should reach battery_mapping without calling _detect_battery_entity
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"


async def test_options_flow_no_guessed_state_returns_empty(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Options flow: _detect_battery_entity returns '' — guessed state absent.

    Covers: 502->507 (guessed entity absent → condition False → return '').
    """
    from unittest.mock import MagicMock, patch

    mock_ent_reg = MagicMock()
    mock_ent_reg.async_get.return_value = None  # no registry entry

    mock_config_entry.add_to_hass(hass)
    # No guessed battery state

    with patch(
        "custom_components.entity_availability.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    with patch(
        "custom_components.entity_availability.config_flow.er.async_get",
        return_value=mock_ent_reg,
    ):
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_ENTITIES: ["binary_sensor.device_a"],
                CONF_BAD_STATES: DEFAULT_BAD_STATES,
                CONF_COOLDOWN: DEFAULT_COOLDOWN,
                CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
                CONF_BATTERY_THRESHOLD: 20,
                CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_mapping"
