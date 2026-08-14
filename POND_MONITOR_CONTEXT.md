# Pond Level Monitor — Project Context

## What it is
A live pond level monitor pulling data from a Sentek D&D 36 soil moisture probe (CropX device G215091) installed in the pond on the Pasture field. Displays current water depth, fill/drain history, and predictions.

## Live site
- **URL:** https://ryanoverleese.github.io/pondlevel (GitHub Pages) or Netlify
- **Repo:** https://github.com/ryanoverleese/pondlevel

## Files
| File | Location | Purpose |
|------|----------|---------|
| `index.html` | `~/Desktop/pondlevel/` | The live web app (pushed to GitHub) |
| `pond_data.json` | `~/Desktop/pondlevel/` | Latest sensor data (auto-updated by cron) |
| `pond_history.json` | `~/Desktop/pondlevel/` | All depth crossing events ever recorded |
| `push_pond_data.py` | `~/Desktop/cropx_data/` | Cron script — fetches CropX, writes JSON, git pushes |
| `pond_server.py` | `~/Desktop/` | Local dev server (run to test at localhost:8765) |
| `pond_level.html` | `~/Desktop/` | Local dev version of the UI |
| `pond_monitor.py` | `~/Downloads/` | Original CLI tool for pulling raw CropX data |

## Cron
Runs every 10 minutes:
```
*/10 * * * * /usr/bin/python3 /Users/ryano/Desktop/cropx_data/push_pond_data.py >> /Users/ryano/Desktop/cropx_data/pond_push.log 2>&1
```
Check log: `cat ~/Desktop/cropx_data/pond_push.log`

## Probe geometry
- **Device:** G215091, Sentek D&D 36" probe
- **Account:** acre.insights (CropX)
- **Probe length:** 36"
- **Probe bottom:** ~8" above pond floor
- **Sensors:** 9 sensors at 2, 6, 10, 14, 18, 22, 26, 30, 34" from probe top
- **Water depth formula:** `water_depth = 44 - sensor_depth_from_top`
  - Sensor at 34" from top = 10" water depth (bottom)
  - Sensor at 2" from top = 42" water depth (max)
- **Max depth:** 42"
- **Wet threshold:** 15% VWC
- **Sediment sensors:** 30" and 34" from top sit in saturated bottom sediment (~39% VWC always)

## CropX API
- **Auth:** GET `https://app.cropx.com/api/jwttoken` with seed token → returns session token at `content.token`
- **Data:** GET `https://app.cropx.com/device-installations/data/{uuid}/graphs/v2?type=SOIL_MOISTURE&fromTimestampUTC=...&toTimestampUTC=...`
- **Seed token:** hardcoded in push_pond_data.py and pond_server.py (may expire — re-grab from DevTools if auth fails)
- **Device UUID:** `3de45c5d-39dc-43fe-a9da-d592317f3028`

## How history works
- `push_pond_data.py` runs every 10 min, detects when any sensor crosses the 15% VWC threshold
- Crossing events saved to `pond_history.json` with timestamp, depth, direction (up/down), VWC
- History used in the Predictions card on the live site

## Pending upgrade — Volume curve predictions (do after 3+ fill cycles)
The pond is bowl/wok shaped — wider at top, narrower at bottom. Current predictions assume linear fill rate which is inaccurate at the extremes.

**Plan:** Once `pond_history.json` has 3+ full fill cycles, update `renderPredictions()` in `index.html` to weight each depth band's predicted rate by its historical average time-per-band. Slower bands = wider pond = automatically learned volume curve. No manual measurements needed.

## Key constants (in index.html and push_pond_data.py)
```
DEPTH_OFFSET  = 44
MAX_DEPTH     = 42
WET_THRESHOLD = 15  # %VWC
HOURS         = 168 # 7 days of data fetched from CropX
```
