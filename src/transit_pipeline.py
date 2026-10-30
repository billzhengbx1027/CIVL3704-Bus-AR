"""Collect and join TfNSW bus, train, metro and light rail feeds."""

from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2
from google.protobuf.message import DecodeError

from map_area import bounds as map_bounds
from transit_modes import FEEDS, POSITION_DISPLAY_SECONDS, mode_for_route_type
from train_static import METRO_STATIC, TRAIN_STATIC


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


def _write_csv(path: Path, columns: tuple[str, ...], records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    os.replace(temporary, path)


def _feed(url: str, api_key: str) -> gtfs_realtime_pb2.FeedMessage:
    response = requests.get(url, headers={"Authorization": f"apikey {api_key}"}, timeout=30)
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


def fetch_all(api_key: str | None = None) -> dict:
    """Poll each source independently; report unavailable feeds without hiding others."""
    if api_key is None:
        load_dotenv(ROOT / ".env")
        api_key = os.getenv("TFNSW_API_KEY")
    if not api_key:
        raise RuntimeError("TFNSW_API_KEY was not found in .env")
    positions, updates, status = [], [], {}
    for source in FEEDS:
        state = {"mode": source.mode, "positions": "unavailable", "updates": "unavailable"}
        for kind, url, parser, target in (
            ("positions", source.vehicle_url, parse_positions, positions),
            ("updates", source.updates_url, parse_updates, updates),
        ):
            try:
                feed = _feed(url, api_key)
                received_at = int(time.time())
                records = (parser(feed, source.mode, source.name, received_at)
                           if kind == "positions" else parser(feed, source.mode, source.name))
                state[kind] = "ok"
                state[kind + "_count"] = len(records)
                if kind == "positions" and not any(_position_in_grace(row, received_at) for row in records):
                    cached = _cached_positions(received_at, source.name)
                    state["positions_cached_count"] = len(cached)
                    if cached:
                        records = cached
                target.extend(records)
            except (requests.RequestException, DecodeError, ValueError) as error:
                state[kind] = f"{type(error).__name__}: {error}"
                print(f"{source.name} {kind}: {state[kind]}")
                if kind == "positions":
                    cached = _cached_positions(int(time.time()), source.name)
                    target.extend(cached)
                    state["positions_cached_count"] = len(cached)
        status[source.name] = state
    if not any(state["positions"] == "ok" for state in status.values()):
        raise RuntimeError("All Vehicle Positions feeds failed; previous snapshot was kept")
    _write_csv(POSITIONS_FILE, POSITION_COLUMNS, positions)
    _write_csv(UPDATES_FILE, UPDATE_COLUMNS, updates)
    status["fetched_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    temporary = FEED_STATUS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, FEED_STATUS_FILE)
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


def _rail_stop_times(root: Path, trip_ids: set[str]) -> tuple[dict, dict]:
    by_stop, by_sequence = {}, {}
    if not trip_ids or not (root / "stop_times.txt").exists():
        return by_stop, by_sequence
    with (root / "stop_times.txt").open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            trip_id = row.get("trip_id", "")
            if trip_id not in trip_ids:
                continue
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


def build_snapshot(*, now: int | None = None) -> pd.DataFrame:
    """Join by trip_id and mode; complete GTFS route_id differs from realtime IDs."""
    now = int(time.time()) if now is None else now
    vehicles = pd.read_csv(POSITIONS_FILE, dtype=str, keep_default_na=False)
    routes = pd.read_csv(STATIC / "routes.txt", dtype=str, keep_default_na=False)
    trips = pd.read_csv(STATIC / "trips.txt", dtype=str, keep_default_na=False)
    routes["mode"] = routes["route_type"].map(mode_for_route_type)
    for mode, root in (("train", TRAIN_STATIC), ("metro", METRO_STATIC)):
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
    result = vehicles.merge(trip_routes, on=["mode", "trip_id"], how="left")

    updates = pd.read_csv(UPDATES_FILE, dtype=str, keep_default_na=False)
    for column in ("trip_update_timestamp", "arrival_time", "departure_time", "delay_seconds", "stop_sequence"):
        updates[column] = pd.to_numeric(updates[column], errors="coerce")
    for mode, root in (("train", TRAIN_STATIC), ("metro", METRO_STATIC)):
        _fill_rail_predictions(updates, vehicles, now, mode, root)
    temporary_updates = ENRICHED_UPDATES_FILE.with_suffix(".tmp")
    updates.to_csv(temporary_updates, index=False)
    os.replace(temporary_updates, ENRICHED_UPDATES_FILE)
    updates["predicted_event_time"] = updates["departure_time"].fillna(updates["arrival_time"])
    upcoming = updates[
        updates["trip_id"].ne("")
        & updates["predicted_event_time"].between(now - 60, now + 3600)
        & updates["trip_update_timestamp"].between(now - FRESH_SECONDS, now + 30)
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
    os.replace(temporary, SNAPSHOT_FILE)
    matched = (result.assign(matched=result["route_short_name"].fillna("").ne(""))
               .groupby("mode")["matched"].sum().to_dict()) if not result.empty else {}
    print(f"Saved {len(result)} vehicles to {SNAPSHOT_FILE}; matched by mode: {matched}")
    return result
