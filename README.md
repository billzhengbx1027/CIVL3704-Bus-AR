# CIVL3704 Transit Map

A live map of buses, Sydney Trains, Sydney Metro and light rail around central Sydney. It shows real-time vehicle positions, predicted delay at the next stop, data freshness and the planned route of each trip. A projection mode (`P`) fits the map onto the Sydney Harbour physical model used in class.

## Quick start

Requires Python 3.10+.

```powershell
pip install -r requirements.txt
python src/visualization.py --demo
```

Demo mode shows fixed vehicles for all four modes and needs no API key. Open `http://127.0.0.1:8080`.

### Live data

1. Create a `.env` file in the project root:

   ```
   TFNSW_API_KEY=your_key_here
   ```

2. Download and unzip the [TfNSW Timetables Complete GTFS](https://opendata.transport.nsw.gov.au/dataset/timetables-complete-gtfs) into `data/static/`. It provides bus and light rail routes and stops.
3. Run the pipeline and the web server in two terminals:

   ```powershell
   python src/pipeline.py
   ```

   ```powershell
   python src/visualization.py
   ```

On its first run the pipeline downloads the dedicated Sydney Trains timetable to `data/static_train/` and the Metro timetable to `data/static_metro/`, then tries to refresh them every 24 hours. Their `trip_id`s match the realtime feeds; the rail trip IDs in the complete GTFS do not. If the download fails, run `python src/train_static.py` (or `--metro`) and check that your API key has access to [Timetables for Realtime](https://opendata.transport.nsw.gov.au/data/dataset/public-transport-timetables-realtime) and [v2](https://opendata.transport.nsw.gov.au/data/dataset/public-transport-timetables-realtime-v2).

### Feeds and request budget

The pipeline reads Vehicle Positions and Trip Updates for buses, Sydney Trains, Sydney Metro, L1 Inner West and L2/L3 CBD and South East light rail, and Parramatta Light Rail (L4 falls outside the current map area). Your TfNSW application needs access to [Vehicle Positions](https://opendata.transport.nsw.gov.au/dataset/public-transport-realtime-vehicle-positions), [Vehicle Positions v2](https://opendata.transport.nsw.gov.au/dataset/public-transport-realtime-vehicle-positions-v2), [Trip Updates](https://opendata.transport.nsw.gov.au/dataset/public-transport-realtime-trip-update) and [Trip Updates v2](https://opendata.transport.nsw.gov.au/dataset/public-transport-realtime-trip-update-v2).

Vehicle positions are polled every 15 seconds and Trip Updates (larger) every 60 seconds, about 43,000 requests a day across the six feeds. The intervals are set at the top of `src/pipeline.py`; check your daily quota before shortening them. If one feed fails the others keep updating, and the failure is shown on the page and in `data/feed_status.json`.

### Outputs

| File | Contents |
| --- | --- |
| `data/central_sydney_vehicles_with_delay.csv` | Latest merged snapshot (also served at `/api/vehicles`; `/api/buses` still returns buses only) |
| `data/journey_routes.sqlite` | Route index for journey search |
| `data/shape_offsets.json`, `data/train_shape_offsets.json`, `data/metro_shape_offsets.json` | Shape indexes, rebuilt when the static files change |

## Direct route search

Under **Find a direct route**, pick a mode and enter an origin and destination (a suburb, landmark or nearby street such as Broadway or Town Hall), or click **Choose … on map**. Stops are searched within 250 m for buses, 350 m for light rail and 500 m for trains and Metro. The search finds routes in GTFS `stop_times.txt` that need no transfer and shows the suggested boarding and alighting stops with straight-line distances. Click a vehicle with an arrival prediction to locate it on the map.

Results come from the static timetable and are not filtered by today's service day, so a listed route may not be running right now. Walking distances are straight-line, not walking routes.

**Arriving soon** only lists vehicles inside the map area whose position and Trip Update are both under 120 seconds old and that are predicted to reach the boarding stop within 60 minutes. Rail and Metro Trip Updates sometimes give only a delay in seconds; the arrival time is then estimated from the dedicated timetable, and no prediction is shown if the trip or stop can't be matched. The page says so explicitly when data is stale, in demo mode, or when no prediction exists.

## Map controls

- **Show on map** filters by All, Bus, Train, Metro or Light rail. Buses are dots; trains, Metro and light rail are small rectangles. Fill colour shows next-stop delay and outline colour shows the mode. You can also filter by route or by the next stop of current vehicles; once a route is selected, the stop menu lists only that route's next stops. If no vehicle matches, the page explains why and keeps the selected stop marker and route shape.
- Selecting a route draws the **planned shapes** of its current trips. Vehicle dots are reported positions, so a vehicle heading to its first stop before the trip starts may sit off the shape.
- Next-stop markers can be shown; the map can be zoomed and dragged. With more than 150 vehicles, vehicle labels stay on only at zoom 15 and above; hover to see them when zoomed out.
- Drag the top of the control panel to move it (mouse or touch); its position is saved in the browser. With the header focused, arrow keys move it (hold Shift for bigger steps). The button at the bottom resets it.

| Key | Action |
| --- | --- |
| `P` | Toggle projection view |
| `C` | Calibrate (in projection view) |
| `L` | Toggle vehicle labels |
| `B` | Switch basemap |
| `F` | Full screen |
| `R` | Refresh now |

## Map area and projection calibration

`map_area.json` holds the south, west, north and east bounds of the map and is shared by the pipeline, route index and web page. The current values (latitude −33.909 to −33.814, longitude 151.106 to 151.271) were estimated from a photo of the Sydney Harbour model and roughly cover Drummoyne to Rose Bay and Lane Cove to Redfern.

To project onto the physical model:

1. Press `F` for full screen, then `P` for projection view (locked to `map_area.json`, no zoom or drag).
2. Press `C` to calibrate. Drag or use arrow keys to move (Shift for bigger steps), scroll or `+`/`-` to zoom, `[`/`]` to fine-tune. The red dashed box is the saved area.
3. Press `Enter` to save the current view to `map_area.json`, or `Esc` to cancel. Saving is only accepted from localhost.

A running `python src/pipeline.py` picks up the new area on its next cycle (within about 15 seconds) without a restart. The route index rebuilds once on the next search, which takes a few minutes.

## Delay classification and data quality

The default mode follows the [TfNSW Greater Sydney Bus Contract departure window for first and mid-route stops](https://www.transport.nsw.gov.au/system/files/media/documents/2023/Schedule%204%20%28Key%20Performance%20Indicators%29.pdf): 1 minute or more early counts as early and 6 minutes or more late counts as late. The map applies this window to the **predicted delay at the next stop**. Trains, Metro and light rail use the same thresholds only for comparison; they are not those modes' official punctuality standards. The colours are this project's own, not an official palette.

**My settings** lets you change the early and late thresholds and colours; settings are stored in your browser. Raw delay seconds are kept in the CSV and `/api/vehicles`.

- Vehicles whose GPS or snapshot is more than 120 seconds old are shown grey.
- If a feed temporarily stops returning central Sydney vehicles, their last positions are kept for up to 10 minutes as grey stale markers, then hidden. Stale positions are excluded from arrival predictions.
- When GTFS-R reports `STOPPED_AT`, the popup shows "feed reported". "Near next stop" appears only within 50 m of the next stop and **does not confirm the vehicle stopped or served passengers**. A missing near-stop record alone doesn't prove a stop was skipped.

## Project structure

```
src/
  pipeline.py          Polling loop (entry point for live data)
  transit_pipeline.py  Fetches all feeds and builds the delay snapshot
  transit_modes.py     Feed and mode definitions
  gtfs_realtime.py     GTFS-Realtime parsing helpers
  train_static.py      Downloads Sydney Trains / Metro static GTFS
  map_area.py          Reads and writes map_area.json
  map_data.py          Stops and route shapes for the map
  journey_search.py    Direct route search and live arrivals
  visualization.py     Web server (entry point for the map)
web/
  templates/index.html
  static/              projection.js, delay_rules.js, style.css
tests/                 Python unittest and Node test files
map_area.json          Shared map extent
```

## Tests

```powershell
python -m unittest discover -s tests
```

```powershell
node --test tests/
```
