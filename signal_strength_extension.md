# Signal Strength Monitoring — Extension Plan

## Overview

Optional feature (mirrors battery pattern exactly). User enables it in the Advanced
settings step with a toggle. When enabled, a final wizard step lets them bind a signal
sensor to each monitored entity and select the network type so we can apply correct
thresholds.

---

## Network Types & Thresholds

Stored as `SIGNAL_NETWORK_TYPES` constant dict. Values are `(good, ok, poor)` in the
native unit for each protocol.

| Key          | Label        | Unit  | Good   | OK      | Poor   | Notes                         |
|--------------|--------------|-------|--------|---------|--------|-------------------------------|
| `wifi`       | Wi-Fi        | dBm   | ≥ -50  | ≥ -70   | < -70  | RSSI                          |
| `zigbee`     | Zigbee       | dBm   | ≥ -50  | ≥ -70   | < -70  | LQI-based sensors report dBm  |
| `zwave`      | Z-Wave       | dBm   | ≥ -70  | ≥ -85   | < -85  | weaker than Wi-Fi/Zigbee      |
| `bluetooth`  | Bluetooth    | dBm   | ≥ -60  | ≥ -80   | < -80  | BLE typical range             |
| `thread`     | Thread       | dBm   | ≥ -60  | ≥ -75   | < -75  | Matter over Thread            |
| `lte`        | LTE/4G       | dBm   | ≥ -80  | ≥ -100  | < -100 | RSSI reported by devices      |
| `lorawan`    | LoRaWAN      | dBm   | ≥ -100 | ≥ -120  | < -120 | much lower expected RSSI      |
| `generic`    | Generic/RSSI | dBm   | ≥ -60  | ≥ -80   | < -80  | fallback                      |

### Signal quality levels

`"good"` → green / no alert  
`"ok"`   → yellow / informational  
`"poor"` → red / alert (fires event, counts toward stats)

---

## Data Model Changes

### `const.py`
```python
CONF_SIGNAL_ENABLED      = "signal_enabled"
CONF_SIGNAL_ENTITY_MAP   = "signal_entity_map"   # {entity_id: {sensor, network_type}}
DEFAULT_SIGNAL_ENABLED   = False

SIGNAL_NETWORK_TYPES = {
    "wifi":      {"label": "Wi-Fi",        "unit": "dBm", "good": -50, "ok": -70},
    "zigbee":    {"label": "Zigbee",       "unit": "dBm", "good": -50, "ok": -70},
    "zwave":     {"label": "Z-Wave",       "unit": "dBm", "good": -70, "ok": -85},
    "bluetooth": {"label": "Bluetooth",    "unit": "dBm", "good": -60, "ok": -80},
    "thread":    {"label": "Thread",       "unit": "dBm", "good": -60, "ok": -75},
    "lte":       {"label": "LTE/4G",       "unit": "dBm", "good": -80, "ok": -100},
    "lorawan":   {"label": "LoRaWAN",      "unit": "dBm", "good": -100, "ok": -120},
    "generic":   {"label": "Generic/RSSI", "unit": "dBm", "good": -60, "ok": -80},
}

EVENT_POOR_SIGNAL         = "entity_availability_poor_signal"
EVENT_SIGNAL_OK           = "entity_availability_signal_ok"
```

### `models.py` — `DeviceState`
Add two fields:
```python
signal_level: int | None = None          # raw dBm value from bound sensor
signal_quality: str | None = None        # "good" | "ok" | "poor" | None
```

---

## Config Flow Changes (`config_flow.py`)

### Step 3 — `async_step_advanced`
Add one new field to the schema:
```python
vol.Optional(CONF_SIGNAL_ENABLED, default=DEFAULT_SIGNAL_ENABLED): selector.BooleanSelector()
```

Branch at the end of `async_step_advanced`:
```python
# existing battery branch first
if self._data[CONF_BATTERY_THRESHOLD] > 0:
    return await self.async_step_battery_mapping()

# new signal branch
if self._data.get(CONF_SIGNAL_ENABLED):
    return await self.async_step_signal_mapping()

self._data[CONF_BATTERY_ENTITY_MAP] = {}
self._data[CONF_SIGNAL_ENTITY_MAP] = {}
return self.async_create_entry(...)
```

`async_step_battery_mapping` similarly chains to signal mapping if enabled:
```python
# at the end instead of async_create_entry:
if self._data.get(CONF_SIGNAL_ENABLED):
    return await self.async_step_signal_mapping()
return self.async_create_entry(...)
```

### Step 5 (new) — `async_step_signal_mapping`
For each entity in `CONF_ENTITIES`, show two fields:
- `{entity_id}__signal_sensor` — optional `EntitySelector(domain="sensor")`
- `{entity_id}__signal_network` — `SelectSelector` from `SIGNAL_NETWORK_TYPES` keys

On submit: build `{entity_id: {"sensor": sensor_id, "network_type": nt}}` dict, store as
`CONF_SIGNAL_ENTITY_MAP`. Skip entries where sensor is empty.

Same mirroring needed in `EntityAvailabilityOptionsFlow`.

---

## Coordinator Changes (`coordinator.py`)

### `__init__`
```python
self._signal_enabled: bool = entry.data.get(CONF_SIGNAL_ENABLED, False)
self._signal_map: dict = entry.data.get(CONF_SIGNAL_ENTITY_MAP, {})
```

