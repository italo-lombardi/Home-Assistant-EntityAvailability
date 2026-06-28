# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.3.10] - 2026-06-28

### Added
- **Combined group: Offline Count sensor** — new `Offline Count` sensor for combined groups, matching the classic group sensor composition. Reports count of unsuppressed offline devices across all included groups. Exposes `entities` and `count` attributes.

### Fixed
- **Combined group: Combined Summary** — `Combined Summary` was reporting offline count instead of total entity count, inconsistent with individual `Group Summary`. Now correctly reports total monitored entities across all included groups.

## [0.3.9] - 2026-06-28

### Added
- **Device name display** (`use_device_names`) — new toggle in Advanced Settings. When enabled, offline/recovered sensor states show the HA device name (e.g. "Entrance Smoke Detector") instead of the entity friendly name (e.g. "Smoke Detector Density"). Applies to `Offline Entities`, `Recently Offline`, `Recently Recovered`, and `Low Battery` sensors, including their combined-group counterparts. Falls back to entity friendly name for entities not linked to an HA device (helpers, template sensors, virtual entities). Opt-in, default off — existing groups are unaffected.

### Changed
- Coordinator: state-change debounce reduced from 2 s to 0.5 s. The debounce coalesces rapid same-entity event bursts before triggering a coordinator refresh; false-alarm filtering is handled separately by the cooldown setting. 0.5 s fully absorbs real protocol flap windows (Zigbee/Z-Wave/WiFi all settle within 1 s) while cutting offline-detection latency by 75 %.

## [0.3.8] - 2026-06-21

### Fixed
- Sensor: `AvailabilitySensor.extra_state_attributes['per_device']` values are now rounded to 1 decimal (matching `native_value`). Previously the per-device floats were unrounded — the rolling-window numerator grew by ~`SCAN_INTERVAL` seconds per coordinator tick, so the unrounded value drifted by tens of thousandths of a percent every tick — defeating `WriteDedupMixin` (the attribute dict comparison always saw a diff) and producing one recorder row per tick on every `*_availability_today` sensor (~2880/day each). The group-level `native_value` was already 1-decimal-rounded; this matches the attribute precision to it. Public API unchanged: state is still a 1-decimal float. Fixes v5.5 audit finding F-EA-1 (1.45M states rows over 50 days).

## [0.3.7] - 2026-06-15

### Fixed
- Combined sensor: `sensor.*_combined_summary` attributes now include an `entities` key (flat list of all monitored entity IDs across all source groups) — required for the Unsuppress All card action to work on combined group cards
- Card: `show_actions` (Suppress All / Unsuppress All buttons) now rendered for combined group cards; previously the actions block was absent regardless of config
- Card: `_getOfflineEntityIds()` now uses the combined-group entity prefix when the card is a combined group — Suppress All was silently suppressing nothing on combined cards

## [0.3.6] - 2026-06-08

### Changed
- Sensor/BinarySensor: skip `async_write_ha_state` when both the native value and `extra_state_attributes` match the previously published pair. The coordinator still ticks every 30 seconds, but unchanged sensors no longer produce redundant recorder rows. Steady-state networks should see a large drop in recorder writes for the offline-count, offline-entities, low-battery, group-summary, recently-offline, recently-recovered, any-offline binary sensor, and their combined-group counterparts. First write after startup always goes through; any change in value or attrs still publishes immediately.
- Repo layout: moved screenshot/docs PNGs from `custom_components/entity_availability/docs/` to repo-root `assets/`. HACS clones the integration package into every user's `config/custom_components/` — non-runtime images now stay out of user installs.

### Fixed
- Sensor: include `available` in the dedup key and reset the cached pair on `async_will_remove_from_hass`, so availability flips and entity removal/re-add cycles always publish a fresh state.

## [0.3.5] - 2026-06-01

### Fixed
- Sensor/BinarySensor: group names containing forward slashes (e.g. "Motion/Presence Sensors") no longer generate invalid entity IDs — all non-alphanumeric characters are now replaced with underscores when building the entity ID slug. This fixes HA 2027.2.0 deprecation warnings.
- Services: suppress, suppress_indefinitely, and unsuppress handlers now skip non-coordinator values in `hass.data[DOMAIN]` — prevents `AttributeError` crash when the card is installed and a service call is made
- Init: removed redundant `resources.loaded = True` assignment after `async_load()` — HA manages this flag internally

