# Next Steps

## Open gaps from PR #53

### Smoke test event payload coverage (EC19-21)

EC19-21 verify sensor state parity (offline_count sensor, offline_entities attribute) — they do not verify the actual event bus payload fields (`entity_id`, `entry_id`, `offline_since`, `downtime_seconds`).

This is a structural limitation: the smoke framework uses HTTP REST, which has no event subscription endpoint. Workarounds:

**Option A — Websocket listener**
Add a websocket client to `smoke.py` that subscribes to `entity_availability_offline` / `entity_availability_recovered` for the duration of a test step, then asserts on captured payload fields. HA's websocket API supports event subscriptions (`subscribe_events`). Requires adding `websockets` or raw `ws://` handling to the smoke script.

**Option B — Pre-planted helper automation**
Create a persistent HA automation (in the devcontainer config) that writes event payload fields to `input_text` helpers on each event. Smoke reads the helper state after triggering the entity transition. No websocket needed, but requires devcontainer setup changes.

**Option C — Accept the gap**
Unit tests cover the payload shape (`test_combined_event_payload_matches_individual_group_shape`, `test_fires_offline_event_when_entity_goes_offline`, etc.). Document the smoke gap and leave EC19-21 as sensor-state tests only.

Recommended: Option A — websocket listener is self-contained in smoke.py and doesn't require devcontainer config changes.

---

## EC4 / EC15 smoke flakiness

EC4 intermittently fails because a prior test leaves an entity in a bad state despite `restore_all`. EC15 flaps due to availability % rounding drift between `wait()` calls.

Fixes:
- EC4: add explicit `restore_all(ctx); wait()` at the top of the EC4 block
- EC15: replace single-read comparison with `wait_for` polling, or widen tolerance to ±0.5%

---

## Low-battery events (item 4 from PR #53 discussion)

✅ Shipped in PR #54: `entity_availability_low_battery` and `entity_availability_battery_ok` events with full parity payload.

---

## Smoke test speed and reliability (observed PR #54 deploy)

The full smoke suite exceeds 5 min wall-clock, causing timeouts when run via `docker exec` without a long enough shell timeout. EC22/EC23 (new battery event tests) were never reached in live runs due to the overall suite length. Root causes and fixes:

### 1. EC_FILTER doesn't skip early mandatory tests (EC1–EC8)

EC1–EC8 run unconditionally even when `EA_SMOKE_EC` is set — they are the state-setup chain. When running targeted checks (e.g. EC22/EC23 only), the full preamble still executes.

**Fix:** Extract a `setup()` phase that runs once regardless of filter, skip only the assertion blocks. Or add a `--skip-setup` flag that assumes clean state and jumps straight to the targeted EC.

### 2. `wait_for` timeout of 90 s per check compounds

Multiple `wait_for(..., timeout=90)` calls in sequence can burn 9+ minutes if HA is slow. The coordinator polls every 30 s — 90 s is 3 ticks, reasonable, but many checks in series multiply.

**Fix:** Reduce default `wait_for` timeout to 60 s (2 ticks). Add a global `--fast` flag that sets timeout=45 s for CI environments where HA is warm and state changes quickly.

### 3. No inter-test cleanup isolation

`restore_all` sets entity states back to `on` but doesn't wait for the coordinator to confirm `offline_count=0` before proceeding. Next test starts while the coordinator is still processing the previous transition.

**Fix:** After `restore_all`, poll `offline_count=0 AND low_battery_count=0` before continuing — same pattern as individual wait_for checks. Wrap into a `restore_and_wait(ctx)` helper replacing all `restore_all(ctx); wait()` pairs.

### 4. EC22/EC23 use sensor proxy instead of event subscription

EC22/EC23 verify the new battery events via `low_battery_count` sensor transition (proxy), not direct event capture. This works but adds a coordinator poll cycle of latency.

**Fix (long-term):** Implement Option A (websocket listener from EC19-21 gap above) — subscribe to `entity_availability_low_battery` / `entity_availability_battery_ok` directly and assert payload fields. Would also close the EC19-21 payload gap simultaneously.

