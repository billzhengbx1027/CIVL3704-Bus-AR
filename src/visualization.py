"""Browser-based display for Central Sydney public transport delays."""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
from map_area import bounds as map_bounds, load_area, save_area
from map_data import route_shape_for_trip, stop_lookup
from journey_search import RouteIndex, find_places, search_area_journey, search_journey
from transit_modes import POSITION_DISPLAY_SECONDS


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LATEST_DATA_FILE = PROJECT_ROOT / "data" / "central_sydney_vehicles_with_delay.csv"
WEB_ROOT = PROJECT_ROOT / "web"
ROUTE_INDEX_FILE = PROJECT_ROOT / "data" / "journey_routes.sqlite"
TRIP_UPDATES_FILE = PROJECT_ROOT / "data" / "transit_trip_updates_enriched.csv"
FEED_STATUS_FILE = PROJECT_ROOT / "data" / "feed_status.json"


def _clean(value):
    """Convert pandas/numpy values into JSON-safe Python values."""
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def load_vehicle_snapshot(data_file: Path) -> dict:
    """Read the latest pipeline CSV and return records for the web display."""
    if not data_file.exists():
        raise FileNotFoundError(
            f"No snapshot found at {data_file}. Run the pipeline or start with --demo."
        )

    frame = pd.read_csv(
        data_file,
        dtype={
            "vehicle_id": str,
            "trip_id": str,
            "route_short_name": str,
            "next_stop_id": str,
            "current_stop_id": str,
        },
    )
    required = {"latitude", "longitude"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Snapshot is missing columns: {', '.join(sorted(missing))}")

    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce")
    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce")
    frame = frame.dropna(subset=["latitude", "longitude"])
    lat_min, lat_max, lon_min, lon_max = map_bounds()
    frame = frame[
        frame["latitude"].between(lat_min, lat_max)
        & frame["longitude"].between(lon_min, lon_max)
    ]

    if "timestamp" in frame.columns:
        # Retain short feed gaps as visibly stale markers rather than hiding a mode.
        captured_at = (pd.to_datetime(frame["snapshot_time"].iloc[0], utc=True, errors="coerce")
                       if "snapshot_time" in frame.columns and not frame.empty else pd.NaT)
        reference = captured_at.timestamp() if pd.notna(captured_at) else data_file.stat().st_mtime
        timestamps = pd.to_numeric(frame["timestamp"], errors="coerce")
        frame = frame[timestamps.gt(0) & (reference - timestamps).between(-30, POSITION_DISPLAY_SECONDS)]
    elif "valid" in frame.columns:
        frame = frame[frame["valid"].map(_truthy)]
    if "mode" in frame.columns and "route_short_name" in frame.columns:
        # TfNSW labels non-revenue rail runs with an empty public line name.
        frame = frame[~frame["mode"].isin(("train", "metro")) | frame["route_short_name"].fillna("").ne("")]

    stops = stop_lookup()
    vehicles = []
    visible_stops = {}
    for row in frame.to_dict("records"):
        next_stop_id = _clean(row.get("next_stop_id"))
        current_stop_id = _clean(row.get("current_stop_id"))
        next_stop = stops.get(next_stop_id) if next_stop_id else None
        current_stop = stops.get(current_stop_id) if current_stop_id else None
        if next_stop:
            visible_stops[next_stop_id] = next_stop
        vehicles.append(
            {
                "mode": _clean(row.get("mode")) or "bus",
                "feed": _clean(row.get("feed")),
                "vehicle_id": _clean(row.get("vehicle_id")),
                "trip_id": _clean(row.get("trip_id")),
                "route": _clean(row.get("route_short_name")) or "?",
                "route_long_name": _clean(row.get("route_long_name")),
                "headsign": _clean(row.get("trip_headsign")),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "delay_seconds": _clean(row.get("delay_seconds")),
                "delay_event_type": _clean(row.get("delay_event_type")),
                "delay_minutes": _clean(row.get("delay_minutes")),
                "delay_status": _clean(row.get("delay_status")) or "no_delay_data",
                "next_stop_id": next_stop_id,
                "next_stop": next_stop,
                "current_stop_id": current_stop_id,
                "current_stop_name": current_stop["name"] if current_stop else None,
                "stop_status": _clean(row.get("stop_status")),
                "timestamp": _clean(row.get("timestamp")),
                "valid": _truthy(row.get("valid")) if "valid" in frame.columns else True,
            }
        )

    if "snapshot_time" in frame.columns and not frame.empty:
        snapshot_time = str(frame["snapshot_time"].iloc[0])
    else:
        snapshot_time = datetime.fromtimestamp(data_file.stat().st_mtime).astimezone().isoformat(
            timespec="seconds"
        )

    try:
        feed_status = json.loads(FEED_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        feed_status = {}
    return {
        "source": "live",
        "snapshot_time": snapshot_time,
        "count": len(vehicles),
        "vehicles": vehicles,
        "stops": list(visible_stops.values()),
        "feed_status": feed_status,
    }


_snapshot_lock = threading.Lock()
_snapshot_cache: tuple[tuple, dict] | None = None


def _file_signature(path: Path):
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def cached_vehicle_snapshot(data_file: Path) -> dict:
    """Rebuild the snapshot only after the pipeline writes new data (about once a minute)."""
    global _snapshot_cache
    signature = (str(data_file), _file_signature(data_file),
                 _file_signature(FEED_STATUS_FILE), map_bounds())
    with _snapshot_lock:
        if _snapshot_cache and _snapshot_cache[0] == signature:
            return _snapshot_cache[1]
        payload = load_vehicle_snapshot(data_file)
        _snapshot_cache = (signature, payload)
        return payload


def demo_snapshot() -> dict:
    """Return fixed sample data so projection can be tested without an API key."""
    demo_rows = [
        ("bus", "333", -33.8730, 151.2060, "on_time", 35),
        ("bus", "343", -33.8785, 151.2110, "minor_delay", 210),
        ("bus", "392", -33.8840, 151.2180, "major_delay", 480),
        ("bus", "373", -33.8680, 151.2140, "early", -95),
        ("train", "T1", -33.8830, 151.2060, "on_time", 30),
        ("train", "T4", -33.8730, 151.2060, "minor_delay", 200),
        ("metro", "M1", -33.8700, 151.2040, "on_time", 20),
        ("light_rail", "L1", -33.8790, 151.2020, "on_time", 20),
        ("light_rail", "L2", -33.8620, 151.2100, "no_delay_data", None),
    ]
    vehicles = []
    for index, (mode, route, lat, lon, status, delay) in enumerate(demo_rows, start=1):
        vehicles.append(
            {
                "mode": mode,
                "vehicle_id": f"DEMO-{index:02d}",
                "trip_id": f"DEMO-TRIP-{index:02d}",
                "route": route,
                "latitude": lat,
                "longitude": lon,
                "delay_seconds": delay,
                "delay_event_type": "departure",
                "delay_minutes": None if delay is None else round(delay / 60, 1),
                "delay_status": status,
                "next_stop_id": None,
                "next_stop": None,
                "current_stop_id": None,
                "current_stop_name": None,
                "stop_status": None,
                "timestamp": int(time.time()),
            }
        )
    return {
        "source": "demo",
        "snapshot_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "count": len(vehicles),
        "vehicles": vehicles,
        "stops": [],
    }


def make_handler(data_file: Path = LATEST_DATA_FILE, demo: bool = False):
    """Build an HTTP handler connected to the selected snapshot source."""
    route_index = RouteIndex(PROJECT_ROOT / "data" / "static", ROUTE_INDEX_FILE)

    class ProjectionHandler(BaseHTTPRequestHandler):
        def _send_bytes(self, content: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, payload: dict, status: int = 200):
            self._send_bytes(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def do_GET(self):
            path = urlparse(self.path).path

            if path == "/":
                html = (WEB_ROOT / "templates" / "index.html").read_bytes()
                self._send_bytes(html, "text/html; charset=utf-8")
                return
            if path == "/static/style.css":
                self._send_bytes(
                    (WEB_ROOT / "static" / "style.css").read_bytes(),
                    "text/css; charset=utf-8",
                )
                return
            if path == "/static/projection.js":
                self._send_bytes(
                    (WEB_ROOT / "static" / "projection.js").read_bytes(),
                    "text/javascript; charset=utf-8",
                )
                return
            if path == "/static/delay_rules.js":
                self._send_bytes(
                    (WEB_ROOT / "static" / "delay_rules.js").read_bytes(),
                    "text/javascript; charset=utf-8",
                )
                return
            if path == "/health":
                self._send_json({"status": "ok"})
                return
            if path == "/api/map-area":
                self._send_json(load_area())
                return
            if path in {"/api/vehicles", "/api/buses"}:
                try:
                    payload = demo_snapshot() if demo else cached_vehicle_snapshot(data_file)
                    if path == "/api/buses":
                        payload = {**payload, "buses": [vehicle for vehicle in payload["vehicles"]
                                                        if vehicle["mode"] == "bus"]}
                        payload["count"] = len(payload["buses"])
                        bus_stop_ids = {vehicle["next_stop_id"] for vehicle in payload["buses"]
                                        if vehicle["next_stop_id"]}
                        payload["stops"] = [stop for stop in payload["stops"]
                                            if stop["id"] in bus_stop_ids]
                        payload.pop("vehicles")
                    self._send_json(payload)
                except (FileNotFoundError, ValueError, pd.errors.ParserError) as error:
                    self._send_json(
                        {
                            "source": "error",
                            "snapshot_time": None,
                            "count": 0,
                            "vehicles": [],
                            "error": str(error),
                        },
                        503,
                    )
                return

            if path in {"/api/stops", "/api/places", "/api/journey", "/api/journey-area"}:
                if not route_index.ensure_ready():
                    if route_index.error:
                        self._send_json({"error": route_index.error}, 503)
                    else:
                        self._send_json({"status": "indexing", "message": "Preparing transit routes from GTFS data"}, 202)
                    return
                query = parse_qs(urlparse(self.path).query)
                if path == "/api/stops":
                    self._send_json({"stops": route_index.find_stops(query.get("q", [""])[0][:100])})
                    return
                if path == "/api/places":
                    self._send_json({"places": find_places(route_index, query.get("q", [""])[0][:100])})
                    return
                origin = query.get("from", [""])[0]
                destination = query.get("to", [""])[0]
                mode = query.get("mode", ["all"])[0]
                if not origin or not destination:
                    self._send_json({"error": "Enter both a starting place and a destination"}, 400)
                    return
                try:
                    snapshot = demo_snapshot() if demo else cached_vehicle_snapshot(data_file)
                except (FileNotFoundError, ValueError, pd.errors.ParserError):
                    snapshot = {"source": "unavailable", "snapshot_time": None, "vehicles": []}
                try:
                    if path == "/api/journey-area":
                        payload = search_area_journey(route_index, origin, destination,
                                                      snapshot, TRIP_UPDATES_FILE, mode=mode)
                    else:
                        payload = search_journey(route_index, origin, destination,
                                                 snapshot, TRIP_UPDATES_FILE, mode=mode)
                except ValueError as error:
                    self._send_json({"error": str(error)}, 400)
                    return
                self._send_json(payload)
                return

            if path == "/api/route-shape":
                trip_id = parse_qs(urlparse(self.path).query).get("trip_id", [""])[0]
                if not trip_id:
                    self._send_json({"error": "trip_id is required"}, 400)
                    return
                try:
                    shape = route_shape_for_trip(trip_id)
                except (OSError, ValueError) as error:
                    self._send_json({"error": str(error)}, 503)
                    return
                if shape is None:
                    self._send_json({"error": "Route shape unavailable"}, 404)
                else:
                    self._send_json(shape)
                return

            self._send_json({"error": "Not found"}, 404)

        def do_POST(self):
            if urlparse(self.path).path != "/api/map-area":
                self._send_json({"error": "Not found"}, 404)
                return
            # Calibration rewrites map_area.json, so only accept it from this computer.
            if self.client_address[0] not in {"127.0.0.1", "::1"}:
                self._send_json({"error": "Map calibration is only allowed from this computer"}, 403)
                return
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 10_000)
                area = save_area(json.loads(self.rfile.read(length) or b"{}"))
            except (ValueError, KeyError, TypeError) as error:
                self._send_json({"error": f"Invalid map area: {error}"}, 400)
                return
            except OSError as error:
                self._send_json({"error": str(error)}, 503)
                return
            self._send_json(area)

        def log_message(self, format, *args):
            if args and str(args[0]).startswith(("GET /api/buses", "GET /api/vehicles")):
                return
            super().log_message(format, *args)

    return ProjectionHandler


def create_server(host: str = "127.0.0.1", port: int = 8080, *, demo: bool = False):
    return ThreadingHTTPServer((host, port), make_handler(LATEST_DATA_FILE, demo))


def run_server(host: str = "127.0.0.1", port: int = 8080, *, demo: bool = False):
    server = create_server(host, port, demo=demo)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nProjection display stopped.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Central Sydney projection display")
    parser.add_argument("--demo", action="store_true", help="show fixed demonstration vehicles")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    run_server(args.host, args.port, demo=args.demo)


if __name__ == "__main__":
    main()