## [0.3.5-beta.2] - 2026-05-31

### Fixed
- Sensor/BinarySensor: group names containing forward slashes (e.g. "Motion/Presence Sensors") no longer generate invalid entity IDs — all non-alphanumeric characters are now replaced with underscores when building the entity ID slug. This fixes HA 2027.2.0 deprecation warnings.

## [0.3.5-beta.1] - 2026-05-31

### Fixed
- Services: suppress, suppress_indefinitely, and unsuppress handlers now skip non-coordinator values in `hass.data[DOMAIN]` — prevents `AttributeError` crash when the card is installed and a service call is made
- Init: removed redundant `resources.loaded = True` assignment after `async_load()` — HA manages this flag internally

## [0.3.4] - 2026-05-31

### Fixed
- Card: low-battery entities are now correctly included in `entity_filter: offline` view. Previously the card hardcoded a `20%` threshold that diverged from the integration's configurable `battery_threshold`, causing low-battery entities to be hidden when the configured threshold differed from 20.
- Sensor: `group_summary` exposes a new `low_battery_entities` attribute (list of entity IDs flagged as degraded by the coordinator) so the card no longer needs to reconstruct the threshold check.

### Changed
- Card: section title for `entity_filter: offline` renamed from "Offline Entities" to "Problem Entities" since the filter includes offline, stale, and low-battery entities.

## [0.3.4-beta.1] - 2026-05-31

### Fixed
- Card: low-battery entities are now correctly included in `entity_filter: offline` view. Previously the card hardcoded a `20%` threshold that diverged from the integration's configurable `battery_threshold`, causing low-battery entities to be hidden when the configured threshold differed from 20.
- Sensor: `group_summary` exposes a new `low_battery_entities` attribute (list of entity IDs flagged as degraded by the coordinator) so the card no longer needs to reconstruct the threshold check.

### Changed
- Card: section title for `entity_filter: offline` renamed from "Offline Entities" to "Problem Entities" since the filter includes offline, stale, and low-battery entities.

## [0.3.3] - 2026-05-22

### Fixed
- Coordinator: timezone-naive `state.last_changed` values are now guarded with `.replace(tzinfo=timezone.utc)` in both the staleness check and `cooldown_start` assignment — prevents comparison errors on systems where HA returns tz-naive datetimes
- Sensor: `group_summary` `online` count no longer over-reports when entities have not yet been processed by the coordinator — count now iterates over `monitored_entities` instead of `device_states.values()`
- Init: `_card_installed` flag moved from a module-level global to `hass.data[DOMAIN]` — prevents cross-instance state bleed when multiple HA instances run in the same process
- Card: `CARD_VERSION` constant corrected to `0.3.3` (was mismatched with integration version)

### Changed
- Storage: unknown availability window strings now log a warning before falling back to 24 h

### Documentation
- README sensor table now lists all four availability window sensors (`today`, `3d`, `5d`, `7d`)
- Added dashboard example screenshot section to README

## [0.3.2-beta.1] - 2026-05-20

### Fixed
- Card: `entity_detail` inline/tooltip now shows `unit_of_measurement` alongside the HA state value (e.g. `85 %` instead of `85` for battery sensors)
- Coordinator: entities with `device_class: battery` now use their own state as the battery level, so mobile companion app battery sensors are correctly tracked and displayed in the card
- Coordinator: `offline_since` now reflects `state.last_changed` when the entity was already in a bad state before the coordinator first polled it — offline duration shown in the card is accurate after HA restarts

## [0.3.2] - 2026-05-20

### Fixed
- Card: `entity_detail` inline/tooltip now shows `unit_of_measurement` alongside the HA state value (e.g. `85 %` instead of `85` for battery sensors)
- Coordinator: entities with `device_class: battery` now use their own state as the battery level, so mobile companion app battery sensors are correctly tracked and displayed in the card
- Coordinator: `offline_since` now reflects `state.last_changed` when the entity was already in a bad state before the coordinator first polled it — offline duration shown in the card is accurate after HA restarts

