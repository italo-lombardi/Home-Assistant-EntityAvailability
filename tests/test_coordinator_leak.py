"""Regression tests for the leaked-second-coordinator lifecycle bug.

A classic/label group fired its availability event TWICE for one state
transition in prod. Root cause: EntityAvailabilityCoordinator.async_shutdown
overrode the base DataUpdateCoordinator.async_shutdown WITHOUT calling super(),
so the base 30s poll timer (self._unsub_refresh) was never cancelled on
unload/reload. An unloaded coordinator kept polling forever with its own
_device_states and independently re-emitted OFFLINE/RECOVERED — a leaked
second coordinator, 3s phase-offset from the live one.

Completeness: two detached refresh paths (label re-resolve, coalesced
state-change refresh) could RE-ARM the base poll timer AFTER shutdown via
async_refresh's finally. The fix sets _shutdown_requested first (via super()),
cancels the in-flight refresh task, and guards both re-arm spawners.

Each test names the one-line mutation that flips it red on the unfixed code.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.entity_availability as ea_init
from custom_components.entity_availability.const import (
    CONF_ENTRY_TYPE,
    CONF_GROUP_NAME,
    CONF_LABELS,
    CONF_NON_ESSENTIAL_LABEL,
    DOMAIN,
    ENTRY_TYPE_LABEL,
)
from custom_components.entity_availability.coordinator import (
    EntityAvailabilityCoordinator,
)


def _make_coord(
    hass: HomeAssistant, mock_config_entry
) -> EntityAvailabilityCoordinator:
    """Build a coordinator with storage I/O mocked (no refresh run)."""
    with (
        patch.object(
            EntityAvailabilityCoordinator, "_async_load_storage", new_callable=AsyncMock
        ),
        patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ),
    ):
        return EntityAvailabilityCoordinator(hass, mock_config_entry)


async def test_shutdown_cancels_base_poll_timer(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """async_shutdown cancels the base 30s poll timer and sets the shutdown flag.

    MUTATION: delete `await super().async_shutdown()` -> timer not cancelled,
    _shutdown_requested stays False -> red.
    """
    coord = _make_coord(hass, mock_config_entry)
    cancel_calls: list[bool] = []
    coord._unsub_refresh = lambda: cancel_calls.append(True)

    with patch.object(coord, "_async_save_storage", new_callable=AsyncMock):
        await coord.async_shutdown()

    assert cancel_calls == [True]
    assert coord._unsub_refresh is None
    assert coord._shutdown_requested is True


async def test_shutdown_saves_dirty_then_still_cancels_timer(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """A raising final save must NOT skip the base poll-timer cancel.

    super() runs before the save (flag-first ordering), so the timer is cancelled
    even when the save raises. MUTATION: move super().async_shutdown() to AFTER
    the dirty-save block -> a save error skips the cancel -> red.
    """
    coord = _make_coord(hass, mock_config_entry)
    cancel_calls: list[bool] = []
    coord._unsub_refresh = lambda: cancel_calls.append(True)
    coord._dirty = True

    with patch.object(
        coord, "_async_save_storage", side_effect=RuntimeError("disk full")
    ):
        with pytest.raises(RuntimeError):
            await coord.async_shutdown()

    assert cancel_calls == [True]
    assert coord._unsub_refresh is None
    assert coord._shutdown_requested is True


async def test_shutdown_idempotent_on_never_refreshed(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Shutting down a coordinator that never refreshed (no timer armed) is safe
    and idempotent — no raise on a second call.

    MUTATION: delete `await super().async_shutdown()` -> _shutdown_requested
    stays False -> red.
    """
    coord = _make_coord(hass, mock_config_entry)
    assert coord._unsub_refresh is None  # never armed

    with patch.object(coord, "_async_save_storage", new_callable=AsyncMock):
        await coord.async_shutdown()
        await coord.async_shutdown()  # second call must not raise

    assert coord._unsub_refresh is None
    assert coord._shutdown_requested is True


