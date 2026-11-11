"""DataUpdateCoordinator for Entity Availability."""

from __future__ import annotations

import asyncio
import gc
import inspect
import logging
import math
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_HYSTERESIS,
    BUCKET_INTERVAL,
    CONF_AVAILABILITY_WINDOWS,
    CONF_BAD_STATES,
    CONF_BATTERY_ENTITY_MAP,
    CONF_BATTERY_THRESHOLD,
    CONF_COLLAPSE_DEVICES,
    CONF_COOLDOWN,
    CONF_ENTITIES,
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
    DEFAULT_COLLAPSE_DEVICES,
    DEFAULT_COOLDOWN,
    DEFAULT_RECOVERY_WINDOW,
    DEFAULT_SIGNAL_ENABLED,
    DEFAULT_STALENESS_THRESHOLD,
    DEFAULT_STALENESS_USE_LAST_UPDATED,
    DEFAULT_USE_DEVICE_NAMES,
    EVENT_BATTERY_OK,
    EVENT_LOW_BATTERY,
    EVENT_OFFLINE,
    EVENT_POOR_SIGNAL,
    EVENT_RECOVERED,
    EVENT_SIGNAL_OK,
    EVENT_STALE,
    EVENT_STALE_RECOVERED,
    SAVE_SLOW_WARN_CPU_MS,
    SAVE_STALL_DEBUG_MS,
    SCAN_INTERVAL,
    SIGNAL_DEBOUNCE_SECONDS,
    SIGNAL_HYSTERESIS,
    SIGNAL_NETWORK_TYPES,
    STALENESS_HYSTERESIS,
    STARTUP_GRACE_PERIOD,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    SignalQuality,
)
from .helpers import collapse_representatives
from .models import DeviceState, EntityAvailabilityData
from .storage import AvailabilityStorage

_LOGGER = logging.getLogger(__name__)

# Coalesces rapid same-entity event bursts before triggering a coordinator refresh.
# 0.5s covers real protocol flap windows (Zigbee/Z-Wave/WiFi all settle within 1s)
# without adding perceptible latency. False-alarm filtering is handled separately
# by the cooldown setting, so debounce only needs to batch burst events.
_STATE_CHANGE_DEBOUNCE = 0.5  # seconds

# Persist availability data at most once per this interval (wall-clock). The
# coordinator ticks on every 30s poll AND on every debounced state-change
# refresh, so gating on a tick COUNT made a busy group save every ~40s. Gating
# on elapsed time makes the save rate a pure ceiling, independent of activity.
SAVE_MIN_INTERVAL = 300  # seconds (5 min)

