import os
import time
from datetime import datetime
from pathlib import Path
import subprocess
import sys

import pandas as pd
import requests
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = PROJECT_ROOT / "data" / "central_sydney_buses.csv"

VEHICLE_POSITIONS_URL = (
    "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/buses"
)

POLL_INTERVAL_SECONDS = 60
STALE_THRESHOLD_SECONDS = 120

LAT_MIN = -33.90
LAT_MAX = -33.84
LON_MIN = 151.18
LON_MAX = 151.23


load_dotenv(PROJECT_ROOT / ".env")
API_KEY = os.getenv("TFNSW_API_KEY")

if not API_KEY:
    raise RuntimeError("TFNSW_API_KEY was not found in .env")


def fetch_realtime_buses():
    """Fetch, validate and save one Vehicle Positions snapshot."""

    received_at = int(time.time())
    response = requests.get(
        VEHICLE_POSITIONS_URL,
        headers={"Authorization": f"apikey {API_KEY}"},
        timeout=30,
    )
    response.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)

    records = []

    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue

        vehicle = entity.vehicle

        records.append(
            {
                "vehicle_id": (
                    vehicle.vehicle.id
                    if vehicle.HasField("vehicle")
                    else ""
                ),
                "trip_id": (
                    vehicle.trip.trip_id
                    if vehicle.HasField("trip")
                    else ""
                ),
                "route_id_realtime": (
                    vehicle.trip.route_id
                    if vehicle.HasField("trip")
                    else ""
                ),
                "latitude": (
                    vehicle.position.latitude
                    if vehicle.HasField("position")
                    else None
                ),
                "longitude": (
                    vehicle.position.longitude
                    if vehicle.HasField("position")
                    else None
                ),
                "timestamp": vehicle.timestamp or None,
                "current_stop_id": (
                    vehicle.stop_id if vehicle.HasField("stop_id") else ""
                ),
                "current_stop_sequence": (
                    vehicle.current_stop_sequence
                    if vehicle.HasField("current_stop_sequence") else None
                ),
                "stop_status": (
                    gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus.Name(
                        vehicle.current_status
                    )
                    if vehicle.HasField("current_status") else ""
                ),
            }
        )

    columns = [
        "vehicle_id",
        "trip_id",
        "route_id_realtime",
        "latitude",
        "longitude",
        "timestamp",
        "current_stop_id",
        "current_stop_sequence",
        "stop_status",
    ]
    df = pd.DataFrame(records, columns=columns)

    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    df["update_age_seconds"] = received_at - df["timestamp"]
    df["stale"] = (
        df["update_age_seconds"] > STALE_THRESHOLD_SECONDS
    )

    essential_columns = [
        "vehicle_id",
        "trip_id",
        "latitude",
        "longitude",
        "timestamp",
    ]
    df["missing_essential_data"] = (
        df[essential_columns].isna().any(axis=1)
        | (df["vehicle_id"].astype(str).str.strip() == "")
        | (df["trip_id"].astype(str).str.strip() == "")
    )

    df["invalid_coordinates"] = (
        (df["latitude"] < -90)
        | (df["latitude"] > 90)
        | (df["longitude"] < -180)
        | (df["longitude"] > 180)
    )

    df["valid"] = (
        ~df["stale"]
        & ~df["missing_essential_data"]
        & ~df["invalid_coordinates"]
    )

    central_df = df[
        df["latitude"].between(LAT_MIN, LAT_MAX)
        & df["longitude"].between(LON_MIN, LON_MAX)
    ].copy()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    temporary_file = OUTPUT_FILE.with_suffix(".tmp")
    central_df.to_csv(temporary_file, index=False)
    os.replace(temporary_file, OUTPUT_FILE)

    print("\n========================================")
    print("Update time:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("Total buses in feed:", len(df))
    print("Buses in Central Sydney:", len(central_df))
    print("Valid Central Sydney buses:", int(central_df["valid"].sum()))
    print("Saved to:", OUTPUT_FILE)


def main():
    print("Starting GTFS-Realtime polling.")
    print(f"Refresh interval: {POLL_INTERVAL_SECONDS} seconds")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            cycle_started_at = time.time()

            try:
                fetch_realtime_buses()
                print("Running Static GTFS matching...")

                subprocess.run(
                    [
                        sys.executable,
                        str(PROJECT_ROOT / "src" / "gtfs_static.py")
                    ],
                    cwd=PROJECT_ROOT,
                    check=True
                )
            except requests.RequestException as error:
                print("API request failed:", error)
            except Exception as error:
                print("Processing failed:", error)

            elapsed = time.time() - cycle_started_at
            wait_time = max(0, POLL_INTERVAL_SECONDS - elapsed)
            print(f"Next update in {wait_time:.0f} seconds...")
            time.sleep(wait_time)

    except KeyboardInterrupt:
        print("\nRealtime polling stopped.")


if __name__ == "__main__":
    main()
