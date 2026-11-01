# Entity Availability — Behavior Reference

How the coordinator decides an entity is offline, stale, low on battery, or on
a poor signal — and exactly when each of the eight bus events fires, with what
payload. This is the runtime truth behind the events in the trigger picker and
the status shown on the card. For ready-made automations that consume these
events see [`AUTOMATION_EXAMPLES.md`](AUTOMATION_EXAMPLES.md).

Source of truth: `custom_components/entity_availability/coordinator.py` (per-group
events) and `combined_sensor.py` (combined-group events). Event names and
thresholds: `const.py`.

---

## 1. What a group does

A group watches a set of entities and reports their health along four
independent axes:

- **offline** — the entity is in one of the group's configured *bad states*
  (default `unavailable`/`unknown`, user-editable per group), or is missing
  entirely.
- **stale** — the entity is reachable but has stopped reporting new data for
  longer than the group's configured staleness threshold (disabled by default).
- **low_battery** — the entity's battery is below the configured threshold
  (on by default; threshold 20 %, set to 0 to disable).
- **poor_signal** — the entity's signal has dropped into the "poor" tier
  (off by default — opt-in, and each entity needs a signal-sensor mapping;
  the good/ok/poor cutoffs are fixed per network type, not user-set).

Each axis is evaluated once per coordinator poll. A change on any axis is
**edge-triggered**: the event fires only on the transition, not every poll it
stays in that state. Everything below is how each edge is decided and what
rides the event when it fires.

Every per-group event payload carries `entry_id = entry.entry_id` — the config
entry of the group that fired it. This is the stable key for scoping an
automation to one group (the named-trigger **Group** picker sets it for you).

---

## 2. The eight events

All **per-group** payloads carry `entity_id`, `group` (the group's display
name), and `entry_id` (the group's config entry). Beyond those three, each
event carries the fields in the table. At fire time the coordinator also stamps
the firing category's `{cat}_count` and `{cat}_entities` onto the payload — a
snapshot of the group's state in that category at the moment of the transition
(device-collapsed representatives, non-suppressed, essential only). Use the
payload count, not the matching sensor: the sensor is written asynchronously
and may lag a poll.

| Event | Fires when | Extra payload fields |
|---|---|---|
| **offline** | An entity enters one of the group's configured bad states (or goes missing) and is confirmed offline (after the group's cooldown, outside the 60 s startup grace). | `offline_since` (ISO, backdated to the cooldown start), `offline_count`, `offline_entities` |
| **recovered** | A previously-offline entity returns online. | `downtime_seconds`, `offline_count`, `offline_entities` (the recovered entity already excluded) |
| **stale** | A reachable entity's data goes stale (rising edge of `is_stale`). | `stale_since` (ISO), `stale_count`, `stale_entities` |
| **stale_recovered** | A stale entity resumes reporting (falling edge). | `stale_since` (ISO), `stale_count`, `stale_entities` (recovered entity excluded) |
| **low_battery** | An entity's battery drops below threshold (rising edge). | `battery_level`, `low_battery_count`, `low_battery_entities` |
| **battery_ok** | A low-battery entity's level rises back above threshold (falling edge; a dead battery that drops to `None` does not fire this — only a fresh numeric reading clears low). | `battery_level`, `low_battery_count`, `low_battery_entities` (recovered excluded) |
| **poor_signal** | An entity's signal drops into the "poor" tier (rising edge). | `signal_level`, `signal_quality`, `poor_signal_count`, `poor_signal_entities` |
| **signal_ok** | An entity's signal recovers out of "poor" (falling edge). | `signal_level`, `signal_quality`, `poor_signal_count`, `poor_signal_entities` (recovered excluded) |

`offline_count`/`offline_entities` (and the stale/battery/signal equivalents)
are snapshots at the transition: the newly-affected entity **is** included on
the rising-edge event and **already excluded** on the falling-edge event.

> **Named triggers expose these at the top level.** Consumed as a named trigger
> (`entity_availability.offline`, …), the payload fields are `trigger.offline_count`,
> `trigger.entity_id`, etc. — not `trigger.event.data.*`. Same data, flatter path.

---

## 3. Offline lifecycle

An entity is **offline** when its state is in the group's configured **bad
states** (`bad_states` option, default `["unavailable", "unknown"]`) — or when
the entity is missing entirely (its state object is `None`). The default list
is editable per group: the config flow exposes it as a multi-select with
`custom_value=True`, so a group can add arbitrary states (e.g. `off`, `idle`,
`standby`) on top of — or instead of — the two defaults. A missing entity
always counts as bad regardless of the list.

Entering a bad state does not fire **offline** immediately. Two gates stand in
front of the edge:

```
entity enters a configured bad state (or goes missing)
   │
   ├─ within 60 s startup grace ───────────────► no event (HA still settling)
   │
   ├─ within the group's cooldown window ──────► no event yet (debounce a blip)
   │
   └─ still offline after cooldown ────────────► OFFLINE   (offline_since backdated
                                                            to the cooldown start)
```

