# Entity Availability for Home Assistant

Monitor entity availability across your Home Assistant setup. Track offline entities, uptime history, and degraded states with a custom dashboard card.

## Features

- Multi-group support — organize entities by function (Security, Climate, Media, etc.)
- Combined groups — merge multiple groups into a single aggregate sensor set for cross-group automations
- Configurable bad states — define which states count as offline (`unavailable`, `unknown`, or custom)
- Cooldown timer — ignore brief blips before marking an entity offline
- Availability % sensors — track uptime over today, 3-day, 5-day, and 7-day windows (5-minute buckets)
- Battery monitoring with entity mapping — auto-detects battery sensors, user confirms/overrides per entity
- Low Battery Count sensor — numeric count for easy automation triggers
- Battery entities that report `low` (text) are supported in addition to numeric percentages
- Device name display — optionally show the HA device name instead of entity friendly name in offline/recovered sensor states
- Degraded entity detection — flag entities with low battery or stale data
- Group Summary sensor — total, online, offline, suppressed, battery_powered, low_battery counts + full entity list
- Maintenance/suppression mode — suppress individual entities or entire groups
- Any Offline binary sensor (Problem class) — triggers automations when entities go offline
- Custom Lovelace card — dashboard-style display with status icon, stats, availability bars, entity list, and visual editor; supports both regular and combined groups
- Card editor auto-detects group type and hides options that don't apply to combined groups
- Group Slug picker — dropdown populated from discovered groups, split into regular and combined optgroups
- Configurable entity sort order in card — by status, name, or battery level (ascending/descending)
- Customizable availability bar colors and thresholds
- Optional suppress/unsuppress action buttons in card
- Survives HA restarts — history persisted via HA Store, no recorder dependency; startup false-positive alerts suppressed for 60 seconds
- Recorder-friendly writes — sensors only publish when value, attributes, or availability actually change, so steady-state networks don't generate redundant history rows every coordinator tick

## Setup

1. Install via HACS
2. Go to **Settings → Devices & Services → Add Integration**
3. Search for **Entity Availability**
4. Follow the config flow to create your first entity group

> **Note:** Availability sensors will show as `unavailable` after first install. This is normal — they need time (at least 5 minutes) to collect data before reporting a percentage.

> This is an unofficial integration not affiliated with Home Assistant.
