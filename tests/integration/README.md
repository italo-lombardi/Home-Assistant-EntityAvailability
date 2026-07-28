# Integration smoke tests

Live tests against the `serene_booth` devcontainer. Run after deploying changes.

## Quick run

```bash
# 1. Deploy integration to container
docker cp custom_components/entity_availability/. \
  serene_booth:/workspaces/home-assistant-core/config/custom_components/entity_availability/

# 2. Copy and run smoke tests
docker cp tests/integration/smoke.py serene_booth:/tmp/smoke.py
docker exec -e EA_SMOKE_TOKEN=<token> serene_booth \
  /home/vscode/.local/ha-venv/bin/python3 /tmp/smoke.py
```

## Get a token

```bash
docker exec serene_booth python3 -c "
import json
auth = json.load(open('/workspaces/home-assistant-core/config/.storage/auth'))
rt = next(t for t in auth['data']['refresh_tokens']
          if t.get('user_id','').startswith('d67fce1b') and t.get('token_type')=='long_lived_access_token')
print(rt['token'])
"
# Then exchange for access token:
docker exec serene_booth sh -c "curl -s -X POST http://localhost:8123/auth/token \
  -d 'grant_type=refresh_token&refresh_token=<refresh_token>' | python3 -c \
  'import json,sys; print(json.load(sys.stdin)[\"access_token\"])'"
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
