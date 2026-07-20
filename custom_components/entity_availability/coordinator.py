"""DataUpdateCoordinator for Entity Availability."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.storage import Store
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_BAD_STATES,
    CONF_BATTERY_ENTITY_MAP,
    CONF_BATTERY_THRESHOLD,
    CONF_COOLDOWN,
    CONF_ENTITIES,
    CONF_RECOVERY_WINDOW,
    CONF_STALENESS_THRESHOLD,
    DEFAULT_BAD_STATES,
    DEFAULT_BATTERY_THRESHOLD,
    DEFAULT_COOLDOWN,
    DEFAULT_RECOVERY_WINDOW,
    DEFAULT_STALENESS_THRESHOLD,
    EVENT_OFFLINE,
    EVENT_RECOVERED,
    SCAN_INTERVAL,
    STARTUP_GRACE_PERIOD,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .models import EntityAvailabilityData, DeviceState
from .storage import AvailabilityStorage

_LOGGER = logging.getLogger(__name__)

# Coalesces rapid same-entity event bursts before triggering a coordinator refresh.
# 0.5s covers real protocol flap windows (Zigbee/Z-Wave/WiFi all settle within 1s)
# without adding perceptible latency. False-alarm filtering is handled separately
# by the cooldown setting, so debounce only needs to batch burst events.
_STATE_CHANGE_DEBOUNCE = 0.5  # seconds

# Save storage every N updates (5 min = 10 updates at 30s interval)
_SAVE_INTERVAL_UPDATES = 10


class EntityAvailabilityCoordinator(DataUpdateCoordinator[EntityAvailabilityData]):
    """Coordinator that monitors device states and tracks availability."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Entity Availability - {entry.title}",
            update_interval=timedelta(seconds=SCAN_INTERVAL),
            config_entry=entry,
        )
        self.entry = entry
        self._entities: list[str] = entry.data.get(CONF_ENTITIES, [])
        self._bad_states: list[str] = entry.data.get(
            CONF_BAD_STATES, DEFAULT_BAD_STATES
        )
        self._cooldown: int = entry.data.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
        self._staleness_threshold: int = entry.data.get(
            CONF_STALENESS_THRESHOLD, DEFAULT_STALENESS_THRESHOLD
        )
        self._battery_threshold: int = entry.data.get(
            CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD
        )
        self._availability_storage = AvailabilityStorage()
        self._store = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry.entry_id}"
        )
        self._last_update: datetime | None = None
        self._startup_time: datetime | None = None
        self._unsub_state_change: CALLBACK_TYPE | None = None
        self._debounce_cancel_map: dict[str, CALLBACK_TYPE] = {}
        self._device_states: dict[str, DeviceState] = {}
        self._suppressed: dict[str, datetime | None] = {}
        self._update_count: int = 0
        self._dirty: bool = False

    @property
    def monitored_entities(self) -> list[str]:
        """Return list of monitored entity IDs."""
        return self._entities

    @property
    def device_states(self) -> dict[str, DeviceState]:
        """Return current device states."""
        return self._device_states

    @property
    def availability_storage(self) -> AvailabilityStorage:
        """Return the availability storage."""
        return self._availability_storage

    @property
    def recovery_window_minutes(self) -> int:
        """Return the recovery window in minutes, reading live from config."""
        return self.entry.data.get(CONF_RECOVERY_WINDOW, DEFAULT_RECOVERY_WINDOW)

    @property
    def group_name(self) -> str:
        """Return the group name."""
        return self.entry.title

    def suppress_entity(self, entity_id: str, until: datetime | None = None) -> None:
        """Suppress alerts for an entity."""
        _LOGGER.debug("[%s] Suppressing %s until %s", self.group_name, entity_id, until)
        if entity_id not in self._device_states:
            self._device_states[entity_id] = DeviceState(entity_id=entity_id)
        self._device_states[entity_id].is_suppressed = True
        self._device_states[entity_id].suppress_until = until
        self._suppressed[entity_id] = until
        self._dirty = True

    def unsuppress_entity(self, entity_id: str) -> None:
        """Resume monitoring for an entity."""
        _LOGGER.debug("[%s] Unsuppressing %s", self.group_name, entity_id)
        if entity_id in self._device_states:
            self._device_states[entity_id].is_suppressed = False
            self._device_states[entity_id].suppress_until = None
        self._suppressed.pop(entity_id, None)
        self._dirty = True

    def reliability_stats(self, entity_id: str, now: datetime) -> dict[str, Any]:
        """Return MTBF/MTTR reliability stats for an entity.

        MTBF (hours) = observed uptime / number of offline events.
        MTTR (minutes) = total offline time / number of offline events.
        Both None until at least one full offline→recovery event exists.
        """
        device = self._device_states.get(entity_id)
        if device is None or device.offline_event_count == 0:
            return {
                "mtbf_hours": None,
                "mttr_minutes": None,
                "offline_events": device.offline_event_count if device else 0,
            }
        uptime = 0.0
        if device.monitored_since:
            uptime = (
                now - device.monitored_since
            ).total_seconds() - device.total_offline_seconds
        return {
            "mtbf_hours": round(
                max(uptime, 0.0) / device.offline_event_count / 3600, 1
            ),
            "mttr_minutes": round(
                device.total_offline_seconds / device.offline_event_count / 60, 1
            ),
            "offline_events": device.offline_event_count,
        }

    def reset_statistics(self, entity_ids: list[str] | None = None) -> None:
        """Clear availability buckets and reliability counters.

        entity_ids=None resets every monitored entity in this group, including
        suppressed ones — a group reset means "forget this group's history",
        and suppression only gates alerting/averaging, not whether history exists.
        """
        targets = entity_ids if entity_ids is not None else list(self._entities)
        now = datetime.now(timezone.utc)
        self._availability_storage.reset(entity_ids)
        for eid in targets:
            device = self._device_states.get(eid)
            if device is None:
                continue
            device.offline_event_count = 0
            device.total_offline_seconds = 0.0
            device.last_downtime_seconds = None
            device.monitored_since = now
            # If offline right now, restart the downtime clock so the eventual
            # recovery only accrues post-reset downtime — otherwise pre-reset
            # time would be added against a zeroed counter and lost/skewed.
            if device.is_offline and device.offline_since is not None:
                device.offline_since = now
        _LOGGER.debug("[%s] Reset statistics for %s", self.group_name, targets)
        self._dirty = True
        if self.data is not None:
            self.async_set_updated_data(self.data)

    async def async_config_entry_first_refresh(self) -> None:
        """Load stored data and do first refresh."""
        _LOGGER.debug(
            "[%s] First refresh: loading storage, entities=%s",
            self.group_name,
            self._entities,
        )
        await self._async_load_storage()
        self._startup_time = datetime.now(timezone.utc)
        _LOGGER.debug(
            "[%s] Startup grace period active until %s",
            self.group_name,
            self._startup_time + timedelta(seconds=STARTUP_GRACE_PERIOD),
        )
        await super().async_config_entry_first_refresh()
        self._setup_state_listeners()

    async def async_shutdown(self) -> None:
        """Clean up on unload."""
        _LOGGER.debug("[%s] Shutting down coordinator", self.group_name)
        if self._unsub_state_change is not None:
            self._unsub_state_change()
            self._unsub_state_change = None
        for cancel in self._debounce_cancel_map.values():
            cancel()
        self._debounce_cancel_map.clear()
        # Final save
        if self._dirty:
            _LOGGER.debug("[%s] Saving dirty storage on shutdown", self.group_name)
            await self._async_save_storage()

    async def _async_load_storage(self) -> None:
        """Load persisted availability data."""
        stored = await self._store.async_load()
        if stored and isinstance(stored, dict):
            _LOGGER.debug(
                "[%s] Loading storage: %d availability entries, %d suppressed, %d device states",
                self.group_name,
                len(stored.get("availability", {})),
                len(stored.get("suppressed", {})),
                len(stored.get("device_states", {})),
            )
            if "availability" in stored:
                self._availability_storage = AvailabilityStorage.from_dict(
                    stored["availability"]
                )
            if "suppressed" in stored:
                for entity_id, until_str in stored["suppressed"].items():
                    if entity_id not in self._entities:
                        continue
                    if until_str is None:
                        # Indefinite suppression — restore without expiry
                        self._suppressed[entity_id] = None
                    else:
                        try:
                            until = datetime.fromisoformat(until_str)
                            if until.tzinfo is None:
                                until = until.replace(tzinfo=timezone.utc)
                            if until > datetime.now(timezone.utc):
                                self._suppressed[entity_id] = until
                                _LOGGER.debug(
                                    "[%s] Restored timed suppression for %s until %s",
                                    self.group_name,
                                    entity_id,
                                    until,
                                )
                        except (ValueError, TypeError):
                            pass
            if "device_states" in stored:
                for entity_id, ds in stored["device_states"].items():
                    device = DeviceState(entity_id=entity_id)
                    device.is_offline = ds.get("is_offline", False)
                    try:
                        raw_os = ds.get("offline_since")
                        if raw_os:
                            ts = datetime.fromisoformat(raw_os)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            device.offline_since = ts
                        else:
                            device.offline_since = None
                    except (ValueError, TypeError):
                        device.offline_since = None
                    try:
                        raw_cs = ds.get("cooldown_start")
                        if raw_cs:
                            ts = datetime.fromisoformat(raw_cs)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            device.cooldown_start = ts
                        else:
                            device.cooldown_start = None
                    except (ValueError, TypeError):
                        device.cooldown_start = None
                    try:
                        raw = ds.get("recently_offline_at")
                        if raw:
                            ts = datetime.fromisoformat(raw)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            window_seconds = (
                                self.entry.data.get(
                                    CONF_RECOVERY_WINDOW, DEFAULT_RECOVERY_WINDOW
                                )
                                * 60
                            )
                            if (
                                datetime.now(timezone.utc) - ts
                            ).total_seconds() <= window_seconds:
                                device.recently_offline_at = ts
                    except (ValueError, TypeError):
                        device.recently_offline_at = None
                    try:
                        raw_ms = ds.get("monitored_since")
                        if raw_ms:
                            ts = datetime.fromisoformat(raw_ms)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            device.monitored_since = ts
                    except (ValueError, TypeError):
                        device.monitored_since = None
                    device.offline_event_count = ds.get("offline_event_count", 0)
                    device.total_offline_seconds = ds.get("total_offline_seconds", 0.0)
                    if entity_id in self._entities:
                        self._device_states[entity_id] = device

    async def _async_save_storage(self) -> None:
        """Persist availability data."""
        device_states_data: dict[str, dict] = {}
        for entity_id, device in self._device_states.items():
            if entity_id not in self._entities:
                continue
            if (
                device.is_offline
                or device.cooldown_start is not None
                or device.recently_offline_at is not None
                or device.offline_event_count > 0
                or device.monitored_since is not None
            ):
                device_states_data[entity_id] = {
                    "is_offline": device.is_offline,
                    "offline_since": device.offline_since.isoformat()
                    if device.offline_since
                    else None,
                    "cooldown_start": device.cooldown_start.isoformat()
                    if device.cooldown_start
                    else None,
                    "recently_offline_at": device.recently_offline_at.isoformat()
                    if device.recently_offline_at
                    else None,
                    "monitored_since": device.monitored_since.isoformat()
                    if device.monitored_since
                    else None,
                    "offline_event_count": device.offline_event_count,
                    "total_offline_seconds": device.total_offline_seconds,
                }
        data = {
            "availability": self._availability_storage.to_dict(),
            "suppressed": {
                entity_id: until.isoformat() if until else None
                for entity_id, until in self._suppressed.items()
                if entity_id in self._entities
            },
            "device_states": device_states_data,
        }
        _LOGGER.debug(
            "[%s] Saving storage: %d availability entries, %d suppressed, %d offline device states",
            self.group_name,
            len(data["availability"]),
            len(data["suppressed"]),
            len(device_states_data),
        )
        await self._store.async_save(data)
        self._dirty = False

    @callback
    def _setup_state_listeners(self) -> None:
        """Set up state change listeners for monitored entities."""
        if self._unsub_state_change is not None:
            self._unsub_state_change()

        _LOGGER.debug(
            "[%s] Setting up state listeners for %d entities",
            self.group_name,
            len(self._entities),
        )
        self._unsub_state_change = async_track_state_change_event(
            self.hass, self._entities, self._handle_state_change
        )

    @callback
    def _handle_state_change(self, event: Event) -> None:
        """Handle state change for a monitored entity (debounced per entity)."""
        entity_id = event.data.get("entity_id", "unknown")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        _LOGGER.debug(
            "[%s] State change: %s  %s -> %s",
            self.group_name,
            entity_id,
            old_state.state if old_state else "None",
            new_state.state if new_state else "None",
        )
        # Per-entity debounce: cancel only this entity's pending timer.
        # A shared group-wide timer would drop all but the last entity's
        # state change when multiple entities change within the debounce window.
        existing = self._debounce_cancel_map.get(entity_id)
        if existing is not None:
            existing()

        @callback
        def _debounced_refresh(_now: Any) -> None:
            """Trigger a coordinator refresh after debounce."""
            self._debounce_cancel_map.pop(entity_id, None)
            self.hass.async_create_task(self.async_request_refresh())

        self._debounce_cancel_map[entity_id] = async_call_later(
            self.hass, _STATE_CHANGE_DEBOUNCE, _debounced_refresh
        )

    async def _async_update_data(self) -> EntityAvailabilityData:
        """Update device states and availability."""
        now = datetime.now(timezone.utc)
        elapsed = (
            (now - self._last_update).total_seconds()
            if self._last_update
            else SCAN_INTERVAL
        )
        self._last_update = now

        # Cap elapsed to avoid huge jumps after HA restart or sleep
        # Maximum reasonable elapsed is 2x the scan interval
        elapsed = min(elapsed, SCAN_INTERVAL * 2)

        for entity_id in self._entities:
            state = self.hass.states.get(entity_id)
            if entity_id not in self._device_states:
                self._device_states[entity_id] = DeviceState(
                    entity_id=entity_id, monitored_since=now
                )

            device = self._device_states[entity_id]

            # Restore suppression from loaded data
            if entity_id in self._suppressed and not device.is_suppressed:
                device.is_suppressed = True
                device.suppress_until = self._suppressed[entity_id]

            # Check suppression expiry
            if device.is_suppressed and device.suppress_until:
                if now > device.suppress_until:
                    _LOGGER.debug(
                        "[%s] Suppression expired for %s", self.group_name, entity_id
                    )
                    device.is_suppressed = False
                    device.suppress_until = None
                    self._suppressed.pop(entity_id, None)
                    self._dirty = True

            # Skip suppressed devices for availability tracking;
            # clear degraded/stale flags so suppressed entities don't surface
            if device.is_suppressed:
                device.is_degraded = False
                device.is_stale = False
                device.is_low_battery = False
                _LOGGER.debug(
                    "[%s] Skipping suppressed entity %s (until=%s)",
                    self.group_name,
                    entity_id,
                    device.suppress_until,
                )
                continue

            # Determine if device is in a bad state
            is_bad = state is None or state.state in self._bad_states

            # Battery check
            device.battery_level = self._get_battery_level(entity_id)
            battery_low = (
                self._battery_threshold > 0
                and device.battery_level is not None
                and device.battery_level < self._battery_threshold
            )

            # Staleness check
            is_stale = False
            if self._staleness_threshold > 0 and state and state.last_changed:
                last_changed = state.last_changed
                if last_changed.tzinfo is None:
                    last_changed = last_changed.replace(tzinfo=timezone.utc)
                age = (now - last_changed).total_seconds() / 60
                if age > self._staleness_threshold:
                    is_stale = True
                    _LOGGER.debug(
                        "[%s] %s is stale: last changed %.1f min ago (threshold=%d min)",
                        self.group_name,
                        entity_id,
                        age,
                        self._staleness_threshold,
                    )
                device.last_changed = state.last_changed

            # Cooldown logic
            if is_bad:
                if device.cooldown_start is None:
                    _lc = state.last_changed if state and state.last_changed else None
                    if _lc is not None and _lc.tzinfo is None:
                        _lc = _lc.replace(tzinfo=timezone.utc)
                    device.cooldown_start = (
                        _lc if _lc is not None and _lc < now else now
                    )
                    _LOGGER.debug(
                        "[%s] %s entered bad state (%s), cooldown started",
                        self.group_name,
                        entity_id,
                        state.state if state else "unavailable",
                    )
                cooldown_elapsed = (now - device.cooldown_start).total_seconds()
                in_grace = (
                    self._startup_time is not None
                    and (now - self._startup_time).total_seconds()
                    < STARTUP_GRACE_PERIOD
                )
                if cooldown_elapsed >= self._cooldown:
                    if not device.is_offline and not in_grace:
                        _LOGGER.debug(
                            "[%s] %s went OFFLINE (cooldown=%.0fs elapsed, since=%s)",
                            self.group_name,
                            entity_id,
                            cooldown_elapsed,
                            device.cooldown_start,
                        )
                        device.is_offline = True
                        device.offline_since = device.cooldown_start
                        device.recently_offline_at = now
                        device.offline_event_count += 1
                        self.hass.bus.async_fire(
                            EVENT_OFFLINE,
                            {
                                "entity_id": entity_id,
                                "group": self.group_name,
                                "offline_since": device.offline_since.isoformat()
                                if device.offline_since
                                else None,
                            },
                        )
                    elif in_grace:
                        _LOGGER.debug(
                            "[%s] %s cooldown elapsed but still in startup grace period",
                            self.group_name,
                            entity_id,
                        )
                else:
                    # Still in cooldown - record as online
                    _LOGGER.debug(
                        "[%s] %s in cooldown: %.0fs / %ds elapsed",
                        self.group_name,
                        entity_id,
                        cooldown_elapsed,
                        self._cooldown,
                    )
                    self._availability_storage.record_online(entity_id, elapsed, now)
            else:
                # Device is online
                if device.is_offline:
                    _LOGGER.debug(
                        "[%s] %s RECOVERED (was offline since %s, downtime=%.0fs)",
                        self.group_name,
                        entity_id,
                        device.offline_since,
                        (now - device.offline_since).total_seconds()
                        if device.offline_since
                        else 0,
                    )
                    device.last_recovery = now
                    if device.offline_since:
                        device.last_downtime_seconds = (
                            now - device.offline_since
                        ).total_seconds()
                        device.total_offline_seconds += device.last_downtime_seconds
                    device.is_offline = False
                    device.offline_since = None
                    device.recently_offline_at = None
                    self.hass.bus.async_fire(
                        EVENT_RECOVERED,
                        {
                            "entity_id": entity_id,
                            "group": self.group_name,
                            "downtime_seconds": device.last_downtime_seconds,
                        },
                    )
                device.cooldown_start = None
                self._availability_storage.record_online(entity_id, elapsed, now)

            # Record offline time (offline seconds are implicitly tracked
            # as total_seconds - online_seconds in the bucket).
            # Skip if still in cooldown — recorded as online above.
            if device.is_offline and not (
                is_bad
                and device.cooldown_start is not None
                and (now - device.cooldown_start).total_seconds() < self._cooldown
            ):
                self._availability_storage.record_offline(entity_id, elapsed, now)

            # Degraded = not offline but battery low or stale
            device.is_stale = is_stale
            device.is_low_battery = (not device.is_offline) and battery_low
            device.is_degraded = (not device.is_offline) and (battery_low or is_stale)

        # Mark as dirty; save periodically (every ~5 min)
        self._dirty = True
        self._update_count += 1
        if self._update_count >= _SAVE_INTERVAL_UPDATES:
            try:
                await self._async_save_storage()
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "[%s] Failed to save storage — will retry next interval",
                    self.group_name,
                )
            finally:
                self._update_count = 0

        return EntityAvailabilityData(
            devices=dict(self._device_states),
            buckets=dict(self._availability_storage.buckets),
        )

    def _get_battery_level(self, entity_id: str) -> int | None:
        """Get battery level for an entity using configured mapping or auto-detection."""
        state = self.hass.states.get(entity_id)
        if (
            state
            and state.attributes.get("device_class") == "battery"
            and state.state not in ("unavailable", "unknown", None)
        ):
            level = self._parse_battery_state(state.state)
            _LOGGER.debug(
                "[%s] Battery for %s via own state (device_class=battery): %s%%",
                self.group_name,
                entity_id,
                level,
            )
            return level

        battery_map = self.entry.data.get(CONF_BATTERY_ENTITY_MAP)
        if battery_map is not None and entity_id in battery_map:
            mapped = battery_map[entity_id]
            if not mapped:
                return None
            bat_state = self.hass.states.get(mapped)
            if bat_state and bat_state.state not in ("unavailable", "unknown", None):
                level = self._parse_battery_state(bat_state.state)
                _LOGGER.debug(
                    "[%s] Battery for %s via map->%s: %s%%",
                    self.group_name,
                    entity_id,
                    mapped,
                    level,
                )
                return level
            return None

        # Auto-detection fallback: no map or entity not in map
        state = self.hass.states.get(entity_id)
        if state and state.attributes:
            battery = state.attributes.get("battery_level") or state.attributes.get(
                "battery"
            )
            if battery is not None:
                level = self._parse_battery_state(str(battery).replace("%", ""))
                _LOGGER.debug(
                    "[%s] Battery for %s via attribute: %s%%",
                    self.group_name,
                    entity_id,
                    level,
                )
                return level

        battery_from_registry = self._get_battery_from_device_registry(entity_id)
        if battery_from_registry is not None:
            _LOGGER.debug(
                "[%s] Battery for %s via device registry: %s%%",
                self.group_name,
                entity_id,
                battery_from_registry,
            )
            return battery_from_registry

        parts = entity_id.split(".", 1)
        if len(parts) == 2:  # pragma: no branch
            battery_entity = f"sensor.{parts[1]}_battery"
            bat_state = self.hass.states.get(battery_entity)
            if bat_state and bat_state.state not in ("unavailable", "unknown", None):
                level = self._parse_battery_state(bat_state.state)
                _LOGGER.debug(
                    "[%s] Battery for %s via guessed entity %s: %s%%",
                    self.group_name,
                    entity_id,
                    battery_entity,
                    level,
                )
                return level

        return None

    @staticmethod
    def _parse_battery_state(value: str) -> int | None:
        """Parse a battery state string into an integer level."""
        if value.lower() == "low":
            return 0
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None

    def _get_battery_from_device_registry(self, entity_id: str) -> int | None:
        """Look up battery level via the device registry."""
        ent_reg = er.async_get(self.hass)

        entry = ent_reg.async_get(entity_id)
        if not entry or not entry.device_id:
            return None

        # Find all entities on the same device with battery device class
        for ent in er.async_entries_for_device(ent_reg, entry.device_id):
            if ent.entity_id == entity_id:
                continue
            if ent.original_device_class == SensorDeviceClass.BATTERY or (
                ent.device_class == SensorDeviceClass.BATTERY
            ):
                bat_state = self.hass.states.get(ent.entity_id)
                if bat_state and bat_state.state not in (
                    "unavailable",
                    "unknown",
                    None,
                ):
                    level = self._parse_battery_state(bat_state.state)
                    if level is not None:
                        return level

        return None
