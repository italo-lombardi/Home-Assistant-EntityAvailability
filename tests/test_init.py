"""Tests for Entity Availability integration setup and unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entity_availability import (
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.entity_availability.const import (
    CONF_COMBINED_GROUPS,
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    DOMAIN,
    ENTRY_TYPE_COMBINED,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)


async def test_async_setup_entry(mock_hass: HomeAssistant, mock_config_entry) -> None:
    """Test successful setup of a config entry."""
    hass = mock_hass
    mock_config_entry.add_to_hass(hass)

    with (
        patch.object(
            EntityAvailabilityCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ) as mock_refresh,
        patch(
            "custom_components.entity_availability.async_setup_services",
            new_callable=AsyncMock,
        ) as mock_services,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ) as mock_forward,
    ):
        result = await async_setup_entry(hass, mock_config_entry)

    assert result is True
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
    assert isinstance(
        hass.data[DOMAIN][mock_config_entry.entry_id],
        EntityAvailabilityCoordinator,
    )
    mock_refresh.assert_called_once()
    mock_services.assert_called_once_with(hass)
    mock_forward.assert_called_once_with(mock_config_entry, PLATFORMS)


async def test_async_unload_entry(mock_hass: HomeAssistant, mock_config_entry) -> None:
    """Test successful unload of a config entry."""
    hass = mock_hass
    mock_config_entry.add_to_hass(hass)

    # First set up
    with (
        patch.object(
            EntityAvailabilityCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.entity_availability.async_setup_services",
            new_callable=AsyncMock,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        await async_setup_entry(hass, mock_config_entry)

    assert mock_config_entry.entry_id in hass.data[DOMAIN]

    # Now unload
    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_unload:
        result = await async_unload_entry(hass, mock_config_entry)

    assert result is True
    assert mock_config_entry.entry_id not in hass.data[DOMAIN]
    mock_unload.assert_called_once_with(mock_config_entry, PLATFORMS)


async def test_async_unload_entry_failure(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test unload returns False when platform unload fails."""
    hass = mock_hass
    mock_config_entry.add_to_hass(hass)

    with (
        patch.object(
            EntityAvailabilityCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.entity_availability.async_setup_services",
            new_callable=AsyncMock,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        await async_setup_entry(hass, mock_config_entry)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await async_unload_entry(hass, mock_config_entry)

    assert result is False
    # Entry should NOT be removed from data since unload failed
    assert mock_config_entry.entry_id in hass.data[DOMAIN]


async def test_setup_creates_coordinator_with_correct_config(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """Test that setup creates coordinator with correct configuration."""
    hass = mock_hass
    mock_config_entry.add_to_hass(hass)

    with (
        patch.object(
            EntityAvailabilityCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.entity_availability.async_setup_services",
            new_callable=AsyncMock,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        await async_setup_entry(hass, mock_config_entry)

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    assert coordinator.monitored_entities == [
        "binary_sensor.device_a",
        "binary_sensor.device_b",
        "binary_sensor.device_c",
    ]
    assert coordinator.group_name == "Test Group"


async def test_platforms_defined() -> None:
    """Test that expected platforms are defined."""
    from homeassistant.const import Platform

    assert Platform.SENSOR in PLATFORMS
    assert Platform.BINARY_SENSOR in PLATFORMS
    assert len(PLATFORMS) == 2


# ---------------------------------------------------------------------------
# Combined entry setup / unload
# ---------------------------------------------------------------------------


def _make_combined_entry(
    entry_id: str, name: str, combined_ids: list[str]
) -> MockConfigEntry:
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title=name,
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_COMBINED,
            CONF_GROUP_NAME: name,
            CONF_COMBINED_GROUPS: combined_ids,
        },
        entry_id=entry_id,
        unique_id=f"{DOMAIN}_combined_{name.lower().replace(' ', '_')}",
    )


async def test_combined_setup_does_not_store_coordinator(
    mock_hass: HomeAssistant,
) -> None:
    """Combined entry setup does NOT put a coordinator into hass.data[DOMAIN]."""
    hass = mock_hass
    combined = _make_combined_entry("combined_id", "My Combined", [])
    combined.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        new_callable=AsyncMock,
    ):
        result = await async_setup_entry(hass, combined)

    assert result is True
    # No coordinator stored under the combined entry_id
    assert "combined_id" not in hass.data.get(DOMAIN, {})


async def test_combined_unload_entry(mock_hass: HomeAssistant) -> None:
    """Combined entry unloads cleanly without touching hass.data[DOMAIN]."""
    hass = mock_hass
    combined = _make_combined_entry("combined_id3", "My Combined", [])
    combined.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        new_callable=AsyncMock,
    ):
        await async_setup_entry(hass, combined)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_unload:
        result = await async_unload_entry(hass, combined)

    assert result is True
    mock_unload.assert_called_once_with(combined, PLATFORMS)


async def test_card_installed_flag_reset_on_full_unload(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """_CARD_INSTALLED_KEY is cleared from hass.data when the last entry unloads."""
    from custom_components.entity_availability import _CARD_INSTALLED_KEY

    hass = mock_hass
    mock_config_entry.add_to_hass(hass)

    with (
        patch.object(
            EntityAvailabilityCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.entity_availability.async_setup_services",
            new_callable=AsyncMock,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        await async_setup_entry(hass, mock_config_entry)

    # Simulate card installed
    hass.data[DOMAIN][_CARD_INSTALLED_KEY] = True

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await async_unload_entry(hass, mock_config_entry)

    assert _CARD_INSTALLED_KEY not in hass.data.get(DOMAIN, {})


# ---------------------------------------------------------------------------
# _async_update_options — triggers reload (line 72)
# ---------------------------------------------------------------------------


async def test_async_update_options_reloads_entry(
    mock_hass: HomeAssistant, mock_config_entry
) -> None:
    """_async_update_options reloads the config entry."""
    from custom_components.entity_availability import _async_update_options

    hass = mock_hass
    mock_config_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_reload",
        new_callable=AsyncMock,
    ) as mock_reload:
        await _async_update_options(hass, mock_config_entry)
        mock_reload.assert_called_once_with(mock_config_entry.entry_id)


# ---------------------------------------------------------------------------
# _async_install_card — already installed short-circuit (line 79)
# ---------------------------------------------------------------------------


async def test_async_install_card_skips_when_already_installed(
    mock_hass: HomeAssistant,
) -> None:
    """_async_install_card returns early when the card is already installed."""
    from custom_components.entity_availability import (
        _CARD_INSTALLED_KEY,
        _async_install_card,
    )

    hass = mock_hass
    hass.data.setdefault(DOMAIN, {})[_CARD_INSTALLED_KEY] = True

    with patch(
        "custom_components.entity_availability._async_register_lovelace_resource",
        new_callable=AsyncMock,
    ) as mock_register:
        await _async_install_card(hass)
        mock_register.assert_not_called()


# ---------------------------------------------------------------------------
# _async_install_card — card JS file not found (lines 83-84)
# ---------------------------------------------------------------------------


async def test_async_install_card_warns_when_js_not_found(
    mock_hass: HomeAssistant, caplog
) -> None:
    """_async_install_card logs a warning and returns when the JS file is missing."""
    import logging

    from custom_components.entity_availability import _async_install_card

    hass = mock_hass
    hass.data.setdefault(DOMAIN, {})

    with (
        patch("pathlib.Path.exists", return_value=False),
        caplog.at_level(logging.WARNING),
    ):
        await _async_install_card(hass)

    assert "Card JS not found" in caplog.text


# ---------------------------------------------------------------------------
# _async_register_lovelace_resource — lines 114-146
# ---------------------------------------------------------------------------


async def test_register_lovelace_resource_loads_and_creates(
    mock_hass: HomeAssistant,
) -> None:
    """Resource is created when the resources collection is empty."""
    from custom_components.entity_availability import _async_register_lovelace_resource

    hass = mock_hass

    mock_resources = MagicMock()
    mock_resources.loaded = False
    mock_resources.async_load = AsyncMock()
    mock_resources.async_items.return_value = []
    mock_resources.async_create_item = AsyncMock()

    hass.data["lovelace"] = MagicMock()
    hass.data["lovelace"].resources = mock_resources

    await _async_register_lovelace_resource(hass, "1.2.3")

    mock_resources.async_load.assert_called_once()
    mock_resources.async_create_item.assert_called_once()


async def test_register_lovelace_resource_updates_existing(
    mock_hass: HomeAssistant,
) -> None:
    """Existing resource with outdated URL is updated to current version."""
    from homeassistant.components.lovelace.resources import ResourceStorageCollection

    from custom_components.entity_availability import (
        CARD_URL,
        _async_register_lovelace_resource,
    )

    hass = mock_hass
    version = "2.0.0"
    expected_url = f"{CARD_URL}?automatically-added&{version}"
    old_url = f"{CARD_URL}?automatically-added&1.0.0"

    mock_resources = MagicMock(spec=ResourceStorageCollection)
    mock_resources.loaded = True
    mock_resources.async_load = AsyncMock()
    mock_resources.async_items.return_value = [{"id": "res_1", "url": old_url}]
    mock_resources.async_update_item = AsyncMock()

    hass.data["lovelace"] = MagicMock()
    hass.data["lovelace"].resources = mock_resources

    await _async_register_lovelace_resource(hass, version)

    mock_resources.async_update_item.assert_called_once_with(
        "res_1", {"res_type": "module", "url": expected_url}
    )


async def test_register_lovelace_resource_removes_duplicates(
    mock_hass: HomeAssistant,
) -> None:
    """Duplicate Lovelace resources are deleted, keeping only the first."""
    from homeassistant.components.lovelace.resources import ResourceStorageCollection

    from custom_components.entity_availability import (
        CARD_URL,
        _async_register_lovelace_resource,
    )

    hass = mock_hass
    version = "3.0.0"
    url = f"{CARD_URL}?automatically-added&{version}"

    mock_resources = MagicMock(spec=ResourceStorageCollection)
    mock_resources.loaded = True
    mock_resources.async_load = AsyncMock()
    mock_resources.async_items.return_value = [
        {"id": "res_1", "url": url},
        {"id": "res_2", "url": url},
    ]
    mock_resources.async_delete_item = AsyncMock()
    mock_resources.async_update_item = AsyncMock()

    hass.data["lovelace"] = MagicMock()
    hass.data["lovelace"].resources = mock_resources

    await _async_register_lovelace_resource(hass, version)

    # The duplicate (res_2) should be deleted
    mock_resources.async_delete_item.assert_called_once_with("res_2")
    # First entry already has the current URL — update not needed
    mock_resources.async_update_item.assert_not_called()


async def test_register_lovelace_resource_no_lovelace_data(
    mock_hass: HomeAssistant, caplog
) -> None:
    """Logs info and returns gracefully when lovelace data is not available."""
    import logging

    from custom_components.entity_availability import _async_register_lovelace_resource

    hass = mock_hass
    # No "lovelace" key in hass.data
    hass.data.pop("lovelace", None)

    with caplog.at_level(logging.INFO):
        await _async_register_lovelace_resource(hass, "1.0.0")

    assert "Could not auto-register Lovelace resource" in caplog.text


async def test_register_lovelace_resource_already_loaded(
    mock_hass: HomeAssistant,
) -> None:
    """Resources already loaded (loaded=True) skips async_load."""
    from custom_components.entity_availability import _async_register_lovelace_resource

    hass = mock_hass
    mock_resources = MagicMock()
    mock_resources.loaded = True
    mock_resources.async_load = AsyncMock()
    mock_resources.async_items.return_value = []
    mock_resources.async_create_item = AsyncMock()

    hass.data["lovelace"] = MagicMock()
    hass.data["lovelace"].resources = mock_resources

    await _async_register_lovelace_resource(hass, "1.0.0")

    mock_resources.async_load.assert_not_called()
    mock_resources.async_create_item.assert_called_once()


async def test_register_lovelace_resource_fallback_data_append(
    mock_hass: HomeAssistant,
) -> None:
    """Fallback: uses resources.data.append when async_create_item is absent."""
    from custom_components.entity_availability import _async_register_lovelace_resource

    hass = mock_hass

    appended = []

    class _FakeData:
        """Non-empty truthy object with an append method."""

        def __bool__(self):
            return True

        def append(self, item):
            appended.append(item)

    # Use a plain class (not MagicMock) so async_create_item truly doesn't exist
    class _ResourcesNoCreate:
        loaded = True
        data = _FakeData()

        def async_items(self):
            return []

        async def async_load(self):
            pass

    mock_resources = _ResourcesNoCreate()

    hass.data["lovelace"] = MagicMock()
    hass.data["lovelace"].resources = mock_resources

    await _async_register_lovelace_resource(hass, "1.0.0")

    assert len(appended) == 1
    assert appended[0]["type"] == "module"


async def test_async_install_card_clears_sentinel_on_register_failure(
    mock_hass: HomeAssistant,
) -> None:
    """_async_install_card clears _CARD_INSTALLED_KEY when _async_register_lovelace_resource raises."""
    from custom_components.entity_availability import (
        _CARD_INSTALLED_KEY,
        _async_install_card,
    )

    hass = mock_hass
    hass.data.setdefault(DOMAIN, {})

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch.object(
            hass,
            "async_add_executor_job",
            new_callable=AsyncMock,
            return_value="1.0.0",
        ),
        patch(
            "custom_components.entity_availability._async_register_lovelace_resource",
            new_callable=AsyncMock,
            side_effect=RuntimeError("lovelace exploded"),
        ),
        patch(
            "custom_components.entity_availability.hass",
            create=True,
        ),
        patch(
            "homeassistant.components.http.StaticPathConfig",
            autospec=False,
        ),
    ):
        mock_http = MagicMock()
        mock_http.async_register_static_paths = AsyncMock()
        hass.http = mock_http
        await _async_install_card(hass)

    assert _CARD_INSTALLED_KEY not in hass.data.get(DOMAIN, {})


async def test_register_lovelace_resource_non_storage_first_url_update(
    mock_hass: HomeAssistant,
) -> None:
    """Non-ResourceStorageCollection: existing resource URL is updated in-place."""
    from custom_components.entity_availability import (
        CARD_URL,
        _async_register_lovelace_resource,
    )

    hass = mock_hass
    version = "4.0.0"
    old_url = f"{CARD_URL}?automatically-added&1.0.0"
    expected_url = f"{CARD_URL}?automatically-added&{version}"

    # Use a plain MagicMock (NOT spec=ResourceStorageCollection) so isinstance check fails
    mock_resources = MagicMock()
    mock_resources.loaded = True
    mock_resources.async_load = AsyncMock()
    existing = {"id": "res_1", "url": old_url}
    mock_resources.async_items.return_value = [existing]

    hass.data["lovelace"] = MagicMock()
    hass.data["lovelace"].resources = mock_resources

    await _async_register_lovelace_resource(hass, version)

    # In-place URL update (line 146)
    assert existing["url"] == expected_url
