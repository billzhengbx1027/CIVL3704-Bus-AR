"""Download dedicated Sydney Trains and Metro GTFS schedules."""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TRAIN_STATIC = DATA / "static_train"
METRO_STATIC = DATA / "static_metro"
SCHEDULE_URL = "https://api.transport.nsw.gov.au/v1/gtfs/schedule/sydneytrains"
METRO_SCHEDULE_URL = "https://api.transport.nsw.gov.au/v2/gtfs/schedule/metro"
REQUIRED_FILES = ("routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "shapes.txt")
OPTIONAL_FILES = ("calendar.txt", "calendar_dates.txt")
# Touched after every successful check; unchanged files keep their old mtime so
# the route index, shape index and trip caches are not rebuilt for nothing.
CHECKED_MARKER = ".last_checked"


def download_static(destination: Path, url: str, label: str, api_key: str | None = None) -> list[str]:
    """Download a schedule and return the names of files whose content changed."""
    if api_key is None:
        load_dotenv(ROOT / ".env")
        api_key = os.getenv("TFNSW_API_KEY")
    if not api_key:
        raise RuntimeError("TFNSW_API_KEY was not found in .env")
    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{label.lower().replace(' ', '_')}_gtfs_", dir=DATA) as temporary:
        temporary = Path(temporary)
        archive_path = temporary / "schedule.zip"
        with requests.get(url, headers={"Authorization": f"apikey {api_key}"},
                          timeout=120, stream=True) as response:
            response.raise_for_status()
            with archive_path.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    output.write(chunk)
        with zipfile.ZipFile(archive_path) as archive:
            entries = {Path(name).name: name for name in archive.namelist() if not name.endswith("/")}
            missing = [name for name in REQUIRED_FILES if name not in entries]
            if missing:
                raise ValueError(f"{label} GTFS archive is missing: {', '.join(missing)}")
            files = [name for name in (*REQUIRED_FILES, *OPTIONAL_FILES) if name in entries]
            staged = temporary / "extracted"
            staged.mkdir()
            for name in files:
                with archive.open(entries[name]) as source, (staged / name).open("wb") as target:
                    shutil.copyfileobj(source, target)
            destination.mkdir(parents=True, exist_ok=True)
            changed = [name for name in files
                       if not (destination / name).exists()
                       or not filecmp.cmp(staged / name, destination / name, shallow=False)]
            for name in changed:
                os.replace(staged / name, destination / name)
    (destination / CHECKED_MARKER).touch()
    return changed


def last_checked(destination: Path) -> float | None:
    """Return when a schedule was last downloaded or confirmed unchanged."""
    for path in (destination / CHECKED_MARKER, destination / "trips.txt"):
        if path.exists():
            return path.stat().st_mtime
    return None


def download_train_static(api_key: str | None = None) -> list[str]:
    return download_static(TRAIN_STATIC, SCHEDULE_URL, "Sydney Trains", api_key)


def download_metro_static(api_key: str | None = None) -> list[str]:
    return download_static(METRO_STATIC, METRO_SCHEDULE_URL, "Metro", api_key)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download dedicated rail GTFS schedules")
    parser.add_argument("--metro", action="store_true", help="Download Metro instead of Sydney Trains")
    args = parser.parse_args()
    label = "Metro" if args.metro else "Sydney Trains"
    changed = download_metro_static() if args.metro else download_train_static()
    print(f"{label} GTFS: " + (f"updated {', '.join(changed)}" if changed else "already up to date"))
