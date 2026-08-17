#!/usr/bin/env python3
"""
Pond Level Data Pusher
Fetches CropX data, updates pond_data.json, commits and pushes to GitHub.
Cron: */10 * * * * /usr/bin/python3 /Users/ryano/Documents/GitHub/pondlevel/push_pond_data.py >> /Users/ryano/Documents/GitHub/pondlevel/pond_push.log 2>&1
"""

import json
import os
import subprocess
import time
from datetime import datetime
from urllib.request import Request, urlopen

def _need(name: str) -> str:
    """Read a credential from the environment, falling back to ~/.cropx_env.

    The fallback is the whole point. cron does not run a login shell and does
    not source anything, so a credential that only exists as a shell export is
    invisible to it. Reading the file directly means the same script works from
    a terminal, from cron, and from a launchd job without a wrapper line that
    has to be remembered separately — which is exactly what was forgotten when
    these tokens moved out of the source and quietly stopped two jobs for four
    days.
    """
    v = os.environ.get(name)
    if v:
        return v
    env = os.path.expanduser("~/.cropx_env")
    try:
        with open(env) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                key, _, val = line.partition("=")
                if key.strip() == name:
                    return val.strip().strip('"').strip("'")
    except OSError:
        pass
    raise SystemExit(
        f"{name} is not set and is not in {env}. Add it there, or run:  "
        f"set -a && . ~/.cropx_env && set +a")


# ── Config ────────────────────────────────────────────────────────────────────

SEED_TOKEN  = _need("SEED_TOKEN")
DEVICE_UUID = "3de45c5d-39dc-43fe-a9da-d592317f3028"
HOURS       = 168
DEPTH_OFFSET  = 44
WET_THRESHOLD = 15

REPO_DIR      = os.path.expanduser("~/Documents/GitHub/pondlevel")   # path to your cloned repo
DATA_FILE     = os.path.join(REPO_DIR, "pond_data.json")
HISTORY_FILE  = os.path.join(REPO_DIR, "pond_history.json")
MANUAL_FILE   = os.path.join(REPO_DIR, "pump_manual.json")

NETLIFY_TOKEN   = _need("NETLIFY_TOKEN")
NETLIFY_SITE_ID = "3b52296d-7bcf-4858-938a-0ad639edb98a"

HEADERS = {
    "accept":   "application/json",
    "origin":   "https://myfarm.cropx.com",
    "referer":  "https://myfarm.cropx.com/",
}

# ── CropX ─────────────────────────────────────────────────────────────────────

def get_token():
    req = Request("https://app.cropx.com/api/jwttoken")
    req.add_header("authorization", f"Bearer {SEED_TOKEN}")
    for k, v in HEADERS.items():
        req.add_header(k, v)
    with urlopen(req) as r:
        d = json.loads(r.read().decode())
    if isinstance(d, str):
        return d
    c = d.get("content", {})
    return (isinstance(c, dict) and c.get("token")) or d.get("token") or d.get("accessToken") or ""

def fetch_sensors():
    token = get_token()
    now     = int(time.time() * 1000)
    from_ts = now - HOURS * 3600 * 1000
    url = (
        f"https://app.cropx.com/device-installations/data/{DEVICE_UUID}/graphs/v2"
        f"?fromTimestampUTC={from_ts}&toTimestampUTC={now}&type=SOIL_MOISTURE"
    )
    req = Request(url)
    req.add_header("authorization", f"Bearer {token}")
    for k, v in HEADERS.items():
        req.add_header(k, v)
    with urlopen(req) as r:
        d = json.loads(r.read().decode())
    return (d.get("content") or d).get("data", [])

# ── History ───────────────────────────────────────────────────────────────────

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {"events": [], "last_states": {}}

def save_history(h):
    with open(HISTORY_FILE, "w") as f:
        json.dump(h, f, indent=2)

