"""Small, cached lookups for the local GTFS map display."""

from __future__ import annotations

import csv
import json
import os
import threading
from functools import lru_cache
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = PROJECT_ROOT / "data" / "static"
SHAPES_FILE = STATIC_ROOT / "shapes.txt"
INDEX_FILE = PROJECT_ROOT / "data" / "shape_offsets.json"
_index_lock = threading.Lock()


@lru_cache(maxsize=1)
def stop_lookup() -> dict[str, dict]:
    """Keep the static stop names and coordinates available for bus popups."""
    result = {}
    stops_file = STATIC_ROOT / "stops.txt"
    if not stops_file.exists():
        return result
    with stops_file.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            try:
                result[row["stop_id"]] = {
                    "id": row["stop_id"],
                    "name": row["stop_name"],
                    "latitude": float(row["stop_lat"]),
                    "longitude": float(row["stop_lon"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
    return result


@lru_cache(maxsize=1)
def trip_shape_lookup() -> dict[str, str]:
    result = {}
    trips_file = STATIC_ROOT / "trips.txt"
    if not trips_file.exists():
        return result
    with trips_file.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if row.get("trip_id") and row.get("shape_id"):
                result[row["trip_id"]] = row["shape_id"]
    return result


def _shape_index() -> dict[str, list[list[int]]]:
    """Index byte ranges once, without loading the nearly 1 GB shapes file."""
    if not SHAPES_FILE.exists():
        return {}

    with _index_lock:
        stat = SHAPES_FILE.stat()
        if INDEX_FILE.exists():
            try:
                cached = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
                if cached["size"] == stat.st_size and cached["mtime_ns"] == stat.st_mtime_ns:
                    return cached["shapes"]
            except (OSError, ValueError, KeyError, TypeError):
                pass

        shapes: dict[str, list[list[int]]] = {}
        with SHAPES_FILE.open("rb") as source:
            source.readline()  # header
            previous_id = None
            start = source.tell()
            while True:
                offset = source.tell()
                line = source.readline()
                if not line:
                    if previous_id is not None:
                        shapes.setdefault(previous_id, []).append([start, offset])
                    break
                shape_id = line.partition(b",")[0].strip(b'"').decode("utf-8")
                if shape_id != previous_id:
                    if previous_id is not None:
                        shapes.setdefault(previous_id, []).append([start, offset])
                    previous_id = shape_id
                    start = offset

        INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "shapes": shapes,
        }
        temporary = INDEX_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, INDEX_FILE)
        return shapes


def route_shape_for_trip(trip_id: str) -> dict | None:
    """Return the scheduled shape for one active trip, if static GTFS has it."""
    shape_id = trip_shape_lookup().get(trip_id)
    if not shape_id:
        return None
    spans = _shape_index().get(shape_id)
    if not spans:
        return None

    points = []
    with SHAPES_FILE.open("rb") as source:
        for start, end in spans:
            source.seek(start)
            rows = csv.reader(source.read(end - start).decode("utf-8").splitlines())
            for row in rows:
                try:
                    points.append((int(row[3]), float(row[1]), float(row[2])))
                except (IndexError, TypeError, ValueError):
                    continue
    points.sort(key=lambda point: point[0])
    return {
        "trip_id": trip_id,
        "shape_id": shape_id,
        "coordinates": [[latitude, longitude] for _, latitude, longitude in points],
    }