# HA 2025.12.0 added a keyword-only ``serialize_in_event_loop`` param to Store
# (absent at 2025.11.0, our manifest floor; present 2025.12.0 + dev). It defaults
# True, which runs the JSON encode ON the event loop in the save path — a
# multi-second stall for large groups (our snapshot can be tens of MB). Passing
# False moves the encode into the executor (the file write already was). We detect
# the param by signature so we never pass an unknown kwarg to a 2025.11 Store
# (that raises TypeError). On <=2025.11 the encode is already off-loop (the old
# monolithic _write_data ran entirely in the executor), so omitting the kwarg is a
# correct no-op there.
# ponytail: signature guard, drop it and pass serialize_in_event_loop=False
# unconditionally once the manifest/hacs floor reaches 2025.12.0.
_STORE_HAS_SERIALIZE_FLAG = (
    "serialize_in_event_loop" in inspect.signature(Store.__init__).parameters
)


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
        self._non_essential: list[str] = entry.data.get(CONF_NON_ESSENTIAL_ENTITIES, [])
        self._bad_states: list[str] = entry.data.get(
            CONF_BAD_STATES, DEFAULT_BAD_STATES
        )
        self._cooldown: int = entry.data.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
        self._staleness_threshold: int = entry.data.get(
            CONF_STALENESS_THRESHOLD, DEFAULT_STALENESS_THRESHOLD
        )
        self._staleness_use_last_updated: bool = entry.data.get(
            CONF_STALENESS_USE_LAST_UPDATED, DEFAULT_STALENESS_USE_LAST_UPDATED
        )
        self._battery_threshold: int = entry.data.get(
            CONF_BATTERY_THRESHOLD, DEFAULT_BATTERY_THRESHOLD
        )
        self._signal_enabled: bool = entry.data.get(
            CONF_SIGNAL_ENABLED, DEFAULT_SIGNAL_ENABLED
        )
        self._signal_map: dict[str, dict[str, str]] = entry.data.get(
            CONF_SIGNAL_ENTITY_MAP, {}
        )
        self._availability_storage = AvailabilityStorage()
        store_kwargs = (
            {"serialize_in_event_loop": False} if _STORE_HAS_SERIALIZE_FLAG else {}
        )
        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry.entry_id}",
            **store_kwargs,
        )
        self._last_update: datetime | None = None
        self._startup_time: datetime | None = None
        self._unsub_state_change: CALLBACK_TYPE | None = None
        # Single group-wide trailing-edge debounce for coalescing state-change
        # bursts into ONE refresh. Replaces the old per-entity timer dict, which
        # scheduled a separate full O(N) refresh per changed entity — so K entities
        # flapping in one window triggered K full scans (O(N*K) ~ O(N^2)). The
        # timer resets on each in-window event; when it fires, exactly one refresh
        # runs. _refresh_again captures any event that lands DURING an in-flight
        # refresh so the trailing refresh is never dropped (see _run_coalesced_refresh).
        self._refresh_timer: CALLBACK_TYPE | None = None
        self._refresh_task: asyncio.Task | None = None
        self._refresh_again: bool = False
        self._device_states: dict[str, DeviceState] = {}
        self._suppressed: dict[str, datetime | None] = {}
        self._last_save: datetime | None = None
        self._dirty: bool = False
        # Save/refresh timing for Diagnostics (perf_counter ms). The slow-save warn
        # is peak-gated on _slow_save_warned_peak_cpu_ms: only a new worst CPU cost
        # above SAVE_SLOW_WARN_CPU_MS re-warns, so it cannot spam.
        self._last_save_onloop_cpu_ms: float | None = None  # thread_time: our cost
        self._last_save_onloop_wall_ms: float | None = None  # perf_counter: incl stall
        self._last_save_total_ms: float | None = None
        self._last_save_bucket_count: int | None = None
        self._last_refresh_ms: float | None = None
        self._slow_save_warned_peak_cpu_ms: float = 0.0
        # Count of saves whose wall span far exceeded their CPU cost — i.e. the host
        # froze the event loop during the save (not integration work). Diagnostics-only.
        self._save_stall_count: int = 0
        self._last_stall_wall_ms: float | None = None  # rolling max wall of a stall
        self._last_stall_at: str | None = None  # iso ts of the most recent stall
        # Memoized entity_id -> representative entity_id map for device-collapse.
        # Rebuilt once per _async_update_data tick (registry + battery/signal churn
        # can change keys), then read by every count/list site so er lookups run
        # N-per-tick, not N x sensors. None = not yet computed this tick.
        self._collapse_map: dict[str, str] | None = None
        # Monotonic tick counter — bumped each _async_update_data. Combined sensors
        # use the sum across their source coordinators as a cheap cache key so the
        # global re-collapse runs once per tick, not once per property read.
        self._collapse_generation: int = 0

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

    @property
    def collapse_generation(self) -> int:
        """Monotonic tick counter for combined-sensor collapse caching."""
        return self._collapse_generation

    def suppress_entity(self, entity_id: str, until: datetime | None = None) -> None:
        """Suppress alerts for an entity."""
        _LOGGER.debug("[%s] Suppressing %s until %s", self.group_name, entity_id, until)
        if entity_id not in self._device_states:
            self._device_states[entity_id] = DeviceState(entity_id=entity_id)
        self._device_states[entity_id].is_suppressed = True
        self._device_states[entity_id].suppress_until = until
        self._suppressed[entity_id] = until
        self._dirty = True
        self._invalidate_collapse()

    def unsuppress_entity(self, entity_id: str) -> None:
        """Resume monitoring for an entity."""
        _LOGGER.debug("[%s] Unsuppressing %s", self.group_name, entity_id)
        if entity_id in self._device_states:
            self._device_states[entity_id].is_suppressed = False
            self._device_states[entity_id].suppress_until = None
        self._suppressed.pop(entity_id, None)
        self._dirty = True
        self._invalidate_collapse()

    def _invalidate_collapse(self) -> None:
        """Drop the per-tick collapse memo and bump the generation.

        Called on state mutations that happen OUTSIDE _async_update_data (suppress/
        unsuppress push via async_set_updated_data, not a refresh) so collapsed
        counts/lists — and combined sensors keyed on collapse_generation — reflect
        the new suppression state immediately instead of after the next 30s tick.
        """
        self._collapse_map = None
        self._collapse_generation += 1

    def reliability_stats(self, entity_id: str, now: datetime) -> dict[str, Any]:
        """Return MTBF/MTTR reliability stats for an entity.

        MTBF (hours) = observed uptime / number of COMPLETED offline events.
        MTTR (minutes) = total offline time / number of COMPLETED offline events.
        Both None until at least one full offline→recovery event exists.

        offline_event_count increments when an outage OPENS (see the offline
        transition) but total_offline_seconds only books at RECOVERY, so a
        currently-open outage would otherwise inflate the denominator with no
        matching numerator — dragging MTTR low and (because its ongoing downtime
        is still counted as uptime) MTBF high. We therefore divide by the
        COMPLETED-outage count and fold the in-progress outage's elapsed downtime
        into both the offline total and the uptime subtraction, keeping numerator
        and denominator consistent. A device that has only ever had open outages
        (no completed one) reports None, honoring the docstring.
        """
        device = self._device_states.get(entity_id)
        if device is None:
            return {"mtbf_hours": None, "mttr_minutes": None, "offline_events": 0}
        # An open outage bumped the count at OPEN but has booked no seconds yet.
        completed = device.offline_event_count - (1 if device.is_offline else 0)
        in_progress = (
            (now - device.offline_since).total_seconds()
            if device.is_offline and device.offline_since
            else 0.0
        )
        if completed <= 0:
            return {
                "mtbf_hours": None,
                "mttr_minutes": None,
                "offline_events": device.offline_event_count,
            }
        uptime = 0.0
        if device.monitored_since:
            # Uptime subtracts ALL downtime including the in-progress outage, so
            # ongoing downtime is never miscounted as uptime (MTBF not inflated).
            uptime = (
                (now - device.monitored_since).total_seconds()
                - device.total_offline_seconds
                - in_progress
            )
        return {
            "mtbf_hours": round(max(uptime, 0.0) / completed / 3600, 1),
            # MTTR = mean time to REPAIR = mean of COMPLETED repairs only. The
            # open outage isn't repaired yet, so its elapsed time is NOT in the
            # numerator (that would bias MTTR); only total_offline_seconds
            # (recovery-booked) counts, divided by the completed count.
            "mttr_minutes": round(device.total_offline_seconds / completed / 60, 1),
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
        # Cancel any pending debounce timer and stop the coalesced-refresh loop
        # from re-running (_refresh_again=False). An in-flight refresh task is
        # left to complete its current pass — it is short-lived and nulls
        # _refresh_task itself in its finally, so we do not await it here.
        if self._refresh_timer is not None:
            self._refresh_timer()
            self._refresh_timer = None
        self._refresh_again = False
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
                # Prune immediately so an oversized pre-fix file does not sit in RAM
                # (and is not re-serialized at full size on the first save) — the
                # restart-freeze. Same retention/entity filter as the save path.
                self._availability_storage.prune(
                    dt_util.utcnow(), self._retention_seconds(), self._entities
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
                    try:
                        raw_lc = ds.get("last_changed")
                        if raw_lc:
                            ts = datetime.fromisoformat(raw_lc)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            device.last_changed = ts
                    except (ValueError, TypeError):
                        device.last_changed = None
                    device.offline_event_count = ds.get("offline_event_count", 0)
                    device.total_offline_seconds = ds.get("total_offline_seconds", 0.0)
                    # Enforce the "offline ⇒ no battery %" invariant on restore.
                    # A device persisted offline can carry a stale battery_level
                    # in storage written by <=0.5.5 (which cleared on is_bad, not
                    # at the offline edge). On the first boot after upgrade such a
                    # device never re-hits the offline transition (already offline
                    # ⇒ no edge), so the edge-clear can't fire and a dead battery
                    # would show a stale % forever. Dropping it here sanitizes old
                    # storage; for new storage the edge already persists None, so
                    # this is a no-op. A restored-online device keeps its level
                    # (the #111 retention path is untouched).
                    device.battery_level = (
                        None if device.is_offline else ds.get("battery_level")
                    )
                    device.is_low_battery = ds.get("is_low_battery", False)
                    if entity_id in self._entities:
                        self._device_states[entity_id] = device
            # Restore persisted host-stall telemetry so a diagnostics download after a
            # restart (which a host freeze usually prompts) still reflects the stall
            # history instead of reading a fresh 0/null. Accumulates across restarts
            # (seed, not reset). Guarded so a missing key (upgrade) or a corrupt block
            # (hand-edited/bit-rot) degrades to defaults and can never crash load or a
            # later stall's += / max().
            stall = stored.get("stall")
            if isinstance(stall, dict):
                raw_count = stall.get("count")
                if (
                    isinstance(raw_count, int)
                    and not isinstance(raw_count, bool)
                    and raw_count >= 0
                ):
                    self._save_stall_count = raw_count
                raw_wall = stall.get("last_wall_ms")
                if (
                    isinstance(raw_wall, (int, float))
                    and math.isfinite(raw_wall)
                    and raw_wall >= 0
                ):
                    self._last_stall_wall_ms = float(raw_wall)
                raw_at = stall.get("last_at")
                if isinstance(raw_at, str):
                    self._last_stall_at = raw_at

    def _retention_seconds(self) -> float:
        """Seconds of availability buckets to retain = longest SELECTED window.

        Bounds RAM, on-disk file, and per-save serialize cost to what the selected
        windows can actually read. 0 when no window is selected (persist nothing).
        The same value feeds the save snapshot and prune (save + load), so RAM is
        never trimmed tighter than the file — a widen-after-narrow (which reloads from
        the file) can only ever recover what the file retained, never less.
        """
        windows = self.entry.data.get(
            CONF_AVAILABILITY_WINDOWS, DEFAULT_AVAILABILITY_WINDOWS
        )
        max_hours = max(
            (AvailabilityStorage._retention_hours(w) for w in windows), default=0
        )
        # +15 min margin (3 buckets) so a boundary eviction never races a read.
        return (max_hours * 3600 + BUCKET_INTERVAL * 3) if max_hours else 0

    async def _async_save_storage(self) -> None:
        """Persist availability data."""
        t_total0 = time.perf_counter()
        device_states_data: dict[str, dict] = {}
        for entity_id, device in self._device_states.items():
            if entity_id not in self._entities:
                continue
            # last_changed is non-None for all active entities after any real
            # state-change event, so this guard includes most monitored devices
            # (broader than pre-PR which only saved offline/low-battery devices).
            # Saves are periodic (SAVE_MIN_INTERVAL, wall-clock) — not per-event.
            if (
                device.is_offline
                or device.cooldown_start is not None
                or device.recently_offline_at is not None
                or device.offline_event_count > 0
                or device.monitored_since is not None
                or device.is_low_battery
                or device.last_changed is not None
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
                    "battery_level": device.battery_level,
                    "is_low_battery": device.is_low_battery,
                    "last_changed": device.last_changed.isoformat()
                    if device.last_changed
                    else None,
                }
        # Bound RAM + disk to the longest selected window, drop orphan entities.
        # Same now + retention for prune (RAM) and the snapshot (disk) so they never
        # diverge. prune() MUTATES _buckets so it must stay on the loop; snapshot()
        # then freezes the pruned buckets BY VALUE into a detached structure, and the
        # expensive isoformat/dict build (encode_snapshot) runs in the EXECUTOR so it
        # does not block the loop. HA's async_save → _write_data (the JSON byte-encode
        # AND the atomic disk write) is also off-loop on every supported HA (>=2025.11,
        # async_add_executor_job; no serialize_in_event_loop flag exists to disable).
        #
        # The residual ON-LOOP cost of a save is prune() + snapshot() themselves.
        # Measured, this is CPU-cheap — a few ms even at the per-entity bucket ceiling
        # (a pure __slots__ tuple-copy + one tz-aware datetime compare per bucket). It
        # is NOT what makes a save look slow: issue #145 logged 3051ms for a step whose
        # actual CPU cost was ~0.4ms, because the host froze the event loop mid-span.
        # So the span is timed on BOTH clocks below. (The compact storage format shrinks
        # the on-disk file and the on-loop decode at RESTART, not this span.)
        now = dt_util.utcnow()
        retention_seconds = self._retention_seconds()
        # ON-LOOP span: prune() + snapshot() are the residual event-loop cost of a
        # save (encode + disk write are off-loop). The step is CPU-cheap — a few ms
        # even at the per-entity bucket ceiling (a __slots__ tuple-copy + one tz-aware
        # datetime compare per bucket). It is NOT what makes a save look slow.
        #
        # Two confounders inflate the measured span, and NEITHER is our code:
        #   * A host freeze (OS deschedule, swap) — inflates WALL but not CPU, because
        #     the loop thread was off-CPU. wall >> cpu ⇒ host stalled the loop (branch ii).
        #   * A Python garbage collection firing in-span — inflates BOTH cpu AND wall
        #     roughly equally, because gc runs ON this thread and burns real CPU that
        #     thread_time() counts as ours. A gen-2 sweep of HA's whole-process heap
        #     costs 1-3s on a loaded install (issue #145), dwarfing our ~1ms of work and
        #     landing in whichever save span it coincides with — unrelated to bucket
        #     count. So thread_time() ALONE cannot tell our slow code from an in-span GC.
        # We therefore read gc collection counts across the span: a gen-1/gen-2 collection
        # during the span means the CPU figure is GC-contaminated, not our work, so it must
        # not WARN in our name. (gen-0 is cheap + constant and would over-suppress, so it
        # is ignored.) wall is still timed for the host-stall branch.
        gc_stats_before = [s["collections"] for s in gc.get_stats()]
        t_onloop_wall0 = time.perf_counter()
        t_onloop_cpu0 = time.thread_time()
        self._availability_storage.prune(now, retention_seconds, self._entities)
        frozen = self._availability_storage.snapshot(
            now, retention_seconds, self._entities
        )
        onloop_cpu_ms = (time.thread_time() - t_onloop_cpu0) * 1000
        onloop_wall_ms = (time.perf_counter() - t_onloop_wall0) * 1000
        gc_delta = [
            a - b
            for a, b in zip((s["collections"] for s in gc.get_stats()), gc_stats_before)
        ]
        # gen-1 or gen-2 collected in-span ⇒ CPU is GC-contaminated, not our work.
        gc_ran = len(gc_delta) >= 3 and (gc_delta[1] > 0 or gc_delta[2] > 0)
        bucket_count = sum(len(v) for v in frozen.values())
        # Classify this save's on-loop cost BEFORE building the payload, so the stall
        # counters we persist reflect THIS save (otherwise the save would serialize the
        # pre-increment value and the stall history would lag one save behind).
        # (i) The integration's own code is genuinely slow (high CPU, NO in-span GC) ->
        # WARN with actionable advice. Peak-gated so it cannot spam. ~Never fires in
        # practice (the step is a few ms even at the bucket ceiling). The `not gc_ran`
        # gate is what fixes issue #145: a high-CPU reading caused by an in-span GC is
        # NOT our code and falls through to silence rather than a false WARN.
        if (
            onloop_cpu_ms > SAVE_SLOW_WARN_CPU_MS
            and not gc_ran
            and onloop_cpu_ms > self._slow_save_warned_peak_cpu_ms
        ):
            self._slow_save_warned_peak_cpu_ms = onloop_cpu_ms
            _LOGGER.warning(
                "[%s] Slow storage save: %.0fms CPU (prune+snapshot), %d buckets. "
                "If this persists, shorter availability windows or fewer monitored "
                "entities will reduce it.",
                self.group_name,
                onloop_cpu_ms,
                bucket_count,
            )
        # (ii) The host froze the loop during the span (high wall, low CPU) — NOT our
        # bug and not actionable on our side, so count it + DEBUG only, never a WARN in
        # our name. Gated on a CPU CEILING (cpu <= the warn threshold) so a genuinely
        # high-CPU save that merely failed the (i) peak-gate is NOT miscounted as a host
        # stall. A pure wall>threshold with low CPU is unambiguously a host stall. This
        # is a ceiling, not a cpu-ratio: it does NOT reintroduce the gap a ratio left
        # (e.g. 600ms CPU / 2100ms wall: 600<=1000 and 2100>2000 → still counted).
        # The counters are persisted (see data["stall"]) so they survive the restart a
        # host freeze usually prompts — the diagnostics download still shows the stall.
        # NOTE: an in-span GC (high cpu AND high wall) matches NEITHER branch — it fails
        # (i) on `not gc_ran` and (ii) on the cpu ceiling — so it falls through silently.
        # That is intentional: the cost is CPython collecting the whole-process heap, not
        # our save, and the user cannot act on it, so it warrants no log line at all.
        elif (
            onloop_cpu_ms <= SAVE_SLOW_WARN_CPU_MS
            and onloop_wall_ms > SAVE_STALL_DEBUG_MS
        ):
            self._save_stall_count += 1
            self._last_stall_wall_ms = max(
                self._last_stall_wall_ms or 0.0, onloop_wall_ms
            )
            self._last_stall_at = now.isoformat()
            _LOGGER.debug(
                "[%s] Event loop stalled %.0fms wall / %.0fms CPU during a storage "
                "save (host scheduling/GC/swap, not integration work); %d buckets.",
                self.group_name,
                onloop_wall_ms,
                onloop_cpu_ms,
                bucket_count,
            )
        # Capture `suppressed` BEFORE the executor await too, so all three persisted
        # fields (availability / device_states / suppressed) reflect the SAME instant
        # — a suppress/unsuppress firing during the encode must not skew the save.
        suppressed_data = {
            entity_id: until.isoformat() if until else None
            for entity_id, until in self._suppressed.items()
            if entity_id in self._entities
        }
        availability = await self.hass.async_add_executor_job(
            AvailabilityStorage.encode_snapshot, frozen
        )
        data = {
            "availability": availability,
            "suppressed": suppressed_data,
            "device_states": device_states_data,
            # Host-stall telemetry, persisted so a diagnostics download after the
            # restart a freeze usually triggers still reflects the stall history.
            "stall": {
                "count": self._save_stall_count,
                "last_wall_ms": self._last_stall_wall_ms,
                "last_at": self._last_stall_at,
            },
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
        # Timing for Diagnostics.
        total_ms = (time.perf_counter() - t_total0) * 1000
        self._last_save_onloop_cpu_ms = onloop_cpu_ms
        self._last_save_onloop_wall_ms = onloop_wall_ms
        self._last_save_total_ms = total_ms
        self._last_save_bucket_count = bucket_count
        self._last_save = now

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
        tracked = list(self._entities)
        if self._signal_enabled:
            signal_sensors = [
                m["sensor"] for m in self._signal_map.values() if m.get("sensor")
            ]
            tracked = list(dict.fromkeys(tracked + signal_sensors))
        battery_map = self.entry.data.get(CONF_BATTERY_ENTITY_MAP, {})
        battery_sensors = [v for v in battery_map.values() if v]
        if battery_sensors:
            tracked = list(dict.fromkeys(tracked + battery_sensors))
        self._unsub_state_change = async_track_state_change_event(
            self.hass, tracked, self._handle_state_change
        )

    @callback
    def _handle_state_change(self, event: Event) -> None:
        """Handle a monitored entity's state change (coalesced group refresh)."""
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
        # Record the real last-seen timestamp from the live event. HA resets
        # state.last_changed on restart, so polling it in _async_update_data
        # would give the boot time. Capturing it here from the event preserves
        # the true last-changed across restarts (persisted in storage).
        if new_state is not None and entity_id in self._device_states:
            ts = new_state.last_changed
            if ts is not None:  # pragma: no branch — HA always sets last_changed
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                self._device_states[entity_id].last_changed = ts
            self._dirty = True
        # Coalesce bursts: a single group-wide trailing-edge timer. Cancel the
        # one pending timer (if any) and re-arm it, so N entities changing within
        # the debounce window trigger exactly ONE refresh instead of N. The
        # per-entity last_changed capture above already ran synchronously, so no
        # entity's data is lost by sharing the timer.
        if self._refresh_timer is not None:
            self._refresh_timer()
        self._refresh_timer = async_call_later(
            self.hass, _STATE_CHANGE_DEBOUNCE, self._debounced_group_refresh
        )

    @callback
    def _debounced_group_refresh(self, _now: Any) -> None:
        """Launch a coalesced refresh after the debounce window settles.

        If a refresh is already in flight, set a flag instead of launching a
        second one — _run_coalesced_refresh re-runs once when it finishes, so an
        event that arrives mid-refresh is never dropped (the drop-window that
        makes HA's Debouncer(immediate=False) unsafe here). Refreshes are thus
        strictly serialized, never overlapping.
        """
        self._refresh_timer = None
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_again = True
            return
        self._refresh_task = self.hass.async_create_task(self._run_coalesced_refresh())

    async def _run_coalesced_refresh(self) -> None:
        """Run refreshes serially, re-running once if events arrived mid-refresh.

        Using async_refresh() (not async_request_refresh()) keeps HA's Debouncer
        execute-lock out of the path; the trailing guarantee comes from the
        _refresh_again flag set in _debounced_group_refresh.

        Serialization is safe on HA's single-threaded loop: there is no await
        between the _refresh_again check and the finally, so no timer callback can
        run in that gap to spawn a second task that the finally would then orphan.
        _debounced_group_refresh only spawns a task when _refresh_task is None or
        done — never while this loop is between iterations.
        """
        try:
            while True:
                await self.async_refresh()
                if self._refresh_again:
                    self._refresh_again = False
                    continue
                break
        finally:
            self._refresh_task = None

    async def _async_update_data(self) -> EntityAvailabilityData:
        """Update device states and availability."""
        t_refresh0 = time.perf_counter()
        now = datetime.now(timezone.utc)
        elapsed = (
            (now - self._last_update).total_seconds()
            if self._last_update
            else SCAN_INTERVAL
        )
        self._last_update = now
        # Invalidate the per-tick device-collapse memo: registry changes and
        # battery/signal drift can change collapse keys between ticks. The
        # monotonic generation lets combined sensors cheaply detect a new tick.
        self._collapse_map = None

        # Cap elapsed to avoid huge jumps after HA restart or sleep
        # Maximum reasonable elapsed is 2x the scan interval
        elapsed = min(elapsed, SCAN_INTERVAL * 2)

        # (event_name, payload, category) — category in {"offline","stale",
        # "low_battery","poor_signal"}. The count/entities keys for each category
        # are stamped in AFTER the loop from a single O(N) scan per touched
        # category, instead of one scan per transition inside the loop (that was
        # O(N·M) — the second starvation source under a flap storm).
        pending_events: list[tuple[str, dict, str]] = []
        # ponytail: full O(N) sweep every tick recomputes every device even when
        # only K flapped. A dirty-set event path (refresh only changed entities on
        # the debounced run, keep the 30s tick as the full time-based sweep) would
        # cut per-flap cost to O(K). Deferred — the coalesced single-timer refresh
        # already removes the O(N²) fan-out; add the dirty-set if profiling on very
        # large groups still shows the per-tick O(N) scan dominating.

        for entity_id in self._entities:
            state = self.hass.states.get(entity_id)
            if entity_id not in self._device_states:
                self._device_states[entity_id] = DeviceState(
                    entity_id=entity_id, monitored_since=now
                )

            device = self._device_states[entity_id]

            # Set non-essential flag (must be before suppressed continue so both flags are set)
            device.is_non_essential = entity_id in self._non_essential

            # Collapse provenance: WHICH sensor supplies battery/signal (static config,
            # not a live value). Must be set BEFORE the suppressed continue below, else
            # a suppressed same-device sibling keeps stale/None sources and mis-splits
            # from its unsuppressed twin for a cycle. Also set the signal unit here for
            # the same reason (it rides the collapse decision).
            device.battery_source = (
                self._get_battery_source(entity_id)
                if self._battery_threshold > 0
                else None
            )
            device.signal_source = self._get_signal_source(entity_id)
            device.signal_unit = self._signal_unit_of(entity_id)

            # Restore suppression from loaded data
            if entity_id in self._suppressed and not device.is_suppressed:
                device.is_suppressed = True
                device.suppress_until = self._suppressed[entity_id]

            # Check suppression expiry
            if (
                device.is_suppressed
                and device.suppress_until
                and now > device.suppress_until
            ):
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
                # Clear stale offline flag if entity recovered while suppressed;
                # done silently — no event fires for a suppressed transition.
                is_bad = state is None or state.state in self._bad_states
                if not is_bad and device.is_offline:
                    device.is_offline = False
                    device.offline_since = None
                    device.recently_offline_at = None
                    device.cooldown_start = None
                _LOGGER.debug(
                    "[%s] Skipping suppressed entity %s (until=%s)",
                    self.group_name,
                    entity_id,
                    device.suppress_until,
                )
                continue

            # Determine if device is in a bad state
            is_bad = state is None or state.state in self._bad_states

            # Battery check — retain last-known level across single-poll sensor
            # flaps while the entity is healthy (RTL-SDR/MQTT/Zigbee batteries
            # report "unknown" for a poll while online). The stale level is
            # cleared at the offline TRANSITION below (not here): the clear must
            # ride the same coordinator write as the offline_count change, or the
            # write-dedup drops it (battery_levels is unrecorded and stripped from
            # the dedup comparison, so a poll that changes only battery_levels is
            # skipped and the card shows a stale % indefinitely).
            # NOT gated on is_bad here: after an HA restart, tracked entities are
            # transiently unavailable/unknown before their first poll while the
            # device is not yet offline (is_offline restored False, offline
            # suppressed during startup grace) — clearing on is_bad wiped the
            # restored battery_level on every restart until a manual reload (#111).
            fresh_level = (
                self._get_battery_level(entity_id)
                if self._battery_threshold > 0
                else None
            )
            if fresh_level is not None:
                device.battery_level = fresh_level
            battery_low = (
                self._battery_threshold > 0
                and device.battery_level is not None
                and device.battery_level < self._battery_threshold
            )
            # De-jitter: once low, stay low until the level clears the threshold by
            # BATTERY_HYSTERESIS — a reading resting on the boundary won't flip the
            # flag (and its recorded count) every poll.
            if (
                device.is_low_battery
                and self._battery_threshold > 0
                and device.battery_level is not None
                and device.battery_level < self._battery_threshold + BATTERY_HYSTERESIS
            ):
                battery_low = True

            # Signal check — always clear level when sensor unavailable. Unlike
            # battery (which retains last-known while the entity is healthy),
            # signal has no retain-branch: no stale value is ever shown.
            if self._signal_enabled:
                fresh_signal = self._get_signal_level(entity_id)
                device.signal_level = fresh_signal
                # signal_unit already set above (before the suppressed continue) so it
                # stays consistent with signal_source for the collapse decision.
                if fresh_signal is not None:
                    candidate = self._classify_signal(
                        entity_id, fresh_signal, device.signal_quality
                    )
                    device.signal_quality = self._commit_signal(device, candidate, now)
                else:
                    # Sensor unavailable: no quality shown, and any in-flight debounce
                    # is abandoned (a cross can't persist across a None gap).
                    device.signal_pending_poor = None
                    device.signal_pending_since = None
                    device.signal_quality = None

            # Seed on first encounter; real updates come from _handle_state_change.
            if device.last_changed is None and state:
                ts = state.last_changed
                if ts is not None:  # pragma: no branch — HA always sets last_changed
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    device.last_changed = ts
                    self._dirty = True

            # Staleness check. Uses last_updated when configured (advances on any
            # state write, including unchanged-value reports); otherwise last_changed.
            is_stale = False
            stale_ts = None
            if self._staleness_threshold > 0 and state:
                # last_updated advances on same-value writes that never fire
                # state_changed, so read directly from HA state each poll.
                stale_ts = (
                    state.last_updated
                    if self._staleness_use_last_updated
                    else device.last_changed
                )
                if stale_ts:  # pragma: no branch
                    if stale_ts.tzinfo is None:  # pragma: no cover
                        stale_ts = stale_ts.replace(tzinfo=timezone.utc)
                    age = (now - stale_ts).total_seconds() / 60
                    # De-jitter: enter stale above threshold; once stale, stay stale
                    # until age falls below the exit threshold, so an age hovering on
                    # the boundary won't flip the flag every poll. The band is capped
                    # at half the threshold so it can never reach 0 (which would trap a
                    # freshly-recovered entity, age≈0, as permanently stale).
                    band = min(STALENESS_HYSTERESIS, self._staleness_threshold / 2)
                    exit_threshold = self._staleness_threshold - band
                    if age > self._staleness_threshold or (
                        device.is_stale and age > exit_threshold
                    ):
                        is_stale = True
                        _LOGGER.debug(
                            "[%s] %s is stale: last activity %.1f min ago (threshold=%d min)",
                            self.group_name,
                            entity_id,
                            age,
                            self._staleness_threshold,
                        )

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
                        # Clear a stale battery % on the same pass as the offline
                        # transition, but ONLY when there is no fresh reading:
                        # a dead-battery device reads its own sensor as "unknown"
                        # (fresh_level None) and a "100%" next to a 23h-offline
                        # row is misleading (#103). A device whose battery sensor
                        # is still reporting while the tracked entity is offline
                        # keeps its last-known level (the retain contract, EC59).
                        # Doing it here (not in the battery block above) means the
                        # clear rides the same coordinator write as
                        # offline_event_count — battery_levels is unrecorded and
                        # stripped from the write-dedup compare, so a clear on a
                        # later quiet poll would be skipped and the stale % would
                        # linger indefinitely (#111 follow-up).
                        # ponytail: if a MAPPED/companion battery sensor outlives
                        # the tracked entity and dies only after this edge, the
                        # already-offline device never re-edges, so its last % is
                        # kept until recovery→offline re-edges the clear or a
                        # restart re-runs the restore guard. Cosmetic (device is
                        # already offline) and narrow (divergent-sensor setups);
                        # a dedup-safe clear on a quiet poll would reopen the
                        # write-amp trap this whole change closes — not worth it.
                        if fresh_level is None:
                            device.battery_level = None
                        pending_events.append(
                            (
                                EVENT_OFFLINE,
                                {
                                    "entity_id": entity_id,
                                    "group": self.group_name,
                                    "entry_id": self.entry.entry_id,
                                    "offline_since": device.offline_since.isoformat()
                                    if device.offline_since
                                    else None,
                                },
                                "offline",
                            )
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
                    pending_events.append(
                        (
                            EVENT_RECOVERED,
                            {
                                "entity_id": entity_id,
                                "group": self.group_name,
                                "entry_id": self.entry.entry_id,
                                "downtime_seconds": device.last_downtime_seconds,
                            },
                            "offline",
                        )
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
            if is_stale and not device.is_stale:
                device.is_stale = True
                pending_events.append(
                    (
                        EVENT_STALE,
                        {
                            "entity_id": entity_id,
                            "group": self.group_name,
                            "entry_id": self.entry.entry_id,
                            "stale_since": (stale_ts or now).isoformat(),
                        },
                        "stale",
                    )
                )
            elif not is_stale and device.is_stale:
                device.is_stale = False
                pending_events.append(
                    (
                        EVENT_STALE_RECOVERED,
                        {
                            "entity_id": entity_id,
                            "group": self.group_name,
                            "entry_id": self.entry.entry_id,
                            "stale_since": (stale_ts or now).isoformat(),
                        },
                        "stale",
                    )
                )
            else:
                device.is_stale = is_stale
            if battery_low and not device.is_low_battery:
                device.is_low_battery = True
                pending_events.append(
                    (
                        EVENT_LOW_BATTERY,
                        {
                            "entity_id": entity_id,
                            "group": self.group_name,
                            "entry_id": self.entry.entry_id,
                            "battery_level": device.battery_level,
                        },
                        "low_battery",
                    )
                )
            elif (
                not battery_low
                and device.is_low_battery
                and device.battery_level is not None
            ):
                device.is_low_battery = False
                pending_events.append(
                    (
                        EVENT_BATTERY_OK,
                        {
                            "entity_id": entity_id,
                            "group": self.group_name,
                            "entry_id": self.entry.entry_id,
                            "battery_level": device.battery_level,
                        },
                        "low_battery",
                    )
                )
            else:
                # Freeze the flag when battery_level is None (unknown/dead): only a
                # fresh numeric reading >= threshold clears low via the elif above.
                # Without the `is not None` guard, clearing a dead low battery to
                # None makes battery_low False and fires a spurious "battery
                # recovered" event.
                device.is_low_battery = battery_low or device.is_low_battery
            device.is_degraded = (not device.is_offline) and (battery_low or is_stale)

            # Signal quality transition events
            if self._signal_enabled:
                signal_poor = device.signal_quality == "poor"
                was_poor = device.prev_signal_poor
                if signal_poor and not was_poor:
                    pending_events.append(
                        (
                            EVENT_POOR_SIGNAL,
                            {
                                "entity_id": entity_id,
                                "group": self.group_name,
                                "entry_id": self.entry.entry_id,
                                "signal_level": device.signal_level,
                                "signal_quality": device.signal_quality,
                            },
                            "poor_signal",
                        )
                    )
                elif not signal_poor and was_poor:
                    pending_events.append(
                        (
                            EVENT_SIGNAL_OK,
                            {
                                "entity_id": entity_id,
                                "group": self.group_name,
                                "entry_id": self.entry.entry_id,
                                "signal_level": device.signal_level,
                                "signal_quality": device.signal_quality,
                            },
                            "poor_signal",
                        )
                    )
                device.prev_signal_poor = signal_poor

        # Mark dirty; persist at most once per SAVE_MIN_INTERVAL (wall-clock).
        # Gating on elapsed time, not a tick count, keeps the save rate a pure
        # ceiling regardless of how many state-change refreshes a busy group
        # fires. First dirty tick (_last_save is None) saves immediately to seed.
        # Refresh timing (Diagnostics only — the O(N) sweep cost, excluding the
        # save below which is timed on its own). No warn path here; the save warn
        # is the single load-bearing one.
        self._last_refresh_ms = (time.perf_counter() - t_refresh0) * 1000
        self._dirty = True
        if (
            self._last_save is None
            or (now - self._last_save).total_seconds() >= SAVE_MIN_INTERVAL
        ):
            try:
                await self._async_save_storage()
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "[%s] Failed to save storage — will retry next tick",
                    self.group_name,
                )

        # Stamp per-category count/entities once per touched category, then fire.
        # Each list reflects FINAL device_states (all transitions in this sweep
        # applied) — for a single transition per sweep this is byte-identical to
        # the old per-site build; for multiple transitions in one sweep the lists
        # are now consistent across every event rather than reflecting whichever
        # partial mid-loop state existed at each site.
        touched = {cat for _, _, cat in pending_events}
        category_ids: dict[str, list[str]] = {}
        if "offline" in touched:
            category_ids["offline"] = self._offline_entity_ids()
        if "stale" in touched:
            category_ids["stale"] = self._stale_entity_ids()
        if "low_battery" in touched:
            category_ids["low_battery"] = self._low_battery_entity_ids()
        if "poor_signal" in touched:
            category_ids["poor_signal"] = self._poor_signal_entity_ids()
        for _event_name, payload, cat in pending_events:
            ids = category_ids[cat]
            payload[f"{cat}_count"] = len(ids)
            # Copy per payload: sibling events of the same category must not alias
            # one list object (a consumer mutating a fired payload would corrupt them).
            payload[f"{cat}_entities"] = list(ids)

        try:
            for event_name, payload, _cat in pending_events:
                self.hass.bus.async_fire(event_name, payload)
        except Exception:  # pragma: no cover
            _LOGGER.warning("[%s] Failed to fire event", self.group_name, exc_info=True)

        # Bump after all state recomputation so combined sensors reading
        # collapse_generation see a generation that matches the finished device_states.
        self._collapse_generation += 1

        return EntityAvailabilityData(
            devices=dict(self._device_states),
            buckets=dict(self._availability_storage.buckets),
        )

    @property
    def collapse_active(self) -> bool:
        """Return True when device-collapse should apply to this group's values.

        Gated on BOTH collapse_devices AND use_device_names — collapse is a no-op
        unless device names are enabled (device identity drives the merge).
        """
        return self.entry.data.get(
            CONF_COLLAPSE_DEVICES, DEFAULT_COLLAPSE_DEVICES
        ) and self.entry.data.get(CONF_USE_DEVICE_NAMES, DEFAULT_USE_DEVICE_NAMES)

    def _collapsed_map(self) -> dict[str, str]:
        """Return {entity_id -> representative entity_id}, memoized per update tick.

        Delegates to the shared collapse_representatives helper. When collapse is
        inactive, every entity maps to itself (identity), so callers need no
        special-casing.
        """
        if self._collapse_map is not None:
            return self._collapse_map
        if not self.collapse_active:
            self._collapse_map = {eid: eid for eid in self._device_states}
            return self._collapse_map
        self._collapse_map = collapse_representatives(self.hass, self._device_states)
        return self._collapse_map

    def _representatives_matching(
        self, predicate: Callable[[DeviceState], bool]
    ) -> list[str]:
        """Return one entity_id per device whose members satisfy predicate.

        A device is counted once per category iff ANY of its collapsed members
        matches — so a device with an offline sibling AND a stale sibling appears
        in BOTH the offline and stale lists (each represented by a matching
        member), rather than only the single global representative's category.
        Within a device the first matching member (insertion order) is emitted.
        When collapse is inactive, rep_of maps every entity to itself, so this is
        identical to filtering every entity per-entity (pre-collapse behavior).

        Note: the emitted ID is the first entity that satisfies the predicate,
        which may differ from the global representative in entities_collapsed
        (worst-severity member). Example: device has eid_a (stale) and eid_b
        (offline, worse → global rep). entities_collapsed = [eid_b]; but
        stale_entities = [eid_a] because eid_b does not satisfy the stale
        predicate. Automations cross-referencing per-category lists against
        entities_collapsed should match by device rather than by entity_id.
        """
        rep_of = self._collapsed_map()
        seen_device: set[str] = set()
        result: list[str] = []
        for eid, d in self._device_states.items():
            if not predicate(d):
                continue
            device = rep_of.get(eid, eid)
            if device in seen_device:
                continue
            seen_device.add(device)
            result.append(eid)
        return result

    def representative_states_matching(
        self, predicate: Callable[[DeviceState], bool]
    ) -> list[DeviceState]:
        """Return representative DeviceStates matching predicate (device-collapsed when active).

        Sensors that format device rows (names, battery %) iterate these instead of
        raw device_states so their counts/rows reflect the collapse.
        """
        return [
            self._device_states[eid]
            for eid in self._representatives_matching(predicate)
        ]

    def collapsed_entities(self) -> list[str]:
        """Return monitored entities collapsed to one representative per device-key.

        Preserves monitored_entities order. When collapse is inactive, returns the
        full membership unchanged. This is the row source the card renders, so
        len(filtered representatives) == visible rows for every category.
        """
        rep_of = self._collapsed_map()
        seen: set[str] = set()
        result: list[str] = []
        for eid in self._entities:
            rep = rep_of.get(eid, eid)
            if rep in seen:
                continue
            seen.add(rep)
            result.append(rep)
        return result

    def collapsed_member_map(self) -> dict[str, list[str]]:
        """Return {representative_entity_id -> [all member entity_ids]} when collapse is active.

        Empty when collapse is inactive. Each list is ordered: representative first,
        remaining members in _collapsed_map insertion order. Rep is always present in
        eids (collapse_representatives maps every entity including the rep to itself),
        so the [rep, *rest] construction is safe.

        NOTE: CombinedGroupSensor builds its own row_members inline from merged_states
        rather than calling this method (it has a different key space). Keep both
        implementations semantically in sync when changing collapse logic.
        """
        if not self.collapse_active:
            return {}
        rep_of = self._collapsed_map()
        members: dict[str, list[str]] = {}
        for eid, rep in rep_of.items():
            members.setdefault(rep, []).append(eid)
        # Only emit entries with >1 member — singleton rows are trivial/unchanged.
        return {
            rep: [rep, *[e for e in eids if e != rep]]
            for rep, eids in members.items()
            if len(eids) > 1
        }

    def _offline_entity_ids(self) -> list[str]:
        """Return offline, non-suppressed, essential entity_ids (device-collapsed when active)."""
        return self._representatives_matching(
            lambda d: d.is_offline and not d.is_suppressed and not d.is_non_essential
        )

    def _low_battery_entity_ids(self) -> list[str]:
        """Return low-battery, non-suppressed, essential entity_ids (device-collapsed when active)."""
        return self._representatives_matching(
            lambda d: (
                d.is_low_battery and not d.is_suppressed and not d.is_non_essential
            )
        )

    def _stale_entity_ids(self) -> list[str]:
        """Return stale, non-suppressed, essential entity_ids (device-collapsed when active)."""
        return self._representatives_matching(
            lambda d: d.is_stale and not d.is_suppressed and not d.is_non_essential
        )

    def _get_battery_level(self, entity_id: str) -> int | None:
        """Get battery level for an entity using configured mapping or auto-detection."""
        # Explicit mapping wins over own-state: the user owns this field, so a
        # configured mapping (even an entity mapped to itself) is authoritative.
        # An unmapped entity falls through to the own-state device_class=battery
        # branch below.
        battery_map = self.entry.data.get(CONF_BATTERY_ENTITY_MAP)
        if battery_map is not None and entity_id in battery_map:
            mapped = battery_map[entity_id]
            if not mapped:
                return None
            bat_state = self.hass.states.get(mapped)
            if bat_state and bat_state.state not in ("unavailable", "unknown", None):
                # binary_sensor battery: "on" = low (0%), "off" = ok (100%)
                if mapped.startswith("binary_sensor."):
                    level = 0 if bat_state.state == "on" else 100
                else:
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

        # Auto-detection fallback: no map or entity not in map
        if state and state.attributes:
            # `is not None`, not `or`: a real 0% reading (dead battery) is falsy and
            # must NOT fall through to a sibling/guessed sensor (that would silently
            # report another sensor's level for a flat battery). Also keeps this branch
            # aligned with _get_battery_source, so value and provenance can't diverge.
            battery = state.attributes.get("battery_level")
            if battery is None:
                battery = state.attributes.get("battery")
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

    def _battery_sibling_of(
        self, entity_id: str, *, require_usable_state: bool = False
    ) -> str | None:
        """Return the entity_id of a device_class=battery sibling on the same device.

        Single sibling-selection path shared by the value getter (registry branch) and
        the source getter, so the two can't drift onto different sensors. Iterates the
        device's entities in registry order and returns the first battery sibling.

        ``require_usable_state=True`` (value path) skips a sibling whose state is
        unavailable/unknown/None or doesn't parse to a level, so the level actually
        comes back — matching where a usable reading lives. ``False`` (source path,
        default) is provenance-only and availability-blind: which sensor supplies the
        battery doesn't change because it blipped unavailable, and a source flipping to
        None mid-blip would wildcard-merge and flap the identity this collapse
        stabilizes. Returns the sibling's entity_id, or None.
        """
        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(entity_id)
        if not entry or not entry.device_id:
            return None
        for ent in er.async_entries_for_device(ent_reg, entry.device_id):
            if ent.entity_id == entity_id:
                continue
            if ent.original_device_class != SensorDeviceClass.BATTERY and (
                ent.device_class != SensorDeviceClass.BATTERY
            ):
                continue
            if require_usable_state:
                bat_state = self.hass.states.get(ent.entity_id)
                if not bat_state or bat_state.state in ("unavailable", "unknown", None):
                    continue
                if self._parse_battery_state(bat_state.state) is None:
                    continue
            return ent.entity_id
        return None

    def _get_battery_from_device_registry(self, entity_id: str) -> int | None:
        """Look up battery level via the device registry (shared sibling selection)."""
        sibling = self._battery_sibling_of(entity_id, require_usable_state=True)
        if sibling is None:
            return None
        bat_state = self.hass.states.get(sibling)
        # Selection already guaranteed a usable, parseable state.
        return self._parse_battery_state(bat_state.state)

    def _get_signal_level(self, entity_id: str) -> int | None:
        """Get signal level from the configured signal sensor for an entity."""
        mapping = self._signal_map.get(entity_id)
        if not mapping:
            return None
        sensor_id = mapping.get("sensor", "")
        if not sensor_id:
            return None
        state = self.hass.states.get(sensor_id)
        if not state or state.state in ("unavailable", "unknown"):
            return None
        try:
            return int(float(state.state))
        except (ValueError, TypeError):
            return None

    def _get_battery_source(self, entity_id: str) -> str | None:
        """Return WHICH sensor supplies this entity's battery (collapse provenance).

        Mirrors _get_battery_level's precedence branch-for-branch so the source always
        matches the value's origin (drift between the two would resurface the wrong-merge
        bug this prevents): explicit map → the mapped sensor; own device_class=battery
        state → own id; own battery_level/battery attribute → own id; a sibling
        device_class=battery entity on the same device → that sibling; the guessed
        ``sensor.<slug>_battery`` entity → that guess. None means "no known source" and
        acts as a wildcard in the collapse meet — so two DISTINCT batteries on one device
        each resolve to a concrete, different id and never wrongly merge into one row.

        Provenance is deliberately NOT gated on the source being currently available
        (unlike the value path, which must clear on unavailable): which sensor supplies
        the battery doesn't change because that sensor blipped unavailable for one poll,
        and a source flipping to None mid-blip would wildcard-merge the row and flap the
        exact identity this collapse stabilizes. So the sibling lookup here is
        availability-blind (``require_usable_state=False``) while the value path's is
        usable-state-gated. On a device with two battery siblings where the first is
        chronically unavailable, source names that first sibling while the value comes
        from the second (usable) one — an accepted cosmetic provenance mismatch, NOT a
        merge hazard: both members of the device compute the same (first) sibling, so the
        row identity stays stable and never flaps.
        """
        state = self.hass.states.get(entity_id)
        battery_map = self.entry.data.get(CONF_BATTERY_ENTITY_MAP)
        if battery_map is not None and entity_id in battery_map:
            return battery_map[entity_id] or None
        if state and state.attributes.get("device_class") == "battery":
            return entity_id
        if (
            state
            and state.attributes
            and (
                state.attributes.get("battery_level") is not None
                or state.attributes.get("battery") is not None
            )
        ):
            return entity_id
        sibling = self._battery_sibling_of(entity_id)
        if sibling is not None:
            return sibling
        parts = entity_id.split(".", 1)
        if len(parts) == 2:  # pragma: no branch
            guessed = f"sensor.{parts[1]}_battery"
            if self.hass.states.get(guessed) is not None:
                return guessed
        return None

    def _get_signal_source(self, entity_id: str) -> str | None:
        """Return WHICH sensor supplies this entity's signal (collapse provenance).

        Signal is map-only, so the source is the mapped sensor id, or None (wildcard).
        """
        if not self._signal_enabled:
            return None
        mapping = self._signal_map.get(entity_id)
        if not mapping:
            return None
        return mapping.get("sensor") or None

    def _signal_unit_of(self, entity_id: str) -> str | None:
        """Return the signal unit for an entity from its mapping, or None."""
        if not self._signal_enabled:
            return None
        mapping = self._signal_map.get(entity_id)
        if not mapping:
            return None
        nt = mapping.get("network_type", "generic")
        return SIGNAL_NETWORK_TYPES.get(nt, SIGNAL_NETWORK_TYPES["generic"])["unit"]

    def _classify_signal(
        self, entity_id: str, level: int, prev: SignalQuality | None = None
    ) -> SignalQuality:
        """Classify signal level as 'good', 'ok', or 'poor' based on network type.

        >= works for both higher-is-better (LQI/%) and lower-is-better (dBm) scales.
        De-jitter, POOR/OK BOUNDARY ONLY: if the entity was already 'poor', the level
        must clear the poor/ok boundary by SIGNAL_HYSTERESIS to leave 'poor', so a level
        resting on that boundary won't flip 'poor' (the sole quality feeding a recorded
        count) every poll. The shift is toward 'ok' on the >= scale for both directions.
        The good/ok boundary is intentionally NOT de-jittered — it feeds no recorded
        count, so a good/ok wobble is display-only, not write amplification.

        This returns the RAW candidate quality (classify + hysteresis). The caller gates
        it through _commit_signal, which debounces the poor<->non-poor boundary before
        the result commits to device.signal_quality.
        """
        mapping = self._signal_map.get(entity_id, {})
        nt = mapping.get("network_type", "generic")
        thresholds = SIGNAL_NETWORK_TYPES.get(nt, SIGNAL_NETWORK_TYPES["generic"])
        if level >= thresholds["good"]:
            return "good"
        ok_threshold = thresholds["ok"]
        if prev == "poor":
            ok_threshold += SIGNAL_HYSTERESIS
        if level >= ok_threshold:
            return "ok"
        return "poor"

    def _commit_signal(
        self, device: DeviceState, candidate: SignalQuality, now: datetime
    ) -> SignalQuality:
        """Debounce the poor<->non-poor boundary (wall-clock); return the quality to commit.

        candidate is the raw _classify_signal output (classify + hysteresis already
        applied). A crossing of the poor<->non-poor boundary must persist for
        SIGNAL_DEBOUNCE_SECONDS of REAL TIME before it commits — a flapping RSSI
        never holds that long and is kept at the committed value, killing the
        poor-signal count/event spam. Wall-clock, not a refresh count: refreshes are
        state-change driven (_handle_state_change coalesces every push), so a chatty
        push sensor fires many refreshes per second and a poll-count debounce would
        clear in moments. Measuring elapsed time is immune to push rate — a 30s-polled
        sensor and a push-every-2s sensor both need the same real duration.

        good<->ok moves and the None seed commit instantly (good/ok feeds no recorded
        count). Debounce stacks AFTER hysteresis: while a cross is held,
        device.signal_quality stays at prev, so the next refresh's hysteresis still
        applies — both mechanisms resist leaving 'poor'.
        """
        prev = device.signal_quality
        # Seed: first classify after None commits instantly, no pending.
        if prev is None:
            device.signal_pending_poor = None
            device.signal_pending_since = None
            return candidate
        candidate_poor = candidate == "poor"
        committed_poor = prev == "poor"
        # No poor-boundary cross (incl. good<->ok): commit instantly, reset pending.
        if candidate_poor == committed_poor:
            device.signal_pending_poor = None
            device.signal_pending_since = None
            return candidate
        # Poor-boundary cross. New direction (first disagreeing refresh, or a reversal
        # of an in-flight pending) stamps the start time and holds the committed value.
        if device.signal_pending_poor != candidate_poor:
            device.signal_pending_poor = candidate_poor
            device.signal_pending_since = now
            return prev
        # Same pending direction persists — commit once it has held long enough.
        elapsed = (now - device.signal_pending_since).total_seconds()
        # Clock moved backward (NTP correction, restart) since the crossing was
        # stamped → elapsed is negative. Re-stamp and keep holding rather than let a
        # bad clock either strand the pending forever or (if it later jumps forward)
        # shortcut the wait. The crossing simply restarts its window from here.
        if elapsed < 0:
            device.signal_pending_since = now
            return prev
        if elapsed >= SIGNAL_DEBOUNCE_SECONDS:
            device.signal_pending_poor = None
            device.signal_pending_since = None
            return candidate
        return prev

    def _poor_signal_entity_ids(self) -> list[str]:
        """Return poor-signal, non-suppressed, essential entity_ids (device-collapsed when active)."""
        return self._representatives_matching(
            lambda d: (
                d.signal_quality == "poor"
                and not d.is_suppressed
                and not d.is_non_essential
            )
        )

    def _poor_signal_ne_entity_ids(self) -> list[str]:
        """Return poor-signal, non-suppressed, non-essential entity_ids (device-collapsed when active)."""
        return self._representatives_matching(
            lambda d: (
                d.signal_quality == "poor"
                and not d.is_suppressed
                and d.is_non_essential
            )
        )

    def _ok_signal_entity_ids(self) -> list[str]:
        """Return ok-signal, non-suppressed, essential entity_ids (device-collapsed when active)."""
        return self._representatives_matching(
            lambda d: (
                d.signal_quality == "ok"
                and not d.is_suppressed
                and not d.is_non_essential
            )
        )
