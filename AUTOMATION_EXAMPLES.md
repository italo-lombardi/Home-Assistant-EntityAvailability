# Entity Availability — Automation Examples

Ready-to-adapt automations for every feature. Replace `security_devices` with your own group slug (the lowercased, underscore-separated group name) and `notify.mobile_app_my_phone` with your notify service.

> **Tip:** the group slug appears in every entity ID the integration creates — e.g. a group named "Security Devices" produces `sensor.entity_availability_security_devices_offline_count`.

---

## Bus events

The integration fires four events on the Home Assistant event bus at each transition (after the group's cooldown, outside the 60 s startup grace period). These are the cleanest automation triggers — no template polling of sensor attributes.

| Event | Fired when | Data |
|-------|-----------|------|
| `entity_availability_offline` | An entity is confirmed offline | `entity_id`, `group`, `entry_id`, `offline_since`, `offline_count`, `offline_entities` |
| `entity_availability_recovered` | An offline entity returns online | `entity_id`, `group`, `entry_id`, `downtime_seconds`, `offline_count`, `offline_entities` |
| `entity_availability_low_battery` | An entity's battery drops below threshold | `entity_id`, `group`, `entry_id`, `battery_level`, `low_battery_count`, `low_battery_entities` |
| `entity_availability_battery_ok` | A low-battery entity's level rises above threshold | `entity_id`, `group`, `entry_id`, `battery_level`, `low_battery_count`, `low_battery_entities` |

`offline_count` and `offline_entities` reflect the group's offline state at the moment the entity transitions. For `entity_availability_offline` the newly-offline entity is included; for `entity_availability_recovered` it is already excluded. Use `trigger.event.data.offline_count` in automations instead of querying the `offline_count` sensor — the sensor state is written asynchronously and may not yet reflect the transition.

`low_battery_count` and `low_battery_entities` follow the same snapshot rule: for `entity_availability_low_battery` the newly-low entity is included; for `entity_availability_battery_ok` it is already excluded.

**`offline_since`** is always an ISO timestamp string for individual group events. For combined group events it may be `null` if the coordinator has not yet recorded the transition time — guard with `| default(none)` before passing to `as_datetime()`:
```yaml
{{ as_local(as_datetime(trigger.event.data.offline_since | default(none))) if trigger.event.data.offline_since else "unknown" }}
```

**Combined groups fire all four events with the same payload shape** — one event per affected entity. An automation written for an individual group works unchanged on a combined group; just change the `group` name in the trigger filter.

### Notify when any monitored entity goes offline

```yaml
automation:
  alias: EA — any entity offline
  trigger:
    - platform: event
      event_type: entity_availability_offline
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >
          {{ trigger.event.data.entity_id }} in
          {{ trigger.event.data.group }} went offline.
          {{ trigger.event.data.offline_count }} device(s) now offline.
```

### Notify only for a specific group

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
        message: "Security device offline: {{ trigger.event.data.entity_id }}"
```

### Notify on recovery, including how long it was down

```yaml
automation:
  alias: EA — entity recovered
  trigger:
    - platform: event
      event_type: entity_availability_recovered
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >
          {{ trigger.event.data.entity_id }} recovered after
          {{ (trigger.event.data.downtime_seconds | float / 60) | round(1) }} min offline.
```

### Rich push + email notification (offline and recovery)

Uses `trigger.event.data.offline_count` and `trigger.event.data.offline_entities` from the event payload directly — no sensor state reads, no race condition.

```yaml
automation:
  alias: EA — offline rich notification
  trigger:
    - platform: event
      event_type: entity_availability_offline
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "Device offline ({{ trigger.event.data.offline_count }} now offline)"
        message: >-
          {{ device_attr(device_id(trigger.event.data.entity_id), 'name_by_user')
             or device_attr(device_id(trigger.event.data.entity_id), 'name')
             or trigger.event.data.entity_id }}
          in {{ trigger.event.data.group }} went offline
          ({{ as_local(as_datetime(trigger.event.data.offline_since)).strftime('%d.%m.%Y %H:%M:%S') }}).
    - service: notify.email
      data:
        title: "Device offline ({{ trigger.event.data.offline_count }} now offline)"
        message: |-
          Device: {{ device_attr(device_id(trigger.event.data.entity_id), 'name_by_user')
                     or device_attr(device_id(trigger.event.data.entity_id), 'name')
                     or trigger.event.data.entity_id }}
          Group: {{ trigger.event.data.group }}
          Offline since: {{ as_local(as_datetime(trigger.event.data.offline_since)).strftime('%d.%m.%Y %H:%M:%S') }}

          Still offline ({{ trigger.event.data.offline_count }}):
          • {{ trigger.event.data.offline_entities | join('\n• ') }}
```

```yaml
automation:
  alias: EA — recovery rich notification
  trigger:
    - platform: event
      event_type: entity_availability_recovered
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "Device recovered ({{ trigger.event.data.offline_count }} still offline)"
        message: >-
          {{ device_attr(device_id(trigger.event.data.entity_id), 'name_by_user')
             or device_attr(device_id(trigger.event.data.entity_id), 'name')
             or trigger.event.data.entity_id }}
          in {{ trigger.event.data.group }} returned online after
          {{ (trigger.event.data.downtime_seconds | float / 60) | round(0) }} minutes.
    - service: notify.email
      data:
        title: "Device recovered ({{ trigger.event.data.offline_count }} still offline)"
        message: |-
          Device: {{ device_attr(device_id(trigger.event.data.entity_id), 'name_by_user')
                     or device_attr(device_id(trigger.event.data.entity_id), 'name')
                     or trigger.event.data.entity_id }}
          Group: {{ trigger.event.data.group }}
          Downtime: {{ (trigger.event.data.downtime_seconds | float / 60) | round(0) }} minutes

          Still offline ({{ trigger.event.data.offline_count }}):
          {% if trigger.event.data.offline_entities %}
          • {{ trigger.event.data.offline_entities | join('\n• ') }}
          {% else %}
          None — all devices online.
          {% endif %}
```

### Escalate only for long outages

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
        title: Long outage
        message: >
          {{ trigger.event.data.entity_id }} was offline for
          {{ (trigger.event.data.downtime_seconds | float / 60) | round }} minutes.
```

### Notify when a battery drops below threshold

```yaml
automation:
  alias: EA — low battery alert
  trigger:
    - platform: event
      event_type: entity_availability_low_battery
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: "Low battery ({{ trigger.event.data.low_battery_count }} device(s))"
        message: >
          {{ trigger.event.data.entity_id }} in {{ trigger.event.data.group }}
          has {{ trigger.event.data.battery_level }}% battery.
          {{ trigger.event.data.low_battery_count }} device(s) now low:
          {{ trigger.event.data.low_battery_entities | join(', ') }}
```

### Notify only for a specific group's battery events

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
        message: "Security device low battery: {{ trigger.event.data.entity_id }} at {{ trigger.event.data.battery_level }}%"
```

### Alert only when battery is critically low (below 10%)

```yaml
automation:
  alias: EA — critical battery
  trigger:
    - platform: event
      event_type: entity_availability_low_battery
  condition:
    - "{{ trigger.event.data.battery_level | int(100) < 10 }}"
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: Critical battery
        message: >
          {{ trigger.event.data.entity_id }} is at {{ trigger.event.data.battery_level }}% —
          replace battery soon.
```

### Confirm battery replaced (battery_ok event)

```yaml
automation:
  alias: EA — battery replaced
  trigger:
    - platform: event
      event_type: entity_availability_battery_ok
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >
          {{ trigger.event.data.entity_id }} battery OK
          ({{ trigger.event.data.battery_level }}%).
          {{ trigger.event.data.low_battery_count }} device(s) still low.
```

---

## Offline / recovery sensors

### Alert when a group has any offline entity (binary sensor)

```yaml
automation:
  alias: EA — group has offline
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
             'offline_count') }} security device(s) offline.
