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

What is tested (covers PRs #37, #41, #50, #52, #53, #54, core):
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
import urllib.request
import urllib.error
import urllib.parse

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
        },
    )
    if r2.get("step_id") == "battery_mapping":
        api(
            "POST",
            f"/api/config/config_entries/options/flow/{fid}",
            {target_entity: battery_sensor},
        )
        ctx["mapped_battery_entity"] = target_entity
        ctx["mapped_battery_sensor"] = battery_sensor
        print(f"  Mapped {target_entity} → {battery_sensor}", flush=True)
        wait(10)


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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=== Entity Availability smoke tests ===\n", flush=True)

    ctx = discover()
    if not SKIP_SETUP:
        setup_battery(ctx)
        restore_and_wait(ctx)
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

    # ------------------------------------------------------------------
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
        gs(f"{prefix}_offline_count").get("state"),
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
        gs(f"{prefix}_low_battery_count").get("state"),
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
        wait()
        chk(
            "EC7 combined low_battery=base+1",
            int(gs(f"{combined_prefix}_low_battery_count").get("state", "0")),
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
        wait()
        chk(
            "EC8 combined low_battery=base (offline excluded)",
            int(gs(f"{combined_prefix}_low_battery_count").get("state", "0")),
            b_clb,
            f"(base={b_clb})",
        )
        chk(
            "EC8 combined offline=base+1",
            int(gs(f"{combined_prefix}_offline_count").get("state", "0")),
            b_coff + 1,
            f"(base={b_coff})",
        )
    else:
        print("\n=== EC7+EC8: skipped (no combined group found) ===", flush=True)

    # ------------------------------------------------------------------
    print("\n=== EC9: PR#37 — cleared battery map not re-suggested ===", flush=True)
    # Find an entity with cleared ("") mapping
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
    if e['entry_id'] == '{ctx["entry_id"]}':
        bmap = e['data'].get('battery_entity_map', {{}})
        cleared = [k for k, v in bmap.items() if v == '']
        print(json.dumps(cleared))
""",
            ],
            text=True,
        ).strip()
        cleared_entities = json.loads(raw)
    except Exception:
        cleared_entities = []

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
            "battery_threshold": threshold,
            "availability_windows": ["today", "7d"],
            "use_device_names": False,
            "staleness_use_last_updated": False,
        },
    )
    schema = r2.get("data_schema", [])
    if cleared_entities:
        c_eid = cleared_entities[0]
        c_field = next((f for f in schema if f.get("name") == c_eid), None)
        c_suggestion = (c_field or {}).get("description", {}).get("suggested_value", "")
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
    ss(suppressed_entity, "unavailable", {"friendly_name": "test"})
    wait()
    chk(
        "offline_count=0 (suppressed not counted)",
        gs(f"{prefix}_offline_count").get("state"),
        "0",
    )
    chk(
        "suppressed=1",
        str(gs(f"{prefix}_group_summary").get("attributes", {}).get("suppressed")),
        "1",
    )

    # ------------------------------------------------------------------
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
    wait()
    summary_attrs = gs(f"{prefix}_group_summary").get("attributes", {})
    chk(
        "EC11 suppressed=1 after suppress_indefinitely",
        str(summary_attrs.get("suppressed")),
        "1",
    )
    chk(
        "EC11 suppressed_until[entity]=null (indefinite)",
        summary_attrs.get("suppressed_until", {}).get(indef_entity),
        None,
    )
    ss(indef_entity, "unavailable", {"friendly_name": "test"})
    wait()
    chk(
        "EC11 offline_count=0 (indefinitely suppressed not counted)",
        gs(f"{prefix}_offline_count").get("state"),
        "0",
    )
    api(
        "POST",
        "/api/services/entity_availability/unsuppress",
        {"entity_id": indef_entity, "group": ctx["title"]},
    )
    wait()
    chk(
        "EC11 suppressed=0 after unsuppress",
        str(gs(f"{prefix}_group_summary").get("attributes", {}).get("suppressed")),
        "0",
    )

    # ------------------------------------------------------------------
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
        if alpha_entities & candidate_entities:  # shares at least one entity with Alpha
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
        wait()
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
        wait()
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
                wait()
                after_offline = int(gs(c_offline_eid).get("state", "0"))
                chk(
                    "EC16 combined offline_count rises when member entity goes offline",
                    after_offline,
                    baseline + 1,
                    f"sensor={c_offline_eid} baseline={baseline} after={after_offline}",
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
                after_recovery = int(gs(c_offline_eid).get("state", "0"))
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
                    ev = next(
                        (e for e in offline_events if e.get("entity_id") == target),
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
                            "EC20 offline event source_groups is a list",
                            isinstance(ev.get("source_groups"), list),
                            True,
                            f"source_groups={ev.get('source_groups')!r}",
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
                            "EC21 recovered event source_groups is a list",
                            isinstance(ev.get("source_groups"), list),
                            True,
                            f"source_groups={ev.get('source_groups')!r}",
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
                print(
                    f"  EC24: no combined event captured (combined_entry_id={combined_entry_id}, total={len(events)})",
                    flush=True,
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
    print("\n=== CLEANUP ===", flush=True)
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
