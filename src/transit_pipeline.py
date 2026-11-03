"""Collect and join TfNSW bus, train, metro, light rail and ferry feeds."""

from __future__ import annotations

import csv
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from time import monotonic as _monotonic, sleep as _sleep
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2
from google.protobuf.message import DecodeError

from map_area import bounds as map_bounds
from map_data import stop_lookup
from transit_modes import FEEDS, POSITION_DISPLAY_SECONDS, mode_for_route_type
from train_static import FERRY_STATIC, METRO_STATIC, TRAIN_STATIC


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = DATA / "static"
POSITIONS_FILE = DATA / "central_sydney_vehicles.csv"
UPDATES_FILE = DATA / "transit_trip_updates.csv"
ENRICHED_UPDATES_FILE = DATA / "transit_trip_updates_enriched.csv"
SNAPSHOT_FILE = DATA / "central_sydney_vehicles_with_delay.csv"
FEED_STATUS_FILE = DATA / "feed_status.json"
FRESH_SECONDS = 120
SYDNEY = ZoneInfo("Australia/Sydney")
# Modes whose realtime trip IDs only match their own dedicated timetable.
DEDICATED_STATIC = (("train", TRAIN_STATIC), ("metro", METRO_STATIC), ("ferry", FERRY_STATIC))

POSITION_COLUMNS = (
    "mode", "feed", "vehicle_id", "trip_id", "route_id_realtime",
    "latitude", "longitude", "timestamp", "current_stop_id",
    "current_stop_sequence", "stop_status", "valid",
)
UPDATE_COLUMNS = (
    "mode", "feed", "trip_id", "route_id", "start_date",
    "trip_update_timestamp", "stop_id", "stop_sequence", "arrival_delay",
    "departure_delay", "arrival_time", "departure_time", "delay_seconds",
    "delay_event_type",
)


def _replace(temporary: Path, path: Path, attempts: int = 10) -> None:
    """Windows refuses to replace a file another program is reading (WinError 32); retry briefly."""
    for attempt in range(attempts):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            _sleep(0.3)


def _write_csv(path: Path, columns: tuple[str, ...], records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    _replace(temporary, path)


# Feeds are fetched in parallel; each worker thread keeps its own keep-alive session.
# TfNSW answers 429 above about 5 requests a second per key, so request starts are spaced.
REQUEST_SPACING_SECONDS = 0.25
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="feed")
_thread_state = threading.local()
_rate_lock = threading.Lock()
_next_request_at = 0.0


def _session() -> requests.Session:
    if not hasattr(_thread_state, "session"):
        _thread_state.session = requests.Session()
    return _thread_state.session


def _wait_for_request_slot() -> None:
    global _next_request_at
    with _rate_lock:
        now = _monotonic()
        start = max(now, _next_request_at)
        _next_request_at = start + REQUEST_SPACING_SECONDS
    if start > now:
        _sleep(start - now)


def _feed(url: str, api_key: str) -> gtfs_realtime_pb2.FeedMessage:
    _wait_for_request_slot()
    response = _session().get(url, headers={"Authorization": f"apikey {api_key}"}, timeout=30)
    response.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)
    return feed


def _event_value(stop_update, event_name: str, field_name: str):
    if not stop_update.HasField(event_name):
        return None
    event = getattr(stop_update, event_name)
    return getattr(event, field_name) if event.HasField(field_name) else None


def parse_positions(feed, mode: str, name: str, now: int) -> list[dict]:
    records = []
    lat_min, lat_max, lon_min, lon_max = map_bounds()
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        vehicle = entity.vehicle
        if not vehicle.HasField("position"):
            continue
        lat, lon = vehicle.position.latitude, vehicle.position.longitude
        if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
            continue
        trip = vehicle.trip if vehicle.HasField("trip") else None
        timestamp = vehicle.timestamp if vehicle.HasField("timestamp") else None
        records.append({
            "mode": mode,
            "feed": name,
            "vehicle_id": vehicle.vehicle.id if vehicle.HasField("vehicle") and vehicle.vehicle.id else entity.id,
            "trip_id": trip.trip_id if trip and trip.HasField("trip_id") else "",
            "route_id_realtime": trip.route_id if trip and trip.HasField("route_id") else "",
            "latitude": lat,
            "longitude": lon,
            "timestamp": timestamp,
            "current_stop_id": vehicle.stop_id if vehicle.HasField("stop_id") else "",
            "current_stop_sequence": vehicle.current_stop_sequence if vehicle.HasField("current_stop_sequence") else "",
            "stop_status": (gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus.Name(vehicle.current_status)
                            if vehicle.HasField("current_status") else ""),
            "valid": bool(timestamp and -30 <= now - timestamp <= FRESH_SECONDS),
        })
    return records