- **Startup grace (60 s).** For the first 60 s after the integration loads, the
  **offline** transition is suppressed — HA is still restoring states and a
  not-yet-seen entity would read a bad state spuriously. The grace gates
  **only** the offline edge; stale, battery, and signal edges are *not*
  grace-gated and can fire within the first 60 s.
- **Cooldown.** A brief unavailable blip inside the group's cooldown window is
  debounced — the event fires only once the entity is *still* offline after the
  window. `offline_since` is then **backdated to the cooldown start**, so the
  reported outage time counts from when the entity actually dropped, not from
  when the event fired.
- **recovered** fires when the entity comes back; `downtime_seconds` is the
  measured outage, and `offline_count`/`offline_entities` already exclude it.

---

## 4. Stale lifecycle

Stale catches an entity that is **reachable** (`available`) but has silently
stopped sending data — a frozen sensor that still answers but never updates.
Disabled by default (`staleness_threshold = 0`); only evaluated when the
threshold is > 0 and a state is present.

**Which timestamp counts** (option `staleness_use_last_updated`, default off):

- **off → `last_changed`** — only a change in the entity's **main state**
  resets the staleness timer. Attribute-only or repeated same-value reports do
  **not** count as activity.
- **on → `last_updated`** — **any** write resets the timer, including
  attribute-only changes and repeated same-value reports. Read straight from
  the HA state each poll (a same-value write never fires `state_changed`, so a
  cached `last_changed` would look stale). **Load-bearing for RTL-SDR / MQTT /
  Zigbee** sensors that transmit often but hold a steady value (temperature,
  power) — with the default they look stale though healthy; turn this on so
  each transmission counts.

**Hysteresis band** (de-jitters an age hovering on the boundary, and prevents
trapping a just-recovered entity):

```
band            = min(STALENESS_HYSTERESIS (1 min), staleness_threshold / 2)
exit_threshold  = staleness_threshold − band
```

The `threshold/2` cap guarantees `exit_threshold > 0` — without it a small
threshold could give `exit_threshold ≤ 0` and trap a freshly-recovered entity
(age ≈ 0) as permanently stale.

**Enter / stay / clear** (one expression, `age` in minutes):

```
is_stale = age > staleness_threshold
           OR (device.is_stale AND age > exit_threshold)
```

- **Enter** — age strictly greater than the threshold → **stale** (rising edge,
  fires `stale`).
- **Stay** — already stale and age still above `exit_threshold`.
- **Clear** — already stale and age drops to ≤ `exit_threshold` → not stale
  (falling edge, fires `stale_recovered`).

`stale_since` on both events is the stale timestamp (`stale_ts`) or `now`, as
ISO. tz-naive timestamps are coerced to UTC before the age is computed.

---

## 5. Battery lifecycle

Disabled by default off? No — `battery_threshold` defaults to **20** (percent;
0 disables). Evaluated only when the threshold is > 0.

**Reading the level** — `_get_battery_level`, first match wins:

1. The entity's **own state**, if its `device_class` is `battery` and the state
   is not unavailable/unknown.
2. A **mapped companion sensor** (`battery_entity_map`). An empty map value
   means "no battery" → `None`. A mapped `binary_sensor` reads `on` = 0 % (low),
   `off` = 100 %. Otherwise the numeric state.
3. **Auto-detect** from the own-state attributes: `battery_level`, else
   `battery` (checked `is not None`, not truthiness — a real 0 % must not fall
   through).
4. The **device registry**.
5. A **guessed** `sensor.{object_id}_battery`.

Text states parse via `_parse_battery_state`: `"low"` → 0, else `int`.