```

### Name the devices that just went offline

`recently_offline` lists friendly names of entities that dropped within the recovery window.

```yaml
automation:
  alias: EA — which devices went offline
  trigger:
    - platform: state
      entity_id: sensor.entity_availability_security_devices_recently_offline
  condition:
    - "{{ trigger.to_state.state not in ['None', 'unknown', 'unavailable'] }}"
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: "Just went offline: {{ trigger.to_state.state }}"
```

### Announce recoveries

```yaml
automation:
  alias: EA — which devices recovered
  trigger:
    - platform: state
      entity_id: sensor.entity_availability_security_devices_recently_recovered
  condition:
    - "{{ trigger.to_state.state not in ['None', 'unknown', 'unavailable'] }}"
  action:
    - service: tts.google_translate_say
      data:
        entity_id: media_player.kitchen
        message: "Recovered: {{ trigger.to_state.state }}"
```

### Persistent notification that auto-clears

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

### Daily availability report

```yaml
automation:
  alias: EA — daily availability report
  trigger:
    - platform: time
      at: "08:00:00"
  action:
    - service: notify.mobile_app_my_phone
      data:
        title: Availability report
        message: >
          Today: {{ states('sensor.entity_availability_security_devices_availability_today') }}%
          7-day: {{ states('sensor.entity_availability_security_devices_availability_7d') }}%