def parse_updates(feed, mode: str, name: str) -> list[dict]:
    records = []
    feed_timestamp = feed.header.timestamp if feed.header.HasField("timestamp") else None
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        update = entity.trip_update
        trip = update.trip
        trip_id = trip.trip_id if trip.HasField("trip_id") else ""
        timestamp = update.timestamp if update.HasField("timestamp") else feed_timestamp
        for stop in update.stop_time_update:
            arrival_delay = _event_value(stop, "arrival", "delay")
            departure_delay = _event_value(stop, "departure", "delay")
            delay_type = "departure" if departure_delay is not None else "arrival" if arrival_delay is not None else ""
            records.append({
                "mode": mode,
                "feed": name,
                "trip_id": trip_id,
                "route_id": trip.route_id if trip.HasField("route_id") else "",
                "start_date": trip.start_date if trip.HasField("start_date") else "",
                "trip_update_timestamp": timestamp,
                "stop_id": stop.stop_id if stop.HasField("stop_id") else "",
                "stop_sequence": stop.stop_sequence if stop.HasField("stop_sequence") else "",
                "arrival_delay": arrival_delay,
                "departure_delay": departure_delay,
                "arrival_time": _event_value(stop, "arrival", "time"),
                "departure_time": _event_value(stop, "departure", "time"),
                "delay_seconds": departure_delay if departure_delay is not None else arrival_delay,
                "delay_event_type": delay_type,
            })
    return records


def _position_in_grace(record: dict, now: int) -> bool:
    try:
        age = now - int(float(record.get("timestamp", "")))
    except (TypeError, ValueError):
        return False
    return -30 <= age <= POSITION_DISPLAY_SECONDS


def _cached_positions(now: int, feed_name: str) -> list[dict]:
    """Keep a feed's last positions briefly when current positions disappear."""
    if not POSITIONS_FILE.exists():
        return []
    with POSITIONS_FILE.open(encoding="utf-8-sig", newline="") as source:
        records = []
        for row in csv.DictReader(source):
            if row.get("feed") != feed_name:
                continue
            if _position_in_grace(row, now):
                row["valid"] = False
                records.append(row)
    return records


_area_stops_cache: tuple[tuple, set[str]] | None = None


def _area_stop_ids() -> set[str]:
    """Static stop IDs inside the map area, rebuilt when the area or stops change."""
    global _area_stops_cache
    stops = stop_lookup()
    key = (id(stops), map_bounds())
    if _area_stops_cache and _area_stops_cache[0] == key:
        return _area_stops_cache[1]
    lat_min, lat_max, lon_min, lon_max = key[1]
    ids = {stop_id for stop_id, stop in stops.items()
           if lat_min <= stop["latitude"] <= lat_max and lon_min <= stop["longitude"] <= lon_max}
    _area_stops_cache = (key, ids)
    return ids


def area_trip_updates(records: list[dict], active_trip_ids: set[str], area_stops: set[str]) -> list[dict]:
    """Keep every stop of trips that are on the map now or call at a stop inside it.

    The bus feed covers all of NSW (about 120,000 stop rows); only a small share
    is ever shown, so trimming it keeps every later read of the file cheap.
    """
    if not area_stops:
        return records  # No static stops to judge by; keep everything.
    keep = {row["trip_id"] for row in records
            if row["trip_id"] in active_trip_ids or row["stop_id"] in area_stops}
    return [row for row in records if row["trip_id"] in keep]


