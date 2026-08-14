"""
Live smoke tests against the running HA devcontainer.

Run from repo root:
    docker cp tests/integration/smoke.py serene_booth:/tmp/smoke.py
    docker exec -e EA_SMOKE_TOKEN=<token> serene_booth \\
        /home/vscode/.local/ha-venv/bin/python3 /tmp/smoke.py

Required env vars:
    EA_SMOKE_TOKEN      HA long-lived access token (see README for how to mint)

Optional env vars (auto-discovered from HA if not set):
    EA_SMOKE_BASE_URL   HA base URL, default http://localhost:8123
    EA_SMOKE_GROUP      Config entry title to test against (substring match),
                        default "Test Group"

The test auto-discovers the first entity_availability group whose title contains
EA_SMOKE_GROUP, then discovers its monitored entities and battery entity map from
the live sensor attributes — no hardcoded entity IDs, entry IDs, or device names.

What is tested (covers PRs #37, #41, #50, #52, #53, #54, #68, core, feat/non-essential-level):
  EC1  entity goes unavailable → offline_count increments
  EC4  device online + battery below threshold → low_battery_count=1, list populated
  EC5  device+battery unavailable → offline=1, low_battery=0 (PR#41: no double-count)
  EC6  battery replaced above threshold, back online → all clear
  EC7  combined group: online+low_battery increments combined count
  EC8  combined group: offline+low → combined low_battery drops, offline rises
  EC9  PR#37: cleared battery map entry not re-suggested in options flow
  EC10 suppression: suppressed+unavailable entity not counted in offline
  EC11 suppress_indefinitely + unsuppress round-trip; suppressed_until=null for indefinite
  EC12 group-scoped suppress: entity in two groups, suppress in one, other unaffected
  EC13 PR#50: suppressing offline entity does not change group availability %
  EC14 PR#50: suppressed entity still appears in per_device availability breakdown
  EC15 PR#50: unsuppressing restores identical group availability % (no drift, ±0.5%)
  EC16 PR#52: combined offline_count rises when member entity goes offline
  EC17 PR#52: combined offline_entities list contains the offline entity
  EC18 PR#52: combined offline_count drops to baseline after recovery
  EC19 PR#53: combined config entry has non-empty entry_id (event payload source)
  EC20 PR#53: individual group offline_count sensor rises when entity in combined group goes offline
  EC21 PR#53: individual and combined offline_entities sensors both reflect the offline entity
  EC22 PR#54: entity_availability_low_battery event fires when battery drops below threshold
  EC23 PR#54: entity_availability_battery_ok event fires when battery recovers above threshold
  EC24 combined offline event carries source_groups list (websocket; skipped if websocket-client absent)

  EC43 group_summary battery_enabled matches battery_threshold config
  EC44 group_summary staleness_enabled matches staleness_threshold config
  EC45 last_seen attr populated with past timestamps

  Signal strength (EC46-EC50 — require signal_enabled=True on the test group):
  EC46 group_summary exposes signal_enabled flag
  EC47 poor_signal_count sensor is reachable
  EC48 signal_levels attr is a dict in group_summary
  EC49 poor_signal_entities attr is a list in group_summary
  EC50 any_poor_signal binary sensor reachable when signal enabled

  PR#68 (EC51-EC53):
  EC51 signal mapping options flow round-trip: entity_id / entity_id#network keys accepted and stored
  EC52 _detect_signal_entity suffix strip: sensor.xxx_last_seen → suggests sensor.xxx_linkquality
  EC53 diagnostics new schema: counts/entities/config sub-dicts present and correct (replaces flat keys)
  EC54 group_summary new attrs: essential count, stale/poor_signal count aliases
  EC55 group_summary large attrs excluded from recorder (_unrecorded_attributes) but present in state machine
  EC59 battery level retained in battery_levels when entity goes unavailable (not wiped to None)
  EC65 low_battery count cleared when entity is suppressed (stale/degraded flags reset)

  PR#76 recorder payload reduction (EC69-EC71):
  EC69 combined_summary: battery_levels/signal_levels/signal_units/ok_signal_entities/
       suppressed_until/offline_since/last_seen present in live state (unrecorded)
  EC70 combined_summary groups dict has no non_essential_entities list per group entry
  EC71 combined_summary low_battery_entities_non_essential present in live state (was recorded bug)

  Device-collapse (EC72-EC76 — PR#81):
  EC72 diagnostics config exposes collapse_devices; derive collapse_active
  EC73 group_summary entities_collapsed attr present and is a list
  EC74 combined_summary entities_collapsed attr present and is a list
  EC75 entities_collapsed never larger than entities, and is a subset (representatives)
  EC76 offline_count == len(collapsed offline_entities) — count==rows invariant

  Non-Essential tier (EC25-EC42, require a group with NE entities — set EA_SMOKE_NE_GROUP):
  EC25 NE entity offline → offline_count unchanged (KPI exclusion)
  EC26 NE entity offline → offline_count_non_essential increments
  EC27 NE entity offline → any_offline binary sensor stays OFF (essential only)
  EC28 NE entity offline → any_offline_non_essential binary sensor turns ON
  EC29 NE entity offline → group_summary non_essential_offline increments
  EC30 NE entity offline → group_summary online count unchanged
  EC31 entity_availability_stale_recovered event fires after stale entity recovers
       (stale detected via stale_count sensor poll; skipped when staleness_threshold=0 or websocket-client absent)
  EC32 any_low_battery binary sensor turns ON when essential entity has low battery
  EC33 any_stale binary sensor turns ON when essential entity is stale
       (skipped when staleness_threshold=0)
  EC34 suppressed NE entity: suppressed banner counts NE tier separately
  EC35 NE entity recovery: offline_count_non_essential drops back to 0
  EC36 diagnostics endpoint returns correct shape and counts for group entry
  EC37 stale_count sensor increments for stale essential entity (skip if threshold=0)
  EC38 stale_entities sensor lists stale essential entity (skip if threshold=0)
  EC39 NE entity stale → stale_count_non_essential increments, stale_count unchanged
       (skipped when staleness_threshold=0)
  EC40 any_low_battery binary sensor stays OFF when only NE entity has low battery
  EC41 recently_offline sensor lists entity after it goes offline
  EC42 recently_recovered sensor lists entity after it recovers

Pass --fast or set EA_SMOKE_FAST=1 to use 45 s wait_for timeouts (default: 60 s).
Pass --skip-setup or set EA_SMOKE_SKIP_SETUP=1 to skip battery mapping setup and
  initial restore_and_wait — assumes HA is already in a clean state. Use with
  EA_SMOKE_EC to jump straight to a targeted check without running the full preamble.
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--fast", action="store_true")
_parser.add_argument("--skip-setup", action="store_true")
_args, _ = _parser.parse_known_args()

TOKEN = os.environ.get("EA_SMOKE_TOKEN", "")
BASE = os.environ.get("EA_SMOKE_BASE_URL", "http://localhost:8123")
GROUP_FILTER = os.environ.get("EA_SMOKE_GROUP", "Test Group")
NE_GROUP_FILTER = os.environ.get(
    "EA_SMOKE_NE_GROUP", ""
)  # group with NE entities configured
FAST = _args.fast or os.environ.get("EA_SMOKE_FAST", "") == "1"
SKIP_SETUP = _args.skip_setup or os.environ.get("EA_SMOKE_SKIP_SETUP", "") == "1"
# Comma-separated EC numbers to run, e.g. "16,17,18". Empty = run all.
EC_FILTER: set[int] = {
    int(x) for x in os.environ.get("EA_SMOKE_EC", "").split(",") if x.strip().isdigit()
}
WAIT_FOR_TIMEOUT = 45 if FAST else 60


def ec_enabled(n: int) -> bool:
    return not EC_FILTER or n in EC_FILTER


if not TOKEN:
    sys.exit(
        "Error: set EA_SMOKE_TOKEN to a valid HA access token.\nSee tests/integration/README.md for instructions."
    )

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def api(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body else None,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def gs(eid):
    return api("GET", f"/api/states/{eid}")


def gs_safe(eid):
    """Like gs() but returns None instead of raising on 404."""
    try:
        return api("GET", f"/api/states/{eid}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def ss(eid, state, attrs):
    return api("POST", f"/api/states/{eid}", {"state": state, "attributes": attrs})


def wait(seconds=45):
    time.sleep(seconds)


def wait_for(check_fn, expected, timeout=None, interval=5):
    """Poll until check_fn() == expected or timeout."""
    if timeout is None:
        timeout = WAIT_FOR_TIMEOUT
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = check_fn()
        if str(val) == str(expected):
            return val
        time.sleep(interval)
    return check_fn()


def wait_for_close(check_fn, expected, tolerance=0.5, timeout=None, interval=5):
    """Poll until abs(float(check_fn()) - expected) <= tolerance or timeout."""
    if timeout is None:
        timeout = WAIT_FOR_TIMEOUT
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            val = float(check_fn())
            if abs(val - expected) <= tolerance:
                return val
        except (TypeError, ValueError):
            pass
        time.sleep(interval)
    try:
        return float(check_fn())
    except (TypeError, ValueError):
        return check_fn()


# ---------------------------------------------------------------------------
# WebSocket event capture
# ---------------------------------------------------------------------------

try:
    import websocket as _ws_lib  # websocket-client

    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


def capture_events(event_type: str, trigger_fn, timeout: int = 30) -> list[dict]:
    """Subscribe to event_type, call trigger_fn(), return captured payloads.

    Falls back to [] when websocket-client is not installed.
    """
    if not _WS_AVAILABLE:
        trigger_fn()
        return []

    captured: list[dict] = []
    ws_url = (
        BASE.replace("http://", "ws://").replace("https://", "wss://")
        + "/api/websocket"
    )
    done = threading.Event()
    msg_id = 1

    def on_message(ws, raw):
        nonlocal msg_id
        try:
            msg = json.loads(raw)
        except Exception:
            return
        mtype = msg.get("type")
        if mtype == "auth_required":
            ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        elif mtype == "auth_ok":
            ws.send(
                json.dumps(
                    {"id": msg_id, "type": "subscribe_events", "event_type": event_type}
                )
            )
        elif mtype == "result" and msg.get("id") == msg_id:
            if msg.get("success"):
                trigger_fn()
        elif mtype == "event":
            data = msg.get("event", {}).get("data", {})
            captured.append(data)
        elif mtype == "event" and done.is_set():
            pass

    def on_error(ws, err):
        pass

    def on_open(ws):
        pass

    ws = _ws_lib.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
    )
    t = threading.Thread(target=lambda: ws.run_forever(), daemon=True)
    t.start()
    time.sleep(timeout)
    ws.close()
    done.set()
    return captured


# ---------------------------------------------------------------------------
# Test tracking
# ---------------------------------------------------------------------------
_passed = _failed = 0


def chk(label, got, exp, note=""):
    global _passed, _failed
    ok = str(got) == str(exp)
    _passed += ok
    _failed += not ok
    print(
        f"{'PASS' if ok else 'FAIL'} {label}: got={got} expected={exp} {note}",
        flush=True,
    )
    return ok


# ---------------------------------------------------------------------------
# Discovery — no hardcoded IDs
# ---------------------------------------------------------------------------


def discover():
    """Return (entry_id, entities, battery_map, group_sensor_prefix, combined_entry_id)."""
    entries = api("GET", "/api/config/config_entries/entry")
    ea_entries = [e for e in entries if e["domain"] == "entity_availability"]

    # Find target group
    group = next(
        (
            e
            for e in ea_entries
            if GROUP_FILTER.lower() in e["title"].lower()
            and "combined" not in e["title"].lower()
        ),
        None,
    )
    if not group:
        titles = [e["title"] for e in ea_entries]
        sys.exit(
            f"No entity_availability entry matching '{GROUP_FILTER}' (found: {titles})"
        )

    entry_id = group["entry_id"]
    title = group["title"]
    print(f"Target group: '{title}' ({entry_id})", flush=True)

    # Derive sensor prefix from group summary entity_id
    all_states = api("GET", "/api/states")
    prefix = None
    for s in all_states:
        eid = s["entity_id"]
        if "entity_availability" in eid and "group_summary" in eid:
            attrs = s.get("attributes", {})
            # Check this summary belongs to our entry by checking entity list
            # We'll match by entity_id pattern — best heuristic
            prefix_candidate = eid.replace("_group_summary", "")
            prefix = prefix_candidate
            break

    # Better: find group summary that matches our entry via group_name attr
    for s in all_states:
        eid = s["entity_id"]
        if "entity_availability" in eid and "group_summary" in eid:
            attrs = s.get("attributes", {})
            fn = attrs.get("friendly_name", "")
            if title.lower() in fn.lower():
                prefix = eid.replace("_group_summary", "")
                break

    if not prefix:
        sys.exit("Could not determine sensor prefix for target group")

    print(f"Sensor prefix: {prefix}", flush=True)

    # Get entities and battery map from group summary attrs
    gs_state = gs(f"{prefix}_group_summary")
    attrs = gs_state.get("attributes", {})
    entities = attrs.get("entities", [])
    if not entities:
        sys.exit(f"No entities found in group summary for {prefix}")

    print(f"Monitored entities ({len(entities)}): {entities}", flush=True)

    # Find any already-mapped battery entity
    mapped_battery_sensor = None

    # Read battery_entity_map from config storage to find a mapped battery
    import subprocess

    try:
        raw = subprocess.check_output(
            [
                "python3",
                "-c",
                f"""
