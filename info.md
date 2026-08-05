# Entity Availability for Home Assistant

Monitor entity availability across your Home Assistant setup. Track offline entities, uptime history, and degraded states with a custom dashboard card.

## Features

- Multi-group support — organize entities by function (Security, Climate, Media, etc.)
- Combined groups — merge multiple groups into a single aggregate sensor set for cross-group automations; combined events include a `source_groups` list so automations know which home group an entity belongs to
- **Non-Essential entity tier** — mark entities as non-essential when creating or editing a group. They appear on the card but are excluded from all KPIs and alerts. Perfect for devices expected to be offline (TV in standby, seasonal sensor) without needing a separate group. Enable the `show_non_essential_stats` card option to see their status on the dashboard.
- Configurable bad states — define which states count as offline (`unavailable`, `unknown`, or custom)
- Cooldown timer — ignore brief blips before marking an entity offline
- Availability % sensors — track uptime over today, 3-day, 5-day, and 7-day windows (5-minute buckets)
- Battery monitoring with entity mapping — auto-detects battery sensors, user confirms/overrides per entity
- Low Battery Count sensor — numeric count for easy automation triggers
- Battery entities that report `low` (text) are supported in addition to numeric percentages
- **Signal strength monitoring** — optional feature (disabled by default). Enable per group, bind a signal sensor and network type to each entity. Supported: Wi-Fi, Zigbee, Z-Wave, Bluetooth, Thread, LTE/4G, 5G, LoRaWAN, Percentage, Generic/RSSI. Auto-detects sensors via device class or naming conventions (Z2MQTT `_linkquality`, BLE `_signal_strength`, etc.). Poor signal triggers `any_poor_signal` binary sensor and `entity_availability_poor_signal` event.
- Device name display — optionally show the HA device name instead of entity friendly name in offline/recovered sensor states
- Area sensors — four sensors per group: Affected Areas Count, Affected Areas, Areas Recently Offline, Areas Recently Recovered
- Degraded entity detection — flag entities with low battery, stale data, or poor signal
- Group Summary sensor — total, online, offline, suppressed, non_essential counts + full entity list
- Maintenance/suppression mode — suppress individual entities or entire groups. Card shows three action buttons: **Suppress All** (offline + stale + poor signal), **Suppress Offline** (offline only), **Unsuppress All**. Works on both regular and combined groups.
- Any Offline binary sensor (Problem class) — triggers automations when entities go offline
- Custom Lovelace card — dashboard-style display with status icon, stats, availability bars, entity list, and visual editor; supports both regular and combined groups
- **Card: icon mode for stats row** — enable `show_stat_icons` to replace text labels with MDI icons in the stats row (default off)
- **Card: sort by signal** — sort entity list by signal quality (`signal_asc` / `signal_desc`); dBm and % normalized so mixed-protocol groups sort correctly
- Card editor auto-detects group type and hides options that don't apply to combined groups
- Group Slug picker — dropdown populated from discovered groups, split into regular and combined optgroups
- Configurable entity sort order in card — by status, name, battery level, or signal quality (ascending/descending)
- Customizable availability bar colors and thresholds
- Survives HA restarts — history persisted via HA Store, no recorder dependency; startup false-positive alerts suppressed for 60 seconds
- Recorder-friendly writes — sensors only publish when value, attributes, or availability actually change

## Setup

1. Install via HACS
2. Go to **Settings → Devices & Services → Add Integration**
3. Search for **Entity Availability**
4. Follow the config flow to create your first entity group

> **Note:** Availability sensors will show as `unavailable` after first install. This is normal — they need time (at least 5 minutes) to collect data before reporting a percentage.

> This is an unofficial integration not affiliated with Home Assistant.
