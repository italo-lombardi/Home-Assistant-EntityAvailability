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
        "battery_threshold": 20,
        "availability_windows": ["today", "7d"],
        "recovery_window": 5,
        "battery_entity_map": {},
        "use_device_names": False,
        "staleness_use_last_updated": False,
    }


@pytest.mark.asyncio
async def test_diagnostics_group_entry(mock_hass: HomeAssistant, mock_config_data_diag):
    """Diagnostics returns correct counts for a group entry."""
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
    assert result["config"]["cooldown_seconds"] == 30
    assert result["config"]["staleness_threshold_minutes"] == 10
    assert result["config"]["battery_threshold_pct"] == 20
    assert result["config"]["availability_windows"] == ["today", "7d"]
    assert result["entity_count"] == 2
    assert result["essential_count"] == 1
    assert result["non_essential_count"] == 1
    assert result["offline_count"] == 1
    assert result["offline_count_non_essential"] == 0
    assert result["suppressed_count"] == 0
    assert result["low_battery_count"] == 0  # NE excluded
    assert result["stale_count"] == 0


@pytest.mark.asyncio
async def test_diagnostics_combined_entry(mock_hass: HomeAssistant):
    """Diagnostics returns summary for a combined entry."""
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
    assert result["combined_groups"] == 2


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
    # Don't add coordinator to hass.data

    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["error"] == "coordinator not loaded"
