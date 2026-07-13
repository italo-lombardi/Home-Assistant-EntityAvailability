"""Tests for Entity Availability services."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant

from custom_components.entity_availability.const import DOMAIN
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)
from custom_components.entity_availability.models import DeviceState
from custom_components.entity_availability.services import (
    ATTR_DURATION,
    ATTR_ENTITY_ID,
    ATTR_GROUP,
    SERVICE_SUPPRESS,
    SERVICE_SUPPRESS_INDEFINITELY,
    SERVICE_UNSUPPRESS,
    async_setup_services,
)


@pytest.fixture
async def setup_services(mock_hass: HomeAssistant, mock_config_entry):
    """Set up services with a coordinator in hass.data."""
    hass = mock_hass
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._device_states = {
            "binary_sensor.device_a": DeviceState(
                entity_id="binary_sensor.device_a",
                is_offline=False,
            ),
            "binary_sensor.device_b": DeviceState(
                entity_id="binary_sensor.device_b",
                is_offline=True,
            ),
        }
        coord.data = None  # minimal data attribute for async_set_updated_data

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coord

    await async_setup_services(hass)
    return hass, coord


async def test_suppress_service(setup_services) -> None:
    """Test suppress service sets suppression on entity."""
    hass, coord = setup_services

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUPPRESS,
        {ATTR_ENTITY_ID: "binary_sensor.device_a", ATTR_DURATION: 30},
        blocking=True,
    )

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.is_suppressed is True
    assert device_a.suppress_until is not None
    # Should be approximately 30 minutes from now
    expected_until = datetime.now(timezone.utc) + timedelta(minutes=30)
    diff = abs((device_a.suppress_until - expected_until).total_seconds())
    assert diff < 5  # Allow 5 seconds tolerance


async def test_suppress_service_default_duration(setup_services) -> None:
    """Test suppress service uses default 60 min duration."""
    hass, coord = setup_services

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUPPRESS,
        {ATTR_ENTITY_ID: "binary_sensor.device_a"},
        blocking=True,
    )

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.is_suppressed is True
    expected_until = datetime.now(timezone.utc) + timedelta(minutes=60)
    diff = abs((device_a.suppress_until - expected_until).total_seconds())
    assert diff < 5


async def test_unsuppress_service(setup_services) -> None:
    """Test unsuppress service clears suppression."""
    hass, coord = setup_services

    # First suppress
    coord.suppress_entity(
        "binary_sensor.device_b",
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert coord.device_states["binary_sensor.device_b"].is_suppressed is True

    # Then unsuppress
    await hass.services.async_call(
        DOMAIN,
        SERVICE_UNSUPPRESS,
        {ATTR_ENTITY_ID: "binary_sensor.device_b"},
        blocking=True,
    )

    device_b = coord.device_states["binary_sensor.device_b"]
    assert device_b.is_suppressed is False
    assert device_b.suppress_until is None


async def test_suppress_entity_not_found_logs_warning(setup_services, caplog) -> None:
    """Test warning logged when entity not in any group."""
    hass, coord = setup_services

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUPPRESS,
            {ATTR_ENTITY_ID: "sensor.nonexistent", ATTR_DURATION: 10},
            blocking=True,
        )

    assert "not found in any monitored group" in caplog.text


async def test_unsuppress_entity_not_found_logs_warning(setup_services, caplog) -> None:
    """Test warning logged when unsuppressing unknown entity."""
    hass, coord = setup_services

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UNSUPPRESS,
            {ATTR_ENTITY_ID: "sensor.nonexistent"},
            blocking=True,
        )

    assert "not found in any monitored group" in caplog.text


async def test_services_registered_only_once(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test services are not registered multiple times."""
    hass = mock_hass
    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord = EntityAvailabilityCoordinator(hass, mock_config_entry)
        coord._device_states = {}
        coord.data = None

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry.entry_id] = coord

    await async_setup_services(hass)
    # Second call should not error
    await async_setup_services(hass)

    assert hass.services.has_service(DOMAIN, SERVICE_SUPPRESS)
    assert hass.services.has_service(DOMAIN, SERVICE_UNSUPPRESS)


async def test_suppress_calls_async_set_updated_data(setup_services) -> None:
    """Test that suppress triggers data update notification."""
    hass, coord = setup_services

    with patch.object(coord, "async_set_updated_data") as mock_update:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUPPRESS,
            {ATTR_ENTITY_ID: "binary_sensor.device_a", ATTR_DURATION: 10},
            blocking=True,
        )
        mock_update.assert_called_once()