```

### Warn when 7-day availability drops below a threshold

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
        message: >
          7-day availability dropped to
          {{ states('sensor.entity_availability_security_devices_availability_7d') }}%.
```

---

## Reliability (MTBF / MTTR)

MTBF (mean time between failures, hours) and MTTR (mean time to recovery, minutes) are separate diagnostic sensors. Use them to spot flaky hardware the availability % hides.

### Weekly reliability report

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
    - service: notify.mobile_app_my_phone
      data:
        title: Weekly reliability
        message: >
          MTBF: {{ states('sensor.entity_availability_security_devices_mtbf') }} h
          MTTR: {{ states('sensor.entity_availability_security_devices_mttr') }} min
          Total outages: {{ state_attr('sensor.entity_availability_security_devices_mtbf',
             'total_offline_events') }}
```

### Flag a flaky group (low MTBF = breaking often)

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
        title: Flaky devices
        message: >
          Security group MTBF is only
          {{ states('sensor.entity_availability_security_devices_mtbf') }} h —
          check the per_device attribute for the culprit.
```

### Find the worst device from the per-device breakdown

```yaml
automation:
  alias: EA — worst reliability device
  trigger:
    - platform: time
      at: "09:05:00"
  action:
    - variables:
        per_device: >
          {{ state_attr('sensor.entity_availability_security_devices_mtbf', 'per_device') }}
        worst: >
          {{ (per_device.items()
              | selectattr('1.mtbf_hours', 'ne', None)
              | sort(attribute='1.mtbf_hours') | first) if per_device else None }}
    - condition: "{{ worst is not none }}"
    - service: notify.mobile_app_my_phone
      data:
        message: >
          Least reliable: {{ worst[0] }}
          (MTBF {{ worst[1].mtbf_hours }} h, {{ worst[1].offline_events }} outages)
```

---

## Battery

### Notify on low battery

```yaml
automation:
  alias: EA — low battery
  trigger:
    - platform: numeric_state
      entity_id: sensor.entity_availability_security_devices_low_battery_count
      above: 0
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >
          Low battery: {{ states('sensor.entity_availability_security_devices_low_battery') }}
```

---

## Affected areas

Area sensors tell you *where* in the home the problem is.

### Announce which rooms are affected

```yaml
automation:
  alias: EA — affected areas
  trigger:
    - platform: numeric_state
      entity_id: sensor.entity_availability_security_devices_affected_areas_count
      above: 0
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >
          Offline entities in: {{ states('sensor.entity_availability_security_devices_affected_areas') }}
```

---

## Combined groups

**Combined groups fire all four events with the same payload shape** — one event per affected entity. An automation written for an individual group works unchanged on a combined group; just change the `group` name in the trigger filter.

