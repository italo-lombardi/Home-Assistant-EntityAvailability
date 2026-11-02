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

Each poll, every axis is re-checked. An event fires only when an axis
**changes state** — the moment it flips, not on every poll it stays there.

Every event says which group it came from, in an `entry_id` field — a unique
ID HA gives each group that never changes, even if the group is renamed. An
automation can match on it to react to just one group; in the editor, the
named trigger's **Group** dropdown fills it in for you.

**One poll can fire more than one event.** The axes are independent, so a
single entity can trip several at once. Most often going offline fires just
`offline`. But because an offline entity stops sending readings, any axis that
was *already* flagged sees "no data" as a recovery: an entity that was stale
fires `stale_recovered`, and one on poor signal fires `signal_ok`, in the same
poll it goes offline. Battery is the exception — its last-known level is
retained, so it stays quiet rather than falsely recovering.

---

## 2. The eight events

All **per-group** payloads carry `entity_id` (HA's address for the affected
entity, e.g. `binary_sensor.front_door`), `group` (the group's display
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

**Going stale, and recovering.** An entity goes **stale** (fires `stale`) once
its data age passes the threshold. To stop the flag flickering when the age
sits right at the threshold, it does not clear the instant the age dips back
under — it clears (fires `stale_recovered`) only once the age drops a little
below, by up to one minute (less if the threshold itself is under two minutes).
That small gap also keeps a just-recovered entity from being re-flagged
immediately.

`stale_since` on both events is when the data went stale, as an ISO timestamp.

---

## 5. Battery lifecycle

Battery monitoring is **on by default** — `battery_threshold` is **20 %** (set
it to 0 to switch battery off entirely).

**Where the level is read from.** Every entity you track gets a battery-mapping
row when you set the group up, so in practice the mapping is what's read:

1. **The mapping you set** (`battery_entity_map`) — always wins, even when the
   entity could report its own battery. A row pointing at a sensor reads that
   sensor (an on/off sensor reads `on` as empty (0 %) and `off` as full (100 %);
   any other uses its numeric reading). Map an entity to **itself** to read its
   own level. A row left **empty** means "this one has no battery" and skips it.

If an entity somehow has no mapping row at all — only an imported or hand-edited
config can reach this, never the normal UI — the integration falls back, in
order, to the first that yields a value:

2. **The entity itself**, if it's a battery entity (device class `battery`) and
   currently reachable.
3. **Auto-detected** from the entity's own attributes — a `battery_level` or
   `battery` attribute (a real 0 % counts; only a genuinely absent attribute is
   skipped).
4. **Home Assistant's device record** for the entity.
5. **A guessed companion** named `sensor.<entity>_battery`.

A text reading of `"low"` counts as 0 %; any other text is read as a number.

**Auto-filled suggestions in the UI.** The mapping screen suggests a battery
**sensor to map** — a narrower search than the runtime read above, because a
suggestion has to be an actual entity you could point the row at. It looks for
two things only: a battery sibling on the same device, then a
`sensor.<entity>_battery` companion. The own-state (device class `battery`) and
own-attribute sources have **no separate sensor** to pre-fill — so auto-fill
cannot suggest them, but you can leave the box **empty** and the runtime read
still finds them (steps 2–3 above).

When you **add** a group, each entity's box is pre-filled with that guess. When
you **edit** a group, your saved choices are restored as-is — a box you left
empty stays empty, a sensor you picked stays picked; only entities you have just
added get a fresh guess. Either way these are **suggestions only** — nothing is
mapped until you save. Whatever sits in the box on save becomes the real mapping,
so clear a box to leave an entity unmapped, or correct a wrong guess before
saving. A guess you keep is your mapping; it's your responsibility to confirm it.

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
