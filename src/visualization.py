"""Browser-based projection display for Central Sydney bus delays."""

from __future__ import annotations

import argparse
import json
import math
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LATEST_DATA_FILE = PROJECT_ROOT / "data" / "central_sydney_buses_with_delay.csv"
WEB_ROOT = PROJECT_ROOT / "web"

LAT_MIN = -33.90
LAT_MAX = -33.84
LON_MIN = 151.18
LON_MAX = 151.23


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


def load_bus_snapshot(data_file: Path) -> dict:
    """Read the latest pipeline CSV and return records for the web display."""
    if not data_file.exists():
        raise FileNotFoundError(
            f"No snapshot found at {data_file}. Run the pipeline or start with --demo."
        )

    frame = pd.read_csv(
        data_file,
        dtype={"vehicle_id": str, "trip_id": str, "route_short_name": str},
    )
    required = {"latitude", "longitude"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Snapshot is missing columns: {', '.join(sorted(missing))}")

    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce")
    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce")
    frame = frame.dropna(subset=["latitude", "longitude"])
    frame = frame[
        frame["latitude"].between(LAT_MIN, LAT_MAX)
        & frame["longitude"].between(LON_MIN, LON_MAX)
    ]

    if "valid" in frame.columns:
        frame = frame[frame["valid"].map(_truthy)]

    buses = []
    for _, row in frame.iterrows():
        buses.append(
            {
                "vehicle_id": _clean(row.get("vehicle_id")),
                "trip_id": _clean(row.get("trip_id")),
                "route": _clean(row.get("route_short_name")) or "?",
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "delay_seconds": _clean(row.get("delay_seconds")),
                "delay_minutes": _clean(row.get("delay_minutes")),
                "delay_status": _clean(row.get("delay_status")) or "no_delay_data",
                "next_stop_id": _clean(row.get("next_stop_id")),
                "timestamp": _clean(row.get("timestamp")),
            }
        )

    if "snapshot_time" in frame.columns and not frame.empty:
        snapshot_time = str(frame["snapshot_time"].iloc[0])
    else:
        snapshot_time = datetime.fromtimestamp(data_file.stat().st_mtime).astimezone().isoformat(
            timespec="seconds"
        )

    return {
        "source": "live",
        "snapshot_time": snapshot_time,
        "count": len(buses),
        "buses": buses,
    }


def demo_snapshot() -> dict:
    """Return fixed sample data so projection can be tested without an API key."""
    demo_rows = [
        ("333", -33.8730, 151.2060, "on_time", 35),
        ("343", -33.8785, 151.2110, "minor_delay", 210),
        ("392", -33.8840, 151.2180, "major_delay", 480),
        ("373", -33.8680, 151.2140, "early", -95),
        ("396", -33.8910, 151.2050, "no_delay_data", None),
        ("440", -33.8615, 151.1990, "on_time", 70),
        ("311", -33.8755, 151.2210, "minor_delay", 180),
        ("320", -33.8860, 151.1960, "major_delay", 620),
    ]
    buses = []
    for index, (route, lat, lon, status, delay) in enumerate(demo_rows, start=1):
        buses.append(
            {
                "vehicle_id": f"DEMO-{index:02d}",
                "trip_id": f"DEMO-TRIP-{index:02d}",
                "route": route,
                "latitude": lat,
                "longitude": lon,
                "delay_seconds": delay,
                "delay_minutes": None if delay is None else round(delay / 60, 1),
                "delay_status": status,
                "next_stop_id": None,
                "timestamp": None,
            }
        )
    return {
        "source": "demo",
        "snapshot_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "count": len(buses),
        "buses": buses,
    }


def make_handler(data_file: Path = LATEST_DATA_FILE, demo: bool = False):
    """Build an HTTP handler connected to the selected snapshot source."""

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
            if path == "/health":
                self._send_json({"status": "ok"})
                return
            if path == "/api/buses":
                try:
                    payload = demo_snapshot() if demo else load_bus_snapshot(data_file)
                    self._send_json(payload)
                except (FileNotFoundError, ValueError, pd.errors.ParserError) as error:
                    self._send_json(
                        {
                            "source": "error",
                            "snapshot_time": None,
                            "count": 0,
                            "buses": [],
                            "error": str(error),
                        },
                        503,
                    )
                return

            self._send_json({"error": "Not found"}, 404)

        def log_message(self, format, *args):
            if args and str(args[0]).startswith("GET /api/buses"):
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
    parser.add_argument("--demo", action="store_true", help="show fixed demonstration buses")
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
