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
TRAIN_ROOT = PROJECT_ROOT / "data" / "static_train"
METRO_ROOT = PROJECT_ROOT / "data" / "static_metro"
SHAPES_FILE = STATIC_ROOT / "shapes.txt"
INDEX_FILE = PROJECT_ROOT / "data" / "shape_offsets.json"
TRAIN_INDEX_FILE = PROJECT_ROOT / "data" / "train_shape_offsets.json"
METRO_INDEX_FILE = PROJECT_ROOT / "data" / "metro_shape_offsets.json"
_index_lock = threading.Lock()
_loaded_indexes: dict[Path, tuple[tuple, dict]] = {}


def _signature(name: str) -> tuple:
    return tuple((root.joinpath(name).stat().st_size, root.joinpath(name).stat().st_mtime_ns)
                 if root.joinpath(name).exists() else None for root in (STATIC_ROOT, TRAIN_ROOT, METRO_ROOT))


def stop_lookup() -> dict[str, dict]:
    return _stop_lookup(_signature("stops.txt"))


@lru_cache(maxsize=4)
def _stop_lookup(_version: tuple) -> dict[str, dict]:
    """Keep static stop names and coordinates available for vehicle popups."""
    result = {}
    for root in (STATIC_ROOT, TRAIN_ROOT, METRO_ROOT):
        stops_file = root / "stops.txt"
        if not stops_file.exists():
            continue
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


def trip_shape_lookup() -> dict[str, tuple[str, Path]]:
    return _trip_shape_lookup(_signature("trips.txt"))


@lru_cache(maxsize=4)
def _trip_shape_lookup(_version: tuple) -> dict[str, tuple[str, Path]]:
    result = {}
    for root in (STATIC_ROOT, TRAIN_ROOT, METRO_ROOT):
        trips_file = root / "trips.txt"
        if not trips_file.exists():
            continue
        with trips_file.open(encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source):
                if row.get("trip_id") and row.get("shape_id"):
                    result[row["trip_id"]] = (row["shape_id"], root)
    return result


def _shape_index(shapes_file: Path, index_file: Path) -> dict[str, list[list[int]]]:
    """Index byte ranges once, without loading the nearly 1 GB shapes file."""
    if not shapes_file.exists():
        return {}

    with _index_lock:
        stat = shapes_file.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        if _loaded_indexes.get(index_file, (None,))[0] == signature:
            return _loaded_indexes[index_file][1]
        if index_file.exists():
            try:
                cached = json.loads(index_file.read_text(encoding="utf-8"))
                if cached["size"] == stat.st_size and cached["mtime_ns"] == stat.st_mtime_ns:
                    _loaded_indexes[index_file] = (signature, cached["shapes"])
                    return cached["shapes"]
            except (OSError, ValueError, KeyError, TypeError):
                pass

        shapes: dict[str, list[list[int]]] = {}
        with shapes_file.open("rb") as source:
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

        index_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "shapes": shapes,
        }
        temporary = index_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, index_file)
        _loaded_indexes[index_file] = (signature, shapes)
        return shapes


def route_shape_for_trip(trip_id: str) -> dict | None:
    """Return the scheduled shape for one active trip, if static GTFS has it."""
    shape_info = trip_shape_lookup().get(trip_id)
    if not shape_info:
        return None
    shape_id, root = shape_info
    shapes_file = root / "shapes.txt"
    index_file = (TRAIN_INDEX_FILE if root == TRAIN_ROOT else
                  METRO_INDEX_FILE if root == METRO_ROOT else INDEX_FILE)
    spans = _shape_index(shapes_file, index_file).get(shape_id)
    if not spans:
        return None

    points = []
    with shapes_file.open("rb") as source:
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
