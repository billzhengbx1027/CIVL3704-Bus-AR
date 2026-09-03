import pandas as pd
import time


# Load matched realtime data
df = pd.read_csv(
    "data/central_sydney_buses_matched.csv"
)

# Current Unix timestamp
current_time = int(time.time())


# Calculate age of each vehicle update
df["update_age_seconds"] = (
    current_time - df["timestamp"]
)


# Mark stale records
# Temporary threshold: 300 seconds
STALE_THRESHOLD = 300

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


# Valid record = not stale and no essential data missing
df["valid"] = (
    (~df["stale"]) &
    (~df["missing_essential_data"])
)


# Show results
print(
    df[
        [
            "vehicle_id",
            "route_short_name",
            "update_age_seconds",
            "stale",
            "missing_essential_data",
            "valid"
        ]
    ].head(10)
)


print("\nTotal buses:", len(df))
print("Valid buses:", df["valid"].sum())
print("Stale buses:", df["stale"].sum())
print(
    "Buses with missing essential data:",
    df["missing_essential_data"].sum()
)


# Save validated dataset
df.to_csv(
    "data/central_sydney_buses_validated.csv",
    index=False
)

print(
    "\nValidated data saved to "
    "data/central_sydney_buses_validated.csv"
)