### Per-tick update (inside the main update loop, after battery)
```python
if self._signal_enabled:
    fresh_signal = self._get_signal_level(entity_id)
    device.signal_level = fresh_signal
    device.signal_quality = self._classify_signal(entity_id, fresh_signal)
    # fire events on quality transitions (same pattern as battery)
```

### `_get_signal_level(entity_id) -> int | None`
1. Check `_signal_map[entity_id]["sensor"]` — read its `state` as int
2. Return `None` if not found or unparseable

### `_classify_signal(entity_id, level) -> str | None`
```python
nt = self._signal_map.get(entity_id, {}).get("network_type", "generic")
thresholds = SIGNAL_NETWORK_TYPES[nt]
if level >= thresholds["good"]:   return "good"
if level >= thresholds["ok"]:     return "ok"
return "poor"
```

### Event firing
On transition to `"poor"` → fire `EVENT_POOR_SIGNAL`  
On transition out of `"poor"` → fire `EVENT_SIGNAL_OK`

---

## Sensor Changes (`sensor.py`)

When `CONF_SIGNAL_ENABLED` is True, add 4 sensors (same structure as battery sensors):

| Sensor unique_id suffix       | What it shows                              | State class   |
|-------------------------------|--------------------------------------------|---------------|
| `_poor_signal`                | comma-separated names of poor-signal entities | list text  |
| `_poor_signal_count`          | count                                      | measurement   |
| `_ne_poor_signal`             | non-essential subset (names)               | list text     |
| `_ne_poor_signal_count`       | non-essential count                        | measurement   |

Entity IDs follow `sensor.entity_availability_{slug}_{suffix}` pattern.

---

## Binary Sensor Changes (`binary_sensor.py`)

Add `AnyPoorSignalBinarySensor` (mirrors `AnyLowBatteryBinarySensor`):
- `BinarySensorDeviceClass.CONNECTIVITY` (closest fit — `SIGNAL_STRENGTH` not a device class)
- `is_on` when any non-suppressed essential entity has `signal_quality == "poor"`

---

## Strings / Translations (`strings.json` + `translations/en.json`)

New keys needed:
- `config.step.advanced.data.signal_enabled`
- `config.step.signal_mapping.title`
- `config.step.signal_mapping.description`
- `entity.sensor.poor_signal.*`
- `entity.sensor.poor_signal_count.*`
- `entity.binary_sensor.any_poor_signal.*`

---

## Card Changes (`entity-availability-card.js`)

### Entity list items (`_buildEntityItems`)
Add `signalLevel`, `signalQuality` to each item, reading from `DeviceState` via the
coordinator data entities attribute (same path as `battery`).

Dot color: `"poor"` overrides to red (alongside offline/low-battery).

### Stats row (`_renderStats`)
Add signal stat pill: only rendered when `poorSignal > 0`. Icon `mdi:signal-variant`.

### Entity detail (inline/tooltip)
Show signal strength next to battery: `📶 -72 dBm (ok)` when bound sensor exists.

### Config editor (no change needed)
Feature is purely backend-driven; no new card config keys required.

---

## Smoke Tests (`tests/smoke/`)

New smoke tests (EC-prefixed, next available numbers):

| ID   | Scenario                                                            |
|------|---------------------------------------------------------------------|
| EC15 | Signal feature disabled — no signal sensors created                 |
| EC16 | Signal feature enabled, entity bound to Wi-Fi sensor at -45 dBm → quality "good", no alert |
| EC17 | Signal drops to -80 dBm (Wi-Fi "poor") → `poor_signal_count` = 1, binary sensor ON, event fired |
| EC18 | Signal recovers → `signal_ok` event, binary sensor OFF             |
| EC19 | Non-essential entity with poor signal → NE count sensor increments, essential binary sensor stays OFF |

---

## Files Changed (summary)

| File                                      | Change type       |
|-------------------------------------------|-------------------|
| `custom_components/entity_availability/const.py`          | add constants     |
| `custom_components/entity_availability/models.py`         | 2 new fields      |
| `custom_components/entity_availability/config_flow.py`    | 1 new step + branching |
| `custom_components/entity_availability/coordinator.py`    | signal reading + events |
| `custom_components/entity_availability/sensor.py`         | 4 new sensors     |
| `custom_components/entity_availability/binary_sensor.py`  | 1 new binary sensor |
| `custom_components/entity_availability/strings.json`      | new translation keys |
| `custom_components/entity_availability/translations/en.json` | same |
| `custom_components/entity_availability/frontend/entity-availability-card.js` | stats + item display |
| `tests/test_config_flow.py`               | new signal flow tests |
| `tests/test_coordinator.py`               | `_get_signal_level`, `_classify_signal`, events |
| `tests/test_sensor.py`                    | 4 new sensor tests |
| `tests/test_binary_sensor.py`             | 1 new binary sensor test |
| `tests/smoke/` (new files)                | EC15–EC19         |

---

## What is NOT included (YAGNI)

- Signal history / availability buckets for signal (not requested)
- Combined-group signal rollup (can add later if needed)
- Per-entity network type override after setup (options flow covers it)
- Signal-strength-based suppression (different feature)