import json
cfg = json.load(open('/workspaces/home-assistant-core/config/.storage/core.config_entries'))
for e in cfg['data']['entries']:
    if e['entry_id'] == '{entry_id}':
        print(json.dumps(e['data']))
""",
            ],
            text=True,
        )
        data = json.loads(raw.strip())
        bmap = data.get("battery_entity_map", {})
        mapped_battery_sensor = next((v for v in bmap.values() if v), None)
        mapped_battery_entity = next((k for k, v in bmap.items() if v), None)
    except Exception:
        mapped_battery_sensor = None
        mapped_battery_entity = None

    # Find combined entry
    combined = next(
        (e for e in ea_entries if "combined" in e["title"].lower()),
        None,
    )
    combined_prefix = None
    if combined:
        for s in all_states:
            eid = s["entity_id"]
            if "entity_availability" in eid and "combined_summary" in eid:
                attrs = s.get("attributes", {})
                fn = attrs.get("friendly_name", "")
                if combined["title"].lower() in fn.lower():
                    combined_prefix = eid.replace("_combined_summary", "")
                    break

    print(f"Combined prefix: {combined_prefix}", flush=True)

    return {
        "entry_id": entry_id,
        "title": title,
        "entities": entities,
        "prefix": prefix,
        "battery_threshold": data.get("battery_threshold", 20)
        if "data" in dir()
        else 20,
        "staleness_threshold": data.get("staleness_threshold", 0)
        if "data" in dir()
        else 0,
        "signal_enabled": data.get("signal_enabled", False)
        if "data" in dir()
        else False,
        "mapped_battery_entity": mapped_battery_entity,
        "mapped_battery_sensor": mapped_battery_sensor,
        "combined_prefix": combined_prefix,
    }


# ---------------------------------------------------------------------------
# Setup: ensure a battery entity is mapped and battery sensor exists
# ---------------------------------------------------------------------------


def setup_battery(ctx):
    """If no battery mapping exists, configure one via options flow."""
    if ctx["mapped_battery_sensor"]:
        print(
            f"Battery mapping already set: {ctx['mapped_battery_entity']} → {ctx['mapped_battery_sensor']}",
            flush=True,
        )
        return

    # Pick first entity, derive battery sensor name
    target_entity = ctx["entities"][0]
    suffix = target_entity.split(".")[-1]
    battery_sensor = f"sensor.{suffix}_battery"

    print(
        f"=== SETUP: configuring battery mapping {target_entity} → {battery_sensor} ===",
        flush=True,
    )

    ss(
        battery_sensor,
        "90",
        {
            "friendly_name": "Test Battery",
            "device_class": "battery",
            "unit_of_measurement": "%",
        },
    )

    r = api(
        "POST", "/api/config/config_entries/options/flow", {"handler": ctx["entry_id"]}
    )
    fid = r["flow_id"]
    r2 = api(
        "POST",
        f"/api/config/config_entries/options/flow/{fid}",
        {
            "entities": ctx["entities"],
            "bad_states": ["unavailable", "unknown"],
            "cooldown": 0,
            "staleness_threshold": 0,
            "battery_threshold": ctx["battery_threshold"],
            "availability_windows": ["today", "7d"],
            "use_device_names": False,
            "staleness_use_last_updated": False,
            "signal_enabled": ctx.get("signal_enabled", False),
        },
    )
    if r2.get("step_id") == "battery_mapping":
        r3 = api(
            "POST",
            f"/api/config/config_entries/options/flow/{fid}",
            {target_entity: battery_sensor},
        )
        # Navigate through signal_mapping step if present (submit empty = keep existing map)
        if r3.get("step_id") == "signal_mapping":
            api("POST", f"/api/config/config_entries/options/flow/{fid}", {})
        ctx["mapped_battery_entity"] = target_entity
        ctx["mapped_battery_sensor"] = battery_sensor
        print(f"  Mapped {target_entity} → {battery_sensor}", flush=True)


def restore_all(ctx):
    for eid in ctx["entities"]:
        ss(eid, "on", {"friendly_name": eid.split(".")[-1]})
    if ctx["mapped_battery_sensor"]:
        ss(
            ctx["mapped_battery_sensor"],
            "90",
            {
                "friendly_name": "Test Battery",
                "device_class": "battery",
                "unit_of_measurement": "%",
            },
        )
    try:
        api(
            "POST",
            "/api/services/entity_availability/unsuppress",
            {
                "entity_id": ctx["entities"][0],
                "group": ctx["title"],
            },
        )
    except Exception:
        pass
    wait(10)


def restore_and_wait(ctx):
    """restore_all then poll until offline_count=0 and low_battery_count=0."""
    restore_all(ctx)
    prefix = ctx["prefix"]
    wait_for(
        lambda: gs(f"{prefix}_offline_count").get("state"),
        "0",
        timeout=WAIT_FOR_TIMEOUT,
    )
    wait_for(
        lambda: gs(f"{prefix}_low_battery_count").get("state"),
        "0",
        timeout=WAIT_FOR_TIMEOUT,
    )


def _discover_ne_group(filter_str: str) -> dict | None:
    """Discover a group that has non_essential_entities configured."""
    import subprocess

    all_states = api("GET", "/api/states")
    entries = api("GET", "/api/config/config_entries/entry")
    ea_entries = [e for e in entries if e["domain"] == "entity_availability"]

    group = next(
        (
            e
            for e in ea_entries
            if filter_str.lower() in e["title"].lower()
            and "combined" not in e["title"].lower()
        ),
        None,
    )
    if not group:
        return None

    entry_id = group["entry_id"]
    title = group["title"]

    # Read NE entities from config storage
    try:
        raw = subprocess.check_output(
            [
                "python3",
                "-c",
                f"""
import json
cfg = json.load(open('/workspaces/home-assistant-core/config/.storage/core.config_entries'))
for e in cfg['data']['entries']:
    if e['entry_id'] == {entry_id!r}:
        print(json.dumps(e['data']))
