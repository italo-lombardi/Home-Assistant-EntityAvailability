# Integration smoke tests

Live tests against a running Home Assistant devcontainer. Run after deploying changes.

Replace `<container>` with your container name throughout.

## Quick run

```bash
# 1. Deploy integration to container
docker cp custom_components/entity_availability/. \
  <container>:/workspaces/home-assistant-core/config/custom_components/entity_availability/

# 2. Run smoke tests from the host
EA_SMOKE_TOKEN=<access_token> python3 tests/integration/smoke.py
```

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
