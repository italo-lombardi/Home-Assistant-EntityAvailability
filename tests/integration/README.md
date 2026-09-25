# Integration smoke tests

Live tests against a running Home Assistant devcontainer. Run after deploying changes.

Replace `<container>` with your container name throughout.

## Quick run

```bash
# 1. Deploy integration to container
docker cp custom_components/entity_availability/. \
  <container>:/workspaces/home-assistant-core/config/custom_components/entity_availability/

# 2. Install optional smoke dependencies (enables EC24 WebSocket assertions)
pip install websocket-client

# 3. Run smoke tests from the host
EA_SMOKE_TOKEN=<access_token> python3 tests/integration/smoke.py

# Fast mode (45 s timeouts instead of 60 s — for warm CI environments):
EA_SMOKE_TOKEN=<access_token> python3 tests/integration/smoke.py --fast

# Target a single EC without running the full preamble (assumes clean HA state):
EA_SMOKE_TOKEN=<access_token> EA_SMOKE_EC=24 python3 tests/integration/smoke.py --skip-setup
```

## Run the whole suite (`--all`)

`--all` runs every EC green in one command by routing each EC family to a group
that can actually satisfy its assertions — no single group has an
essential-mapped battery **and** non-essential entities **and** a collapse
layout, so the suite is split into capability-matched passes. Targets are
auto-discovered from the live state machine + config (`.storage`), so no group
names are hardcoded — it works in any environment that has the needed group
shapes.

```bash
# Requires --skip-setup (per-group battery setup is not looped).
EA_SMOKE_TOKEN=<access_token> python3 tests/integration/smoke.py --all --skip-setup
```

Passes (each restricted to its family via an internal EC filter):

| Pass | Target group capability | ECs |
|------|-------------------------|-----|
| core+battery-count+signal+combined | essential mapped battery + signal + combined | 1–24, 43–85, 90 |
| NE-tier + EC36 | both tiers (essential **and** non-essential) | 25–42 |
| collapse-fixture | collapse-active (device-collapse merging rows) | 86–89 |

Capability predicates (auto-detected per group):
- **essential_mapped_battery** — a `battery_entity_map` key with a non-empty
  value whose key is **not** non-essential. Required by the low-battery *count*
  family (the count is essential-only); a group whose only mapped battery is
  non-essential will spuriously fail EC4/22/23/65 and must not be its target.
- **both_tiers** — has ≥1 non-essential **and** ≥1 essential entity. Required by
  EC36 (diagnostics asserts both counts ≥ 1) and by the recently_* ECs (EC41/42
  need an essential target; they skip loud otherwise).
- **collapse** — device-collapse produces fewer collapsed rows than raw entities.

**Fail-loud:** any family with no satisfying group prints a `DARK` line before
and after the run, so a green aggregate can never hide uncovered coverage. ECs
that need a precondition the environment can't provide (a second group sharing
an entity for EC12, an identical-fingerprint entity across groups for EC82, a
`websocket-client` install for the EC20/21/24/31 WS assertions) skip loud rather
than fail.


## Get a token

```bash
# Find a long-lived access token for your HA user:
docker exec <container> python3 -c "
import json
auth = json.load(open('/workspaces/home-assistant-core/config/.storage/auth'))
for t in auth['data']['refresh_tokens']:
    if t.get('token_type') == 'long_lived_access_token':
        print(t['client_name'], t['token'])
"
# Exchange the refresh token for a short-lived access token:
curl -s -X POST http://localhost:8123/auth/token \
  -d 'grant_type=refresh_token&refresh_token=<refresh_token>' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
```

## Optional dependency

EC24 verifies the `source_groups` field in combined group events via HA's WebSocket API. It requires `websocket-client`:

```bash
pip install websocket-client
```

Without it, EC24 skips with a printed notice. All other ECs use plain HTTP REST and have no additional dependencies. The `source_groups` field itself is covered by unit tests (see `tests/test_combined_sensor.py`) regardless of whether EC24 runs.

## What is covered

| EC | Scenario | PRs |
|----|----------|-----|
| EC1 | Entity unavailable → offline_count increments | core |
| EC4 | Online + battery=5% → low_battery_count=1, list populated | #41 |
| EC5 | Device+battery offline → offline=1, low_battery=0 (no double-count) | #41 |
| EC6 | Battery replaced (90%), recovery → all clear | #41 |
| EC7 | Combined: online+low_battery counted once | #41 |
| EC8 | Combined: offline+low → low_battery drops, offline rises | #41 |
| EC9 | Cleared battery map not re-suggested in options flow | #37 |
| EC10 | Suppressed entity not counted in offline | core |
| EC11 | suppress_indefinitely + unsuppress round-trip; suppressed_until=null for indefinite | #42 |
