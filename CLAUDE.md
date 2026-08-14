# pondlevel — Project Context

## What it is
Live pond level monitor for the Pasture field. Reads a Sentek D&D 36" soil moisture probe via the CropX API, writes JSON data files, and serves a web UI showing current depth, fill/drain history, and fill-time predictions.

## Live site
- URL: https://ryanoverleese.github.io/pondlevel
- Repo: https://github.com/ryanoverleese/pondlevel
- Deploys on push to main via GitHub Pages

## Files
| File | Purpose |
|------|---------|
| `index.html` | Live web app — deployed to GitHub Pages |
| `push_pond_data.py` | Cron script — fetches CropX, updates JSON, git pushes |
| `pond_data.json` | Latest sensor reading (auto-updated by cron every 10 min) |
| `pond_history.json` | All threshold-crossing events ever recorded |
| `pump_manual.json` | Manual pump log entries |
| `pond_server.py` | Local dev server — run to test at localhost:8765 |
| `pond_level.html` | Local dev version of the UI (served by pond_server.py) |
| `pond_monitor_v1.html` | Older version of the UI, kept for reference |

## Cron
Runs every 10 minutes on Ryan's Mac:
```
*/10 * * * * /usr/bin/python3 /Users/ryano/Documents/GitHub/pondlevel/push_pond_data.py >> /Users/ryano/Documents/GitHub/pondlevel/pond_push.log 2>&1
```
Check log: `cat ~/Documents/GitHub/pondlevel/pond_push.log`

## Probe geometry (Sentek D&D 36", device G215091)
- CropX device UUID: `3de45c5d-39dc-43fe-a9da-d592317f3028`
- 9 sensors at 2, 6, 10, 14, 18, 22, 26, 30, 34" from probe top
- Probe bottom sits ~8" above pond floor
- **Water depth formula:** `water_depth = 44 - sensor_depth_from_top`
  - Sensor at 34" from top = 10" water (bottom)
  - Sensor at 2" from top = 42" water (max)
- **Wet threshold:** 15% VWC
- **Sediment sensors:** 30" and 34" from top sit in saturated bottom sediment (~39% VWC always — ignore for level calculations)
- Max usable depth: 42"

## Key constants (keep in sync between index.html and push_pond_data.py)
```
DEPTH_OFFSET  = 44
MAX_DEPTH     = 42
WET_THRESHOLD = 15   # %VWC
HOURS         = 168  # 7 days of history fetched from CropX
```

## CropX API
- Auth: GET `https://app.cropx.com/api/jwttoken` with seed token → session JWT at `content.token`
- Data: GET `https://app.cropx.com/device-installations/data/{uuid}/graphs/v2?type=SOIL_MOISTURE&fromTimestampUTC=...&toTimestampUTC=...`
- Seed token: hardcoded in `push_pond_data.py` and `pond_server.py`
- If auth starts failing (502/401): re-grab seed token from browser DevTools (Network tab on myfarm.cropx.com)

## How history works
`push_pond_data.py` detects when a sensor crosses the 15% VWC threshold. Each crossing (wet→dry or dry→wet) is saved to `pond_history.json` with timestamp, depth, direction, and VWC. History drives the Predictions card.

## Pending upgrade — volume curve predictions
The pond is bowl-shaped (wider at top). Current `renderPredictions()` in `index.html` uses a linear fill rate assumption.

**Plan:** Once `pond_history.json` has 3+ full fill cycles, update `renderPredictions()` to weight each depth band's predicted rate by its historical average time-per-band. Slower bands = wider bowl = learned volume curve. No manual measurements needed.

## Common fixes
| Problem | Fix |
|---------|-----|
| Pond not updating | Run script manually; check pond_push.log |
| git push exit 128 | Run `git push` manually in terminal to re-auth |
| CropX 502 | API is down — retry later |
| Netlify pump-log form missing | Submit one pump event from the live site to create the form |
