#!/usr/bin/env python3
"""
Pond Level Monitor — local server
Run: python3 pond_server.py
Then open: http://localhost:8765
"""

import json
import os
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request, urlopen

def _need(name: str) -> str:
    """Read a credential from the environment.

    These used to sit in the source as string literals, which meant this file
    could not be committed without publishing a live CropX JWT and a Netlify
    token. They live in ~/.cropx_env now:

        set -a && . ~/.cropx_env && set +a && python3 pond_server.py
    """
    v = os.environ.get(name)
    if not v:
        raise SystemExit(
            f"{name} is not set. Run:  set -a && . ~/.cropx_env && set +a")
    return v


SEED_TOKEN = _need("SEED_TOKEN")
DEVICE_UUID  = "3de45c5d-39dc-43fe-a9da-d592317f3028"
HOURS        = 48
PORT         = 8765
DEPTH_OFFSET = 44      # water_depth = DEPTH_OFFSET - sensor_depth_from_top
WET_THRESHOLD = 15     # %VWC
HTML_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pond_level.html")
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pond_history.json")

CROPX_HEADERS = {
    "accept": "application/json",
    "origin": "https://myfarm.cropx.com",
    "referer": "https://myfarm.cropx.com/",
}

_cache      = {"data": None, "ts": 0}
_cache_lock = threading.Lock()


# ── History ──────────────────────────────────────────────────────────────────

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {"events": [], "last_states": {}}

def save_history(h):
    with open(HISTORY_FILE, "w") as f:
        json.dump(h, f, indent=2)

def detect_crossings(sensors, history):
    """
    Scan each sensor's readings since last known timestamp.
    Record any threshold crossings (dry→wet or wet→dry) to history.
    """
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

        # Only look at readings newer than last known
        new_readings = sorted(
            [(ts, vwc) for ts, vwc in zip(timestamps, values) if ts > last_ts]
        )

        for ts, vwc in new_readings:
            is_wet = vwc >= WET_THRESHOLD
            if last_wet is not None and is_wet != last_wet:
                direction = "up" if is_wet else "down"
                event = {
                    "ts":             ts,
                    "depth":          water_depth,
                    "depth_from_top": depth_from_top,
                    "dir":            direction,
                    "vwc":            round(vwc, 1),
                }
                new_events.append(event)
                print(f"  [POND EVENT] {direction.upper():4s}  {water_depth}\" water  ({datetime.fromtimestamp(ts/1000).strftime('%m/%d %H:%M')})")
            last_wet = is_wet

        # Update last state to most recent reading
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


# ── CropX fetch ───────────────────────────────────────────────────────────────

def get_token():
    req = Request("https://app.cropx.com/api/jwttoken")
    req.add_header("authorization", f"Bearer {SEED_TOKEN}")
    for k, v in CROPX_HEADERS.items():
        req.add_header(k, v)
    with urlopen(req) as resp:
        d = json.loads(resp.read().decode())
    if isinstance(d, str):
        return d
    c = d.get("content", {})
    return (isinstance(c, dict) and c.get("token")) or d.get("token") or d.get("accessToken") or ""

def fetch_data():
    token  = get_token()
    now    = int(time.time() * 1000)
    from_ts = now - HOURS * 3600 * 1000
    url = (
        f"https://app.cropx.com/device-installations/data/{DEVICE_UUID}/graphs/v2"
        f"?fromTimestampUTC={from_ts}&toTimestampUTC={now}&type=SOIL_MOISTURE"
    )
    req = Request(url)
    req.add_header("authorization", f"Bearer {token}")
    for k, v in CROPX_HEADERS.items():
        req.add_header(k, v)
    with urlopen(req) as resp:
        d = json.loads(resp.read().decode())
    sensors = (d.get("content") or d).get("data", [])

    # Detect crossings and update history
    history = load_history()
    detect_crossings(sensors, history)

    return {
        "sensors": sensors,
        "history": history.get("events", []),
        "fetched": datetime.now().isoformat(),
    }

def get_cached():
    with _cache_lock:
        age = time.time() - _cache["ts"]
        if _cache["data"] and age < 300:
            return _cache["data"]
    print("  Fetching fresh data from CropX...")
    data = fetch_data()
    with _cache_lock:
        _cache["data"] = data
        _cache["ts"]   = time.time()
    return data


# ── HTTP server ───────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_file(HTML_FILE, "text/html")
        elif self.path == "/data":
            self._serve_data()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def _serve_data(self):
        try:
            data = get_cached()
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(err))
            self.end_headers()
            self.wfile.write(err)


if __name__ == "__main__":
    print("Pond Level Monitor")
    print(f"  http://localhost:{PORT}")
    print(f"  History: {HISTORY_FILE}")
    print("  Ctrl-C to stop\n")
    threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    HTTPServer(("", PORT), Handler).serve_forever()
