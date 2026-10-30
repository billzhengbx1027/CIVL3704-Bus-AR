"""Direct bus, train, metro and light rail routes around Central Sydney places."""

from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from map_area import bounds as map_bounds, contains as in_map_area
from transit_modes import MODES, mode_for_route_type


STATIC_FILES = ("stops.txt", "routes.txt", "trips.txt", "stop_times.txt")
FRESH_SECONDS = 120
ARRIVAL_WINDOW_SECONDS = 60 * 60
AREA_RADIUS_METRES = 250
SEARCH_RADIUS = {"bus": 250, "train": 500, "metro": 500, "light_rail": 350, "all": 500}
INDEX_VERSION = 5

# Approximate local landmarks. Stop coordinates and routes still come from GTFS.
PLACES = (
    ("Broadway", -33.8843, 151.1973, ("Broadway Shopping Centre",)),
    ("Central Station", -33.8830, 151.2065, ("Central",)),
    ("Town Hall", -33.8732, 151.2060, ("Town Hall Station",)),
    ("Circular Quay", -33.8612, 151.2100, ()),
    ("Chinatown", -33.8784, 151.2044, ()),
    ("Darling Harbour", -33.8728, 151.1990, ()),
    ("Haymarket", -33.8803, 151.2043, ()),
    ("Hyde Park", -33.8730, 151.2118, ()),
    ("University of Sydney", -33.8876, 151.1874, ("Sydney University", "USYD")),
    ("UTS", -33.8830, 151.2005, ("University of Technology Sydney",)),
    ("Surry Hills", -33.8840, 151.2123, ()),
    ("Oxford Street", -33.8780, 151.2146, ("Oxford St",)),
    ("Kings Cross", -33.8745, 151.2225, ()),
    ("Sydney Opera House", -33.8568, 151.2153, ("Opera House",)),
    ("Barangaroo", -33.8640, 151.2010, ()),
)


