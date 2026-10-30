"""Shared map extent for the pipeline, route index and web display."""

from __future__ import annotations

import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AREA_FILE = PROJECT_ROOT / "map_area.json"
# Original Central Sydney extent, used when map_area.json is missing or invalid.
DEFAULT_AREA = {"south": -33.90, "west": 151.18, "north": -33.84, "east": 151.23}
_cache: tuple[tuple | None, dict] | None = None


def _validated(area: dict) -> dict:
    south, west, north, east = (float(area[key]) for key in ("south", "west", "north", "east"))
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("Map area must have south < north and west < east")
    return {"south": south, "west": west, "north": north, "east": east}


def load_area(area_file: Path = AREA_FILE) -> dict:
    """Return the current extent, re-reading the file only after it changes."""
    global _cache
    try:
        stat = area_file.stat()
        signature = (str(area_file), stat.st_size, stat.st_mtime_ns)
    except OSError:
        signature = None
    if _cache and _cache[0] == signature:
        return _cache[1]
    area = dict(DEFAULT_AREA)
    if signature:
        try:
            saved = json.loads(area_file.read_text(encoding="utf-8"))
            area = {**saved, **_validated(saved)}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    _cache = (signature, area)
    return area


def save_area(area: dict, area_file: Path = AREA_FILE) -> dict:
    current = load_area(area_file)
    updated = {**current, **_validated(area)}
    updated["note"] = "Calibrated in projection mode."
    temporary = area_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, area_file)
    return load_area(area_file)


def bounds() -> tuple[float, float, float, float]:
    """Return (lat_min, lat_max, lon_min, lon_max)."""
    area = load_area()
    return area["south"], area["north"], area["west"], area["east"]


def contains(latitude: float, longitude: float) -> bool:
    lat_min, lat_max, lon_min, lon_max = bounds()
    return lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max
