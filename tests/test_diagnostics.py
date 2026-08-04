"""Tests for Entity Availability diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.const import (
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)
from custom_components.entity_availability.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.entity_availability.models import DeviceState


@pytest.fixture
def mock_config_data_diag():
    return {
        CONF_GROUP_NAME: "Test Group",
        "entities": ["binary_sensor.device_a", "binary_sensor.device_b"],
        "non_essential_entities": ["binary_sensor.device_b"],
        "bad_states": ["unavailable", "unknown"],
        "cooldown": 30,
        "staleness_threshold": 10,
        "staleness_use_last_updated": True,
        "battery_threshold": 20,
        "signal_enabled": True,
        "availability_windows": ["today", "7d"],
        "recovery_window": 5,
        "battery_entity_map": {"binary_sensor.device_a": "sensor.device_a_battery"},
        "signal_entity_map": {
            "binary_sensor.device_a": {
                "entity": "sensor.device_a_signal",
                "network_type": "zigbee_lqi",
            }
        },
        "use_device_names": False,
    }


@pytest.mark.asyncio
async def test_diagnostics_group_entry(mock_hass: HomeAssistant, mock_config_data_diag):
    """Diagnostics returns correct config, entities, and counts for a group entry."""
    hass = mock_hass
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=mock_config_data_diag,
        entry_id="diag_test_entry",
        unique_id=f"{DOMAIN}_diag_test",
    )
    entry.add_to_hass(hass)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
        coord._device_states = {
            "binary_sensor.device_a": DeviceState(
                entity_id="binary_sensor.device_a",
                is_offline=True,
                is_non_essential=False,
            ),
            "binary_sensor.device_b": DeviceState(
                entity_id="binary_sensor.device_b",
                is_offline=False,
                is_non_essential=True,
                is_low_battery=True,
            ),
        }
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["diag_test_entry"] = coord

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry_type"] == "group"
    assert result["title"] == "Test Group"

    cfg = result["config"]
    assert cfg["cooldown_seconds"] == 30
    assert cfg["staleness_threshold_minutes"] == 10
    assert cfg["staleness_use_last_updated"] is True
    assert cfg["battery_threshold_pct"] == 20
    assert cfg["signal_enabled"] is True
    assert cfg["recovery_window_minutes"] == 5
    assert cfg["bad_states"] == ["unavailable", "unknown"]
    assert cfg["use_device_names"] is False
    assert cfg["availability_windows"] == ["today", "7d"]

    ents = result["entities"]
    assert ents["essential"] == ["binary_sensor.device_a"]
    assert ents["non_essential"] == ["binary_sensor.device_b"]
    assert ents["battery_entity_map"] == {
        "binary_sensor.device_a": "sensor.device_a_battery"
    }
    assert ents["signal_entity_map"] == {
        "binary_sensor.device_a": {
            "entity": "sensor.device_a_signal",
            "network_type": "zigbee_lqi",
        }
    }

    counts = result["counts"]
    assert counts["total"] == 2
    assert counts["essential"] == 1
    assert counts["non_essential"] == 1
    assert counts["offline"] == 1
    assert counts["offline_non_essential"] == 0
    assert counts["suppressed"] == 0
    assert counts["low_battery"] == 0  # NE excluded
    assert counts["stale"] == 0


@pytest.mark.asyncio
async def test_diagnostics_combined_entry(mock_hass: HomeAssistant):
    """Diagnostics returns group list for a combined entry."""
    hass = mock_hass
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Combined",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
            CONF_GROUP_NAME: "Combined",
            "combined_groups": ["entry_a", "entry_b"],
        },
        entry_id="diag_combined",
        unique_id=f"{DOMAIN}_diag_combined",
    )
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry_type"] == "combined"
    assert result["title"] == "Combined"
    assert result["combined_groups"] == ["entry_a", "entry_b"]


@pytest.mark.asyncio
async def test_diagnostics_coordinator_not_loaded(
    mock_hass: HomeAssistant, mock_config_data_diag
):
    """Diagnostics returns error when coordinator not loaded."""
    hass = mock_hass
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=mock_config_data_diag,
        entry_id="diag_unloaded",
        unique_id=f"{DOMAIN}_diag_unloaded",
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["error"] == "coordinator not loaded"


@pytest.mark.asyncio
async def test_diagnostics_group_defaults(mock_hass: HomeAssistant):
    """Diagnostics falls back to defaults when optional fields absent."""
    hass = mock_hass
    minimal_data = {
        CONF_GROUP_NAME: "Minimal",
        "entities": ["binary_sensor.x"],
        "bad_states": ["unavailable"],
        "recovery_window": 5,
        "battery_entity_map": {},
        "use_device_names": False,
        "staleness_use_last_updated": False,
    }
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Minimal",
        data=minimal_data,
        entry_id="diag_minimal",
        unique_id=f"{DOMAIN}_diag_minimal",
    )
    entry.add_to_hass(hass)

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)
        coord._device_states = {}
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["diag_minimal"] = coord

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entities"]["non_essential"] == []
    assert result["entities"]["signal_entity_map"] == {}
    assert result["config"]["signal_enabled"] is False
    assert result["counts"]["total"] == 0