class RouteIndex:
    """Build a SQLite index of direct trips serving stops in the map area."""

    def __init__(self, static_root: Path, database: Path):
        self.static_root = Path(static_root).resolve()
        self.database = Path(database).resolve()
        self._lock = threading.Lock()
        self._building = False
        self.error = None

    def _sources(self) -> list[tuple[Path, set[str]]]:
        complete_modes = set(MODES)
        dedicated = []
        for mode, dirname in (("train", "static_train"), ("metro", "static_metro")):
            root = self.static_root.parent / dirname
            if all((root / name).exists() for name in STATIC_FILES):
                complete_modes.discard(mode)
                dedicated.append((root, {mode}))
        return [(self.static_root, complete_modes), *dedicated]

    def _fingerprint(self) -> str:
        return json.dumps({
            "index_version": INDEX_VERSION,
            "bounds": map_bounds(),
            "sources": [
                {"root": str(root), "modes": sorted(modes), "files": {
                    name: [root.joinpath(name).stat().st_size,
                           root.joinpath(name).stat().st_mtime_ns]
                    for name in STATIC_FILES
                }} for root, modes in self._sources()
            ],
        }, sort_keys=True)

    def is_ready(self) -> bool:
        if not self.database.exists():
            return False
        try:
            with closing(sqlite3.connect(self.database)) as db:
                row = db.execute("SELECT value FROM meta WHERE key = 'fingerprint'").fetchone()
            return bool(row and row[0] == self._fingerprint())
        except (OSError, sqlite3.Error):
            return False

    def ensure_ready(self) -> bool:
        if self.is_ready():
            return True
        with self._lock:
            if not self._building and self.error is None:
                self._building = True
                self.error = None
                threading.Thread(target=self._build_in_background, daemon=True).start()
        return False

    def _build_in_background(self) -> None:
        try:
            self.build()
        except (OSError, ValueError, sqlite3.Error, KeyError) as error:
            self.error = str(error)
        finally:
            with self._lock:
                self._building = False

    def build(self) -> None:
        fingerprint = self._fingerprint()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.database.with_suffix(".building.sqlite")
        temporary.unlink(missing_ok=True)
        try:
            db = sqlite3.connect(temporary)
            try:
                db.executescript("""
                    PRAGMA synchronous = OFF;
                    PRAGMA journal_mode = OFF;
                    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
                    CREATE TABLE stops (id TEXT PRIMARY KEY, name TEXT, latitude REAL, longitude REAL);
                    CREATE TABLE routes (id TEXT PRIMARY KEY, short_name TEXT, long_name TEXT, mode TEXT);
                    CREATE TABLE trips (id TEXT PRIMARY KEY, route_id TEXT, headsign TEXT);
                    CREATE TABLE trip_stops (
                        trip_id TEXT, stop_id TEXT, sequence INTEGER,
                        pickup_type TEXT, drop_off_type TEXT
                    );
                """)
                for root, modes in self._sources():
                    stop_ids = set()
                    with (root / "stops.txt").open(encoding="utf-8-sig", newline="") as source:
                        for row in csv.DictReader(source):
                            try:
                                lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
                            except (KeyError, ValueError):
                                continue
                            if in_map_area(lat, lon):
                                stop_ids.add(row["stop_id"])
                                db.execute("INSERT OR REPLACE INTO stops VALUES (?, ?, ?, ?)",
                                           (row["stop_id"], row["stop_name"], lat, lon))

                    route_ids = set()
                    with (root / "routes.txt").open(encoding="utf-8-sig", newline="") as source:
                        for row in csv.DictReader(source):
                            mode = mode_for_route_type(row.get("route_type"))
                            if mode not in modes:
                                continue
                            route_ids.add(row["route_id"])
                            db.execute("INSERT OR REPLACE INTO routes VALUES (?, ?, ?, ?)",
                                       (row["route_id"], row.get("route_short_name", ""),
                                        row.get("route_long_name", ""), mode))

                    trip_ids = set()
                    with (root / "trips.txt").open(encoding="utf-8-sig", newline="") as source:
                        for row in csv.DictReader(source):
                            if row.get("route_id") in route_ids:
                                trip_ids.add(row["trip_id"])
                                db.execute("INSERT OR REPLACE INTO trips VALUES (?, ?, ?)",
                                           (row["trip_id"], row["route_id"], row.get("trip_headsign", "")))

                    batch = []
                    with (root / "stop_times.txt").open(encoding="utf-8-sig", newline="") as source:
                        for row in csv.DictReader(source):
                            if row.get("trip_id") not in trip_ids or row.get("stop_id") not in stop_ids:
                                continue
                            try:
                                sequence = int(row["stop_sequence"])
                            except (KeyError, ValueError):
                                continue
                            batch.append((row["trip_id"], row["stop_id"], sequence,
                                          row.get("pickup_type", "0"), row.get("drop_off_type", "0")))
                            if len(batch) == 5000:
                                db.executemany("INSERT INTO trip_stops VALUES (?, ?, ?, ?, ?)", batch)
                                batch.clear()
                    if batch:
                        db.executemany("INSERT INTO trip_stops VALUES (?, ?, ?, ?, ?)", batch)
                db.executescript("""
                    CREATE INDEX trip_stops_by_stop ON trip_stops(stop_id, trip_id, sequence);
                    CREATE INDEX trip_stops_by_trip ON trip_stops(trip_id, sequence);
                """)
                db.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fingerprint,))
                db.commit()
            finally:
                db.close()
            if fingerprint != self._fingerprint():
                raise ValueError("GTFS files changed while the route index was being built")
            os.replace(temporary, self.database)
        finally:
            temporary.unlink(missing_ok=True)

    def find_stops(self, query: str, limit: int = 20) -> list[dict]:
        query = query.strip()
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute("""
                SELECT s.id, s.name, s.latitude, s.longitude
                FROM stops s
                WHERE s.name LIKE ? ESCAPE '\\'
                  AND EXISTS (SELECT 1 FROM trip_stops ts WHERE ts.stop_id = s.id)
                ORDER BY CASE WHEN s.name LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END,
                         s.name, s.id
                LIMIT ?
            """, (f"%{_like(query)}%", f"{_like(query)}%", limit)).fetchall()
        return [dict(id=id_, name=name, latitude=lat, longitude=lon)
                for id_, name, lat, lon in rows]

    def get_stop(self, stop_id: str) -> dict | None:
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute("SELECT id, name, latitude, longitude FROM stops WHERE id = ?",
                             (stop_id,)).fetchone()
        return dict(id=row[0], name=row[1], latitude=row[2], longitude=row[3]) if row else None

    def nearby_stops(self, latitude: float, longitude: float,
                     radius: int = AREA_RADIUS_METRES, limit: int = 60) -> list[dict]:
        lat_span = radius / 111_000
        lon_span = radius / (111_000 * math.cos(math.radians(latitude)))
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute("""
                SELECT s.id, s.name, s.latitude, s.longitude
                FROM stops s
                WHERE s.latitude BETWEEN ? AND ? AND s.longitude BETWEEN ? AND ?
                  AND EXISTS (SELECT 1 FROM trip_stops ts WHERE ts.stop_id = s.id)
            """, (latitude - lat_span, latitude + lat_span,
                  longitude - lon_span, longitude + lon_span)).fetchall()
        stops = [dict(id=id_, name=name, latitude=lat, longitude=lon,
                      distance_metres=round(_distance(latitude, longitude, lat, lon)))
                 for id_, name, lat, lon in rows]
        return sorted((stop for stop in stops if stop["distance_metres"] <= radius),
                      key=lambda stop: (stop["distance_metres"], stop["name"]))[:limit]

    def matching_area_trips(self, origins: list[dict], destinations: list[dict],
                            mode: str = "all") -> list[dict]:
        from_ids = [stop["id"] for stop in origins]
        to_ids = [stop["id"] for stop in destinations]
        from_marks = ",".join("?" for _ in from_ids)
        to_marks = ",".join("?" for _ in to_ids)
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute(f"""
                SELECT DISTINCT t.id, t.route_id, r.short_name, r.long_name, r.mode,
                                t.headsign, a.stop_id, b.stop_id
                FROM trip_stops a
                JOIN trip_stops b ON b.trip_id = a.trip_id AND b.sequence > a.sequence
                JOIN trips t ON t.id = a.trip_id
                JOIN routes r ON r.id = t.route_id
                WHERE a.stop_id IN ({from_marks}) AND b.stop_id IN ({to_marks})
                  AND COALESCE(a.pickup_type, '0') != '1'
                  AND COALESCE(b.drop_off_type, '0') != '1'
                  AND (? = 'all' OR r.mode = ?)
            """, from_ids + to_ids + [mode, mode]).fetchall()
        return [dict(trip_id=trip, route_id=route, route=short or long or route,
                     route_long_name=long, mode=trip_mode, headsign=headsign,
                     origin_id=start, destination_id=end)
                for trip, route, short, long, trip_mode, headsign, start, end in rows]

    def matching_trips(self, origin: str, destination: str, mode: str = "all") -> list[dict]:
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute("""
                SELECT DISTINCT t.id, t.route_id, r.short_name, r.long_name, r.mode, t.headsign
                FROM trip_stops a
                JOIN trip_stops b ON b.trip_id = a.trip_id AND b.sequence > a.sequence
                JOIN trips t ON t.id = a.trip_id
                JOIN routes r ON r.id = t.route_id
                WHERE a.stop_id = ? AND b.stop_id = ?
                  AND COALESCE(a.pickup_type, '0') != '1'
                  AND COALESCE(b.drop_off_type, '0') != '1'
                  AND (? = 'all' OR r.mode = ?)
            """, (origin, destination, mode, mode)).fetchall()
        return [dict(trip_id=trip, route_id=route, route=short or long or route,
                     route_long_name=long, mode=trip_mode, headsign=headsign)
                for trip, route, short, long, trip_mode, headsign in rows]


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6_371_000 * math.hypot(x, y)


