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
cannot suggest them. For those, map the entity to **itself** (step 1 then reads
its own state/attributes): leaving the box empty does **not** fall back to
steps 2–3, because the UI writes a row for every tracked entity and an empty row
means "no battery" (step 1 returns skip). The runtime fallback chain (steps 2–5)
is reached only by an entity with **no row at all** — an imported or hand-edited
config, never the normal UI.

When you **add** a group, each entity's box is pre-filled with that guess when
one is found (a battery sibling or `sensor.<entity>_battery` companion); when
neither exists the guess is empty and the box starts blank. When
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

**Hysteresis (3 %).** A battery hovering right at the threshold would otherwise
flip the flag on and off every poll as its reading jitters (20 → 19 → 21 → 20…).
To stop that, the flag uses **two different cutoffs** depending on the state it's
already in — a 3 % dead-band between them:

- **Not flagged yet** → goes low the moment the level drops **below** the
  threshold (`level < threshold`).
- **Already flagged** → stays low until the level climbs back to **threshold +
  `BATTERY_HYSTERESIS` (3 %)** or higher. A reading anywhere in the band
  (threshold up to threshold + 3) keeps the existing low flag.

So with the default 20 % threshold: the flag trips at 19 %, but the battery must
reach **23 %** to clear it. A reading bouncing around 20–22 % won't flip it.

```
battery_low = threshold > 0 AND battery_level is not None AND battery_level < threshold
# stay low while already low and still within the band:
if device.is_low_battery AND battery_level is not None
   AND battery_level < threshold + 3:  battery_low = True
```

| level | was low? | now low? | why |
|---|---|---|---|
| 19 | no | **yes** | 19 < 20 (trips) |
| 21 | yes | **yes** | 21 < 23, in band → stays low |
| 23 | yes | **no** | 23 ≥ 23 → clears |
| 21 | no | no | 21 not < 20 → stays clear |

The `battery_level is not None` guard on both lines skips the comparison when
there's no reading (a dead or unknown battery reads `None`, not a number).

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

Network types and their `good` / `ok` thresholds (from
`SIGNAL_NETWORK_TYPES` in `const.py`). `higher_is_better` is `False` for dBm
scales (a reading `>= good` is green) and `True` for LQI / percentage scales:

| Network type | Unit | good | ok | higher_is_better |
|---|---|---|---|---|
| 5G | dBm | −80 | −100 | no |
| Bluetooth | dBm | −70 | −85 | no |
| Generic/RSSI | dBm | −60 | −80 | no |
| LoRaWAN | dBm | −100 | −115 | no |
| LTE/4G | dBm | −80 | −90 | no |
| Percentage | % | 70 | 40 | **yes** |
| Thread | dBm | −70 | −85 | no |
| Wi-Fi | dBm | −67 | −80 | no |
| Zigbee (LQI) | LQI | 201 | 51 | **yes** |
| Zigbee (RSSI/dBm) | dBm | −70 | −85 | no |
| Z-Wave | dBm | −70 | −85 | no |

Default is **Generic/RSSI**.

---

## 7. Degraded vs offline

```
device.is_degraded = (NOT device.is_offline) AND (battery_low OR is_stale)
```

An entity is **degraded** only while it is **not** offline — offline supersedes
degraded. A low-battery or stale entity that then goes offline is reported as
offline, not degraded.

Poor signal is **not** part of `is_degraded`, even though it too is an opt-in
axis (so is stale — opt-in is not the reason). `is_degraded` captures the
entity's **own** health: its power dying (`battery_low`) or its data frozen
(`is_stale`). Poor signal is a property of the **link** between Home Assistant
and the device, not the device's own condition — a healthy sensor behind a weak
repeater still reports fine. It's surfaced on its own axis (`poor_signal` /
`signal_ok`) instead.

---

## 8. Device names & collapse

Two per-group display settings change how a group's entities are grouped into
**rows** — the unit a sensor counts and lists.

