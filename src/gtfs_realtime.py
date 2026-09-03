import os
import time
import requests
import pandas as pd

from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2


# =========================================================
# 1. Load API key
# =========================================================

load_dotenv()

api_key = os.getenv("TFNSW_API_KEY")

if not api_key:
    print("ERROR: API key not found.")
    exit()

print("API key loaded successfully.")


# =========================================================
# 2. Request GTFS-Realtime Vehicle Positions
# =========================================================

url = "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/buses"

headers = {
    "Authorization": f"apikey {api_key}"
}

response = requests.get(
    url,
    headers=headers,
    timeout=30
)

print("Status code:", response.status_code)
print("Data size:", len(response.content), "bytes")

if response.status_code != 200:
    print("ERROR: Failed to retrieve GTFS-Realtime data.")
    exit()


# Record the time when the feed was received
current_time = int(time.time())


# =========================================================
# 3. Decode GTFS-Realtime feed
# =========================================================

feed = gtfs_realtime_pb2.FeedMessage()

try:
    feed.ParseFromString(response.content)
except Exception as e:
    print("ERROR: Failed to decode GTFS-Realtime feed.")
    print(e)
    exit()

print("Number of entities:", len(feed.entity))


# =========================================================
# 4. Extract vehicle information
# =========================================================

bus_data = []

for entity in feed.entity:

    if entity.HasField("vehicle"):

        vehicle = entity.vehicle

        bus_data.append({

            "vehicle_id":
                vehicle.vehicle.id,

            "trip_id":
                vehicle.trip.trip_id,

            "route_id_realtime":
                vehicle.trip.route_id,

            "latitude":
                vehicle.position.latitude,

            "longitude":
                vehicle.position.longitude,

            "timestamp":
                vehicle.timestamp
        })


df = pd.DataFrame(bus_data)

print("Number of vehicle records:", len(df))


# =========================================================
# 5. Data validation
# =========================================================

# Calculate how old each vehicle update is
df["update_age_seconds"] = (
    current_time - df["timestamp"]
)


# Temporary testing threshold
STALE_THRESHOLD = 120

df["stale"] = (
    df["update_age_seconds"] > STALE_THRESHOLD
)


# Check essential fields
essential_columns = [
    "vehicle_id",
    "trip_id",
    "latitude",
    "longitude",
    "timestamp"
]

df["missing_essential_data"] = (
    df[essential_columns]
    .isna()
    .any(axis=1)
)


# Also treat empty IDs as missing
df["missing_essential_data"] = (
    df["missing_essential_data"]
    |
    (df["vehicle_id"].astype(str).str.strip() == "")
    |
    (df["trip_id"].astype(str).str.strip() == "")
)


# Check whether coordinates are reasonable
df["invalid_coordinates"] = (
    (df["latitude"] < -90)
    | (df["latitude"] > 90)
    | (df["longitude"] < -180)
    | (df["longitude"] > 180)
)


# Final validity flag
df["valid"] = (
    (~df["stale"])
    & (~df["missing_essential_data"])
    & (~df["invalid_coordinates"])
)


# =========================================================
# 6. Filter Central Sydney
# =========================================================

LAT_MIN = -33.90
LAT_MAX = -33.84

LON_MIN = 151.18
LON_MAX = 151.23


central_df = df[
    (df["latitude"] >= LAT_MIN)
    & (df["latitude"] <= LAT_MAX)
    & (df["longitude"] >= LON_MIN)
    & (df["longitude"] <= LON_MAX)
].copy()


# =========================================================
# 7. Show validation summary
# =========================================================

print("\n========================================")
print("REALTIME DATA SUMMARY")
print("========================================")

print("Total buses in feed:", len(df))
print("Buses in Central Sydney:", len(central_df))

print(
    "Valid Central Sydney buses:",
    central_df["valid"].sum()
)

print(
    "Stale Central Sydney buses:",
    central_df["stale"].sum()
)

print(
    "Missing essential data:",
    central_df["missing_essential_data"].sum()
)

print(
    "Invalid coordinates:",
    central_df["invalid_coordinates"].sum()
)


print("\nUpdate age statistics:")

print(
    central_df["update_age_seconds"].describe()
)


# =========================================================
# 8. Show sample records
# =========================================================

display_columns = [
    "vehicle_id",
    "trip_id",
    "route_id_realtime",
    "latitude",
    "longitude",
    "update_age_seconds",
    "stale",
    "valid"
]

print("\nSample Central Sydney buses:")

print(
    central_df[
        display_columns
    ]
    .head(10)
    .to_string(index=False)
)


# =========================================================
# 9. Save output
# =========================================================

central_df.to_csv(
    "data/central_sydney_buses.csv",
    index=False
)

print("\nData saved to:")
print("data/central_sydney_buses.csv")