def find_places(index: RouteIndex, query: str, limit: int = 12) -> list[dict]:
    query = query.strip().casefold()
    if len(query) < 2:
        return []
    places = [dict(name=name, latitude=lat, longitude=lon)
              for name, lat, lon, aliases in PLACES
              if query in name.casefold() or any(query in alias.casefold() for alias in aliases)]
    seen = {place["name"].casefold() for place in places}
    for stop in index.find_stops(query, limit=limit * 3):
        name = stop["name"]
        if name.casefold() not in seen:
            places.append(dict(name=name, latitude=stop["latitude"], longitude=stop["longitude"]))
            seen.add(name.casefold())
        if len(places) >= limit:
            break
    return places[:limit]


def resolve_place(index: RouteIndex, value: str) -> dict:
    value = value.strip()
    if value.startswith("@"):
        try:
            lat, lon = (float(part) for part in value[1:].split(","))
        except ValueError as error:
            raise ValueError("Choose a point on the map again") from error
        if not in_map_area(lat, lon):
            raise ValueError("Choose a point within the map area")
        return dict(name="Map point", latitude=lat, longitude=lon)
    if len(value) < 2:
        raise ValueError("Enter a place or choose a point on the map")
    for name, lat, lon, aliases in PLACES:
        if value.casefold() in [name.casefold(), *(alias.casefold() for alias in aliases)]:
            return dict(name=name, latitude=lat, longitude=lon)
    partial = [(name, lat, lon) for name, lat, lon, aliases in PLACES
               if value.casefold() in name.casefold()
               or any(value.casefold() in alias.casefold() for alias in aliases)]
    if len(partial) == 1:
        name, lat, lon = partial[0]
        return dict(name=name, latitude=lat, longitude=lon)
    matches = index.find_stops(value, limit=50)
    if not matches:
        raise ValueError(f"No place near '{value}' was found in the map area. Try a nearby street or choose on the map.")
    # Exact stop names may appear on both sides of a street; use their centre.
    exact = [stop for stop in matches if stop["name"].casefold() == value.casefold()]
    if len(matches) == 50 and not exact:
        raise ValueError(f"'{value}' matches many stops. Enter a more specific place or choose on the map.")
    candidates = exact or [stop for stop in matches
                           if _distance(matches[0]["latitude"], matches[0]["longitude"],
                                        stop["latitude"], stop["longitude"]) <= 600]
    return dict(name=value, latitude=sum(stop["latitude"] for stop in candidates) / len(candidates),
                longitude=sum(stop["longitude"] for stop in candidates) / len(candidates))