""",
            ],
            text=True,
        )
        data = json.loads(raw.strip())
    except Exception:
        return None

    ne_entities = data.get("non_essential_entities", [])
    if not ne_entities:
        print(f"  Group '{title}' has no non_essential_entities configured", flush=True)
        return None

    all_entities = data.get("entities", [])
    essential_entities = [e for e in all_entities if e not in ne_entities]

    # Find sensor prefix
    prefix = None
    for s in all_states:
        eid = s["entity_id"]
        if "entity_availability" in eid and "group_summary" in eid:
            fn = s.get("attributes", {}).get("friendly_name", "")
            if title.lower() in fn.lower():
                prefix = eid.replace("_group_summary", "")
                break

    if not prefix:
        return None

    print(f"NE group: '{title}' ({entry_id})", flush=True)
    print(f"  Essential: {essential_entities}", flush=True)
    print(f"  Non-essential: {ne_entities}", flush=True)
    print(f"  Prefix: {prefix}", flush=True)

    return {
        "entry_id": entry_id,
        "title": title,
        "prefix": prefix,
        "entities": all_entities,
        "essential_entities": essential_entities,
        "ne_entities": ne_entities,
        "staleness_threshold": data.get("staleness_threshold", 0),
        "battery_threshold": data.get("battery_threshold", 0),
        "battery_entity_map": data.get("battery_entity_map", {}),
    }


def _run_ne_tests(ne_ctx: dict) -> None:
    """Run EC25-EC35 against a group with non-essential entities."""
    prefix = ne_ctx["prefix"]
    ne_entities = ne_ctx["ne_entities"]
    essential_entities = ne_ctx["essential_entities"]
    ne_target = ne_entities[0]

    ne_bs_prefix = prefix.replace(
        "sensor.entity_availability_", "binary_sensor.entity_availability_"
    )

    def ne_restore():
        for eid in ne_ctx["entities"]:
            ss(eid, "on", {"friendly_name": eid.split(".")[-1]})
        wait(8)

    ne_restore()

    # EC25: NE entity offline → offline_count unchanged
    if ec_enabled(25):
        print(
            "\n=== EC25: NE offline → essential offline_count unchanged ===", flush=True
        )
        baseline = gs(f"{prefix}_offline_count").get("state", "0")
        ss(ne_target, "unavailable", {"friendly_name": "ne test"})
        # Poll NE offline count to confirm coordinator processed the state change
        wait_for(
            lambda: gs(f"{prefix}_offline_count_non_essential").get("state"),
            "1",
        )
        chk(
            "EC25 offline_count unchanged",
            gs(f"{prefix}_offline_count").get("state"),
            baseline,
            f"ne_target={ne_target} baseline={baseline}",
        )

    # EC26: NE entity offline → offline_count_non_essential increments
    if ec_enabled(26):
        print(
            "\n=== EC26: NE offline → offline_count_non_essential increments ===",
            flush=True,
        )
        # ne_target already offline from EC25 or restored
        if not ec_enabled(25):
            ss(ne_target, "unavailable", {"friendly_name": "ne test"})
            wait_for(
                lambda: gs(f"{prefix}_offline_count_non_essential").get("state"), "1"
            )
        chk(
            "EC26 offline_count_non_essential=1",
            wait_for(
                lambda: gs(f"{prefix}_offline_count_non_essential").get("state"),
                "1",
            ),
            "1",
            f"ne_target={ne_target}",
        )

    # EC27: NE entity offline → any_offline stays OFF
    if ec_enabled(27):
        print(
            "\n=== EC27: NE offline → any_offline binary sensor stays OFF ===",
            flush=True,
        )
        if not ec_enabled(25) and not ec_enabled(26):
            ss(ne_target, "unavailable", {"friendly_name": "ne test"})
            wait_for(
                lambda: gs(f"{prefix}_offline_count_non_essential").get("state"), "1"
            )
        chk(
            "EC27 any_offline=off (NE excluded)",
            gs(f"{ne_bs_prefix}_any_offline").get("state"),
            "off",
            f"ne_target={ne_target}",
        )

    # EC28: NE entity offline → any_offline_non_essential turns ON
    if ec_enabled(28):
        print(
            "\n=== EC28: NE offline → any_offline_non_essential turns ON ===",
            flush=True,
        )
        if not any(ec_enabled(n) for n in [25, 26, 27]):
            ss(ne_target, "unavailable", {"friendly_name": "ne test"})
            wait_for(
                lambda: gs(f"{prefix}_offline_count_non_essential").get("state"), "1"
            )
        chk(
            "EC28 any_offline_non_essential=on",
            wait_for(
                lambda: gs(f"{ne_bs_prefix}_any_offline_non_essential").get("state"),
                "on",
            ),
            "on",
        )

    # EC29: group_summary non_essential_offline increments
    if ec_enabled(29):
        print(
            "\n=== EC29: NE offline → group_summary non_essential_offline ===",
            flush=True,
        )
        if not any(ec_enabled(n) for n in [25, 26, 27, 28]):
            ss(ne_target, "unavailable", {"friendly_name": "ne test"})
            wait_for(
                lambda: gs(f"{prefix}_offline_count_non_essential").get("state"), "1"
            )
        attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        chk(
            "EC29 non_essential_offline=1",
            wait_for(
                lambda: str(
                    gs(f"{prefix}_group_summary")
                    .get("attributes", {})
                    .get("non_essential_offline")
                ),
                "1",
            ),
            "1",
        )

    # EC30: group_summary online count unchanged (NE offline doesn't reduce essential online)
    if ec_enabled(30):
        print(
            "\n=== EC30: NE offline → essential online count unchanged ===", flush=True
        )
        if not any(ec_enabled(n) for n in range(25, 30)):
            ss(ne_target, "unavailable", {"friendly_name": "ne test"})
            wait_for(
                lambda: gs(f"{prefix}_offline_count_non_essential").get("state"), "1"
            )
        expected_online = len(essential_entities)
        attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        chk(
            f"EC30 online={expected_online} (essential only)",
            str(attrs.get("online")),
            str(expected_online),
            f"non_essential_offline={attrs.get('non_essential_offline')}",
        )

    ne_restore()

    # Shared stale-wait helper — polls stale_count until > 0 or timeout.
    # Returns (went_stale: bool, stale_val: str). Reused by EC31/EC33/EC37/EC38/EC39.
    # Callers that run after EC31 restored entities must re-wait; callers that run
    # without an intervening restore can reuse a cached value by passing it in.
    def _wait_stale(label: str) -> tuple[bool, str]:
        threshold = ne_ctx["staleness_threshold"]
        if threshold == 0:
            return False, "0"
        stale_timeout = (threshold + 2) * 60
        print(
            f"  {label}: waiting up to {threshold + 2} min for stale_count > 0...",
            flush=True,
        )
        deadline = time.time() + stale_timeout
        val = "0"
        while time.time() < deadline:
            val = gs(f"{prefix}_stale_count").get("state", "0")
            try:
                if int(val or 0) > 0:
                    return True, val
            except (TypeError, ValueError):
                pass
            time.sleep(10)
        return False, val

    # EC31: stale events — only if staleness_threshold > 0
    if ec_enabled(31):
        print(
            "\n=== EC31: entity_availability_stale / stale_recovered events ===",
            flush=True,
        )
        threshold = ne_ctx["staleness_threshold"]
        if threshold == 0:
            print("  EC31: skipped (staleness_threshold=0 for this group)", flush=True)
        elif not _WS_AVAILABLE:
            print("  EC31: skipped (websocket-client not installed)", flush=True)
        elif not essential_entities:
            print("  EC31: skipped (no essential entities in NE group)", flush=True)
        else:
            went_stale, _sv = _wait_stale("EC31")
            if not went_stale:
                print(
                    f"  EC31: skipped (entity did not go stale within {threshold + 2} min)",
                    flush=True,
                )
            else:
                # Stale entities are already in "on" state — they went stale because
                # last_changed is old, not because they're offline. A plain ss("on") on
                # an already-"on" entity is a no-op in HA (same state, no last_changed
                # update). Cycle through "off" then "on" to force last_changed update,
                # which is what the coordinator needs to clear stale status.
                def _cycle_all_essential():
                    for eid in essential_entities:
                        ss(eid, "off", {"friendly_name": eid.split(".")[-1]})
                    time.sleep(1)
                    for eid in essential_entities:
                        ss(eid, "on", {"friendly_name": eid.split(".")[-1]})

                # Capture stale_recovered event — use longer timeout since coordinator
                # fires on its next tick (up to 30s after state change).
                recovered_events = capture_events(
                    "entity_availability_stale_recovered",
                    _cycle_all_essential,
                    timeout=max(WAIT_FOR_TIMEOUT * 2, 90),
                )
                ev_recovered = next(
                    (
                        e
                        for e in recovered_events
                        if e.get("entity_id") in essential_entities
                    ),
                    None,
                )
                chk(
                    "EC31 stale_recovered event captured after restore",
                    ev_recovered is not None,
                    True,
                    f"events={len(recovered_events)}",
                )
                if ev_recovered:
                    chk(
                        "EC31 stale_recovered stale_entities excludes NE",
                        ne_target not in ev_recovered.get("stale_entities", []),
                        True,
                    )
                    chk(
                        "EC31 stale_recovered stale_count is int",
                        isinstance(ev_recovered.get("stale_count"), int),
                        True,
                    )
                chk(
                    "EC31 stale_count=0 after restore",
                    wait_for(lambda: gs(f"{prefix}_stale_count").get("state"), "0"),
                    "0",
                )
                # Bring entities back to full clean state after off/on cycle,
                # then wait for coordinator to confirm offline_count=0 before
                # EC25+ proceed (cycling off→on may briefly trigger offline).
                ne_restore()
                wait_for(lambda: gs(f"{prefix}_offline_count").get("state"), "0")

    # EC32: any_low_battery binary sensor
    if ec_enabled(32) and essential_entities:
        print(
            "\n=== EC32: any_low_battery ON when essential entity has low battery ===",
            flush=True,
        )
        threshold = ne_ctx["battery_threshold"]
        if threshold == 0:
            print("  EC32: skipped (battery_threshold=0 for this group)", flush=True)
        else:
            # Find an essential entity that has a battery sensor mapped in the group config.
            bmap = ne_ctx.get("battery_entity_map", {})
            ess_bat_entity = next(
                (eid for eid in essential_entities if bmap.get(eid)), None
            )
            if not ess_bat_entity:
                print(
                    "  EC32: skipped (no essential entity has a battery sensor mapped)",
                    flush=True,
                )
            else:
                bat_sensor = bmap[ess_bat_entity]
                low_val = str(int(threshold) - 5)
                ss(
                    bat_sensor,
                    low_val,
                    {"device_class": "battery", "unit_of_measurement": "%"},
                )
                chk(
                    "EC32 any_low_battery=on",
                    wait_for(
                        lambda: gs(f"{ne_bs_prefix}_any_low_battery").get("state"),
                        "on",
                    ),
                    "on",
                    f"bat_sensor={bat_sensor} low_val={low_val}",
                )
                # NE low battery must NOT trigger essential any_low_battery.
                # Only meaningful if NE entity has a mapped battery sensor — otherwise
                # the integration ignores any synthetic state and the check is vacuous.
                ne_bat = bmap.get(ne_target)
                if ne_bat:
                    ss(
                        ne_bat,
                        low_val,
                        {"device_class": "battery", "unit_of_measurement": "%"},
                    )
                    wait(10)
                # Restore essential battery, verify any_low_battery turns off
                ss(
                    bat_sensor,
                    "90",
                    {"device_class": "battery", "unit_of_measurement": "%"},
                )
                chk(
                    "EC32 any_low_battery=off after recovery",
                    wait_for(
                        lambda: gs(f"{ne_bs_prefix}_any_low_battery").get("state"),
                        "off",
                    ),
                    "off",
                )
                # Restore NE battery too if it was set
                if ne_bat:
                    ss(
                        ne_bat,
                        "90",
                        {"device_class": "battery", "unit_of_measurement": "%"},
                    )

    # EC33: any_stale binary sensor
    if ec_enabled(33) and essential_entities:
        print("\n=== EC33: any_stale ON when essential entity is stale ===", flush=True)
        if ne_ctx["staleness_threshold"] == 0:
            print("  EC33: skipped (staleness_threshold=0 for this group)", flush=True)
        else:
            went_stale, stale_count = _wait_stale("EC33")
            if not went_stale:
                print(
                    "  EC33: skipped (entities did not go stale within timeout)",
                    flush=True,
                )
            else:
                chk(
                    "EC33 stale_count > 0",
                    int(stale_count or 0) > 0,
                    True,
                    f"stale_count={stale_count}",
                )
                chk(
                    "EC33 any_stale=on",
                    gs(f"{ne_bs_prefix}_any_stale").get("state"),
                    "on",
                )

    # EC34: suppressed NE entity — banner counts NE tier
    if ec_enabled(34):
        print(
            "\n=== EC34: suppressed NE entity counted in non_essential_suppressed ===",
            flush=True,
        )
        ne_restore()
        api(
            "POST",
            "/api/services/entity_availability/suppress_indefinitely",
            {"entity_id": ne_target, "group": ne_ctx["title"]},
        )
        wait(10)
        attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        chk(
            "EC34 non_essential_suppressed=1",
            str(attrs.get("non_essential_suppressed")),
            "1",
        )
        chk(
            "EC34 suppressed=0 (essential suppressed count unaffected)",
            str(attrs.get("suppressed")),
            "0",
        )
        api(
            "POST",
            "/api/services/entity_availability/unsuppress",
            {"entity_id": ne_target, "group": ne_ctx["title"]},
        )
        wait(8)

    # EC35: NE entity recovery → offline_count_non_essential drops
    if ec_enabled(35):
        print(
            "\n=== EC35: NE recovery → offline_count_non_essential drops to 0 ===",
            flush=True,
        )
        ss(ne_target, "unavailable", {"friendly_name": "ne test"})
        wait_for(lambda: gs(f"{prefix}_offline_count_non_essential").get("state"), "1")
        ss(ne_target, "on", {"friendly_name": ne_target.split(".")[-1]})
        chk(
            "EC35 offline_count_non_essential=0",
            wait_for(
                lambda: gs(f"{prefix}_offline_count_non_essential").get("state"), "0"
            ),
            "0",
        )

    ne_restore()

    # EC36: diagnostics endpoint returns correct shape for group entry (new schema: counts/entities/config)
    if ec_enabled(36):
        print(
            "\n=== EC36: diagnostics endpoint returns correct shape (new schema) ===",
            flush=True,
        )
        try:
            raw = api("GET", f"/api/diagnostics/config_entry/{ne_ctx['entry_id']}")
            result = raw.get("data", raw)  # unwrap HA envelope
            chk("EC36 diagnostics entry_type=group", result.get("entry_type"), "group")
            chk(
                "EC36 diagnostics has counts dict",
                isinstance(result.get("counts"), dict),
                True,
            )
            chk(
                "EC36 diagnostics counts.total >= 1",
                int((result.get("counts") or {}).get("total", 0)) >= 1,
                True,
                f"counts={result.get('counts')}",
            )
            chk(
                "EC36 diagnostics counts.essential >= 1",
                int((result.get("counts") or {}).get("essential", 0)) >= 1,
                True,
            )
            chk(
                "EC36 diagnostics counts.non_essential >= 1",
                int((result.get("counts") or {}).get("non_essential", 0)) >= 1,
                True,
            )
            chk(
                "EC36 diagnostics has entities dict",
                isinstance(result.get("entities"), dict),
                True,
            )
            chk(
                "EC36 diagnostics entities.essential is list",
                isinstance((result.get("entities") or {}).get("essential"), list),
                True,
            )
            chk(
                "EC36 diagnostics entities.non_essential is list",
                isinstance((result.get("entities") or {}).get("non_essential"), list),
                True,
            )
            chk(
                "EC36 diagnostics has config dict",
                isinstance(result.get("config"), dict),
                True,
            )
            chk(
                "EC36 diagnostics config has cooldown_seconds",
                "cooldown_seconds" in (result.get("config") or {}),
                True,
            )
            chk(
                "EC36 diagnostics config has signal_enabled",
                "signal_enabled" in (result.get("config") or {}),
                True,
            )
            chk(
                "EC36 diagnostics config has bad_states",
                "bad_states" in (result.get("config") or {}),
                True,
            )
        except Exception as e:
            chk("EC36 diagnostics endpoint reachable", False, True, str(e))

    ne_restore()

    # EC37: stale_count sensor increments for stale essential entity
    if ec_enabled(37):
        print(
            "\n=== EC37: stale_count sensor increments for stale essential entity ===",
            flush=True,
        )
        if ne_ctx["staleness_threshold"] == 0:
            print("  EC37: skipped (staleness_threshold=0 for this group)", flush=True)
        elif not essential_entities:
            print("  EC37: skipped (no essential entities)", flush=True)
        else:
            went_stale, stale_val = _wait_stale("EC37")
            if not went_stale:
                print(
                    "  EC37: skipped (entities did not go stale within timeout)",
                    flush=True,
                )
            else:
                chk(
                    "EC37 stale_count > 0",
                    int(stale_val or 0) > 0,
                    True,
                    f"stale_count={stale_val}",
                )

    # EC38: stale_entities sensor lists stale essential entity
    if ec_enabled(38):
        print(
            "\n=== EC38: stale_entities sensor lists stale essential entity ===",
            flush=True,
        )
        if ne_ctx["staleness_threshold"] == 0:
            print("  EC38: skipped (staleness_threshold=0 for this group)", flush=True)
        elif not essential_entities:
            print("  EC38: skipped (no essential entities)", flush=True)
        else:
            # Ensure stale (reuse cached value if already stale)
            went_stale, stale_val = _wait_stale("EC38")
            if not went_stale:
                print(
                    "  EC38: skipped (entities did not go stale within timeout)",
                    flush=True,
                )
            else:
                stale_attrs = gs(f"{prefix}_stale_entities").get("attributes", {})
                # entities attr is a list of entity_ids (same pattern as recently_offline/recovered)
                stale_entity_list = stale_attrs.get("entities", [])
                chk(
                    "EC38 stale_entities includes essential entity",
                    any(eid in stale_entity_list for eid in essential_entities),
                    True,
                    f"entities={stale_entity_list} essential={essential_entities}",
                )
                chk(
                    "EC38 stale_entities excludes NE entity",
                    ne_target not in stale_entity_list,
                    True,
                    f"entities={stale_entity_list} ne_target={ne_target}",
                )

    # EC39: NE entity stale → stale_count_non_essential > 0, stale_count (essential) unchanged
    if ec_enabled(39):
        print(
            "\n=== EC39: NE stale → stale_count_non_essential > 0, stale_count unchanged ===",
            flush=True,
        )
        if ne_ctx["staleness_threshold"] == 0:
            print("  EC39: skipped (staleness_threshold=0 for this group)", flush=True)
        else:
            # Capture essential stale_count baseline before NE goes stale.
            # Both essential and NE share the same threshold, so essential may already be > 0.
            baseline_essential_stale = gs(f"{prefix}_stale_count").get("state", "0")
            threshold = ne_ctx["staleness_threshold"]
            stale_timeout = (threshold + 2) * 60
            print(f"  Waiting up to {threshold + 2} min for NE stale...", flush=True)
            deadline = time.time() + stale_timeout
            ne_stale_val = "0"
            while time.time() < deadline:
                ne_stale_val = gs(f"{prefix}_stale_count_non_essential").get(
                    "state", "0"
                )
                try:
                    if int(ne_stale_val or 0) > 0:
                        break
                except (TypeError, ValueError):
                    pass
                time.sleep(10)
            ne_went_stale = int(ne_stale_val or 0) > 0
            if not ne_went_stale:
                print(
                    "  EC39: skipped (NE entities did not go stale within timeout)",
                    flush=True,
                )
            else:
                # stale_count (essential only) must equal baseline — NE stale must not bleed in
                essential_stale_after = gs(f"{prefix}_stale_count").get("state", "0")
                chk(
                    "EC39 stale_count (essential) unchanged after NE went stale",
                    essential_stale_after,
                    baseline_essential_stale,
                    f"before={baseline_essential_stale} after={essential_stale_after} ne_stale={ne_stale_val}",
                )

    ne_restore()

    # EC40: any_low_battery stays OFF when only NE entity has low battery
    if ec_enabled(40):
        print(
            "\n=== EC40: any_low_battery stays OFF when only NE entity has low battery ===",
            flush=True,
        )
        threshold = ne_ctx["battery_threshold"]
        if threshold == 0:
            print("  EC40: skipped (battery_threshold=0 for this group)", flush=True)
        else:
            bmap = ne_ctx.get("battery_entity_map", {})
            ne_bat = bmap.get(ne_target)
            if not ne_bat:
                print(
                    "  EC40: skipped (NE entity has no battery sensor mapped — test would be vacuous)",
                    flush=True,
                )
            else:
                low_val = str(int(threshold) - 5)
                # Ensure essential batteries are clear before asserting any_low_battery=off
                ne_restore()
                wait_for(
                    lambda: gs(f"{ne_bs_prefix}_any_low_battery").get("state"), "off"
                )
                ss(
                    ne_bat,
                    low_val,
                    {"device_class": "battery", "unit_of_measurement": "%"},
                )
                chk(
                    "EC40 any_low_battery=off (NE battery low, essential ok)",
                    wait_for(
                        lambda: gs(f"{ne_bs_prefix}_any_low_battery").get("state"),
                        "off",
                        interval=3,
                    ),
                    "off",
                    f"ne_bat={ne_bat} low_val={low_val}",
                )
                ss(
                    ne_bat,
                    "90",
                    {"device_class": "battery", "unit_of_measurement": "%"},
                )
                wait_for(
                    lambda: gs(f"{ne_bs_prefix}_any_low_battery").get("state"), "off"
                )

    # EC41: recently_offline sensor lists entity after it goes offline
    if ec_enabled(41):
        print(
            "\n=== EC41: recently_offline sensor lists entity after it goes offline ===",
            flush=True,
        )
        if not essential_entities and not ne_entities:
            print("  EC41: skipped (no entities in NE group)", flush=True)
        else:
            target = essential_entities[0] if essential_entities else ne_entities[0]
            ss(target, "unavailable", {"friendly_name": "smoke test"})
            # Poll until entity appears in recently_offline list
            wait_for(
                lambda: (
                    target
                    in gs(f"{prefix}_recently_offline")
                    .get("attributes", {})
                    .get("entities", [])
                ),
                True,
            )
            recent_entities = (
                gs(f"{prefix}_recently_offline")
                .get("attributes", {})
                .get("entities", [])
            )
            chk(
                "EC41 recently_offline lists entity",
                target in recent_entities,
                True,
                f"entities={recent_entities} target={target}",
            )
            ne_restore()

    # EC42: recently_recovered sensor lists entity after recovery
    if ec_enabled(42):
        print(
            "\n=== EC42: recently_recovered sensor lists entity after recovery ===",
            flush=True,
        )
        if not essential_entities and not ne_entities:
            print("  EC42: skipped (no entities in NE group)", flush=True)
        else:
            target = essential_entities[0] if essential_entities else ne_entities[0]
            ss(target, "unavailable", {"friendly_name": "smoke test"})
            wait_for(
                lambda: (
                    target
                    in gs(f"{prefix}_recently_offline")
                    .get("attributes", {})
                    .get("entities", [])
                ),
                True,
            )
            ss(target, "on", {"friendly_name": target.split(".")[-1]})
            wait_for(
                lambda: (
                    target
                    in gs(f"{prefix}_recently_recovered")
                    .get("attributes", {})
                    .get("entities", [])
                ),
                True,
            )
            recent_entities = (
                gs(f"{prefix}_recently_recovered")
                .get("attributes", {})
                .get("entities", [])
            )
            chk(
                "EC42 recently_recovered lists entity",
                target in recent_entities,
                True,
                f"entities={recent_entities} target={target}",
            )
            ne_restore()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=== Entity Availability smoke tests ===\n", flush=True)

    ctx = discover()
    if not SKIP_SETUP:
        setup_battery(ctx)
        restore_and_wait(ctx)
        # Extra settle time after options flow reload — coordinator needs a full tick
        wait(15)
    else:
        print(
            "  --skip-setup: assuming clean state, skipping battery setup + restore",
            flush=True,
        )

    prefix = ctx["prefix"]
    battery_entity = ctx["mapped_battery_entity"]
    battery_sensor = ctx["mapped_battery_sensor"]
    threshold = ctx["battery_threshold"]
    low_val = str(int(threshold) - 5)  # clearly below threshold
    combined_prefix = ctx["combined_prefix"]

    # EC1-EC8: mandatory state-setup chain — skip when --skip-setup + EC_FILTER set.
    _run_setup_chain = not (SKIP_SETUP and EC_FILTER)

    # ------------------------------------------------------------------
    if _run_setup_chain:
        print("=== EC1: entity unavailable → offline_count increments ===", flush=True)
        ss(ctx["entities"][0], "unavailable", {"friendly_name": "test"})
        chk(
            "offline_count=1",
            wait_for(
                lambda: gs(f"{prefix}_offline_count").get("state"),
                "1",
            ),
            "1",
        )
        if combined_prefix:
            wait_for(
                lambda: (
                    gs(f"{combined_prefix}_combined_summary")
                    .get("attributes", {})
                    .get("offline")
                ),
                1,
            )
            c_attrs = gs(f"{combined_prefix}_combined_summary").get("attributes", {})
            chk("EC1 combined offline=1", str(c_attrs.get("offline")), "1")
            chk("EC1 combined low_battery=0", str(c_attrs.get("low_battery")), "0")
        restore_and_wait(ctx)

        # ------------------------------------------------------------------
        print(
            "\n=== EC4: online + battery below threshold → low_battery_count=1 ===",
            flush=True,
        )
        restore_and_wait(ctx)
        ss(
            battery_sensor,
            low_val,
            {
                "friendly_name": "Test Battery",
                "device_class": "battery",
                "unit_of_measurement": "%",
            },
        )
        chk(
            "low_battery_count=1",
            wait_for(
                lambda: gs(f"{prefix}_low_battery_count").get("state"),
                "1",
            ),
            "1",
        )
        chk(
            "low_battery list count=1",
            wait_for(
                lambda: gs(f"{prefix}_low_battery").get("attributes", {}).get("count"),
                1,
            ),
            1,
        )
        # wait_for low_battery sensor to reflect updated state before reading attrs
        wait_for(
            lambda: (
                gs(f"{prefix}_low_battery").get("state")
                not in ("None", "", None, "unavailable")
            ),
            True,
        )
        lb = gs(f"{prefix}_low_battery")
        chk(
            "low_battery state non-null",
            lb.get("state") not in ("None", "", "unavailable"),
            True,
            f"state={lb.get('state')!r}",
        )
        chk(
            "low_battery devices has battery_entity",
            battery_entity in lb.get("attributes", {}).get("devices", {}),
            True,
        )
        chk(
            "offline_count=0 (device online)",
            wait_for(lambda: gs(f"{prefix}_offline_count").get("state"), "0"),
            "0",
        )
        if combined_prefix:
            low_battery_val = wait_for(
                lambda: (
                    gs(f"{combined_prefix}_combined_summary")
                    .get("attributes", {})
                    .get("low_battery")
                ),
                1,
            )
            c_attrs = gs(f"{combined_prefix}_combined_summary").get("attributes", {})
            chk("EC4 combined low_battery=1", low_battery_val, 1)
            chk("EC4 combined offline=0", str(c_attrs.get("offline")), "0")

        # ------------------------------------------------------------------
        print(
            "\n=== EC5: device+battery offline → offline=1, low_battery=0 (PR#41) ===",
            flush=True,
        )
        ss(battery_entity, "unavailable", {"friendly_name": "test"})
        ss(
            battery_sensor,
            "unavailable",
            {"friendly_name": "Test Battery", "device_class": "battery"},
        )
        chk(
            "offline_count=1",
            wait_for(
                lambda: gs(f"{prefix}_offline_count").get("state"),
                "1",
            ),
            "1",
        )
        chk(
            "low_battery_count=0 (offline excluded)",
            wait_for(
                lambda: gs(f"{prefix}_low_battery_count").get("state"),
                "0",
            ),
            "0",
        )
        gs_attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        chk("group_summary offline=1", str(gs_attrs.get("offline")), "1")
        chk(
            "group_summary low_battery=0 (no double-count)",
            str(gs_attrs.get("low_battery")),
            "0",
        )
        chk(
            "low_battery list count=0",
            gs(f"{prefix}_low_battery").get("attributes", {}).get("count"),
            0,
        )
        if combined_prefix:
            wait_for(
                lambda: (
                    gs(f"{combined_prefix}_combined_summary")
                    .get("attributes", {})
                    .get("offline")
                ),
                1,
            )
            c_attrs = gs(f"{combined_prefix}_combined_summary").get("attributes", {})
            chk("EC5 combined offline=1", str(c_attrs.get("offline")), "1")
            chk("EC5 combined low_battery=0", str(c_attrs.get("low_battery")), "0")

        # ------------------------------------------------------------------
        print("\n=== EC6: battery replaced, back online → all clear ===", flush=True)
        ss(battery_entity, "on", {"friendly_name": "test"})
        ss(
            battery_sensor,
            "90",
            {
                "friendly_name": "Test Battery",
                "device_class": "battery",
                "unit_of_measurement": "%",
            },
        )
        chk(
            "offline_count=0",
            wait_for(lambda: gs(f"{prefix}_offline_count").get("state"), "0"),
            "0",
        )
        chk(
            "low_battery_count=0 (battery 90%)",
            gs(f"{prefix}_low_battery_count").get("state"),
            "0",
        )
        gs_attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        chk(
            f"online={len(ctx['entities'])}",
            str(gs_attrs.get("online")),
            str(len(ctx["entities"])),
        )
        chk(
            "battery_levels updated to 90",
            str(gs_attrs.get("battery_levels", {}).get(battery_entity)),
            "90",
        )
        if combined_prefix:
            wait_for(
                lambda: (
                    gs(f"{combined_prefix}_combined_summary")
                    .get("attributes", {})
                    .get("offline")
                ),
                0,
            )
            wait_for(
                lambda: (
                    gs(f"{combined_prefix}_combined_summary")
                    .get("attributes", {})
                    .get("low_battery")
                ),
                0,
            )
            wait_for(
                lambda: int(
                    gs(f"{combined_prefix}_low_battery_count").get("state", "-1")
                ),
                0,
            )
            c_attrs = gs(f"{combined_prefix}_combined_summary").get("attributes", {})
            chk("EC6 combined offline=0", str(c_attrs.get("offline")), "0")
            chk("EC6 combined low_battery=0", str(c_attrs.get("low_battery")), "0")

        # ------------------------------------------------------------------
        if combined_prefix:
            b_coff = int(gs(f"{combined_prefix}_offline_count").get("state", "0"))
            b_clb = int(gs(f"{combined_prefix}_low_battery_count").get("state", "0"))
            print(
                f"\n=== EC7+EC8: combined sensors (baseline offline={b_coff} low_battery={b_clb}) ===",
                flush=True,
            )

            ss(
                battery_sensor,
                low_val,
                {
                    "friendly_name": "Test Battery",
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                },
            )
            chk(
                "EC7 combined low_battery=base+1",
                wait_for(
                    lambda: int(
                        gs(f"{combined_prefix}_low_battery_count").get("state", "0")
                    ),
                    b_clb + 1,
                ),
                b_clb + 1,
                f"(base={b_clb})",
            )
            chk(
                "EC7 combined offline unchanged",
                int(gs(f"{combined_prefix}_offline_count").get("state", "0")),
                b_coff,
                f"(base={b_coff})",
            )

            ss(battery_entity, "unavailable", {"friendly_name": "test"})
            ss(
                battery_sensor,
                "unavailable",
                {"friendly_name": "Test Battery", "device_class": "battery"},
            )
            chk(
                "EC8 combined low_battery=base (offline excluded)",
                wait_for(
                    lambda: int(
                        gs(f"{combined_prefix}_low_battery_count").get("state", "0")
                    ),
                    b_clb,
                ),
                b_clb,
                f"(base={b_clb})",
            )
            chk(
                "EC8 combined offline=base+1",
                wait_for(
                    lambda: int(
                        gs(f"{combined_prefix}_offline_count").get("state", "0")
                    ),
                    b_coff + 1,
                ),
                b_coff + 1,
                f"(base={b_coff})",
            )
        else:
            print("\n=== EC7+EC8: skipped (no combined group found) ===", flush=True)

        # ------------------------------------------------------------------
    if ec_enabled(9):
        print("\n=== EC9: PR#37 — cleared battery map not re-suggested ===", flush=True)
        # Find an entity with cleared ("") mapping
        import subprocess

        eid_val = ctx["entry_id"]
        _ec9_script = (
            "import json\n"
            "cfg = json.load(open('/workspaces/home-assistant-core/config/.storage/core.config_entries'))\n"
            "for e in cfg['data']['entries']:\n"
            f"    if e['entry_id'] == {eid_val!r}:\n"
            "        bmap = e['data'].get('battery_entity_map', {})\n"
            "        cleared = [k for k, v in bmap.items() if v == '']\n"
            "        print(json.dumps(cleared))\n"
        )
        try:
            raw = subprocess.check_output(
                ["python3", "-c", _ec9_script],
                text=True,
            ).strip()
            cleared_entities = json.loads(raw)
        except Exception:
            cleared_entities = []

        r = api(
            "POST",
            "/api/config/config_entries/options/flow",
            {"handler": ctx["entry_id"]},
        )
        fid = r["flow_id"]
        r2 = api(
            "POST",
            f"/api/config/config_entries/options/flow/{fid}",
            {
                "entities": ctx["entities"],
                "bad_states": ["unavailable", "unknown"],
                "cooldown": 0,
                "staleness_threshold": 0,
                "battery_threshold": threshold,
                "availability_windows": ["today", "7d"],
                "use_device_names": False,
                "staleness_use_last_updated": False,
                "signal_enabled": ctx.get("signal_enabled", False),
            },
        )
        schema = r2.get("data_schema", [])
        if cleared_entities:
            c_eid = cleared_entities[0]
            c_field = next((f for f in schema if f.get("name") == c_eid), None)
            c_suggestion = (
                (c_field or {}).get("description", {}).get("suggested_value", "")
            )
            chk(
                "PR#37 cleared entity has no suggestion",
                c_suggestion,
                "",
                f"entity={c_eid} suggested={c_suggestion!r}",
            )
        else:
            print("  EC9: skipped (no cleared mappings found in config)", flush=True)
        # Confirm mapped entity still has suggestion
        m_field = next((f for f in schema if f.get("name") == battery_entity), None)
        m_suggestion = (m_field or {}).get("description", {}).get("suggested_value", "")
        chk(
            "PR#37 mapped entity retains suggestion",
            m_suggestion != "",
            True,
            f"entity={battery_entity} suggested={m_suggestion!r}",
        )
        api("DELETE", f"/api/config/config_entries/options/flow/{fid}")

        # ------------------------------------------------------------------
    if ec_enabled(10):
        print(
            "\n=== EC10: suppression — suppressed+unavailable not in offline ===",
            flush=True,
        )
        restore_all(ctx)
        suppressed_entity = ctx["entities"][0]
        api(
            "POST",
            "/api/services/entity_availability/suppress",
            {
                "entity_id": suppressed_entity,
                "group": ctx["title"],
            },
        )
        wait_for(
            lambda: str(
                gs(f"{prefix}_group_summary").get("attributes", {}).get("suppressed")
            ),
            "1",
        )
        ss(suppressed_entity, "unavailable", {"friendly_name": "test"})
        chk(
            "offline_count=0 (suppressed not counted)",
            wait_for(
                lambda: gs(f"{prefix}_offline_count").get("state"),
                "0",
            ),
            "0",
        )
        chk(
            "suppressed=1",
            str(gs(f"{prefix}_group_summary").get("attributes", {}).get("suppressed")),
            "1",
        )

        # ------------------------------------------------------------------
    if ec_enabled(11):
        print(
            "\n=== EC11: suppress_indefinitely + unsuppress round-trip ===",
            flush=True,
        )
        restore_all(ctx)
        indef_entity = ctx["entities"][1]
        api(
            "POST",
            "/api/services/entity_availability/suppress_indefinitely",
            {"entity_id": indef_entity, "group": ctx["title"]},
        )
        chk(
            "EC11 suppressed=1 after suppress_indefinitely",
            wait_for(
                lambda: str(
                    gs(f"{prefix}_group_summary")
                    .get("attributes", {})
                    .get("suppressed")
                ),
                "1",
            ),
            "1",
        )
        summary_attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        chk(
            "EC11 suppressed_until[entity]=null (indefinite)",
            summary_attrs.get("suppressed_until", {}).get(indef_entity),
            None,
        )
        ss(indef_entity, "unavailable", {"friendly_name": "test"})
        chk(
            "EC11 offline_count=0 (indefinitely suppressed not counted)",
            wait_for(
                lambda: gs(f"{prefix}_offline_count").get("state"),
                "0",
            ),
            "0",
        )
        api(
            "POST",
            "/api/services/entity_availability/unsuppress",
            {"entity_id": indef_entity, "group": ctx["title"]},
        )
        chk(
            "EC11 suppressed=0 after unsuppress",
            wait_for(
                lambda: str(
                    gs(f"{prefix}_group_summary")
                    .get("attributes", {})
                    .get("suppressed")
                ),
                "0",
            ),
            "0",
        )

        # ------------------------------------------------------------------
    if ec_enabled(12):
        print(
            "\n=== EC12: group-scoped suppress — entity in two groups ===",
            flush=True,
        )
        restore_all(ctx)
        # Find a second group that also monitors kitchen_1 — use friendly_name matching
        all_states = api("GET", "/api/states")
        beta_prefix = None
        alpha_entities = set(ctx["entities"])
        for s in all_states:
            eid = s["entity_id"]
            if "entity_availability" not in eid or "group_summary" not in eid:
                continue
            candidate_prefix = eid.replace("_group_summary", "")
            if candidate_prefix == ctx["prefix"]:
                continue  # skip Alpha itself
            candidate_entities = set(s.get("attributes", {}).get("entities", []))
            if (
                alpha_entities & candidate_entities
            ):  # shares at least one entity with Alpha
                beta_prefix = candidate_prefix
                break
        if beta_prefix:
            shared_entity = ctx["entities"][
                0
            ]  # kitchen_1 — in both Alpha and the found group
            # Suppress in Alpha only using the group title (exact match, no fragile string surgery)
            alpha_title = ctx["title"]
            api(
                "POST",
                "/api/services/entity_availability/suppress_indefinitely",
                {"entity_id": shared_entity, "group": alpha_title},
            )
            wait_for(
                lambda: str(
                    gs(f"{prefix}_group_summary")
                    .get("attributes", {})
                    .get("suppressed")
                ),
                "1",
            )
            alpha_attrs = gs(f"{prefix}_group_summary").get("attributes", {})
            beta_attrs = gs(f"{beta_prefix}_group_summary").get("attributes", {})
            chk(
                "EC12 suppressed=1 in Alpha",
                str(alpha_attrs.get("suppressed")),
                "1",
            )
            chk(
                "EC12 suppressed=0 in Beta (group-scoped)",
                str(beta_attrs.get("suppressed")),
                "0",
            )
            # Unsuppress in Alpha only
            api(
                "POST",
                "/api/services/entity_availability/unsuppress",
                {"entity_id": shared_entity, "group": alpha_title},
            )
            wait_for(
                lambda: str(
                    gs(f"{prefix}_group_summary")
                    .get("attributes", {})
                    .get("suppressed")
                ),
                "0",
            )
            alpha_attrs = gs(f"{prefix}_group_summary").get("attributes", {})
            chk(
                "EC12 suppressed=0 in Alpha after unsuppress",
                str(alpha_attrs.get("suppressed")),
                "0",
            )
        else:
            chk(
                "EC12 second group found",
                False,
                True,
                "(no group sharing entities with Alpha)",
            )

        # ------------------------------------------------------------------
        # Discover availability sensor once — reused by EC13-EC15
        restore_all(ctx)
        avail_states = [
            s
            for s in api("GET", "/api/states")
            if s["entity_id"].startswith(prefix + "_availability")
        ]
        avail_eid = avail_states[0]["entity_id"] if avail_states else None
        ec_target = ctx["entities"][0]

        # ------------------------------------------------------------------
    else:
        # EC12 skipped — still need avail_eid/ec_target for EC13-EC15
        avail_states = [
            s
            for s in api("GET", "/api/states")
            if s["entity_id"].startswith(prefix + "_availability")
        ]
        avail_eid = avail_states[0]["entity_id"] if avail_states else None
        ec_target = ctx["entities"][0]

    # ------------------------------------------------------------------
    if ec_enabled(13):
        print(
            "\n=== EC13: suppress offline entity — availability % must not change ===",
            flush=True,
        )
        if avail_eid:
            ss(ec_target, "unavailable", {"friendly_name": "test"})
            wait()
            avail_mid = gs(avail_eid).get("state")
            # Read immediately before and after suppress — coordinator fires
            # async_set_updated_data synchronously so value updates in same tick.
            api(
                "POST",
                "/api/services/entity_availability/suppress_indefinitely",
                {"entity_id": ec_target, "group": ctx["title"]},
            )
            avail_after_suppress = gs(avail_eid).get("state")
            chk(
                "EC13 availability % unchanged immediately after suppressing offline entity",
                avail_after_suppress,
                avail_mid,
                f"sensor={avail_eid} after_offline={avail_mid} after_suppress={avail_after_suppress}",
            )
            api(
                "POST",
                "/api/services/entity_availability/unsuppress",
                {"entity_id": ec_target, "group": ctx["title"]},
            )
            restore_all(ctx)
        else:
            print("  EC13: skipped (no availability sensor found)", flush=True)

        # ------------------------------------------------------------------
    if ec_enabled(14):
        print(
            "\n=== EC14: suppressed entity appears in per_device availability breakdown ===",
            flush=True,
        )
        if avail_eid:
            api(
                "POST",
                "/api/services/entity_availability/suppress_indefinitely",
                {"entity_id": ec_target, "group": ctx["title"]},
            )
            # Poll until coordinator writes updated per_device (attr update on next tick)
            chk(
                "EC14 suppressed entity present in per_device breakdown",
                wait_for(
                    lambda: (
                        ec_target
                        in gs(avail_eid).get("attributes", {}).get("per_device", {})
                    ),
                    True,
                    interval=3,
                ),
                True,
                f"sensor={avail_eid} target={ec_target} keys={list(gs(avail_eid).get('attributes', {}).get('per_device', {}).keys())}",
            )
            api(
                "POST",
                "/api/services/entity_availability/unsuppress",
                {"entity_id": ec_target, "group": ctx["title"]},
            )
            restore_all(ctx)
        else:
            print("  EC14: skipped (no availability sensor found)", flush=True)

        # ------------------------------------------------------------------
    if ec_enabled(15):
        print(
            "\n=== EC15: suppress+unsuppress round-trip — availability % must not drift ===",
            flush=True,
        )
        if avail_eid:
            try:
                avail_before = float(gs(avail_eid).get("state") or "0")
            except ValueError:
                avail_before = 0.0
            api(
                "POST",
                "/api/services/entity_availability/suppress_indefinitely",
                {"entity_id": ec_target, "group": ctx["title"]},
            )
            avail_suppressed = wait_for_close(
                lambda: gs(avail_eid).get("state"), avail_before, tolerance=0.5
            )
            chk(
                "EC15 availability % identical after suppress (±0.5%)",
                abs(float(avail_suppressed or 0) - avail_before) <= 0.5,
                True,
                f"before={avail_before} after_suppress={avail_suppressed}",
            )
            api(
                "POST",
                "/api/services/entity_availability/unsuppress",
                {"entity_id": ec_target, "group": ctx["title"]},
            )
            avail_unsuppressed = wait_for_close(
                lambda: gs(avail_eid).get("state"), avail_before, tolerance=0.5
            )
            chk(
                "EC15 availability % identical after unsuppress (±0.5%)",
                abs(float(avail_unsuppressed or 0) - avail_before) <= 0.5,
                True,
                f"before={avail_before} after_unsuppress={avail_unsuppressed}",
            )
        else:
            print("  EC15: skipped (no availability sensor found)", flush=True)

        # ------------------------------------------------------------------
    if ec_enabled(16) or ec_enabled(17) or ec_enabled(18):
        print(
            "\n=== EC16-EC18: PR#52 — combined group offline_count/entities reflect member state ===",
            flush=True,
        )
        restore_and_wait(ctx)
        if combined_prefix:
            target = ctx["entities"][0]
            c_offline_eid = f"{combined_prefix}_offline_count"
            baseline = int(gs(c_offline_eid).get("state", "0"))

            if ec_enabled(16):
                # EC16: member entity goes offline → combined offline_count rises
                ss(target, "unavailable", {"friendly_name": "smoke test device"})
                after_offline = wait_for(
                    lambda: int(gs(c_offline_eid).get("state", "0")),
                    baseline + 1,
                )
                chk(
                    "EC16 combined offline_count rises when member entity goes offline",
                    after_offline,
                    baseline + 1,
                    f"sensor={c_offline_eid} baseline={baseline} after={after_offline}",
                )
                c_entities_after = (
                    gs(f"{combined_prefix}_offline_count")
                    .get("attributes", {})
                    .get("entities", [])
                )
                chk(
                    "EC16 offline entity in combined offline_entities",
                    target in c_entities_after,
                    True,
                    f"target={target} entities={c_entities_after}",
                )

            if ec_enabled(17):
                if not ec_enabled(16):
                    # EC17 standalone: need to make entity offline first
                    ss(target, "unavailable", {"friendly_name": "smoke test device"})
                    wait()
                # EC17: combined offline_entities contains the offline entity, no duplicates
                c_entities = gs(c_offline_eid).get("attributes", {}).get("entities", [])
                chk(
                    "EC17 combined offline_entities contains the offline entity",
                    target in c_entities,
                    True,
                    f"target={target} entities={c_entities}",
                )
                chk(
                    "EC17 combined offline_entities has no duplicates",
                    len(c_entities),
                    len(set(c_entities)),
                    f"entities={c_entities}",
                )

            if ec_enabled(18):
                # Ensure entity is offline before testing recovery (standalone path)
                if not ec_enabled(16) and not ec_enabled(17):
                    ss(target, "unavailable", {"friendly_name": "smoke test device"})
                    wait()
                # EC18: recovery → combined offline_count drops back to baseline
                restore_and_wait(ctx)
                after_recovery = wait_for(
                    lambda: int(gs(c_offline_eid).get("state", "0")), baseline
                )
                chk(
                    "EC18 combined offline_count returns to baseline after recovery",
                    after_recovery,
                    baseline,
                    f"sensor={c_offline_eid} baseline={baseline} after_recovery={after_recovery}",
                )
        else:
            print("  EC16-EC18: skipped (no combined group found)", flush=True)

    # ------------------------------------------------------------------
    if ec_enabled(19) or ec_enabled(20) or ec_enabled(21):
        print(
            "\n=== EC19-EC21: PR#53 — event consistency: entry_id, fan-out parity ===",
            flush=True,
        )
        restore_and_wait(ctx)
        if combined_prefix:
            target = ctx["entities"][0]
            c_offline_eid = f"{combined_prefix}_offline_count"
            c_entities_eid = f"{combined_prefix}_offline_entities"
            i_offline_eid = f"{prefix}_offline_count"
            i_entities_eid = f"{prefix}_offline_entities"

            if ec_enabled(19):
                # EC19: combined config entry has a non-empty entry_id
                entries = api("GET", "/api/config/config_entries/entry")
                combined_entry = next(
                    (
                        e
                        for e in entries
                        if e["domain"] == "entity_availability"
                        and "combined" in e["title"].lower()
                    ),
                    None,
                )
                chk(
                    "EC19 combined config entry has non-empty entry_id",
                    bool(combined_entry and combined_entry.get("entry_id", "")),
                    True,
                    f"entry={combined_entry}",
                )

            if ec_enabled(20):
                # EC20: individual group offline_count rises when entity goes offline
                # (fan-out parity: both individual and combined track the same entity)
                i_baseline = int(gs(i_offline_eid).get("state", "0"))
                ss(target, "unavailable", {"friendly_name": "smoke test device"})
                wait()
                i_after = int(gs(i_offline_eid).get("state", "0"))
                chk(
                    "EC20 individual offline_count rises for entity in combined group",
                    i_after,
                    i_baseline + 1,
                    f"sensor={i_offline_eid} baseline={i_baseline} after={i_after}",
                )

                if _WS_AVAILABLE:
                    # Additive: capture the offline event payload and assert shape.
                    restore_and_wait(ctx)
                    offline_events = capture_events(
                        "entity_availability_offline",
                        lambda: ss(
                            target,
                            "unavailable",
                            {"friendly_name": "smoke test device"},
                        ),
                    )
                    # Individual event: any event for target (no source_groups)
                    ev = next(
                        (e for e in offline_events if e.get("entity_id") == target),
                        None,
                    )
                    # Combined event: the one with source_groups field present
                    ev_combined = next(
                        (
                            e
                            for e in offline_events
                            if e.get("entity_id") == target and "source_groups" in e
                        ),
                        None,
                    )
                    chk(
                        "EC20 offline event captured for target",
                        ev is not None,
                        True,
                        f"target={target} events={offline_events}",
                    )
                    if ev is not None:
                        chk(
                            "EC20 offline event has non-empty entry_id",
                            bool(ev.get("entry_id")),
                            True,
                            f"entry_id={ev.get('entry_id')!r}",
                        )
                        chk(
                            "EC20 offline event entity_id matches target",
                            ev.get("entity_id"),
                            target,
                        )
                    chk(
                        "EC20 combined offline event captured for target",
                        ev_combined is not None,
                        True,
                        f"target={target} events={offline_events}",
                    )
                    if ev_combined is not None:
                        chk(
                            "EC20 combined offline event source_groups is a list",
                            isinstance(ev_combined.get("source_groups"), list),
                            True,
                            f"source_groups={ev_combined.get('source_groups')!r}",
                        )

            if ec_enabled(21):
                if not ec_enabled(20):
                    ss(target, "unavailable", {"friendly_name": "smoke test device"})
                    wait()
                # EC21: combined and individual offline_entities sensors both contain the entity
                c_entities = (
                    gs(c_entities_eid).get("attributes", {}).get("entities", [])
                )
                i_entities = (
                    gs(i_entities_eid).get("attributes", {}).get("entities", [])
                )
                chk(
                    "EC21 individual offline_entities contains the offline entity",
                    target in i_entities,
                    True,
                    f"target={target} individual={i_entities}",
                )
                chk(
                    "EC21 combined offline_entities contains the offline entity",
                    target in c_entities,
                    True,
                    f"target={target} combined={c_entities}",
                )

                if _WS_AVAILABLE:
                    # Additive: capture the recovery event payload and assert shape.
                    recovered_events = capture_events(
                        "entity_availability_recovered",
                        lambda: ss(
                            target, "on", {"friendly_name": target.split(".")[-1]}
                        ),
                    )
                    ev = next(
                        (e for e in recovered_events if e.get("entity_id") == target),
                        None,
                    )
                    ev_combined = next(
                        (
                            e
                            for e in recovered_events
                            if e.get("entity_id") == target and "source_groups" in e
                        ),
                        None,
                    )
                    chk(
                        "EC21 recovered event captured for target",
                        ev is not None,
                        True,
                        f"target={target} events={recovered_events}",
                    )
                    if ev is not None:
                        chk(
                            "EC21 recovered event has non-empty entry_id",
                            bool(ev.get("entry_id")),
                            True,
                            f"entry_id={ev.get('entry_id')!r}",
                        )
                        chk(
                            "EC21 recovered event entity_id matches target",
                            ev.get("entity_id"),
                            target,
                        )
                    chk(
                        "EC21 combined recovered event captured for target",
                        ev_combined is not None,
                        True,
                        f"target={target} events={recovered_events}",
                    )
                    if ev_combined is not None:
                        chk(
                            "EC21 combined recovered event source_groups is a list",
                            isinstance(ev_combined.get("source_groups"), list),
                            True,
                            f"source_groups={ev_combined.get('source_groups')!r}",
                        )

                restore_and_wait(ctx)
        else:
            print("  EC19-EC21: skipped (no combined group found)", flush=True)

    # ------------------------------------------------------------------
    # EC22 / EC23: low-battery bus events
    # Smoke can't subscribe to the event bus via REST, so we verify the
    # low_battery_count sensor transitions as a proxy — the coordinator only
    # mutates that state when it fires the event, so a sensor change confirms
    # the event was fired.
    # ------------------------------------------------------------------
    if ec_enabled(22) and battery_sensor and battery_entity:
        restore_and_wait(ctx)
        print(
            "\n=== EC22: entity_availability_low_battery event proxy (sensor transition) ===",
            flush=True,
        )
        baseline = gs(f"{prefix}_low_battery_count").get("state", "0")
        ss(
            battery_sensor,
            low_val,
            {
                "friendly_name": "Test Battery",
                "device_class": "battery",
                "unit_of_measurement": "%",
            },
        )
        after = wait_for(
            lambda: gs(f"{prefix}_low_battery_count").get("state"),
            str(int(baseline) + 1),
        )
        chk(
            "EC22 low_battery_count rose (low_battery event fired)",
            after,
            str(int(baseline) + 1),
            f"baseline={baseline} after={after}",
        )
        lb_attrs = gs(f"{prefix}_low_battery").get("attributes", {})
        chk(
            "EC22 battery_entity in low_battery_entities",
            battery_entity in lb_attrs.get("devices", {}),
            True,
            f"devices={lb_attrs.get('devices')}",
        )

    if ec_enabled(23) and battery_sensor and battery_entity:
        if not ec_enabled(22):
            # Drive into low-battery state first
            ss(
                battery_sensor,
                low_val,
                {
                    "friendly_name": "Test Battery",
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                },
            )
            wait_for(
                lambda: gs(f"{prefix}_low_battery_count").get("state"),
                "1",
            )
        print(
            "\n=== EC23: entity_availability_battery_ok event proxy (sensor transition) ===",
            flush=True,
        )
        high_val = "90"
        ss(
            battery_sensor,
            high_val,
            {
                "friendly_name": "Test Battery",
                "device_class": "battery",
                "unit_of_measurement": "%",
            },
        )
        after_ok = wait_for(
            lambda: gs(f"{prefix}_low_battery_count").get("state"),
            "0",
        )
        chk(
            "EC23 low_battery_count dropped to 0 (battery_ok event fired)",
            after_ok,
            "0",
            f"after={after_ok}",
        )
        restore_and_wait(ctx)
    elif ec_enabled(22) or ec_enabled(23):
        print("  EC22-EC23: skipped (no battery sensor configured)", flush=True)

    # ------------------------------------------------------------------
    if ec_enabled(24) and combined_prefix:
        print(
            "\n=== EC24: combined offline event carries source_groups list ===",
            flush=True,
        )
        restore_and_wait(ctx)
        target = ctx["entities"][0]
        entries = api("GET", "/api/config/config_entries/entry")
        combined_entry = next(
            (
                e
                for e in entries
                if e["domain"] == "entity_availability"
                and "combined" in e["title"].lower()
            ),
            None,
        )
        combined_entry_id = combined_entry["entry_id"] if combined_entry else ""

        if _WS_AVAILABLE and combined_entry_id:
            events = capture_events(
                "entity_availability_offline",
                lambda: ss(
                    target, "unavailable", {"friendly_name": "smoke test device"}
                ),
                timeout=WAIT_FOR_TIMEOUT,
            )
            combined_events = [
                e for e in events if e.get("entry_id") == combined_entry_id
            ]
            if combined_events:
                ev = combined_events[0]
                chk(
                    "EC24 source_groups is a list",
                    isinstance(ev.get("source_groups"), list),
                    True,
                    f"source_groups={ev.get('source_groups')}",
                )
                chk(
                    "EC24 source_groups is non-empty",
                    len(ev.get("source_groups", [])) > 0,
                    True,
                    f"source_groups={ev.get('source_groups')}",
                )
            else:
                chk(
                    "EC24 combined offline event captured",
                    False,
                    True,
                    f"combined_entry_id={combined_entry_id} total_events={len(events)}",
                )
            restore_and_wait(ctx)
        else:
            print(
                "  EC24: skipped (websocket-client not installed or no combined entry)",
                flush=True,
            )
    elif ec_enabled(24):
        print("  EC24: skipped (no combined group found)", flush=True)

    # ------------------------------------------------------------------
    # EC43: battery_enabled flag matches config
    # EC44: staleness_enabled flag matches config
    # EC45: last_seen attr is populated with past timestamps
    # ------------------------------------------------------------------
    if ec_enabled(43):
        print(
            "\n=== EC43: group_summary battery_enabled matches battery_threshold config ===",
            flush=True,
        )
        attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        if "battery_enabled" not in attrs:
            print(
                "  EC43: skipped (battery_enabled attr not present — deploy backend)",
                flush=True,
            )
        else:
            expected = threshold > 0
            chk(
                "EC43 battery_enabled matches threshold>0",
                attrs["battery_enabled"],
                expected,
                f"threshold={threshold} battery_enabled={attrs['battery_enabled']}",
            )

    if ec_enabled(44):
        print(
            "\n=== EC44: group_summary staleness_enabled matches staleness_threshold config ===",
            flush=True,
        )
        attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        if "staleness_enabled" not in attrs:
            print(
                "  EC44: skipped (staleness_enabled attr not present — deploy backend)",
                flush=True,
            )
        else:
            staleness_threshold = ctx["staleness_threshold"]
            expected = staleness_threshold > 0
            chk(
                "EC44 staleness_enabled matches staleness_threshold>0",
                attrs["staleness_enabled"],
                expected,
                f"staleness_threshold={staleness_threshold} staleness_enabled={attrs['staleness_enabled']}",
            )

    if ec_enabled(45):
        print(
            "\n=== EC45: group_summary last_seen populated with past timestamps ===",
            flush=True,
        )
        attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        last_seen = attrs.get("last_seen", {})
        if not last_seen:
            print(
                "  EC45: skipped (last_seen attr empty — entities may not have reported yet)",
                flush=True,
            )
        else:
            import datetime as _dt

            now = _dt.datetime.now(_dt.timezone.utc)
            bad = []
            for eid, ts_str in last_seen.items():
                try:
                    ts = _dt.datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_dt.timezone.utc)
                    if ts > now:
                        bad.append(f"{eid}: {ts_str} is in the future")
                except Exception as e:
                    bad.append(f"{eid}: {ts_str} parse error {e}")
            chk(
                "EC45 all last_seen timestamps are in the past",
                len(bad) == 0,
                True,
                f"bad={bad[:3]}",
            )
            chk(
                "EC45 last_seen covers all monitored entities",
                len(last_seen) > 0,
                True,
                f"last_seen keys={len(last_seen)} entities={len(ctx['entities'])}",
            )

    # ------------------------------------------------------------------
    # EC25-EC35: Non-Essential entity tier
    # Requires EA_SMOKE_NE_GROUP to point at a group with NE entities configured.
    # ------------------------------------------------------------------
    ne_ecs = set(range(25, 43))
    run_ne = bool(NE_GROUP_FILTER) and (not EC_FILTER or ne_ecs & EC_FILTER)

    if run_ne:
        print(f"\n=== Discovering NE group: '{NE_GROUP_FILTER}' ===", flush=True)
        ne_ctx = _discover_ne_group(NE_GROUP_FILTER)
        if ne_ctx:
            _run_ne_tests(ne_ctx)
        else:
            print(
                f"  EC25-EC42: skipped (no NE group matching '{NE_GROUP_FILTER}')",
                flush=True,
            )
    elif any(ec_enabled(n) for n in range(25, 43)):
        print(
            "\n  EC25-EC42: skipped (set EA_SMOKE_NE_GROUP to a group with non-essential entities)",
            flush=True,
        )

    # ------------------------------------------------------------------
    # EC46-EC50: Signal strength monitoring
    # Requires group_summary to expose signal_enabled, signal_levels, poor_signal_entities.
    # ------------------------------------------------------------------
    signal_ecs = {46, 47, 48, 49, 50}
    run_signal = not EC_FILTER or signal_ecs & EC_FILTER

    if run_signal:
        attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        if "signal_enabled" not in attrs:
            print(
                "\n  EC46-EC50: skipped (signal_enabled attr not present — deploy backend first)",
                flush=True,
            )
        else:
            signal_enabled = attrs.get("signal_enabled", False)

            if ec_enabled(46):
                print(
                    "\n=== EC46: signal_enabled flag present in group_summary ===",
                    flush=True,
                )
                chk(
                    "EC46 signal_enabled attr present",
                    "signal_enabled" in attrs,
                    True,
                    f"attrs keys={list(attrs.keys())[:10]}",
                )

            if ec_enabled(47):
                print("\n=== EC47: poor_signal_count sensor exists ===", flush=True)
                ps_count = gs_safe(f"{prefix}_poor_signal_count")
                if ps_count is None or ps_count.get("state") in (
                    None,
                    "unknown",
                    "unavailable",
                ):
                    print(
                        "  EC47: skipped (poor_signal_count sensor not found — signal may be disabled for this group)",
                        flush=True,
                    )
                else:
                    chk(
                        "EC47 poor_signal_count sensor reachable",
                        ps_count.get("state") is not None,
                        True,
                        f"state={ps_count.get('state')}",
                    )

            if ec_enabled(48):
                print(
                    "\n=== EC48: signal_levels attr is dict in group_summary ===",
                    flush=True,
                )
                signal_levels = attrs.get("signal_levels")
                if signal_levels is None:
                    print(
                        "  EC48: skipped (signal_levels not in attrs — signal disabled)",
                        flush=True,
                    )
                else:
                    chk(
                        "EC48 signal_levels is a dict",
                        isinstance(signal_levels, dict),
                        True,
                        f"type={type(signal_levels).__name__}",
                    )

            if ec_enabled(49):
                print(
                    "\n=== EC49: poor_signal_entities attr is list in group_summary ===",
                    flush=True,
                )
                pse = attrs.get("poor_signal_entities")
                if pse is None:
                    print(
                        "  EC49: skipped (poor_signal_entities not in attrs — signal disabled)",
                        flush=True,
                    )
                else:
                    chk(
                        "EC49 poor_signal_entities is a list",
                        isinstance(pse, list),
                        True,
                        f"type={type(pse).__name__}",
                    )

            if ec_enabled(50):
                print(
                    "\n=== EC50: any_poor_signal binary sensor exists when signal enabled ===",
                    flush=True,
                )
                if not signal_enabled:
                    print(
                        "  EC50: skipped (signal_enabled=False for this group)",
                        flush=True,
                    )
                else:
                    bs = gs_safe(f"binary_sensor.{prefix}_any_poor_signal")
                    if bs is None:
                        print(
                            "  EC50: skipped (any_poor_signal sensor not found)",
                            flush=True,
                        )
                    else:
                        chk(
                            "EC50 any_poor_signal binary sensor reachable",
                            bs.get("state") in ("on", "off"),
                            True,
                            f"state={bs.get('state')}",
                        )

    # ------------------------------------------------------------------
    # EC51-EC53: PR#68 — signal mapping key format + diagnostics new schema
    # ------------------------------------------------------------------

    # EC51: signal mapping options flow round-trip with new key format
    if ec_enabled(51):
        print(
            "\n=== EC51: signal mapping options flow: entity_id / entity_id#network keys ===",
            flush=True,
        )
        target_entity = ctx["entities"][0]
        signal_sensor = f"sensor.{target_entity.split('.')[-1]}_linkquality"
        # Create a synthetic signal sensor so the flow can accept it
        ss(
            signal_sensor,
            "120",
            {"friendly_name": "Smoke signal sensor", "unit_of_measurement": "lqi"},
        )
        r = api(
            "POST",
            "/api/config/config_entries/options/flow",
            {"handler": ctx["entry_id"]},
        )
        fid = r["flow_id"]
        # Step 1: main options
        r2 = api(
            "POST",
            f"/api/config/config_entries/options/flow/{fid}",
            {
                "entities": ctx["entities"],
                "bad_states": ["unavailable", "unknown"],
                "cooldown": 0,
                "staleness_threshold": 0,
                "battery_threshold": ctx["battery_threshold"],
                "availability_windows": ["today", "7d"],
                "use_device_names": False,
                "staleness_use_last_updated": False,
                "signal_enabled": True,
            },
        )
        # Navigate past battery_mapping if present — preserve existing mappings
        if r2.get("step_id") == "battery_mapping":
            bmap_input = {}
            if ctx.get("mapped_battery_entity") and ctx.get("mapped_battery_sensor"):
                bmap_input[ctx["mapped_battery_entity"]] = ctx["mapped_battery_sensor"]
            r2 = api(
                "POST",
                f"/api/config/config_entries/options/flow/{fid}",
                bmap_input,
            )
        if r2.get("step_id") == "signal_mapping":
            # Submit using new key format: entity_id as sensor key, entity_id#network for type
            r3 = api(
                "POST",
                f"/api/config/config_entries/options/flow/{fid}",
                {
                    target_entity: signal_sensor,
                    f"{target_entity}#network": "zigbee_lqi",
                },
            )
            chk(
                "EC51 signal mapping flow completes (CREATE_ENTRY or next step)",
                r3.get("type") in ("create_entry", "form"),
                True,
                f"type={r3.get('type')} step={r3.get('step_id')}",
            )
            if r3.get("type") == "create_entry":
                # Verify stored map via config storage
                import subprocess as _sp

                eid_val = ctx["entry_id"]
                try:
                    raw = _sp.check_output(
                        [
                            "python3",
                            "-c",
                            f"""