**`source_groups` (combined group events only):** a list of the home group name(s) that contain the entity. Single-membership entities yield `["Switches"]`; entities shared across multiple home groups list all names. Use it to include the originating group in notifications without a separate per-group automation.

### Notify when anything goes offline across all groups

```yaml
automation:
  alias: EA — anything offline anywhere (event)
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
          {{ trigger.event.data.entity_id }} in
          {{ trigger.event.data.group }} went offline.
          {{ trigger.event.data.offline_count }} device(s) now offline.
```

### Notify on recovery from a combined group

```yaml
automation:
  alias: EA — anything recovered anywhere (event)
  trigger:
    - platform: event
      event_type: entity_availability_recovered
      event_data:
        group: All Home
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >-
          {{ trigger.event.data.entity_id }} recovered.
          {{ trigger.event.data.offline_count }} device(s) still offline.
```

### Include source group name in combined notification

`source_groups` tells you which home group the entity came from, even when triggering on a combined group.

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
        message: >-
          {{ trigger.event.data.entity_id }} went offline.
          Group: {{ trigger.event.data.source_groups | join(', ') }}
          ({{ trigger.event.data.offline_count }} now offline)
```

### Avoid double-fires — filter by entry_id

Combined groups fire their own event **and** the home group fires its own. To respond only to the combined group and ignore the individual group's event, filter on `entry_id`:

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
        message: "{{ trigger.event.data.entity_id }} offline in {{ trigger.event.data.source_groups | join(', ') }}"
```

Alternatively filter by group name (`event_data: group: All Home`) which also guarantees only one automation fires per transition.

### Binary sensor fallback (no event needed)

The `any_offline` binary sensor is a simple whole-home trigger when event details aren't needed.

```yaml
automation:
  alias: EA — anything offline anywhere (binary sensor)
  trigger:
    - platform: state
      entity_id: binary_sensor.entity_availability_combined_all_devices_any_offline
      to: "on"
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: "Something is offline across the home."
```

---

## Services

> **`group:` takes the group *name*** (e.g. `Security Devices`), not the entity-ID slug. From the UI action editor the group picker passes the config-entry ID automatically; in hand-written YAML use the exact group name as shown in Settings.

### Suppress during planned maintenance

> **Note on availability %:** Suppressing an entity silences offline alerts and stops accumulating new offline time — it does not remove past downtime from the availability sensor. The group availability % only improves as offline buckets age out of the rolling window. To erase the maintenance outage from history entirely, call `reset_statistics` after unsuppressing (see example below).

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
    # ... do maintenance ...
```

### Reset statistics after known maintenance

Clears availability history **and** reliability counters so a planned outage doesn't skew the numbers.

```yaml
automation:
  alias: EA — reset after firmware update
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

### Suppress a single flapping entity indefinitely

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
  action:
    - service: notify.telegram
      data:
        message: >-
          🔴 *{{ trigger.event.data.entity_id }}* offline
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
  action:
    - service: notify.slack
      data:
        message: >-
          :red_circle: `{{ trigger.event.data.entity_id }}` went offline
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
  action:
    - service: tts.speak
      target:
        entity_id: tts.piper
      data:
        media_player_entity_id: media_player.living_room
        message: >-
          {{ state_attr('sensor.entity_availability_security_devices_offline_count', 'offline_entities')
             | map('replace', 'sensor.', '') | map('replace', '_', ' ') | list | join(' and ') }}
          {% if trigger.event.data.offline_count | int == 1 %}is{% else %}are{% endif %} offline.
```

---

## Advanced patterns

### Alert only when ALL devices are back online

Fire once when the last offline device recovers (offline_count drops to 0).

```yaml
automation:
  alias: EA — all devices back online
  trigger:
    - platform: event
      event_type: entity_availability_recovered
  condition:
    - "{{ trigger.event.data.offline_count | int == 0 }}"
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: "All devices in {{ trigger.event.data.group }} are back online."
```

### Suppress during a time window (overnight)

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

### Log offline events to a helper (for dashboards)

Writes the last offline entity name and time to `input_text` helpers for display in a Lovelace card.

```yaml
automation:
  alias: EA — log last offline
  trigger:
    - platform: event
      event_type: entity_availability_offline
  action:
    - service: input_text.set_value
      target:
        entity_id: input_text.last_offline_entity
      data:
        value: >-
          {{ trigger.event.data.entity_id }}
          ({{ as_local(as_datetime(trigger.event.data.offline_since)).strftime('%H:%M:%S') }})