def _previous_status() -> dict:
    try:
        return json.loads(FEED_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _fetch_source(source, api_key: str, include_updates: bool, previous: dict,
                  area_stops: set[str]) -> tuple[dict, list[dict], list[dict]]:
    """Fetch one feed's positions (and updates); failures stay inside this feed."""
    positions, updates = [], []
    state = {"mode": source.mode, "positions": "unavailable", "updates": "unavailable"}
    if not include_updates:
        for key in ("updates", "updates_count"):
            if key in previous.get(source.name, {}):
                state[key] = previous[source.name][key]
    kinds = [("positions", source.vehicle_url)]
    if include_updates:
        kinds.append(("updates", source.updates_url))
    for kind, url in kinds:
        try:
            feed = _feed(url, api_key)
            received_at = int(time.time())
            if kind == "positions":
                records = parse_positions(feed, source.mode, source.name, received_at)
                if not any(_position_in_grace(row, received_at) for row in records):
                    cached = _cached_positions(received_at, source.name)
                    state["positions_cached_count"] = len(cached)
                    if cached:
                        records = cached
                positions = records
            else:
                active_trips = {row["trip_id"] for row in positions if row.get("trip_id")}
                records = area_trip_updates(parse_updates(feed, source.mode, source.name),
                                            active_trips, area_stops)
                updates = records
            state[kind] = "ok"
            state[kind + "_count"] = len(records)
        except (requests.RequestException, DecodeError, ValueError) as error:
            state[kind] = f"{type(error).__name__}: {error}"
            print(f"{source.name} {kind}: {state[kind]}")
            if kind == "positions":
                positions = _cached_positions(int(time.time()), source.name)
                state["positions_cached_count"] = len(positions)
    return state, positions, updates


def fetch_all(api_key: str | None = None, *, include_updates: bool = True) -> dict:
    """Poll each source independently; report unavailable feeds without hiding others.

    With include_updates=False only Vehicle Positions are fetched and the previous
    Trip Updates file is kept, which saves half the API requests on quick cycles.
    """
    if api_key is None:
        load_dotenv(ROOT / ".env")
        api_key = os.getenv("TFNSW_API_KEY")
    if not api_key:
        raise RuntimeError("TFNSW_API_KEY was not found in .env")
    if not include_updates and not UPDATES_FILE.exists():
        include_updates = True
    previous = {} if include_updates else _previous_status()
    area_stops = _area_stop_ids() if include_updates else set()
    results = list(_executor.map(
        lambda source: _fetch_source(source, api_key, include_updates, previous, area_stops), FEEDS))
    positions, updates, status = [], [], {}
    for source, (state, feed_positions, feed_updates) in zip(FEEDS, results):
        status[source.name] = state
        positions.extend(feed_positions)
        updates.extend(feed_updates)
    if not any(state["positions"] == "ok" for state in status.values()):
        raise RuntimeError("All Vehicle Positions feeds failed; previous snapshot was kept")
    _write_csv(POSITIONS_FILE, POSITION_COLUMNS, positions)
    status["fetched_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if include_updates:
        _write_csv(UPDATES_FILE, UPDATE_COLUMNS, updates)
        status["updates_fetched_at"] = status["fetched_at"]
    elif "updates_fetched_at" in previous:
        status["updates_fetched_at"] = previous["updates_fetched_at"]
    temporary = FEED_STATUS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    _replace(temporary, FEED_STATUS_FILE)
    return status


def scheduled_prediction(schedule_time: str, delay_seconds: float, now: int,
                         start_date: str = "") -> float | None:
    """Resolve a GTFS wall-clock time (which may exceed 24h) near the feed time."""
    try:
        hours, minutes, seconds = (int(part) for part in schedule_time.split(":"))
    except (AttributeError, ValueError):
        return None
    if not (hours >= 0 and 0 <= minutes < 60 and 0 <= seconds < 60):
        return None
    if start_date:
        try:
            days = [datetime.strptime(start_date, "%Y%m%d").date()]
        except ValueError:
            days = []
    else:
        today = datetime.fromtimestamp(now, SYDNEY).date()
        days = [today - timedelta(days=1), today, today + timedelta(days=1)]
    candidates = []
    for day in days:
        midnight = datetime.combine(day, clock_time(), SYDNEY)
        predicted = (midnight + timedelta(hours=hours, minutes=minutes, seconds=seconds)).timestamp()
        predicted += delay_seconds
        if now - 60 <= predicted <= now + 3600:
            candidates.append(predicted)
    return min(candidates) if candidates else None


_stop_time_indexes: dict[Path, tuple[tuple, list[str], dict[str, list[tuple[int, int]]]]] = {}


def _stop_time_spans(stop_times: Path) -> tuple[list[str], dict[str, list[tuple[int, int]]]]:
    """Index each trip's byte ranges once, so later cycles skip the full 100+ MB scan."""
    stat = stop_times.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    cached = _stop_time_indexes.get(stop_times)
    if cached and cached[0] == signature:
        return cached[1], cached[2]
    spans: dict[str, list[tuple[int, int]]] = {}
    with stop_times.open("rb") as source:
        header = next(csv.reader([source.readline().decode("utf-8-sig")]))
        column = header.index("trip_id")
        previous, start = None, source.tell()
        while True:
            offset = source.tell()
            line = source.readline()
            parts = line.split(b",")
            trip_id = (parts[column].strip().strip(b'"').decode("utf-8")
                       if len(parts) > column else previous) if line else None
            if trip_id != previous:
                if previous is not None:
                    spans.setdefault(previous, []).append((start, offset))
                previous, start = trip_id, offset
            if not line:
                break
    _stop_time_indexes[stop_times] = (signature, header, spans)
    return header, spans


def _rail_stop_times(root: Path, trip_ids: set[str]) -> tuple[dict, dict]:
    by_stop, by_sequence = {}, {}
    stop_times = root / "stop_times.txt"
    if not trip_ids or not stop_times.exists():
        return by_stop, by_sequence
    header, spans = _stop_time_spans(stop_times)
    with stop_times.open("rb") as source:
        for trip_id in trip_ids:
            for start, end in spans.get(trip_id, ()):
                source.seek(start)
                for values in csv.reader(source.read(end - start).decode("utf-8").splitlines()):
                    row = dict(zip(header, values))
                    times = (row.get("arrival_time", ""), row.get("departure_time", ""))
                    by_stop[(trip_id, row.get("stop_id", ""))] = times
                    by_sequence[(trip_id, row.get("stop_sequence", ""))] = times
    return by_stop, by_sequence


def _fill_rail_predictions(updates: pd.DataFrame, vehicles: pd.DataFrame,
                           now: int, mode: str, root: Path) -> None:
    rail_ids = set(vehicles.loc[vehicles["mode"] == mode, "trip_id"])
    pending = updates[
        (updates["mode"] == mode) & updates["trip_id"].isin(rail_ids)
        & updates["arrival_time"].isna() & updates["departure_time"].isna()
        & updates["delay_seconds"].notna()
    ]
    by_stop, by_sequence = _rail_stop_times(root, set(pending["trip_id"]))
    for index, row in pending.iterrows():
        schedule = by_stop.get((row["trip_id"], row["stop_id"]))
        if schedule is None and pd.notna(row["stop_sequence"]):
            schedule = by_sequence.get((row["trip_id"], str(int(row["stop_sequence"]))))
        if schedule is None:
            continue
        event = "departure_time" if row["delay_event_type"] == "departure" else "arrival_time"
        schedule_time = schedule[1] if event == "departure_time" else schedule[0]
        if not schedule_time:
            schedule_time = schedule[0] or schedule[1]
        predicted = scheduled_prediction(schedule_time, row["delay_seconds"], now,
                                         row.get("start_date", ""))
        if predicted is not None:
            updates.at[index, event] = predicted


_trip_routes_cache: tuple[tuple, pd.DataFrame] | None = None
_enriched_text: str | None = None


def _trip_routes() -> pd.DataFrame:
    """Static trip -> route join, rebuilt only when a GTFS file changes."""
    global _trip_routes_cache
    signature = tuple(
        (str(path), path.stat().st_size, path.stat().st_mtime_ns) if path.exists() else str(path)
        for root in (STATIC, *(root for _, root in DEDICATED_STATIC))
        for path in (root / "routes.txt", root / "trips.txt")
    )
    if _trip_routes_cache and _trip_routes_cache[0] == signature:
        return _trip_routes_cache[1]
    routes = pd.read_csv(STATIC / "routes.txt", dtype=str, keep_default_na=False)
    trips = pd.read_csv(STATIC / "trips.txt", dtype=str, keep_default_na=False)
    routes["mode"] = routes["route_type"].map(mode_for_route_type)
    for mode, root in DEDICATED_STATIC:
        if all((root / name).exists() for name in ("routes.txt", "trips.txt")):
            # Dedicated schedules share trip IDs with their realtime feeds.
            routes = routes[routes["mode"] != mode]
            trips = trips[trips["route_id"].isin(routes["route_id"])]
            rail_routes = pd.read_csv(root / "routes.txt", dtype=str, keep_default_na=False)
            rail_trips = pd.read_csv(root / "trips.txt", dtype=str, keep_default_na=False)
            rail_routes["mode"] = rail_routes["route_type"].map(mode_for_route_type)
            routes = pd.concat([routes, rail_routes], ignore_index=True)
            trips = pd.concat([trips, rail_trips], ignore_index=True)
    trip_routes = trips[["trip_id", "route_id", "trip_headsign"]].merge(
        routes[["route_id", "route_short_name", "route_long_name", "mode"]],
        on="route_id", how="inner",
    ).drop_duplicates(["mode", "trip_id"])
    _trip_routes_cache = (signature, trip_routes)
    return trip_routes


def build_snapshot(*, now: int | None = None, updates_received_at: int | None = None) -> pd.DataFrame:
    """Join by trip_id and mode; complete GTFS route_id differs from realtime IDs.

    updates_received_at is when the Trip Updates file was fetched; freshness is
    judged at that time, since quick cycles reuse updates for up to a minute.
    """
    now = int(time.time()) if now is None else now
    updates_at = now if updates_received_at is None else min(now, updates_received_at)
    vehicles = pd.read_csv(POSITIONS_FILE, dtype=str, keep_default_na=False)
    result = vehicles.merge(_trip_routes(), on=["mode", "trip_id"], how="left")

    updates = pd.read_csv(UPDATES_FILE, dtype=str, keep_default_na=False)
    for column in ("trip_update_timestamp", "arrival_time", "departure_time", "delay_seconds", "stop_sequence"):
        updates[column] = pd.to_numeric(updates[column], errors="coerce")
    for mode, root in DEDICATED_STATIC:
        _fill_rail_predictions(updates, vehicles, now, mode, root)
    # Rewrite only when predictions change, so the web server keeps its parsed copy.
    global _enriched_text
    enriched = updates.to_csv(index=False)
    if enriched != _enriched_text or not ENRICHED_UPDATES_FILE.exists():
        temporary_updates = ENRICHED_UPDATES_FILE.with_suffix(".tmp")
        temporary_updates.write_text(enriched, encoding="utf-8", newline="")
        _replace(temporary_updates, ENRICHED_UPDATES_FILE)
        _enriched_text = enriched
    updates["predicted_event_time"] = updates["departure_time"].fillna(updates["arrival_time"])
    upcoming = updates[
        updates["trip_id"].ne("")
        & updates["predicted_event_time"].between(now - 60, now + 3600)
        & updates["trip_update_timestamp"].between(updates_at - FRESH_SECONDS, updates_at + 30)
    ].copy()
    upcoming = upcoming.sort_values(["mode", "trip_id", "predicted_event_time", "stop_sequence"])
    next_stops = upcoming.drop_duplicates(["mode", "trip_id"])[[
        "mode", "trip_id", "stop_id", "stop_sequence", "predicted_event_time",
        "delay_seconds", "delay_event_type", "trip_update_timestamp",
    ]].rename(columns={"stop_id": "next_stop_id", "stop_sequence": "next_stop_sequence"})
    result = result.merge(next_stops, on=["mode", "trip_id"], how="left")
    result["delay_minutes"] = result["delay_seconds"] / 60
    result["snapshot_time"] = datetime.now().astimezone().isoformat(timespec="seconds")
    temporary = SNAPSHOT_FILE.with_suffix(".tmp")
    result.to_csv(temporary, index=False)
    _replace(temporary, SNAPSHOT_FILE)
    matched = (result.assign(matched=result["route_short_name"].fillna("").ne(""))
               .groupby("mode")["matched"].sum().to_dict()) if not result.empty else {}
    print(f"Saved {len(result)} vehicles to {SNAPSHOT_FILE}; matched by mode: {matched}")
    return result