async def test_shutdown_cancels_inflight_refresh_task(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """An in-flight coalesced-refresh task is cancelled on shutdown, so its
    async_refresh finally cannot re-arm the base poll timer after teardown.

    MUTATION: remove the `self._refresh_task.cancel()` block in async_shutdown
    -> task stays pending/not-cancelled -> red.
    """
    coord = _make_coord(hass, mock_config_entry)

    async def _never() -> None:
        await asyncio.sleep(3600)

    task = hass.async_create_task(_never())
    await asyncio.sleep(0)  # let it start
    coord._refresh_task = task

    with patch.object(coord, "_async_save_storage", new_callable=AsyncMock):
        await coord.async_shutdown()

    await asyncio.sleep(0)
    assert task.cancelled()
    assert coord._unsub_refresh is None


async def test_setup_registers_shutdown_hook_exactly_once(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """After a successful setup the unload hook is registered EXACTLY ONCE.

    The base DataUpdateCoordinator.__init__ registers async_shutdown as an
    on_unload hook at construction (config_entry is passed), before first_refresh
    — so the abort path is already covered and an explicit registration in
    async_setup_entry would be a duplicate. A duplicate makes async_shutdown —
    and its super() poll-timer cancel plus final save — run twice on every unload.

    MUTATION: re-add `entry.async_on_unload(coordinator.async_shutdown)` in
    __init__.py -> hook registered twice -> count 2 -> red.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        patch.object(
            EntityAvailabilityCoordinator, "_async_load_storage", new_callable=AsyncMock
        ),
        patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ),
        # Mock first_refresh: on HA >= 2025.11 the real one raises ConfigEntryError
        # unless the entry is in SETUP_IN_PROGRESS (HA's own setup state machine, not
        # exercised when calling async_setup_entry directly). We only care that the
        # unload hook is registered once by the time setup finishes, which the base
        # __init__ (at construction) and our __init__.py flow both run regardless.
        patch.object(
            EntityAvailabilityCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch.object(ea_init, "async_setup_services", new_callable=AsyncMock),
        patch.object(ea_init, "_async_install_card", new_callable=AsyncMock),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
        ),
    ):
        ok = await ea_init.async_setup_entry(hass, mock_config_entry)

    assert ok is True
    count = sum(
        1
        for cb in mock_config_entry._on_unload or []
        if getattr(cb, "__func__", None) is EntityAvailabilityCoordinator.async_shutdown
    )
    assert count == 1


async def test_reresolve_noops_after_shutdown(hass: HomeAssistant) -> None:
    """A registry event landing after shutdown must not re-resolve/refresh — the
    guarded _reresolve_membership no-ops instead of spawning a refresh that would
    re-arm the base poll timer (the label-group re-arm race).

    MUTATION: remove the `if self._shutdown_requested: return` guard in
    _reresolve_membership -> async_create_task IS called -> red.
    """
    from homeassistant.helpers import label_registry as lr

    lbl = lr.async_get(hass).async_create("LeakTest").label_id
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Label Group",
        entry_id="leak_lbl",
        data={
            CONF_GROUP_NAME: "Label Group",
            CONF_ENTRY_TYPE: ENTRY_TYPE_LABEL,
            CONF_LABELS: [lbl],
            CONF_NON_ESSENTIAL_LABEL: [],
        },
    )
    with (
        patch.object(
            EntityAvailabilityCoordinator, "_async_load_storage", new_callable=AsyncMock
        ),
        patch.object(
            EntityAvailabilityCoordinator, "_async_save_storage", new_callable=AsyncMock
        ),
    ):
        coord = EntityAvailabilityCoordinator(hass, entry)

    coord._shutdown_requested = True
    # Force a membership CHANGE so the task-spawn at the end of
    # _reresolve_membership is actually reached — otherwise the before==after
    # early-return would mask a missing guard and the test would have no teeth.
    coord._entities = ["binary_sensor.stale_ghost_member"]
    coord._non_essential = []
    with patch.object(hass, "async_create_task") as mock_create:
        coord._reresolve_membership(None)

    mock_create.assert_not_called()


async def test_debounced_refresh_noops_after_shutdown(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """A debounce timer firing after shutdown must not spawn a coalesced refresh
    — its async_refresh finally would re-arm the base poll timer.

    MUTATION: remove the `if self._shutdown_requested: return` guard in
    _debounced_group_refresh -> async_create_task IS called -> red.
    """
    coord = _make_coord(hass, mock_config_entry)
    coord._shutdown_requested = True

    with patch.object(hass, "async_create_task") as mock_create:
        coord._debounced_group_refresh(None)

    mock_create.assert_not_called()
    assert coord._refresh_task is None
