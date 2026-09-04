import time
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUSES_FILE = PROJECT_ROOT / "data" / "central_sydney_buses_matched.csv"
TRIP_UPDATES_FILE = PROJECT_ROOT / "data" / "trip_updates_sample.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "central_sydney_buses_with_delay.csv"


def delay_status(delay_seconds):
    """Temporary project categories for later visualisation."""

    if pd.isna(delay_seconds):
        return "no_delay_data"
    if delay_seconds < -60:
        return "early"
    if delay_seconds <= 120:
        return "on_time"
    if delay_seconds <= 300:
        return "minor_delay"
    return "major_delay"


def main():
    buses = pd.read_csv(
        BUSES_FILE,
        dtype={"trip_id": str},
    )
    updates = pd.read_csv(
        TRIP_UPDATES_FILE,
        dtype={"trip_id": str, "stop_id": str},
    )

    numeric_columns = [
        "trip_update_timestamp",
        "arrival_time",
        "departure_time",
        "arrival_delay",
        "departure_delay",
        "delay_seconds",
        "stop_sequence",
    ]

    for column in numeric_columns:
        updates[column] = pd.to_numeric(
            updates[column], errors="coerce"
        )

    # Prefer predicted arrival time; use departure time when needed.
    updates["predicted_event_time"] = updates["arrival_time"].fillna(
        updates["departure_time"]
    )

    # Use each Trip Update's own timestamp so saved snapshots remain reproducible.
    updates["reference_time"] = updates["trip_update_timestamp"].fillna(
        int(time.time())
    )

    upcoming = updates[
        updates["trip_id"].notna()
        & updates["predicted_event_time"].notna()
        & updates["delay_seconds"].notna()
        & (updates["predicted_event_time"] >= updates["reference_time"])
    ].copy()

    # The earliest future event is treated as the vehicle's next stop.
    next_stops = (
        upcoming.sort_values(
            ["trip_id", "predicted_event_time", "stop_sequence"]
        )
        .groupby("trip_id", as_index=False)
        .first()
    )

    next_stops = next_stops[
        [
            "trip_id",
            "stop_id",
            "stop_sequence",
            "predicted_event_time",
            "delay_seconds",
            "trip_update_timestamp",
        ]
    ].rename(
        columns={
            "stop_id": "next_stop_id",
            "stop_sequence": "next_stop_sequence",
        }
    )

    result = buses.merge(next_stops, on="trip_id", how="left")
    result["delay_minutes"] = result["delay_seconds"] / 60
    result["delay_status"] = result["delay_seconds"].apply(delay_status)

    result.to_csv(OUTPUT_FILE, index=False)

    buses_with_delay = int(result["delay_seconds"].notna().sum())
    coverage = buses_with_delay / len(result) * 100 if len(result) else 0

    print("\n========================================")
    print("CENTRAL SYDNEY DELAY RESULT")
    print("========================================")
    print("Central Sydney buses:", len(result))
    print("Buses with delay data:", buses_with_delay)
    print(f"Delay coverage: {coverage:.1f}%")

    print("\nDelay status counts:")
    print(result["delay_status"].value_counts().to_string())

    display_columns = [
        "vehicle_id",
        "trip_id",
        "route_short_name",
        "next_stop_id",
        "delay_seconds",
        "delay_minutes",
        "delay_status",
    ]

    print("\nSample buses with delay:")
    print(
        result[result["delay_seconds"].notna()][display_columns]
        .head(10)
        .to_string(index=False)
    )
    print("\nSaved to:", OUTPUT_FILE)


if __name__ == "__main__":
    main()
