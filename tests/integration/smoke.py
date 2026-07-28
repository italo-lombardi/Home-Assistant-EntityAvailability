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

What is tested (covers PRs #37, #41, core):
  EC1  entity goes unavailable → offline_count increments
  EC4  device online + battery below threshold → low_battery_count=1, list populated
  EC5  device+battery unavailable → offline=1, low_battery=0 (PR#41: no double-count)
  EC6  battery replaced above threshold, back online → all clear
  EC7  combined group: online+low_battery increments combined count
  EC8  combined group: offline+low → combined low_battery drops, offline rises
  EC9  PR#37: cleared battery map entry not re-suggested in options flow
  EC10 suppression: suppressed+unavailable entity not counted in offline
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("EA_SMOKE_TOKEN", "")
BASE = os.environ.get("EA_SMOKE_BASE_URL", "http://localhost:8123")
GROUP_FILTER = os.environ.get("EA_SMOKE_GROUP", "Test Group")

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


def wait_for(label, check_fn, expected, timeout=60, interval=5):
    """Poll until check_fn() == expected or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = check_fn()
        if str(val) == str(expected):
            return val
        time.sleep(interval)
    return check_fn()


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
    wait(35)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=== Entity Availability smoke tests ===\n", flush=True)

    ctx = discover()
    setup_battery(ctx)
    restore_all(ctx)

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
            "offline_count",
            lambda: gs(f"{prefix}_offline_count").get("state"),
            "1",
            timeout=90,
        ),
        "1",
    )
    if combined_prefix:
        c_attrs = gs(f"{combined_prefix}_combined_summary").get("attributes", {})
        chk("EC1 combined offline=1", str(c_attrs.get("offline")), "1")
        chk("EC1 combined low_battery=0", str(c_attrs.get("low_battery")), "0")
    restore_all(ctx)

    # ------------------------------------------------------------------
    print(
        "\n=== EC4: online + battery below threshold → low_battery_count=1 ===",
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
        "low_battery_count=1",
        wait_for(
            "low_battery_count",
            lambda: gs(f"{prefix}_low_battery_count").get("state"),
            "1",
            timeout=90,
        ),
        "1",
    )
    lb = gs(f"{prefix}_low_battery")
    chk("low_battery list count=1", lb.get("attributes", {}).get("count"), 1)
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
        c_attrs = gs(f"{combined_prefix}_combined_summary").get("attributes", {})
        chk("EC4 combined low_battery=1", str(c_attrs.get("low_battery")), "1")
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
            "offline_count",
            lambda: gs(f"{prefix}_offline_count").get("state"),
            "1",
            timeout=90,
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
    wait()
    chk("offline_count=0", gs(f"{prefix}_offline_count").get("state"), "0")
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