async def test_unsuppress_calls_async_set_updated_data(setup_services) -> None:
    """Test that unsuppress triggers data update notification."""
    hass, coord = setup_services

    # First suppress
    coord.suppress_entity(
        "binary_sensor.device_a",
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    with patch.object(coord, "async_set_updated_data") as mock_update:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UNSUPPRESS,
            {ATTR_ENTITY_ID: "binary_sensor.device_a"},
            blocking=True,
        )
        mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# suppress_indefinitely service
# ---------------------------------------------------------------------------


async def test_suppress_indefinitely_sets_no_expiry(setup_services) -> None:
    """suppress_indefinitely sets suppression with suppress_until=None."""
    hass, coord = setup_services

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUPPRESS_INDEFINITELY,
        {ATTR_ENTITY_ID: "binary_sensor.device_a"},
        blocking=True,
    )

    device_a = coord.device_states["binary_sensor.device_a"]
    assert device_a.is_suppressed is True
    assert device_a.suppress_until is None


async def test_suppress_indefinitely_by_group(setup_services) -> None:
    """suppress_indefinitely with group suppresses all entities in the group indefinitely."""
    hass, coord = setup_services

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUPPRESS_INDEFINITELY,
        {ATTR_GROUP: "Test Group"},
        blocking=True,
    )

    # All monitored entities should be suppressed (some may only be in _suppressed)
    for entity_id in coord.monitored_entities:
        if entity_id in coord.device_states:
            device = coord.device_states[entity_id]
            assert device.is_suppressed is True
            assert device.suppress_until is None
        else:
            # Entity not yet in _device_states — suppression stored in _suppressed
            assert entity_id in coord._suppressed
            assert coord._suppressed[entity_id] is None


async def test_suppress_indefinitely_unknown_entity_logs_warning(
    setup_services, caplog
) -> None:
    """suppress_indefinitely logs a warning when entity is not in any group."""
    hass, coord = setup_services

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUPPRESS_INDEFINITELY,
            {ATTR_ENTITY_ID: "sensor.nonexistent"},
            blocking=True,
        )

    assert "not found in any monitored group" in caplog.text


async def test_suppress_indefinitely_unknown_group_logs_warning(
    setup_services, caplog
) -> None:
    """suppress_indefinitely logs a warning when the group name is unknown."""
    hass, coord = setup_services

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUPPRESS_INDEFINITELY,
            {ATTR_GROUP: "No Such Group"},
            blocking=True,
        )

    assert "not found" in caplog.text


async def test_suppress_indefinitely_no_args_logs_warning(
    setup_services, caplog
) -> None:
    """suppress_indefinitely logs a warning when neither entity_id nor group is provided."""
    hass, coord = setup_services

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUPPRESS_INDEFINITELY,
            {},
            blocking=True,
        )

    assert "Either entity_id or group must be provided" in caplog.text


async def test_suppress_indefinitely_calls_async_set_updated_data(
    setup_services,
) -> None:
    """suppress_indefinitely triggers a data update notification."""
    hass, coord = setup_services

    with patch.object(coord, "async_set_updated_data") as mock_update:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUPPRESS_INDEFINITELY,
            {ATTR_ENTITY_ID: "binary_sensor.device_a"},
            blocking=True,
        )
        mock_update.assert_called_once()


async def test_suppress_service_by_group(setup_services) -> None:
    """suppress service with group= suppresses all entities in the group."""
    hass, coord = setup_services

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUPPRESS,
        {ATTR_GROUP: "Test Group", ATTR_DURATION: 30},
        blocking=True,
    )

    for entity_id in coord.monitored_entities:
        if entity_id in coord.device_states:
            device = coord.device_states[entity_id]
            assert device.is_suppressed is True
            expected_until = datetime.now(timezone.utc) + timedelta(minutes=30)
            diff = abs((device.suppress_until - expected_until).total_seconds())
            assert diff < 5
        else:
            assert entity_id in coord._suppressed
            assert coord._suppressed[entity_id] is not None


async def test_suppress_service_unknown_group_logs_warning(
    setup_services, caplog
) -> None:
    """suppress service logs a warning when the group name is not found."""
    hass, coord = setup_services

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUPPRESS,
            {ATTR_GROUP: "No Such Group", ATTR_DURATION: 10},
            blocking=True,
        )

    assert "not found" in caplog.text


async def test_suppress_service_no_args_logs_warning(setup_services, caplog) -> None:
    """suppress service logs a warning when neither entity_id nor group is given."""
    hass, coord = setup_services

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SUPPRESS,
            {ATTR_DURATION: 10},
            blocking=True,
        )

    assert "Either entity_id or group must be provided" in caplog.text


