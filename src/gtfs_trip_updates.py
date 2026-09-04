import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = PROJECT_ROOT / "data" / "trip_updates_sample.csv"
TRIP_UPDATES_URL = (
    "https://api.transport.nsw.gov.au/v1/gtfs/realtime/buses"
)


load_dotenv(PROJECT_ROOT / ".env")
API_KEY = os.getenv("TFNSW_API_KEY")

if not API_KEY:
    raise RuntimeError("TFNSW_API_KEY was not found in .env")


def optional_event_value(stop_update, event_name, field_name):
    """Read an optional arrival/departure field safely."""

    if not stop_update.HasField(event_name):
        return None

    event = getattr(stop_update, event_name)

    if not event.HasField(field_name):
        return None

    return getattr(event, field_name)


def fetch_trip_updates():
    response = requests.get(
        TRIP_UPDATES_URL,
        headers={"Authorization": f"apikey {API_KEY}"},
        timeout=30,
    )
    response.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)

    records = []
    trip_update_count = 0

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue

        trip_update_count += 1
        trip_update = entity.trip_update
        trip = trip_update.trip

        trip_id = trip.trip_id if trip.HasField("trip_id") else ""
        route_id = trip.route_id if trip.HasField("route_id") else ""
        start_date = trip.start_date if trip.HasField("start_date") else ""

        for stop_update in trip_update.stop_time_update:
            arrival_delay = optional_event_value(
                stop_update, "arrival", "delay"
            )
            departure_delay = optional_event_value(
                stop_update, "departure", "delay"
            )
            arrival_time = optional_event_value(
                stop_update, "arrival", "time"
            )
            departure_time = optional_event_value(
                stop_update, "departure", "time"
            )

            if arrival_delay is not None:
                delay_seconds = arrival_delay
            else:
                delay_seconds = departure_delay

            records.append(
                {
                    "trip_id": trip_id,
                    "route_id": route_id,
                    "start_date": start_date,
                    "trip_update_timestamp": (
                        trip_update.timestamp
                        if trip_update.HasField("timestamp")
                        else None
                    ),
                    "stop_id": (
                        stop_update.stop_id
                        if stop_update.HasField("stop_id")
                        else ""
                    ),
                    "stop_sequence": (
                        stop_update.stop_sequence
                        if stop_update.HasField("stop_sequence")
                        else None
                    ),
                    "arrival_delay": arrival_delay,
                    "departure_delay": departure_delay,
                    "arrival_time": arrival_time,
                    "departure_time": departure_time,
                    "delay_seconds": delay_seconds,
                }
            )

    df = pd.DataFrame(records)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_FILE, index=False)

    print("Trip update entities:", trip_update_count)
    print("Stop time update rows:", len(df))

    if df.empty:
        print("No stop time updates were returned.")
        return

    print("Rows with direct delay:", int(df["delay_seconds"].notna().sum()))
    print("Rows with arrival time:", int(df["arrival_time"].notna().sum()))
    print("Rows with departure time:", int(df["departure_time"].notna().sum()))

    display_columns = [
        "trip_id",
        "route_id",
        "stop_id",
        "stop_sequence",
        "arrival_delay",
        "departure_delay",
        "delay_seconds",
    ]

    print("\nSample rows containing delay values:")
    print(
        df[df["delay_seconds"].notna()][display_columns]
        .head(10)
        .to_string(index=False)
    )
    print("\nSaved to:", OUTPUT_FILE)


if __name__ == "__main__":
    try:
        fetch_trip_updates()
    except requests.RequestException as error:
        print("Trip Updates API request failed:", error)
    except Exception as error:
        print("Trip Updates processing failed:", error)