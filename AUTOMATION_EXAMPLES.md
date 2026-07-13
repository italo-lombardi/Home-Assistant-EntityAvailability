# Entity Availability — Automation Examples

Ready-to-adapt automations for every feature. Replace `security_devices` with your own group slug (the lowercased, underscore-separated group name) and `notify.mobile_app_my_phone` with your notify service.

> **Tip:** the group slug appears in every entity ID the integration creates — e.g. a group named "Security Devices" produces `sensor.entity_availability_security_devices_offline_count`.

---

## Bus events

The integration fires two events on the Home Assistant event bus at each transition (after the group's cooldown, outside the 60 s startup grace period). These are the cleanest automation triggers — no template polling of sensor attributes.

| Event | Fired when | Data |
|-------|-----------|------|
| `entity_availability_offline` | An entity is confirmed offline | `entity_id`, `group`, `offline_since` |
| `entity_availability_recovered` | An offline entity returns online | `entity_id`, `group`, `downtime_seconds` |

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

Combined groups aggregate several groups into one sensor set. The `any_offline` binary sensor is the simplest whole-home trigger.

```yaml
automation:
  alias: EA — anything offline anywhere
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