async def test_unsuppress_service_by_group(setup_services) -> None:
    """unsuppress service with group= clears suppression for all entities in the group."""
    hass, coord = setup_services

    # First suppress all
    for eid in coord.monitored_entities:
        coord.suppress_entity(eid, datetime.now(timezone.utc) + timedelta(hours=1))

    await hass.services.async_call(
        DOMAIN,
        SERVICE_UNSUPPRESS,
        {ATTR_GROUP: "Test Group"},
        blocking=True,
    )

    for entity_id in coord.monitored_entities:
        if entity_id in coord.device_states:
            device = coord.device_states[entity_id]
            assert device.is_suppressed is False
            assert device.suppress_until is None
        # Either way the entity should be absent from _suppressed
        assert entity_id not in coord._suppressed


async def test_unsuppress_service_unknown_group_logs_warning(
    setup_services, caplog
) -> None:
    """unsuppress service logs a warning when the group name is not found."""
    hass, coord = setup_services

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UNSUPPRESS,
            {ATTR_GROUP: "No Such Group"},
            blocking=True,
        )

    assert "not found" in caplog.text


async def test_unsuppress_service_no_args_logs_warning(setup_services, caplog) -> None:
    """unsuppress service logs a warning when neither entity_id nor group is given."""
    hass, coord = setup_services

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UNSUPPRESS,
            {},
            blocking=True,
        )

    assert "Either entity_id or group must be provided" in caplog.text


# ---------------------------------------------------------------------------
# isinstance guard — non-coordinator values in hass.data[DOMAIN]
# ---------------------------------------------------------------------------


async def test_suppress_skips_non_coordinator_values(setup_services) -> None:
    """Service entity loop skips non-coordinator values (e.g. _card_installed=True)."""
    hass, coord = setup_services
    # Reorder so non-coordinator value is iterated before the coordinator
    entry_id = list(hass.data[DOMAIN].keys())[0]
    coord_ref = hass.data[DOMAIN].pop(entry_id)
    hass.data[DOMAIN]["_card_installed"] = True
    hass.data[DOMAIN][entry_id] = coord_ref

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUPPRESS,
        {ATTR_ENTITY_ID: "binary_sensor.device_a", ATTR_DURATION: 10},
        blocking=True,
    )

    assert coord.device_states["binary_sensor.device_a"].is_suppressed is True


async def test_suppress_indefinitely_skips_non_coordinator_values(
    setup_services,
) -> None:
    """suppress_indefinitely entity loop skips non-coordinator values."""
    hass, coord = setup_services
    entry_id = list(hass.data[DOMAIN].keys())[0]
    coord_ref = hass.data[DOMAIN].pop(entry_id)
    hass.data[DOMAIN]["_card_installed"] = True
    hass.data[DOMAIN][entry_id] = coord_ref

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUPPRESS_INDEFINITELY,
        {ATTR_ENTITY_ID: "binary_sensor.device_a"},
        blocking=True,
    )

    assert coord.device_states["binary_sensor.device_a"].is_suppressed is True
    assert coord.device_states["binary_sensor.device_a"].suppress_until is None


async def test_unsuppress_skips_non_coordinator_values(setup_services) -> None:
    """unsuppress entity loop skips non-coordinator values."""
    hass, coord = setup_services
    entry_id = list(hass.data[DOMAIN].keys())[0]
    coord_ref = hass.data[DOMAIN].pop(entry_id)
    hass.data[DOMAIN]["_card_installed"] = True
    hass.data[DOMAIN][entry_id] = coord_ref
    coord.suppress_entity(
        "binary_sensor.device_a",
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    await hass.services.async_call(
        DOMAIN,
        SERVICE_UNSUPPRESS,
        {ATTR_ENTITY_ID: "binary_sensor.device_a"},
        blocking=True,
    )

    assert coord.device_states["binary_sensor.device_a"].is_suppressed is False


async def test_find_coordinator_skips_non_coordinator_values(setup_services) -> None:
    """_find_coordinator skips non-coordinator values in group lookup."""
    hass, coord = setup_services
    entry_id = list(hass.data[DOMAIN].keys())[0]
    coord_ref = hass.data[DOMAIN].pop(entry_id)
    hass.data[DOMAIN]["_card_installed"] = True
    hass.data[DOMAIN][entry_id] = coord_ref

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUPPRESS,
        {ATTR_GROUP: "Test Group", ATTR_DURATION: 10},
        blocking=True,
    )

    for entity_id in coord.monitored_entities:
        if entity_id in coord.device_states:
            assert coord.device_states[entity_id].is_suppressed is True


