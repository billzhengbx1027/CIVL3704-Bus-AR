import time
from datetime import datetime
from pathlib import Path

import pandas as pd


# =========================================================
# 1. File paths
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

BUSES_FILE = (
    PROJECT_ROOT
    / "data"
    / "central_sydney_buses_matched.csv"
)

TRIP_UPDATES_FILE = (
    PROJECT_ROOT
    / "data"
    / "trip_updates_sample.csv"
)

# Latest snapshot: overwritten every cycle
OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "central_sydney_buses_with_delay.csv"
)

# Historical snapshots: appended every cycle
HISTORY_DIR = PROJECT_ROOT / "data" / "history"


# =========================================================
# 2. Delay classification
# =========================================================

def delay_status(delay_seconds):
    """
    Temporary delay categories for visualisation.

    Negative value = early
    Positive value = late
    """

    if pd.isna(delay_seconds):
        return "no_delay_data"

    if delay_seconds < -60:
        return "early"

    if delay_seconds <= 120:
        return "on_time"

    if delay_seconds <= 300:
        return "minor_delay"

    return "major_delay"


# =========================================================
# 3. Main processing
# =========================================================

def main():

    # -----------------------------------------------------
    # Load Central Sydney buses
    # -----------------------------------------------------

    buses = pd.read_csv(
        BUSES_FILE,
        dtype={"trip_id": str},
    )

    # -----------------------------------------------------
    # Load Trip Updates
    # -----------------------------------------------------

    updates = pd.read_csv(
        TRIP_UPDATES_FILE,
        dtype={
            "trip_id": str,
            "stop_id": str,
        },
    )

    # -----------------------------------------------------
    # Convert required columns to numeric values
    # -----------------------------------------------------

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
            updates[column],
            errors="coerce",
        )

    # -----------------------------------------------------
    # Determine predicted event time
    # -----------------------------------------------------

    if "delay_event_type" not in updates.columns:
        updates["delay_event_type"] = "arrival"
    updates["predicted_event_time"] = (
        updates["departure_time"]
        .where(updates["delay_event_type"] == "departure", updates["arrival_time"])
        .fillna(updates["arrival_time"])
        .fillna(updates["departure_time"])
    )

    # A predicted stop that has already passed is not an upcoming stop,
    # even when the trip update itself is old.
    updates["reference_time"] = (
        updates["trip_update_timestamp"].fillna(
            int(time.time())
        ).clip(lower=int(time.time()))
    )

    # -----------------------------------------------------
    # Keep future stop updates
    # -----------------------------------------------------

    upcoming = updates[
        updates["trip_id"].notna()
        & updates["predicted_event_time"].notna()
        & updates["delay_seconds"].notna()
        & (
            updates["predicted_event_time"]
            >= updates["reference_time"]
        )
    ].copy()

    # -----------------------------------------------------
    # Select the next stop for each trip
    # -----------------------------------------------------

    next_stops = (
        upcoming
        .sort_values(
            [
                "trip_id",
                "predicted_event_time",
                "stop_sequence",
            ]
        )
        .groupby(
            "trip_id",
            as_index=False,
        )
        .first()
    )

    next_stops = next_stops[
        [
            "trip_id",
            "stop_id",
            "stop_sequence",
            "predicted_event_time",
            "delay_seconds",
            "delay_event_type",
            "trip_update_timestamp",
        ]
    ].rename(
        columns={
            "stop_id": "next_stop_id",
            "stop_sequence": "next_stop_sequence",
        }
    )

    # -----------------------------------------------------
    # Match delay data to individual buses
    # -----------------------------------------------------

    result = buses.merge(
        next_stops,
        on="trip_id",
        how="left",
    )

    result["delay_minutes"] = (
        result["delay_seconds"] / 60
    )

    result["delay_status"] = (
        result["delay_seconds"].apply(
            delay_status
        )
    )

    # -----------------------------------------------------
    # Add snapshot timestamp
    # -----------------------------------------------------

    snapshot_time = datetime.now().astimezone()

    result.insert(
        0,
        "snapshot_time",
        snapshot_time.isoformat(
            timespec="seconds"
        ),
    )

    # -----------------------------------------------------
    # Save latest snapshot
    # -----------------------------------------------------

    # This file is overwritten each cycle.
    # The visualisation can always read the latest state.
    result.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    # -----------------------------------------------------
    # Append historical data
    # -----------------------------------------------------

    HISTORY_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    history_file = HISTORY_DIR / (
        f"bus_delay_history_"
        f"{snapshot_time:%Y-%m-%d}.csv"
    )

    # Include column names only when creating a new file.
    write_header = not history_file.exists()

    result.to_csv(
        history_file,
        mode="a",
        header=write_header,
        index=False,
    )

    # -----------------------------------------------------
    # Calculate coverage
    # -----------------------------------------------------

    buses_with_delay = int(
        result["delay_seconds"].notna().sum()
    )

    if len(result) > 0:
        coverage = (
            buses_with_delay
            / len(result)
            * 100
        )
    else:
        coverage = 0

    # -----------------------------------------------------
    # Display summary
    # -----------------------------------------------------

    print("\n========================================")
    print("CENTRAL SYDNEY DELAY RESULT")
    print("========================================")

    print(
        "Snapshot time:",
        snapshot_time.isoformat(
            timespec="seconds"
        ),
    )

    print(
        "Central Sydney buses:",
        len(result),
    )

    print(
        "Buses with delay data:",
        buses_with_delay,
    )

    print(
        f"Delay coverage: {coverage:.1f}%"
    )

    print("\nDelay status counts:")

    print(
        result["delay_status"]
        .value_counts()
        .to_string()
    )

    # -----------------------------------------------------
    # Display sample buses
    # -----------------------------------------------------

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
        result[
            result["delay_seconds"].notna()
        ][display_columns]
        .head(10)
        .to_string(index=False)
    )

    print(
        "\nLatest snapshot saved to:",
        OUTPUT_FILE,
    )

    print(
        "History appended to:",
        history_file,
    )


# =========================================================
# 4. Run script
# =========================================================

if __name__ == "__main__":
    main()