- **Use device names** (off by default). Off: each row is labelled by the
  entity's own name. On: rows are labelled by the entity's **device** name
  (from HA's device registry). Pure labelling — it does not merge anything on
  its own.
- **Collapse devices** (off by default; requires *use device names* on — it is
  a no-op without it). On: entities that belong to the **same device** fold into
  **one row**. A door sensor and its battery sensor on one physical device show
  as a single row, counted once, instead of two.

**What "same device" means.** Collapse buckets entities by device, then merges
within a bucket by **source compatibility**: two entities share a row when their
(battery sensor, signal sensor) pairs do not conflict — equal on an axis, or one
side unset (unset = "don't care", merges). Two **distinct** concrete sensors on
the same axis (e.g. two different battery sensors on one device) stay separate
rows. A live reading drifting (−70 → −71 dBm) never reshuffles rows — only the
mapped *sensor*, not its value, decides identity.

**Counts follow rows.** Each axis count (offline, stale, low_battery,
poor_signal) counts rows, so with collapse on, one physical device with a low
battery is **one** low_battery — not one per entity on it.

---

## 9. Combined groups

A **combined group** aggregates several individual groups into one entry. It
fires **all eight events** — the same set an individual group fires (offline,
recovered, low_battery, battery_ok, stale, stale_recovered, poor_signal,
signal_ok). An automation written for an individual group works **identically**
on a combined group; only the `entry_id` it matches on differs.

- **Relayed, not re-derived.** The combined group does not re-compute edges. It
  listens on the bus for the eight events, and whenever a **member** individual
  group fires one, it re-fires that same event under the **combined entry's own**
  `entry_id` (and `group` name). The relay runs in the
  `CombinedGroupSensor` (`{entry_id}_combined_summary`), off the sensor's
  state path — it fires **iff** a member already fired, adding no event volume.
- **Per-entity fields pass straight through.** `offline_since`,
  `downtime_seconds`, `battery_level`, `stale_since`, `signal_level`,
  `signal_quality` — whatever the member coordinator stamped on the child event
  is copied verbatim onto the relayed event. No rollup, no loss.
- **Fleet-aggregate fields are restamped for the whole combined fleet.** The
  firing category's `{cat}_count` / `{cat}_entities` are recomputed across **all**
  member groups at fire time — **device-collapsed, non-suppressed, essential
  only** (the same filter the combined counts use). So `offline_count` on a
  relayed offline event is the combined fleet's offline count, not the member
  group's.
- The payload `entity_id` is the **collapse representative's bare entity_id**
  string (never the internal `row_key`). Collapse applies when the owning group
  has both *use device names* and *collapse devices* on.

**Extra payload key.** Combined payloads carry **`source_groups`** — the sorted
list of owning individual-group names for that entity — which the per-group
events do **not** have. Beyond `entity_id` / `group` / `entry_id` /
`source_groups`, each relayed event carries exactly the per-entity and
`{cat}_count` / `{cat}_entities` fields its individual-group counterpart carries
(see the §2 table).

`offline_since` / `downtime_seconds` / `battery_level` / `stale_since` /
`signal_level` may be `null` if the member coordinator did not record the
underlying value — a combined event still fires with the field null rather than
being dropped. Guard before `as_datetime()`.

**Double-fire.** A combined group fires its own events *and* each constituent
individual group fires its own. To react only to the combined view, scope on the
combined entry's `entry_id` (the named-trigger **Group** picker, or a
`config_entry_id` on the trigger) or on the combined group name.

**Collapse across member groups.** A combined group has no collapse setting of
its own — it inherits each member group's. A device folds to **one combined
row** only when **every** member group that monitors it has collapse on *and* their battery/signal mappings for it are compatible. If any owning member
group has collapse off, that group's view of the device stays its **own row**,
and the device is counted once per surviving row.

So the same physical device monitored by two member groups appears as:

- **both groups collapse on, compatible mappings** → one row, counted once;
- **both on but mappings conflict** (e.g. two different battery sensors) → one
  row per distinct mapping;
- **either group collapse off** → that group keeps a separate row — a device in
  an Alpha (collapse on) and a Beta (collapse off) is two rows, so e.g. one low
  battery on it counts **2** in the combined summary.

The combined count mirrors each member group's own collapse choice rather than
imposing one — a member group that opted out of collapse shows the same
un-collapsed rows inside combined that it shows on its own summary.

**Compatible mappings.** Two member groups' mappings for one device are
*compatible* when, on each axis (battery sensor, signal sensor) independently,
they name the **same** sensor **or** at least one leaves that axis **unset**.
An unset axis is a wildcard — it merges with anything, so a group that maps only
the battery and a group that maps battery + signal still fold to one row (the
merged row takes the concrete sensor from whichever group set it). They conflict
only when both name a **different concrete** sensor on the same axis — e.g. two
distinct battery sensors on the device — which keeps them as separate rows so
neither reading is hidden.