async def test_suppress_updates_all_coordinators_sharing_entity(
    mock_hass: HomeAssistant, mock_config_data
) -> None:
    """Suppress service calls suppress_entity on ALL coordinators that monitor the entity."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.entity_availability.const import CONF_ENTITIES

    hass = mock_hass

    config_a = dict(mock_config_data)
    config_a[CONF_ENTITIES] = ["binary_sensor.device_a"]
    entry_a = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Group A",
        data=config_a,
        entry_id="entry_multi_a",
        unique_id=f"{DOMAIN}_entry_multi_a",
    )

    config_b = dict(mock_config_data)
    config_b[CONF_ENTITIES] = ["binary_sensor.device_a", "binary_sensor.device_b"]
    entry_b = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Group B",
        data=config_b,
        entry_id="entry_multi_b",
        unique_id=f"{DOMAIN}_entry_multi_b",
    )

    with patch.object(
        EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
    ):
        coord_a = EntityAvailabilityCoordinator(hass, entry_a)
        coord_a._device_states = {
            "binary_sensor.device_a": DeviceState(
                entity_id="binary_sensor.device_a", is_offline=False
            )
        }
        coord_a.data = None

        coord_b = EntityAvailabilityCoordinator(hass, entry_b)
        coord_b._device_states = {
            "binary_sensor.device_a": DeviceState(
                entity_id="binary_sensor.device_a", is_offline=False
            ),
            "binary_sensor.device_b": DeviceState(
                entity_id="binary_sensor.device_b", is_offline=False
            ),
        }
        coord_b.data = None

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["entry_multi_a"] = coord_a
    hass.data[DOMAIN]["entry_multi_b"] = coord_b

    await async_setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUPPRESS,
        {ATTR_ENTITY_ID: "binary_sensor.device_a", ATTR_DURATION: 10},
        blocking=True,
    )

    assert coord_a.device_states["binary_sensor.device_a"].is_suppressed is True
    assert coord_b.device_states["binary_sensor.device_a"].is_suppressed is True


async def test_reset_statistics_by_entity(setup_services) -> None:
    """reset_statistics service clears counters for one entity."""
    hass, coord = setup_services
    d = coord.device_states["binary_sensor.device_b"]
    d.offline_event_count = 4
    d.total_offline_seconds = 500.0

    await hass.services.async_call(
        DOMAIN,
        "reset_statistics",
        {ATTR_ENTITY_ID: "binary_sensor.device_b"},
        blocking=True,
    )
    assert d.offline_event_count == 0
    assert d.total_offline_seconds == 0.0


async def test_reset_statistics_by_group(setup_services) -> None:
    """reset_statistics service clears all entities in a group."""
    hass, coord = setup_services
    coord.device_states["binary_sensor.device_a"].offline_event_count = 2
    coord.device_states["binary_sensor.device_b"].offline_event_count = 3

    await hass.services.async_call(
        DOMAIN,
        "reset_statistics",
        {ATTR_GROUP: "Test Group"},
        blocking=True,
    )
    assert coord.device_states["binary_sensor.device_a"].offline_event_count == 0
    assert coord.device_states["binary_sensor.device_b"].offline_event_count == 0


async def test_reset_statistics_no_args(setup_services, caplog) -> None:
    """reset_statistics with neither entity_id nor group warns and no-ops."""
    hass, coord = setup_services
    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(DOMAIN, "reset_statistics", {}, blocking=True)
    assert "Either entity_id or group" in caplog.text


async def test_reset_statistics_unknown_group(setup_services, caplog) -> None:
    """reset_statistics with unknown group warns."""
    hass, coord = setup_services
    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN, "reset_statistics", {ATTR_GROUP: "Nope"}, blocking=True
        )
    assert "not found" in caplog.text


async def test_reset_statistics_unknown_entity(setup_services, caplog) -> None:
    """reset_statistics with unmonitored entity warns."""
    hass, coord = setup_services
    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN,
            "reset_statistics",
            {ATTR_ENTITY_ID: "binary_sensor.nope"},
            blocking=True,
        )
    assert "not found in any monitored group" in caplog.text


async def test_reset_statistics_entity_path_skips_non_coordinator(
    setup_services,
) -> None:
    """Entity-path reset loop skips non-coordinator hass.data values."""
    hass, coord = setup_services
    hass.data[DOMAIN]["_card_installed"] = True  # non-coordinator sentinel
    coord.device_states["binary_sensor.device_b"].offline_event_count = 3

    await hass.services.async_call(
        DOMAIN,
        "reset_statistics",
        {ATTR_ENTITY_ID: "binary_sensor.device_b"},
        blocking=True,
    )
    assert coord.device_states["binary_sensor.device_b"].offline_event_count == 0