## [0.3.1] - 2026-05-19

### Added
- Card: `group_sort_by` option for combined group cards — sort the group breakdown table by `name_asc` (default), `name_desc`, or `offline_desc` (most offline first, ties broken by name)
- Card editor: **Sort Groups By** dropdown shown when a combined group is selected; replaces the entity sort controls that are not applicable to combined groups
- Debug logging across coordinator, sensor setup, and storage — enable with `logger: logs: custom_components.entity_availability: debug` in HA configuration; logs cover state transitions, cooldown/offline/recovery events, suppression changes, battery detection source, storage load/save, and availability bucket lifecycle

### Fixed
- Card: fixed iOS Companion App "configuration error" — replaced `customElements.whenDefined("ha-panel-lovelace")` (lazy-loaded, may not fire on iOS WKWebView) with a multi-element bootstrap that tries `home-assistant-main` first (always in HA's initial bundle), falling back to `ha-panel-lovelace` and `hui-view`; also tries element registration immediately if any anchor element is already defined
- Card: `html`/`nothing`/`css` are now sourced from both the constructor and prototype to handle variation across HA bundle builds
- Card editor: group dropdown was empty when the config entry was renamed to remove the "Entity Availability" prefix — sensors now register with stable `entity_availability_` prefixed entity IDs regardless of entry title, so the card can always discover them; **note:** applies to new installs only — existing installs with already-renamed entries need to delete and re-add the integration to get stable IDs

## [0.3.0-beta.4] - 2026-05-11

### Fixed
- Entities removed from a group no longer persist in storage after editing the group — stale device states and suppression entries are now pruned on load and save
- Re-adding a previously suppressed entity to a group now starts without inherited suppression state

## [0.3.0-beta.3] - 2026-05-09

### Added
- Combined groups: `sensor.*_recently_offline` — aggregates recently offline entities from all member groups into a single sensor; uses each source group's own recovery window
- Combined groups: `sensor.*_recently_recovered` — aggregates recently recovered entities from all member groups; same per-group window logic
- Combined groups: `battery_powered` count added to `sensor.*_combined_summary` attributes (top-level total and per-group breakdown in the `groups` attribute)

## [0.3.0-beta.2] - 2026-05-09

### Added
- Sensor: `recently_offline` — tracks entities that went offline within a configurable window (default 5 minutes); state is the friendly name list, attribute `entities` is the entity ID list
- Sensor: `recently_recovered` — tracks entities that recovered from offline within the same window; state is the friendly name list, attribute `entities` is the entity ID list
- Config: `recovery_window` setting (minutes) in Advanced Settings and Options flow — controls how long entities remain visible in both sensors after a state transition
- Action: `suppress_indefinitely` — suppress an entity or group with no expiry; cleared by the existing `unsuppress` action

### Fixed
- Indefinite suppressions (no expiry) now survive HA restarts
- `recently_offline_at` timestamps are now persisted to storage and restored on restart, so entities that went offline before a restart correctly appear in the `recently_offline` sensor within the configured window
- Changes to `recovery_window` in the Options flow now take effect immediately without requiring an integration reload

## [0.3.0-beta.1] - 2026-05-08

### Added
- Combined groups — select two or more existing groups and get a unified set of sensors that aggregate data across all of them
- Combined group sensors: offline count, offline entities list, low battery list, low battery count
- Binary sensor `any_offline` — turns ON when any entity in any included group is offline
- Card: combined groups now supported — card auto-detects group type and adapts its layout
- Card: combined group view shows per-group breakdown table (online / offline / low battery per group)
- Card editor: Group Slug field replaced with a dropdown populated from discovered groups; regular and combined groups shown in separate optgroups
- Card editor: controls that don't apply to combined groups (availability bars, filters, sort, entity detail, suppress buttons, color thresholds) are hidden automatically when a combined group is selected

### Fixed
- Automations no longer fire on HA restart for devices that were already offline before the restart
- Random false-positive triggers during HA startup (devices briefly appearing offline while HA loads) are now suppressed for the first 60 seconds after startup
- Suppress/unsuppress services are no longer unloaded while a combined group entry is still loaded

## [0.2.0] - 2026-05-07

### Added
- Card: `entity_detail` option — `"off"` / `"tooltip"` / `"inline"` (replaces `show_entity_tooltips`)
- Card: `entity_filter` option — `"all"` / `"offline"` / `"online"` to filter entity list by health status
- Card: stale entity detection — grey dot + "Stale" for entities past the staleness threshold
- Card: human-readable durations and timestamps throughout (e.g. "2 hours ago", "today at 14:30")
- Sensor: `suppressed_until`, `stale_entities`, `offline_since` added to `GroupSummarySensor` attributes
- Translations: config flow help text added for all 10 supported languages

### Changed
- Card: entity status shows single-concern label (Suppressed / Offline for X / Stale / Low Battery / Online)
- Integration: card JS served directly from component directory, no longer copied to `www/`
- Integration: stale Lovelace resource entries cleaned up automatically on startup

### Fixed
- Card: custom element missing from card picker after fresh install
- Card: JS file loaded twice causing conflicts
- Card: entity tooltips clipped by overflow hidden
- Translations: non-English files were accidentally written in English

### Migration
- `show_entity_tooltips: true` automatically maps to `entity_detail: "tooltip"` — no action needed

## [0.1.1] - 2026-05-05

### Added
- Card: configurable entity sort order via `sort_by` option (`status`, `name_asc`, `name_desc`, `battery_asc`, `battery_desc`)
- Card: sort dropdown in visual editor
- 32 new unit tests covering all sort modes and edge cases

### Fixed
- `async_request_refresh` called without `await` inside a `@callback` debounce function, causing a `RuntimeWarning` about an unawaited coroutine — now scheduled via `hass.async_create_task()`

## [0.1.0] - 2026-05-04

### Changed
- Renamed integration from "Device Availability" to "Entity Availability"
- Renamed domain from `device_availability` to `entity_availability`
- Availability tracking uses 5-minute buckets (previously hourly)
- Battery monitoring requires explicit entity mapping via config flow step
- Low Battery sensor only created when battery threshold > 0
- Removed "All OK" binary sensor (redundant with "Any Offline")
- Battery entity selector shows all sensors (not filtered by device_class)
- Battery mapping uses suggested values — user can clear selections for non-battery entities
- Low Battery and Offline Entities sensors show "None" state when no issues (not empty/unknown)
- Lovelace card completely redesigned with dashboard-style layout

### Added
- Battery entity mapping step in config flow — auto-detects and lets user confirm/override
- Support for text battery states (`low`) in addition to numeric percentages
- "Any Offline" binary sensor (Problem device class) — ON when any entity is offline
- Group Summary sensor with total_entities, online, offline, suppressed, battery_powered, low_battery, entities, battery_levels attributes
- Low Battery Count sensor — numeric count for easy automation triggers
- Options flow includes battery mapping step
- Auto-detection of battery entities via device registry and naming convention
- Suppress/unsuppress services support `group` parameter for group-level operations
- Card: status icon (mdi:check-circle / alert-circle / close-circle) colored by group health
- Card: stats row with Online / Offline / Low Battery counts
- Card: all configured availability windows shown with colored progress bars
- Card: customizable bar colors and thresholds via visual editor (keys: `high`, `mid`, `low`)
- Card: expandable entity list with legend header, battery %, sorted by severity
- Card: optional suppress/unsuppress action buttons
- Card: suppressed entities banner
- Card: visual card editor with all options configurable
- Integration icon (icon.png, icon@2x.png) for HACS display
- Monitor entity availability with configurable groups
- Track offline, degraded, and suppressed entity states
- Availability tracking with configurable time windows (today, 3d, 5d, 7d)
- Suppress/unsuppress services for temporary exclusion from monitoring
- Sensors for offline count, offline entities list, degraded entities, and availability percentage
- Configurable bad states, cooldown period, staleness threshold, and battery threshold
- Persistent storage of availability data and suppression state
- Custom Lovelace card for visualizing entity availability
