# Entity Availability — Automation Examples

Ready-to-adapt automations for every feature. Replace `security_devices` with your own group slug (the lowercased, underscore-separated group name) and `notify.mobile_app_my_phone` with your notify service.

> **Tip:** the group slug appears in every entity ID the integration creates — e.g. a group named "Security Devices" produces `sensor.entity_availability_security_devices_offline_count`.

---

## Bus events

The integration fires six events on the Home Assistant event bus at each transition (after the group's cooldown, outside the 60 s startup grace period). These are the cleanest automation triggers — no template polling of sensor attributes.

| Event | Fired when | Data |
|-------|-----------|------|
| `entity_availability_offline` | An entity is confirmed offline | `entity_id`, `group`, `entry_id`, `offline_since`, `offline_count`, `offline_entities` |
| `entity_availability_recovered` | An offline entity returns online | `entity_id`, `group`, `entry_id`, `downtime_seconds`, `offline_count`, `offline_entities` |
| `entity_availability_low_battery` | An entity's battery drops below threshold | `entity_id`, `group`, `entry_id`, `battery_level`, `low_battery_count`, `low_battery_entities` |
| `entity_availability_battery_ok` | A low-battery entity's level rises above threshold | `entity_id`, `group`, `entry_id`, `battery_level`, `low_battery_count`, `low_battery_entities` |
| `entity_availability_stale` | An entity stops reporting new values | `entity_id`, `group`, `entry_id`, `stale_since`, `stale_count`, `stale_entities` |
| `entity_availability_stale_recovered` | A stale entity resumes reporting | `entity_id`, `group`, `entry_id`, `stale_count`, `stale_entities` |

`offline_count` and `offline_entities` reflect the group's offline state at the moment the entity transitions. For `entity_availability_offline` the newly-offline entity is included; for `entity_availability_recovered` it is already excluded. Use `trigger.event.data.offline_count` in automations instead of querying the `offline_count` sensor — the sensor state is written asynchronously and may not yet reflect the transition.

`low_battery_count` and `low_battery_entities` follow the same snapshot rule: for `entity_availability_low_battery` the newly-low entity is included; for `entity_availability_battery_ok` it is already excluded.

`stale_count` and `stale_entities` follow the same snapshot rule: for `entity_availability_stale` the newly-stale entity is included; for `entity_availability_stale_recovered` it is already excluded.

**`offline_since`** is always an ISO timestamp string for individual group events. For combined group events it may be `null` if the coordinator has not yet recorded the transition time — guard with `| default(none)` before passing to `as_datetime()`:
```yaml
{{ as_local(as_datetime(trigger.event.data.offline_since | default(none))) if trigger.event.data.offline_since else "unknown" }}
```

**Combined groups fire all six events with the same payload shape** — one event per affected entity. An automation written for an individual group works unchanged on a combined group; just change the `group` name in the trigger filter.

---

### Automation 1 — Offline alert for a specific group

Filter on `group` to avoid firing for every group in the installation.

```yaml
automation:
  alias: EA — security group offline
  trigger:
    - platform: event
      event_type: entity_availability_offline
      event_data:
        group: Security Devices
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "Device offline ({{ trigger.event.data.offline_count }} now offline)"
        message: >-
          {{ device_attr(device_id(trigger.event.data.entity_id), 'name_by_user')
             or device_attr(device_id(trigger.event.data.entity_id), 'name')
             or trigger.event.data.entity_id }}
          went offline at
          {{ as_local(as_datetime(trigger.event.data.offline_since)).strftime('%H:%M:%S') }}.
          Still offline ({{ trigger.event.data.offline_count }}):
          {{ trigger.event.data.offline_entities | join(', ') }}
```

---

### Automation 2 — Recovery notification with downtime

Fires once per entity when it comes back. Uses event payload directly — no sensor read, no race condition.

```yaml
automation:
  alias: EA — entity recovered
  trigger:
    - platform: event
      event_type: entity_availability_recovered
      event_data:
        group: Security Devices
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "Device recovered ({{ trigger.event.data.offline_count }} still offline)"
        message: >-
          {{ device_attr(device_id(trigger.event.data.entity_id), 'name_by_user')
             or device_attr(device_id(trigger.event.data.entity_id), 'name')
             or trigger.event.data.entity_id }}
          back online after
          {{ (trigger.event.data.downtime_seconds | float / 60) | round(0) }} min.
          {% if trigger.event.data.offline_count | int == 0 %}
          All devices are now online.
          {% else %}
          Still offline: {{ trigger.event.data.offline_entities | join(', ') }}
          {% endif %}
```

---

### Automation 3 — Escalate long outages only

Skip short blips — only alert when downtime exceeds 30 minutes.

```yaml
automation:
  alias: EA — long outage escalation
  trigger:
    - platform: event
      event_type: entity_availability_recovered
  condition:
    - "{{ trigger.event.data.downtime_seconds | float > 1800 }}"  # > 30 min
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: Long outage resolved
        message: >
          {{ trigger.event.data.entity_id }} in {{ trigger.event.data.group }}
          was offline for
          {{ (trigger.event.data.downtime_seconds | float / 60) | round }} minutes.
```

---

### Automation 4 — Low battery alert with count

```yaml
automation:
  alias: EA — security group low battery
  trigger:
    - platform: event
      event_type: entity_availability_low_battery
      event_data:
        group: Security Devices
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "Low battery ({{ trigger.event.data.low_battery_count }} device(s))"
        message: >-
          {{ trigger.event.data.entity_id }} in {{ trigger.event.data.group }}
          is at {{ trigger.event.data.battery_level }}%.
          Low battery devices: {{ trigger.event.data.low_battery_entities | join(', ') }}
```

---

### Automation 5 — Battery replaced confirmation

```yaml
automation:
  alias: EA — battery replaced confirmation
  trigger:
    - platform: event
      event_type: entity_availability_battery_ok
      event_data:
        group: Security Devices
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >
          {{ trigger.event.data.entity_id }} battery OK
          ({{ trigger.event.data.battery_level }}%).
          {% if trigger.event.data.low_battery_count | int > 0 %}
          Still low: {{ trigger.event.data.low_battery_entities | join(', ') }}
          {% else %}
          All batteries healthy.
          {% endif %}
```

---

## Stale entity events

Stale events fire when a device stops updating its state — it is reachable (not `unavailable`) but its last-changed or last-updated timestamp exceeds the staleness threshold. This catches frozen sensors that appear online but have silently stopped working.

---

### Automation 6 — Alert when a device goes stale

```yaml
automation:
  alias: EA — device gone stale
  trigger:
    - platform: event
      event_type: entity_availability_stale
      event_data:
        group: Security Devices
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "Device stale ({{ trigger.event.data.stale_count }} now stale)"
        message: >-
          {{ trigger.event.data.entity_id }} in {{ trigger.event.data.group }}
          stopped reporting at
          {{ as_local(as_datetime(trigger.event.data.stale_since)).strftime('%H:%M:%S') }}.
          All stale: {{ trigger.event.data.stale_entities | join(', ') }}
```

---

### Automation 7 — Stale device resumed reporting

```yaml
automation:
  alias: EA — stale device recovered
  trigger:
    - platform: event
      event_type: entity_availability_stale_recovered
      event_data:
        group: Security Devices
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >-
          {{ trigger.event.data.entity_id }} in {{ trigger.event.data.group }}
          is reporting again.
          {% if trigger.event.data.stale_count | int > 0 %}
          Still stale: {{ trigger.event.data.stale_entities | join(', ') }}
          {% else %}
          All devices reporting normally.
          {% endif %}
```

---

## Offline / recovery sensors

Use state-based triggers when you want `for:` delays or want to react to the current sensor value rather than a per-entity transition.

> **Note on snapshot behavior:** `offline_count` on the event payload is always current at the moment of the event. The `offline_count` sensor may lag by up to one coordinator cycle (~30 s). Prefer event payload fields in time-sensitive automations.

---

### Automation 8 — Binary sensor with 2-minute delay

Avoids false alerts from brief connectivity glitches.

```yaml
automation:
  alias: EA — group has offline (binary, 2 min delay)
  trigger:
    - platform: state
      entity_id: binary_sensor.entity_availability_security_devices_any_offline
      to: "on"
      for: "00:02:00"
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >
          {{ state_attr('binary_sensor.entity_availability_security_devices_any_offline',
             'offline_count') }} security device(s) still offline after 2 minutes.
          Devices: {{ states('sensor.entity_availability_security_devices_offline_entities') }}
```

---

### Automation 9 — Persistent notification that auto-clears

Creates a persistent dashboard notification when devices are offline; dismisses it when all recover.

```yaml
automation:
  alias: EA — persistent offline notice
  trigger:
    - platform: state
      entity_id: sensor.entity_availability_security_devices_offline_count
  action:
    - choose:
        - conditions:
            - "{{ states('sensor.entity_availability_security_devices_offline_count') | int(0) > 0 }}"
          sequence:
            - service: persistent_notification.create
              data:
                notification_id: ea_security_offline
                title: Security devices offline
                message: >
                  {{ states('sensor.entity_availability_security_devices_offline_count') }} offline:
                  {{ states('sensor.entity_availability_security_devices_offline_entities') }}
      default:
        - service: persistent_notification.dismiss
          data:
            notification_id: ea_security_offline
```

---

## Availability %

Three rolling windows are available for each group: `availability_today`, `availability_3d`, `availability_5d`, and `availability_7d`.

---

### Automation 10 — Daily availability report

```yaml
automation:
  alias: EA — daily availability report
  trigger:
    - platform: time
      at: "08:00:00"
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: Daily availability report — Security Devices
        message: >
          Today:  {{ states('sensor.entity_availability_security_devices_availability_today') }}%
          3-day:  {{ states('sensor.entity_availability_security_devices_availability_3d') }}%
          5-day:  {{ states('sensor.entity_availability_security_devices_availability_5d') }}%
          7-day:  {{ states('sensor.entity_availability_security_devices_availability_7d') }}%
```

---

### Automation 11 — Warn when 7-day availability drops below threshold

```yaml
automation:
  alias: EA — availability below 95%
  trigger:
    - platform: numeric_state
      entity_id: sensor.entity_availability_security_devices_availability_7d
      below: 95
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: Availability degraded
        message: >
          Security Devices 7-day availability dropped to
          {{ states('sensor.entity_availability_security_devices_availability_7d') }}%
          (today: {{ states('sensor.entity_availability_security_devices_availability_today') }}%).
```

---

## Reliability (MTBF / MTTR)

MTBF (mean time between failures, hours) and MTTR (mean time to recovery, minutes) are separate diagnostic sensors. Both expose a `per_device` attribute with individual device breakdowns. Use them to spot flaky hardware that the availability % hides — a device could have 99% uptime but 50 micro-outages per day.

---

### Automation 12 — Weekly reliability report with worst device

```yaml
automation:
  alias: EA — weekly reliability report
  trigger:
    - platform: time
      at: "09:00:00"
  condition:
    - condition: time
      weekday: [mon]
  action:
    - variables:
        per_device: >
          {{ state_attr('sensor.entity_availability_security_devices_mtbf', 'per_device') }}
        worst: >
          {{ (per_device.items()
              | selectattr('1.mtbf_hours', 'ne', None)
              | sort(attribute='1.mtbf_hours') | first) if per_device else None }}
    - service: notify.mobile_app_my_phone
      data:
        title: Weekly reliability — Security Devices
        message: >
          MTBF: {{ states('sensor.entity_availability_security_devices_mtbf') }} h
          MTTR: {{ states('sensor.entity_availability_security_devices_mttr') }} min
          Total outages: {{ state_attr('sensor.entity_availability_security_devices_mtbf',
             'total_offline_events') }}
          {% if worst %}
          Least reliable: {{ worst[0] }}
          (MTBF {{ worst[1].mtbf_hours }} h, {{ worst[1].offline_events }} outages)
          {% endif %}
```

---

### Automation 13 — Flag a flaky group (low MTBF)

```yaml
automation:
  alias: EA — flaky group alert
  trigger:
    - platform: numeric_state
      entity_id: sensor.entity_availability_security_devices_mtbf
      below: 6          # MTBF under 6 h — something keeps dropping
      for: "01:00:00"
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: Flaky devices detected
        message: >
          Security Devices MTBF is only
          {{ states('sensor.entity_availability_security_devices_mtbf') }} h —
          check the per_device attribute for the culprit.
          MTTR: {{ states('sensor.entity_availability_security_devices_mttr') }} min avg recovery.
```

---

## Battery sensors

Battery sensors track the count, list, and binary state of devices below the configured threshold.

---

### Automation 14 — Notify on low battery count sensor

Useful when you want a single daily check instead of per-event alerts.

```yaml
automation:
  alias: EA — low battery count check
  trigger:
    - platform: numeric_state
      entity_id: sensor.entity_availability_security_devices_low_battery_count
      above: 0
      for: "00:10:00"
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "{{ states('sensor.entity_availability_security_devices_low_battery_count') }} device(s) low battery"
        message: >
          Low battery devices:
          {{ states('sensor.entity_availability_security_devices_low_battery') }}
```

---

## Affected areas

Area sensors tell you *where* in the home the problem is, using HA's native area registry.

---

### Automation 15 — Announce which rooms are affected

```yaml
automation:
  alias: EA — affected areas alert
  trigger:
    - platform: numeric_state
      entity_id: sensor.entity_availability_security_devices_affected_areas_count
      above: 0
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "{{ states('sensor.entity_availability_security_devices_affected_areas_count') }} area(s) affected"
        message: >
          Offline devices detected in:
          {{ states('sensor.entity_availability_security_devices_affected_areas') }}
```

---

## Combined groups

**Combined groups fire all six events with the same payload shape** — one event per affected entity. An automation written for an individual group works unchanged on a combined group; just change the `group` name in the trigger filter.

**`source_groups` (combined group events only):** a list of the home group name(s) that contain the entity. Single-membership entities yield `["Security Devices"]`; entities shared across multiple home groups list all names.

**Avoiding double-fires:** combined groups fire their own event *and* each constituent home group fires its own. To respond only to the combined group, filter on `entry_id` (Settings → Integrations → entry ID in URL) or on the combined group name.

---

### Automation 16 — Combined group offline with source group

```yaml
automation:
  alias: EA — combined offline with source group
  trigger:
    - platform: event
      event_type: entity_availability_offline
      event_data:
        group: All Home
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "Device offline ({{ trigger.event.data.offline_count }} total)"
        message: >-
          {{ trigger.event.data.entity_id }} went offline.
          Belongs to: {{ trigger.event.data.source_groups | join(', ') }}
          Total offline: {{ trigger.event.data.offline_count }}
```

---

### Automation 17 — Combined only via entry_id (no double-fire)

```yaml
automation:
  alias: EA — combined only (no double-fire)
  trigger:
    - platform: event
      event_type: entity_availability_offline
      event_data:
        entry_id: "your_combined_entry_id_here"   # Settings → Integrations → entry ID in URL
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >-
          {{ trigger.event.data.entity_id }} offline in
          {{ trigger.event.data.source_groups | join(', ') }}.
          Total across all groups: {{ trigger.event.data.offline_count }}
```

---

## Services

> **`group:` takes the group *name*** (e.g. `Security Devices`), not the entity-ID slug. From the UI action editor the group picker passes the config-entry ID automatically; in hand-written YAML use the exact group name as shown in Settings.

> **Note on availability %:** Suppressing an entity silences offline alerts and stops accumulating new offline time — it does not remove past downtime. The group availability % only improves as offline buckets age out of the rolling window. To erase a maintenance outage from history entirely, call `reset_statistics` after unsuppressing.

---

### Automation 18 — Suppress during maintenance, reset after

Triggered by a helper toggle that you flip before and after maintenance.

```yaml
automation:
  alias: EA — suppress during maintenance
  trigger:
    - platform: state
      entity_id: input_boolean.maintenance_mode
      to: "on"
  action:
    - service: entity_availability.suppress
      data:
        group: Security Devices
        duration: 120        # minutes

automation:
  alias: EA — unsuppress and reset after maintenance
  trigger:
    - platform: state
      entity_id: input_boolean.maintenance_mode
      to: "off"
  action:
    - service: entity_availability.unsuppress
      data:
        group: Security Devices
    - service: entity_availability.reset_statistics
      data:
        group: Security Devices
```

---

### Automation 19 — Suppress a known-flaky entity indefinitely

```yaml
automation:
  alias: EA — mute known-bad sensor
  trigger:
    - platform: event
      event_type: entity_availability_offline
      event_data:
        entity_id: sensor.flaky_attic_sensor
  action:
    - service: entity_availability.suppress_indefinitely
      data:
        entity_id: sensor.flaky_attic_sensor
```

---

## Notification channels

### Telegram

```yaml
automation:
  alias: EA — offline to Telegram
  trigger:
    - platform: event
      event_type: entity_availability_offline
      event_data:
        group: Security Devices
  action:
    - service: notify.telegram
      data:
        message: >-
          *{{ trigger.event.data.entity_id }}* offline
          in {{ trigger.event.data.group }}.
          {{ trigger.event.data.offline_count }} device(s) now offline.
```

### Slack

```yaml
automation:
  alias: EA — offline to Slack
  trigger:
    - platform: event
      event_type: entity_availability_offline
      event_data:
        group: Security Devices
  action:
    - service: notify.slack
      data:
        message: >-
          :red_circle: `{{ trigger.event.data.entity_id }}` offline
          in *{{ trigger.event.data.group }}*
          ({{ trigger.event.data.offline_count }} now offline).
```

### TTS announcement on speaker

```yaml
automation:
  alias: EA — announce offline on speaker
  trigger:
    - platform: event
      event_type: entity_availability_offline
      event_data:
        group: Security Devices
  action:
    - service: tts.speak
      target:
        entity_id: tts.piper
      data:
        media_player_entity_id: media_player.living_room
        message: >-
          {{ trigger.event.data.entity_id | replace('sensor.', '') | replace('_', ' ') }}
          in {{ trigger.event.data.group }} is offline.
```

---

## Advanced patterns

### Cooldown — one notification per 10-minute window

Suppresses repeated alerts when multiple devices drop at once. Stores the last-notification timestamp in an `input_datetime` helper.

> **Prerequisites:** create `input_datetime.last_offline_notification` with "has time" enabled.

```yaml
automation:
  alias: EA — offline with cooldown
  trigger:
    - platform: event
      event_type: entity_availability_offline
      event_data:
        group: Security Devices
  condition:
    - condition: template
      value_template: >-
        {% set last = states('input_datetime.last_offline_notification') %}
        {% set last_dt = last | as_datetime if last not in ['unknown','unavailable'] else (now() - timedelta(hours=1)) %}
        {{ (now() - last_dt).total_seconds() > 600 }}
  action:
    - service: input_datetime.set_datetime
      target:
        entity_id: input_datetime.last_offline_notification
      data:
        datetime: "{{ now().strftime('%Y-%m-%d %H:%M:%S') }}"
    - service: notify.mobile_app_my_phone
      data:
        message: >
          {{ trigger.event.data.offline_count }} device(s) offline in
          {{ trigger.event.data.group }}.
```

### `mode: queued` guidance

`mode: queued` is needed only when a single automation could be triggered again before the previous run finishes. With per-entity events (`entity_availability_offline` fires once per entity) this is rarely necessary.

**Remove `mode: queued` if:**
- You filter on a specific `group` or `entry_id` — one event per entity, no reentrance.

**Keep `mode: queued` if:**
- Your automation has no filter and multiple entities can go offline simultaneously.
- The action includes a long `delay:` or `wait_template:`.

### Suppress during overnight hours

```yaml
automation:
  alias: EA — overnight suppress
  trigger:
    - platform: time
      at: "23:00:00"
  action:
    - service: entity_availability.suppress
      data:
        group: Security Devices
        duration: 480        # 8 hours

automation:
  alias: EA — morning unsuppress
  trigger:
    - platform: time
      at: "07:00:00"
  action:
    - service: entity_availability.unsuppress
      data:
        group: Security Devices
```

---

## Non-Essential entities

Non-essential entities appear on the card and count toward `total_entities` but are excluded from offline/battery events and KPIs. The dedicated sensors let you observe them separately without triggering the same alerts as essential devices.

| Sensor / binary sensor | Covers |
|---|---|
| `sensor.entity_availability_security_devices_offline_count_non_essential` | Count of non-essential devices currently offline |
| `sensor.entity_availability_security_devices_stale_count_non_essential` | Count of non-essential devices currently stale |
| `sensor.entity_availability_security_devices_low_battery_count_non_essential` | Count of non-essential devices below battery threshold |
| `binary_sensor.entity_availability_security_devices_any_offline_non_essential` | `on` when any non-essential device is offline |

---

### Automation 20 — Morning briefing combining all health signals

Covers essential offline, stale, low-battery, availability %, and non-essential offline in one daily summary.

```yaml
automation:
  alias: EA — morning device health briefing
  trigger:
    - platform: time
      at: "07:00:00"
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: Morning device health — Security Devices
        message: >-
          {% set offline = states('sensor.entity_availability_security_devices_offline_count') | int(0) %}
          {% set stale = states('sensor.entity_availability_security_devices_stale_count') | int(0) %}
          {% set low_bat = states('sensor.entity_availability_security_devices_low_battery_count') | int(0) %}
          {% set ne_offline = states('sensor.entity_availability_security_devices_offline_count_non_essential') | int(0) %}
          {% set avail_7d = states('sensor.entity_availability_security_devices_availability_7d') %}
          {% if offline == 0 and stale == 0 and low_bat == 0 %}
          All essential devices healthy.
          {% else %}
          Issues: {{ offline }} offline, {{ stale }} stale, {{ low_bat }} low battery.
          {% if offline > 0 %}
          Offline: {{ states('sensor.entity_availability_security_devices_offline_entities') }}
          {% endif %}
          {% if stale > 0 %}
          Stale: {{ state_attr('sensor.entity_availability_security_devices_stale_entities', 'entities') | join(', ') }}
          {% endif %}
          {% endif %}
          7-day availability: {{ avail_7d }}%
          {% if ne_offline > 0 %}
          Non-essential offline (expected): {{ ne_offline }}
          {% endif %}
```

---

## Stale entity sensors

Stale sensors count and list devices that have stopped reporting new values but remain reachable (`available`). Separate non-essential variants let you track low-priority devices without noise.

```yaml
automation:
  alias: EA — stale entity alert
  trigger:
    - platform: numeric_state
      entity_id: sensor.entity_availability_security_devices_stale_count
      above: 0
      for: "00:05:00"
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "{{ states('sensor.entity_availability_security_devices_stale_count') }} device(s) stale"
        message: >-
          Devices stopped reporting:
          {{ state_attr('sensor.entity_availability_security_devices_stale_entities', 'entities') | join(', ') }}

automation:
  alias: EA — non-essential stale alert
  trigger:
    - platform: numeric_state
      entity_id: sensor.entity_availability_security_devices_stale_count_non_essential
      above: 0
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >-
          Non-essential stale ({{ states('sensor.entity_availability_security_devices_stale_count_non_essential') }}):
          {{ state_attr('sensor.entity_availability_security_devices_stale_entities_non_essential', 'entities') | join(', ') }}
```

---

## group_summary templates

The `group_summary` sensor exposes rich attributes for templates and automations. Replace `zigbee_devices` with your group slug.

### Summary message

```yaml
{% set s = states['sensor.entity_availability_zigbee_devices_group_summary'].attributes %}
Total {{ s.total_entities }} | Essential {{ s.essential }}: {{ s.online }} online, {{ s.offline }} offline | NE {{ s.non_essential }}: {{ s.non_essential_online }} online, {{ s.non_essential_offline }} offline
```

### Status line with degraded indicators

```yaml
{% set s = states['sensor.entity_availability_zigbee_devices_group_summary'].attributes %}
{% if s.offline > 0 %}⚠️ {{ s.offline }} offline{% if s.stale > 0 %}, {{ s.stale }} stale{% endif %}{% if s.poor_signal > 0 %}, {{ s.poor_signal }} poor signal{% endif %}
{% else %}✅ All {{ s.essential }} essential online{% if s.stale > 0 %} ({{ s.stale }} stale){% endif %}{% endif %}
```

### Automation trigger on any degraded state

```yaml
automation:
  alias: EA — any degraded essential device
  trigger:
    - platform: template
      value_template: >
        {% set s = states['sensor.entity_availability_zigbee_devices_group_summary'].attributes %}
        {{ s.offline > 0 or s.stale > 0 or s.poor_signal > 0 }}
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >
          {% set s = states['sensor.entity_availability_zigbee_devices_group_summary'].attributes %}
          Degraded: {{ s.offline }} offline, {{ s.stale }} stale, {{ s.poor_signal }} poor signal
          ({{ s.online }}/{{ s.essential }} essential online)
```

### Low battery list with levels

```yaml
{% set lvls = state_attr('sensor.entity_availability_zigbee_devices_group_summary', 'battery_levels') %}
{% set names = state_attr('sensor.entity_availability_zigbee_devices_group_summary', 'display_names') %}
Low battery devices:
{% for eid, pct in lvls.items() if pct < 30 %}
- {{ names.get(eid, eid) }}: {{ pct }}%
{% endfor %}
```

### Essential vs non-essential count in a notification

```yaml
automation:
  alias: EA — daily health summary
  trigger:
    - platform: time
      at: "08:00:00"
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: Zigbee daily summary
        message: >
          {% set s = states['sensor.entity_availability_zigbee_devices_group_summary'].attributes %}
          Essential: {{ s.essential }} ({{ s.online }} online)
          Non-essential: {{ s.non_essential }} ({{ s.non_essential_online }} online)
          {% if s.stale > 0 %}Stale: {{ s.stale }}{% endif %}
          {% if s.poor_signal > 0 %}Poor signal: {{ s.poor_signal }}{% endif %}
```