_updates_lock = threading.Lock()
_updates_cache: tuple[tuple, dict] | None = None


def _stop_updates(updates_file: Path) -> dict[tuple[str, str], list[tuple]]:
    """Group Trip Update rows by (mode, trip_id), parsing the CSV once per pipeline cycle."""
    global _updates_cache
    stat = updates_file.stat()
    signature = (str(updates_file), stat.st_size, stat.st_mtime_ns)
    with _updates_lock:
        if _updates_cache and _updates_cache[0] == signature:
            return _updates_cache[1]
        grouped: dict[tuple[str, str], list[tuple]] = {}
        with updates_file.open(encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source):
                grouped.setdefault((row.get("mode") or "bus", row.get("trip_id")), []).append((
                    row.get("stop_id"), row.get("trip_update_timestamp"),
                    _number(row.get("arrival_time")), _number(row.get("departure_time")),
                ))
        _updates_cache = (signature, grouped)
        return grouped


def upcoming_arrivals(trips: list[dict], vehicles: list[dict], updates_file: Path,
                      snapshot_time: str | None, origin: str, *, now: float | None = None) -> list[dict]:
    """Return only fresh, active vehicles with a future prediction at origin."""
    now = time.time() if now is None else now
    try:
        snapshot_age = now - datetime.fromisoformat(snapshot_time).timestamp()
    except (TypeError, ValueError):
        return []
    if snapshot_age > FRESH_SECONDS or not updates_file.exists():
        return []

    matching = {(trip["mode"], trip["trip_id"]): trip for trip in trips}
    active = {(vehicle.get("mode", "bus"), str(vehicle.get("trip_id"))): vehicle
              for vehicle in vehicles
              if (vehicle.get("mode", "bus"), str(vehicle.get("trip_id"))) in matching
              and vehicle.get("valid") is not False
              and _fresh(vehicle.get("timestamp"), now)}
    if not active:
        return []

    predictions = {}
    updates = _stop_updates(updates_file)
    for key in active:
        for stop_id, updated_at, arrival, departure in updates.get(key, ()):
            if stop_id != origin or not _fresh(updated_at, now):
                continue
            eta = arrival if arrival is not None else departure
            if eta is None or eta < now - 60 or eta > now + ARRIVAL_WINDOW_SECONDS:
                continue
            eta = max(eta, now)
            if key not in predictions or eta < predictions[key]:
                predictions[key] = eta

    result = []
    for key, eta in predictions.items():
        vehicle = active[key]
        trip = matching[key]
        result.append({
            "vehicle_id": vehicle.get("vehicle_id"),
            "trip_id": key[1], "mode": trip["mode"],
            "route_id": trip["route_id"],
            "route": trip["route"],
            "headsign": trip["headsign"],
            "eta": datetime.fromtimestamp(eta).astimezone().isoformat(timespec="seconds"),
            "minutes": max(0, int((eta - now + 59) // 60)),
            "latitude": vehicle.get("latitude"),
            "longitude": vehicle.get("longitude"),
        })
    return sorted(result, key=lambda item: item["eta"])


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fresh(timestamp, now: float) -> bool:
    value = _number(timestamp)
    return value is not None and 0 <= now - value <= FRESH_SECONDS


def search_journey(index: RouteIndex, origin: str, destination: str,
                   snapshot: dict, updates_file: Path, *, mode: str = "all",
                   now: float | None = None) -> dict:
    if mode not in (*MODES, "all"):
        raise ValueError("Choose bus, train, metro, light rail or all modes")
    if origin == destination:
        raise ValueError("Choose two different stops")
    start = index.get_stop(origin)
    end = index.get_stop(destination)
    if not start or not end:
        raise ValueError("Choose stops in the Central Sydney map area")
    trips = index.matching_trips(origin, destination, mode)
    arrivals = [] if snapshot.get("source") == "demo" else upcoming_arrivals(
        trips, snapshot.get("vehicles", snapshot.get("buses", [])), updates_file, snapshot.get("snapshot_time"),
        origin, now=now)
    grouped = {}
    for trip in trips:
        key = (trip["mode"], trip["route"])
        if key not in grouped:
            grouped[key] = {
                "route_id": trip["route_id"], "route": trip["route"], "mode": trip["mode"],
                "route_long_name": trip["route_long_name"],
                "headsign": trip["headsign"], "buses": [],
            }
        elif grouped[key]["headsign"] != trip["headsign"]:
            grouped[key]["headsign"] = ""
            grouped[key]["route_long_name"] = ""
    for bus in arrivals:
        grouped[(bus["mode"], bus["route"])]["buses"].append(bus)
    routes = sorted(grouped.values(), key=lambda route: (
        not bool(route["buses"]), route["route"], route["headsign"]))
    return {
        "origin": start, "destination": end, "routes": routes,
        "source": snapshot.get("source"),
        "snapshot_time": snapshot.get("snapshot_time"),
        "arrival_window_minutes": ARRIVAL_WINDOW_SECONDS // 60,
    }


def search_area_journey(index: RouteIndex, origin: str, destination: str,
                        snapshot: dict, updates_file: Path, *, mode: str = "all",
                        now: float | None = None) -> dict:
    """Find direct trips between any boardable stops near two places."""
    if mode not in (*MODES, "all"):
        raise ValueError("Choose bus, train, metro, light rail or all modes")
    start = resolve_place(index, origin)
    end = resolve_place(index, destination)
    if _distance(start["latitude"], start["longitude"],
                 end["latitude"], end["longitude"]) < 150:
        raise ValueError("Choose two different areas")
    radius = SEARCH_RADIUS[mode]
    from_stops = index.nearby_stops(start["latitude"], start["longitude"], radius=radius)
    to_stops = index.nearby_stops(end["latitude"], end["longitude"], radius=radius)
    if not from_stops or not to_stops:
        raise ValueError(f"No stops were found within {radius} m of one of those places. Choose another nearby point.")

    from_by_id = {stop["id"]: stop for stop in from_stops}
    to_by_id = {stop["id"]: stop for stop in to_stops}
    grouped = {}
    candidates = {}
    for trip in index.matching_area_trips(from_stops, to_stops, mode):
        boarding = from_by_id[trip["origin_id"]]
        alighting = to_by_id[trip["destination_id"]]
        if (boarding["distance_metres"] > SEARCH_RADIUS[trip["mode"]]
                or alighting["distance_metres"] > SEARCH_RADIUS[trip["mode"]]):
            continue
        if boarding["id"] == alighting["id"]:
            continue
        score = boarding["distance_metres"] + alighting["distance_metres"]
        key = (trip["mode"], trip["route"])
        if key not in grouped:
            grouped[key] = {
                "route_id": trip["route_id"], "route": trip["route"], "mode": trip["mode"],
                "route_long_name": trip["route_long_name"],
                "headsign": trip["headsign"], "boarding_stop": boarding,
                "alighting_stop": alighting, "walk_metres": score, "buses": [],
            }
        else:
            if grouped[key]["headsign"] != trip["headsign"]:
                grouped[key]["headsign"] = ""
                grouped[key]["route_long_name"] = ""
            if score < grouped[key]["walk_metres"]:
                grouped[key].update(boarding_stop=boarding, alighting_stop=alighting,
                                    walk_metres=score)
        candidate_key = (trip["mode"], trip["trip_id"], boarding["id"])
        if candidate_key not in candidates or score < candidates[candidate_key]["score"]:
            candidates[candidate_key] = dict(trip=trip, route_key=key,
                                             boarding=boarding, alighting=alighting,
                                             score=score)

    now = time.time() if now is None else now
    try:
        snapshot_age = now - datetime.fromisoformat(snapshot.get("snapshot_time")).timestamp()
    except (TypeError, ValueError):
        snapshot_age = float("inf")
    if (snapshot.get("source") != "demo" and snapshot_age <= FRESH_SECONDS
            and updates_file.exists() and candidates):
        active = {(vehicle.get("mode", "bus"), str(vehicle.get("trip_id"))): vehicle
                  for vehicle in snapshot.get("vehicles", snapshot.get("buses", []))
                  if vehicle.get("valid") is not False and _fresh(vehicle.get("timestamp"), now)}
        predictions = {}
        updates = _stop_updates(updates_file)
        for key in active.keys() & {candidate_key[:2] for candidate_key in candidates}:
            for stop_id, updated_at, arrival, departure in updates.get(key, ()):
                candidate = candidates.get((*key, stop_id))
                if not candidate or not _fresh(updated_at, now):
                    continue
                eta = arrival if arrival is not None else departure
                if eta is None or eta < now - 60 or eta > now + ARRIVAL_WINDOW_SECONDS:
                    continue
                eta = max(eta, now)
                choice = (candidate["score"], eta)
                if key not in predictions or choice < predictions[key][0]:
                    predictions[key] = (choice, candidate, eta)
        for key, (_, candidate, eta) in predictions.items():
            trip = candidate["trip"]
            vehicle = active[key]
            grouped[candidate["route_key"]]["buses"].append({
                "vehicle_id": vehicle.get("vehicle_id"), "trip_id": key[1], "mode": trip["mode"],
                "route_id": trip["route_id"], "route": trip["route"],
                "headsign": trip["headsign"],
                "eta": datetime.fromtimestamp(eta).astimezone().isoformat(timespec="seconds"),
                "minutes": max(0, int((eta - now + 59) // 60)),
                "latitude": vehicle.get("latitude"), "longitude": vehicle.get("longitude"),
                "boarding_stop": candidate["boarding"],
                "alighting_stop": candidate["alighting"],
            })
    routes = list(grouped.values())
    for route in routes:
        route["buses"].sort(key=lambda bus: bus["eta"])
    routes.sort(key=lambda route: (not bool(route["buses"]),
                                   route["route"].upper().startswith("N"), route["walk_metres"],
                                   route["route"], route["headsign"]))
    return {
        "origin": start, "destination": end,
        "radius_metres": radius,
        "origin_stop_count": len(from_stops), "destination_stop_count": len(to_stops),
        "routes": routes, "source": snapshot.get("source"),
        "snapshot_time": snapshot.get("snapshot_time"),
        "arrival_window_minutes": ARRIVAL_WINDOW_SECONDS // 60,
    }