def detect_crossings(sensors, history):
    last_states = history.get("last_states", {})
    new_events  = []
    for s in sensors:
        depth_from_top = s["metaData"]["depth"]["value"]
        water_depth    = DEPTH_OFFSET - depth_from_top
        timestamps     = s.get("timestamps", [])
        values         = s.get("values", [])
        if not timestamps or not values:
            continue
        key      = str(depth_from_top)
        last     = last_states.get(key, {})
        last_ts  = last.get("ts", 0)
        last_wet = last.get("wet", None)
        new_readings = sorted([(ts, vwc) for ts, vwc in zip(timestamps, values) if ts > last_ts])
        for ts, vwc in new_readings:
            is_wet = vwc >= WET_THRESHOLD
            if last_wet is not None and is_wet != last_wet:
                direction = "up" if is_wet else "down"
                new_events.append({
                    "ts":             ts,
                    "depth":          water_depth,
                    "depth_from_top": depth_from_top,
                    "dir":            direction,
                    "vwc":            round(vwc, 1),
                })
                print(f"  [EVENT] {direction.upper():4s}  {water_depth}\" water  "
                      f"({datetime.fromtimestamp(ts/1000).strftime('%m/%d %H:%M')})")
            last_wet = is_wet
        latest_ts  = max(timestamps)
        latest_idx = timestamps.index(latest_ts)
        last_states[key] = {
            "wet": values[latest_idx] >= WET_THRESHOLD,
            "ts":  latest_ts,
            "vwc": round(values[latest_idx], 1),
        }
    history["last_states"] = last_states
    if new_events:
        history["events"] = history.get("events", []) + new_events
    save_history(history)
    return new_events

# ── Netlify pump log ──────────────────────────────────────────────────────────

def fetch_pump_events():
    """Fetch pump-log form submissions from Netlify."""
    try:
        # Get forms list to find pump-log form ID
        req = Request(f"https://api.netlify.com/api/v1/sites/{NETLIFY_SITE_ID}/forms")
        req.add_header("authorization", f"Bearer {NETLIFY_TOKEN}")
        with urlopen(req) as r:
            forms = json.loads(r.read().decode())
        form = next((f for f in forms if f.get("name") == "pump-log"), None)
        if not form:
            print("  No pump-log form found yet (submit once to create it).")
            return []
        # Fetch submissions
        req = Request(f"https://api.netlify.com/api/v1/forms/{form['id']}/submissions?per_page=100")
        req.add_header("authorization", f"Bearer {NETLIFY_TOKEN}")
        with urlopen(req) as r:
            subs = json.loads(r.read().decode())
        events = [{"ts": s["data"].get("ts"), "action": s["data"].get("action"), "depth": s["data"].get("depth")} for s in subs]
        print(f"  Got {len(events)} pump log entries.")
        return events
    except Exception as e:
        print(f"  Pump log fetch failed: {e}")
        return []

# ── Git push ──────────────────────────────────────────────────────────────────

def git_push():
    cmds = [
        ["git", "-C", REPO_DIR, "add", "pond_data.json", "pond_history.json"],
        ["git", "-C", REPO_DIR, "commit", "-m",
         f"data: {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
        ["git", "-C", REPO_DIR, "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # "nothing to commit" is fine
            if "nothing to commit" in result.stdout + result.stderr:
                print("  No changes to push.")
                return
            print(f"  git error: {result.stderr.strip()}")
            return
    print("  Pushed to GitHub.")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Fetching pond data...")
    sensors = fetch_sensors()
    print(f"  Got {len(sensors)} sensors.")

    history = load_history()
    detect_crossings(sensors, history)
    netlify_events = fetch_pump_events()
    manual_events  = json.load(open(MANUAL_FILE)) if os.path.exists(MANUAL_FILE) else []
    pump_events    = manual_events + netlify_events

    payload = {
        "sensors": sensors,
        "history": history.get("events", []),
        "pump":    pump_events,
        "fetched": datetime.now().isoformat(),
    }
    with open(DATA_FILE, "w") as f:
        json.dump(payload, f)
    print(f"  Wrote {DATA_FILE}")

    git_push()

if __name__ == "__main__":
    main()
