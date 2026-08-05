"""Shared fixtures for Entity Availability tests."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability.const import (
    CONF_AVAILABILITY_WINDOWS,
    CONF_BAD_STATES,
    CONF_BATTERY_ENTITY_MAP,
    CONF_BATTERY_THRESHOLD,
    CONF_COOLDOWN,
    CONF_ENTITIES,
    CONF_GROUP_NAME,
    CONF_STALENESS_THRESHOLD,
    DEFAULT_AVAILABILITY_WINDOWS,
    DEFAULT_BAD_STATES,
    DEFAULT_BATTERY_THRESHOLD,
    DEFAULT_COOLDOWN,
    DEFAULT_STALENESS_THRESHOLD,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield


@pytest.fixture
def mock_config_data() -> dict[str, Any]:
    """Return a standard config data dict."""
    return {
        CONF_GROUP_NAME: "Test Group",
        CONF_ENTITIES: [
            "binary_sensor.device_a",
            "binary_sensor.device_b",
            "binary_sensor.device_c",
        ],
        CONF_BAD_STATES: DEFAULT_BAD_STATES,
        CONF_COOLDOWN: DEFAULT_COOLDOWN,
        CONF_STALENESS_THRESHOLD: DEFAULT_STALENESS_THRESHOLD,
        CONF_BATTERY_THRESHOLD: DEFAULT_BATTERY_THRESHOLD,
        CONF_AVAILABILITY_WINDOWS: DEFAULT_AVAILABILITY_WINDOWS,
        CONF_BATTERY_ENTITY_MAP: {},
    }


@pytest.fixture
def mock_config_entry(mock_config_data: dict[str, Any]) -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Group",
        data=mock_config_data,
        entry_id="test_entry_id",
        unique_id=f"{DOMAIN}_test_group",
    )


@pytest.fixture
def mock_hass(hass: HomeAssistant) -> HomeAssistant:
    """Return a HA instance with some states pre-populated."""
    hass.states.async_set(
        "binary_sensor.device_a", STATE_ON, {"friendly_name": "Device A"}
    )
    hass.states.async_set(
        "binary_sensor.device_b", STATE_ON, {"friendly_name": "Device B"}
    )
    hass.states.async_set(
        "binary_sensor.device_c", STATE_ON, {"friendly_name": "Device C"}
    )
    return hass