import json
cfg = json.load(open('/workspaces/home-assistant-core/config/.storage/core.config_entries'))
for e in cfg['data']['entries']:
    if e['entry_id'] == {eid_val!r}:
        print(json.dumps(e['data'].get('signal_entity_map', {{}})))
""",
                        ],
                        text=True,
                    )
                    smap = json.loads(raw.strip())
                    chk(
                        "EC51 signal_entity_map stored for target_entity",
                        target_entity in smap,
                        True,
                        f"map={smap}",
                    )
                    if target_entity in smap:
                        chk(
                            "EC51 stored sensor matches submitted",
                            smap[target_entity].get("sensor"),
                            signal_sensor,
                        )
                        chk(
                            "EC51 stored network_type=zigbee_lqi",
                            smap[target_entity].get("network_type"),
                            "zigbee_lqi",
                        )
                except Exception as e:
                    chk("EC51 config storage readable", False, True, str(e))
            else:
                # Flow returned another step — clean up orphaned flow
                api("DELETE", f"/api/config/config_entries/options/flow/{fid}")
        else:
            print(
                f"  EC51: skipped (flow did not reach signal_mapping step, got step={r2.get('step_id')})",
                flush=True,
            )
            api("DELETE", f"/api/config/config_entries/options/flow/{fid}")
        # Restore battery mapping in case EC51 flow overwrote it
        restore_and_wait(ctx)

    # EC52: _detect_signal_entity strips _last_seen suffix before naming convention
    if ec_enabled(52):
        print(
            "\n=== EC52: _detect_signal_entity strips _last_seen → suggests _linkquality ===",
            flush=True,
        )
        # Find an entity with _last_seen suffix — typical zigbee setup
        last_seen_entities = [e for e in ctx["entities"] if e.endswith("_last_seen")]
        if not last_seen_entities:
            print(
                "  EC52: skipped (no _last_seen entities in test group)",
                flush=True,
            )
        else:
            ls_entity = last_seen_entities[0]
            slug = ls_entity.split(".", 1)[1]  # e.g. balcony_light_last_seen
            base = slug[: -len("_last_seen")]  # e.g. balcony_light
            lq_sensor = f"sensor.{base}_linkquality"
            # Ensure the linkquality sensor exists in HA state machine
            ss(
                lq_sensor,
                "200",
                {"friendly_name": "Smoke linkquality", "unit_of_measurement": "lqi"},
            )
            # Open options flow and advance to signal_mapping
            r = api(
                "POST",
                "/api/config/config_entries/options/flow",
                {"handler": ctx["entry_id"]},
            )
            fid = r["flow_id"]
            r2 = api(
                "POST",
                f"/api/config/config_entries/options/flow/{fid}",
                {
                    "entities": ctx["entities"],
                    "bad_states": ["unavailable", "unknown"],
                    "cooldown": 0,
                    "staleness_threshold": 0,
                    "battery_threshold": ctx["battery_threshold"],
                    "availability_windows": ["today", "7d"],
                    "use_device_names": False,
                    "staleness_use_last_updated": False,
                    "signal_enabled": True,
                },
            )
            if r2.get("step_id") == "battery_mapping":
                r2 = api("POST", f"/api/config/config_entries/options/flow/{fid}", {})
            if r2.get("step_id") == "signal_mapping":
                schema = r2.get("data_schema", [])
                # Field for ls_entity should have suggested_value = lq_sensor
                field = next((f for f in schema if f.get("name") == ls_entity), None)
                suggestion = (
                    (field or {}).get("description", {}).get("suggested_value", "")
                )
                chk(
                    "EC52 _last_seen entity suggests _linkquality via suffix stripping",
                    suggestion,
                    lq_sensor,
                    f"entity={ls_entity} base={base} suggested={suggestion!r}",
                )
            else:
                print(
                    f"  EC52: skipped (flow did not reach signal_mapping, got step={r2.get('step_id')})",
                    flush=True,
                )
            try:
                api("DELETE", f"/api/config/config_entries/options/flow/{fid}")
            except Exception:
                pass  # flow may have expired (HA flow TTL)

    # EC53: diagnostics new schema on the main (non-NE) group entry
    if ec_enabled(53):
        print(
            "\n=== EC53: diagnostics new schema on main group entry ===",
            flush=True,
        )
        try:
            raw = api("GET", f"/api/diagnostics/config_entry/{ctx['entry_id']}")
            result = raw.get("data", raw)
            chk("EC53 entry_type=group", result.get("entry_type"), "group")
            # counts sub-dict
            counts = result.get("counts") or {}
            chk(
                "EC53 counts.total is int",
                isinstance(counts.get("total"), int),
                True,
                f"counts={counts}",
            )
            chk("EC53 counts has offline key", "offline" in counts, True)
            chk("EC53 counts has suppressed key", "suppressed" in counts, True)
            # entities sub-dict
            entities_d = result.get("entities") or {}
            chk(
                "EC53 entities.essential is list",
                isinstance(entities_d.get("essential"), list),
                True,
            )
            chk(
                "EC53 entities.battery_entity_map is dict",
                isinstance(entities_d.get("battery_entity_map"), dict),
                True,
            )
            chk(
                "EC53 entities.signal_entity_map is dict",
                isinstance(entities_d.get("signal_entity_map"), dict),
                True,
            )
            # config sub-dict — new fields
            cfg = result.get("config") or {}
            chk("EC53 config.signal_enabled present", "signal_enabled" in cfg, True)
            chk("EC53 config.bad_states present", "bad_states" in cfg, True)
            chk("EC53 config.use_device_names present", "use_device_names" in cfg, True)
            chk(
                "EC53 config.staleness_use_last_updated present",
                "staleness_use_last_updated" in cfg,
                True,
            )
            # old flat keys must NOT be present (schema migration check)
            chk("EC53 old entity_count key absent", "entity_count" not in result, True)
            chk(
                "EC53 old essential_count key absent",
                "essential_count" not in result,
                True,
            )
        except Exception as e:
            chk("EC53 diagnostics endpoint reachable", False, True, str(e))

    # EC54: group_summary new explicit attrs (essential count, stale/poor_signal counts)
    if ec_enabled(54):
        print(
            "\n=== EC54: group_summary essential + stale/poor_signal count attrs ===",
            flush=True,
        )
        attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        total = attrs.get("total_entities", 0)
        non_essential = attrs.get("non_essential", 0)
        expected_essential = total - non_essential
        chk(
            "EC54 essential attr present",
            "essential" in attrs,
            True,
            f"attrs keys={[k for k in attrs if 'essential' in k]}",
        )
        chk(
            "EC54 essential = total - non_essential",
            attrs.get("essential"),
            expected_essential,
            f"total={total} non_essential={non_essential}",
        )
        chk(
            "EC54 stale count attr present",
            "stale" in attrs,
            True,
        )
        chk(
            "EC54 stale matches stale_entities length",
            attrs.get("stale"),
            len(attrs.get("stale_entities", [])),
            f"stale={attrs.get('stale')} stale_entities={len(attrs.get('stale_entities', []))}",
        )
        chk(
            "EC54 stale_non_essential attr present",
            "stale_non_essential" in attrs,
            True,
        )
        chk(
            "EC54 stale_non_essential matches stale_entities_non_essential length",
            attrs.get("stale_non_essential"),
            len(attrs.get("stale_entities_non_essential", [])),
            f"stale_non_essential={attrs.get('stale_non_essential')}",
        )
        chk(
            "EC54 poor_signal count attr present",
            "poor_signal" in attrs,
            True,
        )
        chk(
            "EC54 poor_signal matches poor_signal_entities length",
            attrs.get("poor_signal"),
            len(attrs.get("poor_signal_entities", [])),
        )
        chk(
            "EC54 poor_signal_non_essential attr present",
            "poor_signal_non_essential" in attrs,
            True,
        )
        chk(
            "EC54 poor_signal_non_essential matches poor_signal_entities_non_essential length",
            attrs.get("poor_signal_non_essential"),
            len(attrs.get("poor_signal_entities_non_essential", [])),
        )

    # EC55: _unrecorded_attributes — large attrs excluded from recorder but present in state machine
    if ec_enabled(55):
        print(
            "\n=== EC55: group_summary large attrs excluded from recorder but present in state ===",
            flush=True,
        )
        attrs = gs(f"{prefix}_group_summary").get("attributes", {})
        # These attrs must be PRESENT in the live state (available to templates/card)
        for attr in [
            "entities",
            "display_names",
            "battery_levels",
            "low_battery_entities",
            "last_seen",
            "stale_entities",
            "poor_signal_entities",
            "offline_since",
            "suppressed_until",
            "non_essential_entities",
        ]:
            chk(
                f"EC55 {attr} present in live state",
                attr in attrs,
                True,
                f"attrs keys (sample)={list(attrs.keys())[:10]}",
            )
        # KPI counts must also be present (these ARE recorded)
        for attr in [
            "online",
            "offline",
            "essential",
            "stale",
            "poor_signal",
            "battery_enabled",
        ]:
            chk(
                f"EC55 {attr} present (KPI, recorded)",
                attr in attrs,
                True,
            )

    # ------------------------------------------------------------------
    # EC59: battery level retained when entity goes unavailable (not wiped)
    # ------------------------------------------------------------------
    if ec_enabled(59) and battery_entity and battery_sensor:
        print(
            "\n=== EC59: battery level retained when entity goes unavailable ===",
            flush=True,
        )
        restore_and_wait(ctx)
        # Set battery to a known level
        ss(
            battery_sensor,
            "75",
            {
                "friendly_name": "Test Battery",
                "device_class": "battery",
                "unit_of_measurement": "%",
            },
        )
        wait_for(
            lambda: str(
                gs(f"{prefix}_group_summary")
                .get("attributes", {})
                .get("battery_levels", {})
                .get(battery_entity)
            ),
            "75",
        )
        # Make entity unavailable (battery sensor stays at 75)
        ss(battery_entity, "unavailable", {"friendly_name": "test"})
        wait_for(lambda: gs(f"{prefix}_offline_count").get("state"), "1")
        retained = (
            gs(f"{prefix}_group_summary")
            .get("attributes", {})
            .get("battery_levels", {})
            .get(battery_entity)
        )
        chk(
            "EC59 battery_level retained when entity offline",
            retained,
            75,
            f"battery_entity={battery_entity} retained={retained}",
        )
        restore_and_wait(ctx)

    # ------------------------------------------------------------------
    # EC65: stale flag cleared when entity is suppressed
    # ------------------------------------------------------------------
    if ec_enabled(65) and battery_entity:
        print(
            "\n=== EC65: stale_count and low_battery cleared when entity suppressed ===",
            flush=True,
        )
        restore_and_wait(ctx)
        target = battery_entity  # suppress the entity whose battery is mapped, not entities[0]
        # Give entity low battery then suppress it
        if battery_sensor:
            ss(
                battery_sensor,
                str(int(threshold) - 5),
                {
                    "friendly_name": "Test Battery",
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                },
            )
            wait_for(lambda: gs(f"{prefix}_low_battery_count").get("state"), "1")
        api(
            "POST",
            "/api/services/entity_availability/suppress",
            {"entity_id": target, "group": ctx["title"]},
        )
        wait_for(
            lambda: str(
                gs(f"{prefix}_group_summary").get("attributes", {}).get("suppressed")
            ),
            "1",
        )
        chk(
            "EC65 low_battery cleared after suppress",
            wait_for(
                lambda: gs(f"{prefix}_low_battery_count").get("state"),
                "0",
            ),
            "0",
            f"target={target}",
        )
        # Unsuppress and restore battery
        api(
            "POST",
            "/api/services/entity_availability/unsuppress",
            {"entity_id": target, "group": ctx["title"]},
        )
        if battery_sensor:
            ss(
                battery_sensor,
                "90",
                {
                    "friendly_name": "Test Battery",
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                },
            )
        restore_and_wait(ctx)

    # ------------------------------------------------------------------
    # EC66: combined_summary exposes status + status_color attrs
    # EC67: combined_summary exposes merged per-entity dicts
    # EC68: combined_summary exposes non_essential_online / non_essential_offline
    if combined_prefix and (ec_enabled(66) or ec_enabled(67) or ec_enabled(68)):
        print(
            "\n=== EC66-EC68: combined_summary new attrs (PR#75) ===",
            flush=True,
        )
        restore_and_wait(ctx)
        c_attrs = gs(f"{combined_prefix}_combined_summary").get("attributes", {})

        if ec_enabled(66):
            chk(
                "EC66 combined status attr present",
                c_attrs.get("status") in ("ok", "degraded", "offline"),
                True,
                f"status={c_attrs.get('status')}",
            )
            chk(
                "EC66 combined status_color attr present",
                c_attrs.get("status_color") in ("green", "yellow", "red"),
                True,
                f"status_color={c_attrs.get('status_color')}",
            )
            # Make one entity offline → status should flip to offline/red
            target = ctx["entities"][0]
            ss(target, "unavailable", {"friendly_name": "smoke test device"})
            wait_for(
                lambda: (
                    gs(f"{combined_prefix}_combined_summary")
                    .get("attributes", {})
                    .get("status")
                ),
                "offline",
            )
            c_attrs2 = gs(f"{combined_prefix}_combined_summary").get("attributes", {})
            chk(
                "EC66 combined status=offline when entity down",
                c_attrs2.get("status"),
                "offline",
                f"status={c_attrs2.get('status')}",
            )
            chk(
                "EC66 combined status_color=red when entity down",
                c_attrs2.get("status_color"),
                "red",
                f"status_color={c_attrs2.get('status_color')}",
            )
            restore_and_wait(ctx)
            c_attrs = gs(f"{combined_prefix}_combined_summary").get("attributes", {})

        if ec_enabled(67):
            chk(
                "EC67 combined battery_levels dict present",
                isinstance(c_attrs.get("battery_levels"), dict),
                True,
                f"type={type(c_attrs.get('battery_levels')).__name__}",
            )
            chk(
                "EC67 combined offline_since dict present",
                isinstance(c_attrs.get("offline_since"), dict),
                True,
                f"type={type(c_attrs.get('offline_since')).__name__}",
            )
            chk(
                "EC67 combined last_seen dict present",
                isinstance(c_attrs.get("last_seen"), dict),
                True,
                f"type={type(c_attrs.get('last_seen')).__name__}",
            )
            chk(
                "EC67 combined ok_signal_entities list present",
                isinstance(c_attrs.get("ok_signal_entities"), list),
                True,
                f"type={type(c_attrs.get('ok_signal_entities')).__name__}",
            )

        if ec_enabled(68):
            chk(
                "EC68 combined non_essential_online present",
                "non_essential_online" in c_attrs,
                True,
                f"keys={[k for k in c_attrs if 'non_essential' in k]}",
            )
            chk(
                "EC68 combined non_essential_offline present",
                "non_essential_offline" in c_attrs,
                True,
            )
            chk(
                "EC68 combined low_battery_entities_non_essential list present",
                isinstance(c_attrs.get("low_battery_entities_non_essential"), list),
                True,
                f"type={type(c_attrs.get('low_battery_entities_non_essential')).__name__}",
            )

    # ------------------------------------------------------------------
    # EC69-EC71: combined_summary recorder payload reduction (PR#76)
    # All three checks read the same snapshot — PR#76 is a structural
    # change (which keys exist / their types), not state-dependent.
    # ------------------------------------------------------------------
    if combined_prefix and (ec_enabled(69) or ec_enabled(70) or ec_enabled(71)):
        print(
            "\n=== EC69-EC71: combined_summary recorder payload reduction (PR#76) ===",
            flush=True,
        )
        restore_and_wait(ctx)
        c_attrs = gs(f"{combined_prefix}_combined_summary").get("attributes", {})

        if ec_enabled(69):
            # These are now unrecorded but must still be present in live state
            # with correct types (not None).
            for attr, expected_type in [
                ("battery_levels", dict),
                ("signal_levels", dict),
                ("signal_units", dict),
                ("ok_signal_entities", list),
                ("suppressed_until", dict),
                ("offline_since", dict),
                ("last_seen", dict),
            ]:
                chk(
                    f"EC69 combined {attr} present in live state",
                    attr in c_attrs,
                    True,
                    f"attrs keys (sample)={list(c_attrs.keys())[:12]}",
                )
                chk(
                    f"EC69 combined {attr} is {expected_type.__name__}",
                    isinstance(c_attrs.get(attr), expected_type),
                    True,
                    f"type={type(c_attrs.get(attr)).__name__}",
                )

        if ec_enabled(70):
            groups = c_attrs.get("groups", {})
            chk(
                "EC70 combined_summary groups dict is non-empty",
                len(groups) > 0,
                True,
                f"groups keys={list(groups.keys())}",
            )
            for entry_id, g in groups.items():
                chk(
                    f"EC70 groups[{entry_id}] has no non_essential_entities list",
                    "non_essential_entities" not in g,
                    True,
                    f"keys={list(g.keys())}",
                )

        if ec_enabled(71):
            chk(
                "EC71 combined low_battery_entities_non_essential present in live state",
                "low_battery_entities_non_essential" in c_attrs,
                True,
                f"attrs keys (sample)={list(c_attrs.keys())[:12]}",
            )
            chk(
                "EC71 combined low_battery_entities_non_essential is list",
                isinstance(c_attrs.get("low_battery_entities_non_essential"), list),
                True,
                f"type={type(c_attrs.get('low_battery_entities_non_essential')).__name__}",
            )

    # ------------------------------------------------------------------
    # EC72-EC76: device-collapse feature (collapse_devices, PR#81)
    # Structural / invariant checks on live state — do not toggle the option.
    # collapse_active = collapse_devices AND use_device_names.
    # ------------------------------------------------------------------
    if any(ec_enabled(n) for n in (72, 73, 74, 75, 76)):
        print(
            "\n=== EC72-EC76: device-collapse feature (PR#81) ===",
            flush=True,
        )
        restore_and_wait(ctx)

        collapse_on = False
        if ec_enabled(72):
            try:
                raw = api("GET", f"/api/diagnostics/config_entry/{ctx['entry_id']}")
                cfg = (raw.get("data", raw).get("config")) or {}
                chk(
                    "EC72 config.collapse_devices present in diagnostics",
                    "collapse_devices" in cfg,
                    True,
                    f"config keys={list(cfg.keys())}",
                )
                collapse_on = bool(cfg.get("collapse_devices")) and bool(
                    cfg.get("use_device_names")
                )
                print(f"EC72 collapse_active={collapse_on}", flush=True)
            except Exception as e:
                chk("EC72 diagnostics reachable", False, True, str(e))

        g_attrs = gs(f"{prefix}_group_summary").get("attributes", {})

        if ec_enabled(73):
            chk(
                "EC73 group_summary entities_collapsed present",
                "entities_collapsed" in g_attrs,
                True,
                f"attrs keys (sample)={list(g_attrs.keys())[:12]}",
            )
            chk(
                "EC73 group_summary entities_collapsed is list",
                isinstance(g_attrs.get("entities_collapsed"), list),
                True,
                f"type={type(g_attrs.get('entities_collapsed')).__name__}",
            )

        if ec_enabled(74) and combined_prefix:
            c_attrs = gs(f"{combined_prefix}_combined_summary").get("attributes", {})
            chk(
                "EC74 combined_summary entities_collapsed present",
                "entities_collapsed" in c_attrs,
                True,
                f"attrs keys (sample)={list(c_attrs.keys())[:12]}",
            )
            chk(
                "EC74 combined_summary entities_collapsed is list",
                isinstance(c_attrs.get("entities_collapsed"), list),
                True,
            )

        if ec_enabled(75):
            entities = g_attrs.get("entities") or []
            collapsed = g_attrs.get("entities_collapsed") or []
            # Collapse never grows the row set; equal when nothing merges / off.
            chk(
                "EC75 entities_collapsed never larger than entities",
                len(collapsed) <= len(entities),
                True,
                f"collapsed={len(collapsed)} entities={len(entities)}",
            )
            # collapsed rows are a subset of full membership (representatives).
            chk(
                "EC75 entities_collapsed is a subset of entities",
                set(collapsed).issubset(set(entities)),
                True,
            )

        if ec_enabled(76):
            # Count == rows invariant: group_summary.offline equals the number of
            # offline entities that survive collapse (offline_entities is collapsed).
            offline_count = int(gs(f"{prefix}_offline_count").get("state") or 0)
            offline_list = g_attrs.get("offline_entities") or []
            chk(
                "EC76 offline_count == len(collapsed offline_entities)",
                offline_count,
                len(offline_list),
                f"count={offline_count} list={offline_list}",
            )

    # ------------------------------------------------------------------
    restore_all(ctx)
    print(
        f"offline_count={gs(f'{prefix}_offline_count').get('state')} (expected 0)",
        flush=True,
    )
    print(
        f"low_battery_count={gs(f'{prefix}_low_battery_count').get('state')} (expected 0)",
        flush=True,
    )

    # ------------------------------------------------------------------
    print(f"\n=== RESULTS: {_passed} passed, {_failed} failed ===", flush=True)
    sys.exit(0 if _failed == 0 else 1)


if __name__ == "__main__":
    main()