```

### `mode: queued` — when to use it

`mode: queued` is needed only when a single automation could be triggered again before the previous run finishes. With per-entity events (`entity_availability_offline` fires once per entity) this is rare. **Remove `mode: queued` if:**
- You filter on a specific `group` or `entry_id` — one event per entity, no reentrance.

**Keep `mode: queued` if:**
- Your automation has no filter and multiple entities can go offline simultaneously — the automation could be invoked while still running for the previous entity.
- The action includes a long `delay:` or `wait_template:`.

### Deduplicate notifications with a cooldown

Suppress repeated alerts within a window (e.g. one Slack message per 10 minutes regardless of how many devices drop):

```yaml
automation:
  alias: EA — offline with cooldown
  trigger:
    - platform: event
      event_type: entity_availability_offline
  condition:
    - condition: template
      value_template: >-
        {{ (now() - (states('input_datetime.last_offline_notification') | as_datetime | default(now() - timedelta(hours=1))).total_seconds() > 600 }}
  action:
    - service: input_datetime.set_datetime
      target:
        entity_id: input_datetime.last_offline_notification
      data:
        datetime: "{{ now().strftime('%Y-%m-%d %H:%M:%S') }}"
    - service: notify.mobile_app_my_phone
      data:
        message: "{{ trigger.event.data.offline_count }} device(s) offline in {{ trigger.event.data.group }}."
```

---

## Non-Essential entities

Non-essential entities appear on the card and count toward `total_entities` but are excluded from offline/battery events and KPIs. There are no special automations needed — the point is that you *don't* get alerts for them. The examples below show how to use `group_summary` attributes if you ever want to reference them.

### Check how many non-essential entities are currently "offline-but-expected"

```yaml
automation:
  alias: EA — report non-essential offline count
  trigger:
    - platform: state
      entity_id: sensor.entity_availability_living_room_group_summary
  condition:
    - condition: template
      value_template: >-
        {{ state_attr('sensor.entity_availability_living_room_group_summary', 'non_essential') | int(0) > 0 }}
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >-
          {{ state_attr('sensor.entity_availability_living_room_group_summary', 'non_essential') }}
          non-essential device(s) offline (expected):
          {{ state_attr('sensor.entity_availability_living_room_group_summary', 'non_essential_entities') | join(', ') }}
```

### Confirm all non-essential devices came back online (e.g. morning check)

```yaml
automation:
  alias: EA — non-essential all back online
  trigger:
    - platform: time
      at: "09:00:00"
  condition:
    - condition: template
      value_template: >-
        {{ state_attr('sensor.entity_availability_living_room_group_summary', 'non_essential') | int(0) > 0 }}
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: >-
          Still offline (non-essential):
          {{ state_attr('sensor.entity_availability_living_room_group_summary', 'non_essential_entities') | join(', ') }}
```

### Filter out non-essential from a combined group event

Combined group events still fire for non-essential entities — if you want to silence them for non-essential, filter using the `group_summary` attribute:

```yaml
automation:
  alias: EA — combined offline (skip non-essential)
  trigger:
    - platform: event
      event_type: entity_availability_offline
      event_data:
        group: My Combined Group
  condition:
    - condition: template
      value_template: >-
        {{ trigger.event.data.entity_id not in
           state_attr('sensor.entity_availability_my_group_group_summary', 'non_essential_entities') | default([]) }}
  action:
    - service: notify.mobile_app_my_phone
      data:
        message: "{{ trigger.event.data.entity_id }} offline in {{ trigger.event.data.group }}"
```
