# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.5.0] - 2026-08-14

### ⚠️ Breaking

- **"Collapse entities by device" moved from the card to the group settings.** It's now a group option (Advanced Settings / Options) instead of a card checkbox, and it requires **Show device names**. When you turn it on for a group, entities that belong to the same device are counted as one everywhere — the sensor counts, the lists, and the notification events — so the numbers finally match what the card shows.
  - Automations that react to the offline/low-battery/stale/poor-signal events will see one entry per device (not per entity) for groups that use this option. The event still fires for the entity that actually changed; only the count/list in the event data is combined.
  - Availability %, MTBF and MTTR stay per-entity and are not affected.
  - Nothing changes until you turn the option on (it's off by default). Turning it on will show a one-time step in the count-sensor history graphs.

### Added
- **"Collapse entities by device" group option** — counts several entities of the same physical device as one across all sensor values, lists and events. Needs **Show device names** on; entities not tied to a device are never merged. Available when creating a group and in its Options. Translated into all supported languages. (#81)

### Changed
- **Combined groups** count a device once even when its entities are spread across several included groups. Each group's own setting is respected — entities from a group without the option stay separate.
- **The card** now simply shows the numbers the integration reports (it no longer does any of its own merging).

## [0.4.1] - 2026-08-07

> These changes were inadvertently omitted from the v0.4.0 release tag and are included here as a patch release.

### Added
- **Card: `collapse_devices` config key** — when enabled, entities sharing the same HA device, display name, and battery/signal values collapse into a single row showing worst-case status. Entities with no device assignment are never collapsed. Requires **Use Device Names**. Defaults to `false`. (#79)
- **Card: `show_groups` and `show_entities` split on combined cards** — `show_groups` independently controls the groups breakdown table; `show_entities` controls only the flat entity list. Sort selection drives both. Entity list expand/collapse is now independent of the groups breakdown toggle. (#78)

### Fixed
- **Combined sensor: recorder payload reduced** — high-cardinality list attributes moved to `_unrecorded_attributes`; only scalar counts recorded. (#76)
- **Combined card: Non-Essential stats row and full entity table** — `show_non_essential_stats: true` now renders correctly on combined cards; `show_entities: true` + `entities_expanded: true` renders the full per-entity table. Merged sensor attributes aligned. (#75)

## [0.4.0] - 2026-08-05

### Added
- **Signal strength monitoring** — optional feature (disabled by default, no migration required). Enable per group in Advanced settings → Enable signal strength monitoring. A new wizard step binds a signal sensor and network type to each entity. Supported protocols: Wi-Fi, Zigbee, Z-Wave, Bluetooth, Thread, LTE/4G, 5G, LoRaWAN, Percentage (0–100%), Generic/RSSI. Thresholds are validated against real-world engineering references per protocol.
  - Auto-detects signal sensors by device class (`SIGNAL_STRENGTH`) or naming convention (`*_linkquality`, `*_signal_strength`, `*_rssi`, `*_lqi`, `*_signal`) — Z2MQTT and BLE conventions supported out of the box. Signal sensor name ending in `_linkquality` or `_lqi` auto-selects "Zigbee (LQI)" network type.
  - 4 new sensors per group: `poor_signal` (list), `poor_signal_count`, `poor_signal_non_essential`, `poor_signal_count_non_essential`
  - 1 new binary sensor per group: `any_poor_signal` (Problem class) — ON when any essential, non-suppressed entity has poor signal; `any_poor_signal_non_essential` binary sensor also added
  - 2 new bus events: `entity_availability_poor_signal` / `entity_availability_signal_ok` on quality transitions
  - Signal level and unit exposed in `group_summary` attributes (`signal_levels`, `signal_units`, `poor_signal_entities`, `signal_enabled`)
  - Card: "Poor Signal: N" stat pill (hidden when 0); entity dot turns yellow; detail view shows signal value with correct unit (dBm or %)
- **Battery mapping accepts `binary_sensor`** — devices that expose battery status as a binary sensor (e.g. `binary_sensor.device_battery_low`) can now be mapped. `on` = low (0%), `off` = ok (100%).
- **`AnyLowBatteryNonEssentialBinarySensor`** — new binary sensor; ON when any non-essential entity has low battery
- **`EVENT_STALE_RECOVERED` payload** now includes `stale_since` field (ISO timestamp)
- **`group_summary` new attributes** — `essential`, `stale`, `stale_non_essential`, `poor_signal`, `poor_signal_non_essential` count aliases; `offline_entities` list (was count-only). Eliminates boilerplate in templates.
- **Combined summary expanded attributes** — full per-entity dicts now exposed: `battery_levels`, `signal_levels`, `signal_units`, `suppressed_until`, `offline_since`, `last_seen` (first-wins merge across sub-groups); `ok_signal_entities`; `status` (`"ok"|"degraded"|"offline"`) and `status_color` (`"green"|"yellow"|"red"`) for use in automations/templates. NE list attrs: `non_essential_online`, `non_essential_offline`, `low_battery_entities_non_essential`. Top-level lists: `stale_entities`, `poor_signal_entities`, `offline_entities_non_essential`, `stale_entities_non_essential`, `poor_signal_entities_non_essential`; `groups` dict gains per-group `offline_entities`, `stale_entities`, `poor_signal_entities` and NE variants.
- **Card: combined group entity detail table** — `show_entities: true` + `entities_expanded: true` now renders the full per-entity table (battery, signal, stale, suppress state) on combined cards, matching single-group behaviour.
- **Automation examples**: new `group_summary` template section in `AUTOMATION_EXAMPLES.md`
- **Card: icon mode for stats row** — `show_stat_icons` option (default off) replaces text labels with MDI icons in the stats row
- **Card: icon mode for entity list / groups table header** — `show_table_icons` option (default off) replaces column header text with MDI icons
- **Card: `show_entity_health` toggle** — hides/shows Bat. & Signal columns in the single-group entity list (default on); mirrors `show_group_health` on combined card
- **Card: sort by signal** — `signal_asc` / `signal_desc` sort options; dBm and % normalized to a 0–100 quality score so mixed-unit groups sort correctly; nulls last
- **Card: 3-button suppress actions** — replaces Suppress All / Unsuppress All with **Suppress All** (offline + stale + poor signal, 60 min), **Suppress Offline** (offline only, 60 min), **Unsuppress All**. NE entities included when `show_non_essential_stats` is on. Available on both regular and combined groups.
- **Card: `show_groups` config key** — independently controls the groups breakdown table on combined cards (previously `show_entities` gated both). Defaults to `true`; set `false` to hide the breakdown while keeping the entity list visible.
- **Card: `collapse_devices` config key** — when enabled, entities that share the same physical HA device (same `device_id`), same display name, and identical battery/signal values are collapsed into a single row showing worst-case status. Entities with no device assignment are never collapsed. Requires **Use Device Names** on the group. Defaults to `false`.

### Changed
- Signal `signal_enabled` toggle positioned directly below `battery_threshold` in Advanced settings
- All signal sensor display uses the correct unit for the selected network type (dBm or %)
- 28 supported languages receive full translations for all new strings
- Card: `show_actions` checkbox now available for combined group cards in the editor
- **Card: combined card `show_entities` now controls only the flat entity list** — previously it gated both the groups breakdown table and the entity list; use `show_groups` to control the breakdown independently.
- **Card: editor "Sort Groups By" renamed to "Sort Groups & Entities By"** — the sort selection now drives both the groups breakdown table order and the flat entity list order on combined cards.
- **Card: entity list expand/collapse on combined cards is now independent** — the entity list section has its own expand toggle, decoupled from the groups breakdown toggle.

### Fixed
- **Battery sensor state changes now trigger live updates** — mapped battery sensors (e.g. `sensor.device_battery`) were not subscribed to HA state change events; updates no longer require waiting for the next poll cycle.
- **Combined card: Non-Essential stats row was silently omitted** — `show_non_essential_stats: true` on a combined group card now renders the NE Online/Offline/Stale/Low Battery/Poor Signal row, matching single-group behaviour.
- **Card: entity list height cap removed** — groups with >50 entities no longer have Suppress buttons overlapping the last rows
- **Card: Suppress/Unsuppress buttons move above entity list for large groups** (>50 entities)
- **Card: `Signal: OK` status removed** — healthy-signal entities show `Online` (green dot); yellow reserved for degraded only
- **Card: status sort — stale entities now sort above online**
- **Card: combined group suppress was silently failing** — suppress handlers were passing the combined entry_id as `group=` to the service; each entity is now scoped to its source group's entry_id.
- **Diagnostics: new schema** — `counts`, `entities`, and `config` sub-dicts replace flat keys
- **Card: `show_entities` guard consistent across combined and regular render paths** — both now use `!== false`, preventing `show_entities: undefined` from silently hiding the entity list.
- **Card: "Entity List Expanded by Default" editor toggle now disables when `show_entities` is `false`** — applies to both regular and combined cards.


## [0.3.14] - 2026-08-02

### Added
- **Non-Essential entity tier** — mark entities as Non-Essential per group; excluded from all KPIs (availability %, offline count, MTBF, MTTR) and alerts. Zero migration: existing groups default to all-essential.
- **9 new sensors per group**: offline/stale/low-battery count+list for non-essential tier; `any_low_battery`, `any_stale`, `any_offline_non_essential` binary sensors
- **6 new bus events**: `entity_availability_stale/stale_recovered`, `entity_availability_low_battery/battery_ok`; combined groups fire offline/recovered; all payloads include `entry_id` and `source_groups`
- **Card: Non-Essential stats row** (`show_non_essential_stats`, default off) — opt-in row for NE Online/Offline/Stale/Low Battery counts; NE entities sorted to bottom of entity list
- **Card: Stale count chip** in stats row; hidden when zero
- **Card: per-entity suppress toggle** (`show_suppress_toggle`, default off)
- **Card: combined groups table** — Total column (entities per group), conditional Bat. and Stale columns (shown only when feature is enabled and count > 0); `show_group_total` and `show_group_health` toggles to hide those columns
- **Card: combined groups NE sub-row** (`show_non_essential_stats`, default off) — opt-in `↳ Non-Essential` sub-row per group showing NE total, Online/Offline and (when feature enabled) Bat./Stale counts; `show_non_essential_stats` toggle now available for combined card in config editor
- **Combined sensor: per-group NE breakdown** — `groups` dict now includes `non_essential_online`, `non_essential_offline`, `non_essential_stale`, `non_essential_low_battery`, `battery_enabled`, `staleness_enabled` per group; `total` now excludes non-essential entities (matches Online/Offline scope)
- **Sensor: `battery_enabled` / `staleness_enabled` attrs** — group summary and combined summary sensors now expose these boolean flags so the card hides battery/stale indicators when the feature is disabled (`threshold=0`), even if stale battery levels exist in entity state

### Fixed
- Suppressing an entity no longer affects historical availability %, MTBF, or MTTR
- Stale entities with healthy battery no longer counted as low battery (explicit `is_low_battery` flag)
- Card: stale entities now trigger yellow "Degraded" header (was "All OK")
- Offline/recovered events include `offline_count` and `offline_entities`
- Combined groups deduplicate entities across member groups
- Battery mapping no longer re-populates cleared entries on edit
- Low battery flag preserved when device also goes offline
- Card: space key no longer scrolls page when entity row is focused
- Card: non-essential entity indicator icon (◌) now renders correctly when `show_non_essential_stats` is off (default) — entities appeared in list without the icon
- Card: compact render stale count now correctly reads `stale_entities` array length for regular groups (was always 0, so stale entities never triggered "Degraded" status in compact mode)
- Sensor: `stale_entities` / `stale_entities_non_essential` attributes now exclude offline entities — offline entities with old timestamps were incorrectly included, causing them to show "just now" on the card instead of their actual offline duration
- Card: Lovelace resource URL now updates on HACS upgrade without requiring a full HA restart
- **Card: "last seen" time resets to boot time after HA restart** — coordinator now seeds `last_changed` on first poll for all entities regardless of staleness config, persists it to storage, and the card hides the battery column when `battery_threshold=0`. Resolves #34.
- **Card: battery/stale columns hidden when feature disabled** — setting `battery_threshold=0` or `staleness_threshold=0` now correctly suppresses those columns in both the entity list and combined groups table, even when battery levels exist in entity state.
- Card: combined groups table — groups with feature disabled show `—` instead of `0` in Bat./Stale columns; stats row order now matches table (Low Battery before Stale); columns stay visible on narrow screens; header separator borders aligned with data rows

### Changed
- Card entity rows and combined group rows are clickable (opens more-info dialog)

### Breaking Changes
- `suppressed_until` includes indefinitely-suppressed entities as `null`. Use `entity_id in suppressed_until` to check suppression status.


## [0.3.13] - 2026-07-22

### Fixed
- **Availability sensor recorder churn** — `AvailabilitySensor.native_value` and `extra_state_attributes` truncate `now` to minute resolution before computing the rolling window cutoff. Previously, sub-second precision advancing every 30s coordinator tick caused the computed % to drift fractionally on every update; even after 1-decimal rounding, high-churn groups wrote a new distinct value every ~35s (~2,400 writes/day per `_today` sensor, 821K total over 45 days across 17 groups). With minute truncation the cutoff moves at most once per minute, capping writes at 1,440/day per sensor. No user-visible impact: the 1-decimal display precision is preserved and the ≤59s window shift is smaller than the rounding resolution. - 2026-07-20

### Fixed
- **Combined group coordinator staleness** — combined sensors now resolve source coordinators from `hass.data` at every update instead of holding references captured at setup time. Coordinators that load after the combined entry (boot-order race) are automatically subscribed on first access (`_active_coordinators` late-subscribe path). `hasattr` guard replaced with a typed class-level `_on_coordinator_update: ... | None = None` annotation so the guard is safe even if properties are called before `async_added_to_hass`.
- **Combined group config flow entry filter** — the "Groups to Include" selector in both the create and edit flows now only shows entries in `ConfigEntryState.LOADED` state, preventing partially-loaded or errored entries from appearing as valid options.
- **`strings.json` out of sync** — `strings.json` was missing the `group` step, `selector.entry_type`, and `options` data descriptions introduced in 0.3.x. Synced to match `en.json`.

### Added
- **Staleness timestamp source** — new per-group option *"Count attribute updates as activity"* (in Monitoring settings). When off (default, unchanged behavior) staleness is measured from `last_changed` — only a change in the entity's main state resets the timer. When on, staleness is measured from `last_updated`, so any update from the entity — including attribute-only changes and repeated same-value reports — counts as activity. Turn it on for devices that report frequently but rarely change value (e.g. a temperature or power sensor holding steady), which were previously flagged stale despite still reporting. Resolves #24.

### Fixed
- **Stale entities miscounted as low battery** — stale entities whose battery was *above* the configured threshold were reported as low battery (e.g. a stale entity at 50% shown as low battery with a 40% threshold). The low-battery counters and lists used "degraded and has a battery reading" as a stand-in for "low battery", but degraded means low battery **or** stale, so any stale device with a battery reading was miscounted. Low battery is now tracked with an explicit `is_low_battery` flag set only when the battery is genuinely below threshold. Degraded status, staleness, offline/cooldown, and suppression are unchanged — this only corrects the low-battery count/list. Thanks to @dimatx for the fix.
- **Card: stale entities now trigger "Degraded" status** — groups with stale entities but no offline or low-battery entities were shown as "All OK" (green). The card header now turns yellow and shows "Degraded" when `stale_count > 0`, consistent with the backend `is_degraded` flag which already included staleness.

## [0.3.12] - 2026-07-13

### Added
- **Bus events on transitions** — the coordinator now fires `entity_availability_offline` and `entity_availability_recovered` on the Home Assistant event bus when a monitored entity crosses offline/recovered (after cooldown, outside the startup grace period). Event data: `entity_id`, `group`, plus `offline_since` / `downtime_seconds`. Use these as native automation triggers instead of watching sensor attributes with templates.
- **Reliability sensors (MTBF + MTTR)** — two new diagnostic sensors per group that flag devices which keep flaking out. **Mean Time Between Failures** shows how *often* devices break (in hours); **Mean Time To Recovery** shows how *long* each outage lasts (in minutes). Together they distinguish a genuinely flaky device from one that had a single long outage at the same uptime %. Both are `EntityCategory.DIAGNOSTIC` (grouped under the device's Diagnostic section, kept off the main dashboard) with `device_class: duration`. Each exposes `total_offline_events` and its own `per_device` breakdown as attributes (the MTBF sensor lists `mtbf_hours` + `offline_events`; the MTTR sensor lists `mttr_minutes` + `offline_events`). Derived from all-time event counters (no extra storage growth, no `state_class` so they do not generate long-term statistics). Values populate once an entity has completed at least one offline→recovery cycle. Combined-group counterparts are not included in this release.
- **`reset_statistics` action** — new service to clear availability history *and* reliability counters (MTBF/MTTR, offline-event count) for an entity or an entire group. Useful after planned maintenance so a known outage does not skew the numbers. Accepts optional `entity_id` and/or `group`, matching the suppress actions.

### Changed
- **Translatable sensor names** — all sensors now use `translation_key` instead of hardcoded English names, so their display names localize with the Home Assistant UI language. Names are provided for all bundled languages (de, es, fr, it, nl, da, nb, pl, pt, sv) with English fallback. Entity IDs and unique IDs are unchanged, so existing dashboards, cards, and automations are unaffected.

### Fixed
- **Statistics bloat: group summary sensors** — `Group Summary` and `Combined Summary` sensors no longer declare `state_class: measurement`. Their value is an entity count that only changes when a group is edited, so the recorder was writing ~288 identical rows per day per group to `statistics_short_term` indefinitely. They remain valid state sensors (current count still shown); only long-term statistics generation stops. The availability-% sensors are unaffected — those are legitimate time-series statistics.
  - **Existing rows:** this stops *new* statistics from being generated but does not delete the rows already accumulated. Home Assistant surfaces a **Settings → System → Repairs** issue for the affected `sensor.*_group_summary` / `sensor.*_combined_summary` entities ("... no longer has a state class"); resolve it to purge the orphaned statistics, or use **Developer Tools → Statistics** and delete them manually.
- **Card: empty filtered list** — when `entity_filter` is `offline` or `online` and no entities match (e.g. all healthy), the card no longer renders the dangling `Entity / State / Bat.` column legend. The section header still shows the filtered count (e.g. `Problem Entities (0)`) so the active filter stays visible.
- **Combined group creation: no group pre-selected** — the "groups to include" multi-select on the Create Combined Group form no longer auto-selects the first group. The `Required` field lacked a default, so Home Assistant's frontend pre-checked the first option; it now defaults to an empty selection (the ≥2 groups validation is unchanged). The edit/options flow already behaved correctly.

## [0.3.11] - 2026-06-29

### Added
- **Area sensors** — four new sensors per group and combined group exposing which physical areas are affected by offline entities:
  - **Affected Areas Count** (`affected_areas_count`) — integer count of unique HA areas containing ≥1 offline, unsuppressed entity. Entities with no area assigned contribute under the reserved sentinel `(No Area)` so `offline_count > 0` always implies `affected_areas_count ≥ 1`.
  - **Affected Areas** (`affected_areas`) — comma-separated sorted list of area names (including `(No Area)` when unassigned entities are offline). Attributes: `areas` (list), `count`, `unassigned_entities` (entity IDs with no area assigned).
  - **Areas Recently Offline** (`affected_areas_recently_offline`) — areas where ≥1 entity went offline within the group's `recovery_window_minutes`. Attributes: `areas`, `count`, `window_minutes`.
  - **Areas Recently Recovered** (`affected_areas_recently_recovered`) — areas where ALL non-suppressed entities are back online and the most recent recovery happened within `recovery_window_minutes`. Attributes: `areas`, `count`, `window_minutes`.
  - All four sensors appear on combined groups as well, with area deduplication across source groups.
  - Area resolution: entity `area_id` → device `area_id` → `(No Area)` sentinel. The `unassigned_entities` attribute on `Affected Areas` lists entity IDs with no area for diagnostics.
  - No new configuration required — sensors appear automatically on update and reuse the group's existing `recovery_window_minutes` setting.
  - Entities are excluded if suppressed, consistent with all other sensors.

### Changed
- **Card: Affected Areas pill row** — new opt-in section in the Lovelace card. When `show_affected_areas: true` (off by default), a compact pill row appears between the stats and availability sections listing offline area names. Named areas render in red; entities with no area assigned appear as italic "Unassigned". Works identically for regular and combined groups. Toggle via the card editor "Show Affected Areas" checkbox.

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

## [0.3.4] - 2026-05-31

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