**Retain-last-known.** If a fresh read returns `None` (a Zigbee/MQTT/RTL-SDR
battery often reports `unknown` for a poll while still online), the device keeps
its last-known level — the retain is **not** gated on the device being offline,
so an HA-restart transient-unavailable does not wipe a restored level (issue
#111).

**Hysteresis (3 %).** Once low, an entity stays flagged until its level clears
the threshold by `BATTERY_HYSTERESIS = 3` %:

```
battery_low = threshold > 0 AND battery_level is not None AND battery_level < threshold
# stay low while already low and still within the band:
if device.is_low_battery AND battery_level is not None
   AND battery_level < threshold + 3:  battery_low = True
```

So a reading sitting on the boundary won't flip the flag (and the count) on and
off each poll.

**The frozen-flag guard — why a dead battery doesn't fire `battery_ok`.**
`battery_ok` fires on the falling edge **only when `device.battery_level is not
None`**. A dead battery reads `None` (its sensor goes unknown/unavailable, and
at the offline edge a stale % with no fresh reading is cleared to `None`). When
the level is `None`, `battery_low` is also `False` (its condition requires
`is not None`) — so without the guard the `not battery_low` branch would fire a
**spurious** `battery_ok` for a battery that actually *died*. The guard freezes
the flag instead (`device.is_low_battery = battery_low OR device.is_low_battery`
sticks `True`); only a **fresh numeric reading ≥ threshold** clears low and
emits `battery_ok`.

---

## 6. Signal lifecycle

**Opt-in** (`signal_enabled`, default **False**) and **map-only**: each
monitored entity needs an explicit signal-sensor mapping with a network type.
No mapping → `signal_level`/`signal_quality` stay `None` and no signal events
fire.

**Classifying a reading** — `_classify_signal`, per network type
(`SIGNAL_NETWORK_TYPES` supplies `good`/`ok` thresholds and a
`higher_is_better` flag). With `>=` working for both scales:

```
level >= good        → "good"
level >= ok_threshold → "ok"
otherwise             → "poor"
```

Unlike battery, signal has **no retain** — a `None`/non-numeric read this poll
makes `signal_quality` `None` for the poll, never a stale value.

**Hysteresis (3) at the poor↔ok boundary only.** When the previous quality was
`"poor"`, `ok_threshold` is nudged by `SIGNAL_HYSTERESIS = 3` so a reading
hovering at the edge doesn't flip poor/ok each poll. The good/ok boundary is
**not** de-jittered — it's display-only and feeds no recorded count. **"poor" is
the only tier that feeds a recorded count.**

**Edge events** — tracked via `device.prev_signal_poor`, with
`signal_poor = (signal_quality == "poor")`:

- `poor_signal` fires on the poor edge (`signal_poor AND NOT was_poor`).
- `signal_ok` fires on the ok edge (`NOT signal_poor AND was_poor`).

Only **poor ↔ non-poor** transitions fire; moving between "ok" and "good" fires
nothing.

Network types (and good/ok thresholds): 5G (−80/−100), Bluetooth (−70/−85),
Generic/RSSI (−60/−80), LoRaWAN (−100/−115), LTE/4G (−80/−90), Percentage
(70/40, higher-is-better), Thread (−70/−85), Wi-Fi (−67/−80), Zigbee LQI
(201/51, higher-is-better), Zigbee RSSI (−70/−85), Z-Wave (−70/−85). Default is
Generic.

---

## 7. Degraded vs offline

```
device.is_degraded = (NOT device.is_offline) AND (battery_low OR is_stale)
```

An entity is **degraded** only while it is **not** offline — offline supersedes
degraded. A low-battery or stale entity that then goes offline is reported as
offline, not degraded. (Poor signal is tracked on its own axis and is not part
of `is_degraded`.)

---

## 8. Combined groups

A **combined group** aggregates several individual groups into one entry. It
fires **only four of the eight events** — **offline, recovered, low_battery,
battery_ok**. Stale and signal transitions are surfaced **only** by the
per-group coordinators, never by a combined group.

- Fired once **per combined entry** (not per sensor) by the
  `CombinedGroupSummarySensor` (`{entry_id}_combined_summary`) in its update
  callback.
- The payload `entry_id` is the **combined entry's own** entry_id, and `group`
  is the combined entry's name.
- **Diff-based**, not edge-per-entity: each tick the sensor diffs the current
  offline / low-battery sets against the previous tick — `current − prev` fires
  `offline`, `prev − current` fires `recovered`, and the battery sets likewise.
- The payload `entity_id` is the **collapse representative's bare entity_id**
  string (never the internal `row_key`). Collapse applies when the owning group
  has both *use device names* and *collapse devices* on.

**Extra payload key.** Combined payloads carry **`source_groups`** — the sorted
list of owning individual-group names for that entity — which the per-group
events do **not** have. Keys verbatim:

- **offline**: `entity_id`, `group`, `entry_id`, `offline_since` (ISO or `null`),
  `offline_count`, `offline_entities`, `source_groups`.
- **recovered**: …, `downtime_seconds` (or `null`), `offline_count`,
  `offline_entities`, `source_groups`.
- **low_battery** / **battery_ok**: …, `battery_level` (or `null`),
  `low_battery_count`, `low_battery_entities`, `source_groups`.

`offline_since` / `downtime_seconds` / `battery_level` may be `null` if the
coordinator has not yet recorded the underlying value — a combined event still
fires with the field null rather than being dropped. Guard before
`as_datetime()`.

**Double-fire.** A combined group fires its own events *and* each constituent
individual group fires its own. To react only to the combined view, scope on the
combined entry's `entry_id` (the named-trigger **Group** picker, or a
`config_entry_id` on the trigger) or on the combined group name.

**Stale and signal triggers cannot be scoped to a combined group.** A combined
group fires only `offline`, `recovered`, `low_battery`, and `battery_ok`. The
`stale`, `stale_recovered`, `poor_signal`, and `signal_ok` events are emitted
exclusively by the per-group coordinators and always carry an *individual*
group's `entry_id`. A named trigger for one of those four events scoped to a
*combined* entry's `config_entry_id` therefore never matches and never fires —
scope stale/signal triggers to the underlying individual group instead.
