import pandas as pd


# =========================================================
# 1. Load Static GTFS files
# =========================================================

print("Loading Static GTFS data...")

routes = pd.read_csv(
    "data/static/routes.txt",
    dtype=str
)

trips = pd.read_csv(
    "data/static/trips.txt",
    dtype=str
)

print("Static GTFS loaded successfully.")
print("Number of routes:", len(routes))
print("Number of trips:", len(trips))


# =========================================================
# 2. Load Realtime bus data
# =========================================================

print("\nLoading realtime bus data...")

realtime = pd.read_csv(
    "data/central_sydney_buses.csv",
    dtype=str
)

print("Realtime data loaded successfully.")
print("Number of realtime buses:", len(realtime))


# =========================================================
# 3. Check required columns
# =========================================================

required_realtime_columns = [
    "vehicle_id",
    "trip_id",
    "latitude",
    "longitude",
    "timestamp"
]

missing_columns = [
    column
    for column in required_realtime_columns
    if column not in realtime.columns
]

if missing_columns:
    print("\nERROR: Missing columns in realtime data:")
    print(missing_columns)
    print("Please run gtfs_realtime.py again with trip_id included.")
    exit()


# =========================================================
# 4. Prepare Static GTFS trip-route information
# =========================================================

# trips.txt tells us which route each trip belongs to
# routes.txt gives us the actual public route name

trip_route_data = trips[
    [
        "trip_id",
        "route_id",
        "service_id",
        "trip_headsign",
        "direction_id"
    ]
].copy()


route_data = routes[
    [
        "route_id",
        "route_short_name",
        "route_long_name"
    ]
].copy()


# Match trips.txt with routes.txt using route_id
trip_route_data = trip_route_data.merge(
    route_data,
    on="route_id",
    how="left"
)


print("\nTrip and route information prepared successfully.")


# =========================================================
# 5. Match Realtime buses with Static GTFS
# =========================================================

matched = realtime.merge(
    trip_route_data,
    on="trip_id",
    how="left"
)


# =========================================================
# 6. Calculate matching success
# =========================================================

matched_count = matched["route_short_name"].notna().sum()
total_count = len(matched)

if total_count > 0:
    match_rate = matched_count / total_count * 100
else:
    match_rate = 0


print("\n========================================")
print("GTFS MATCHING RESULT")
print("========================================")

print("Total realtime buses:", total_count)
print("Successfully matched:", matched_count)
print(f"Match rate: {match_rate:.1f}%")


# =========================================================
# 7. Show sample matched buses
# =========================================================

display_columns = [
    "vehicle_id",
    "trip_id",
    "route_short_name",
    "route_long_name",
    "trip_headsign",
    "direction_id",
    "latitude",
    "longitude",
    "timestamp"
]

print("\nSample matched buses:")

print(
    matched[display_columns]
    .head(10)
    .to_string(index=False)
)


# =========================================================
# 8. Show unmatched records
# =========================================================

unmatched = matched[
    matched["route_short_name"].isna()
].copy()

print("\nUnmatched buses:", len(unmatched))

if len(unmatched) > 0:
    print("\nSample unmatched trip IDs:")

    print(
        unmatched[
            [
                "vehicle_id",
                "trip_id"
            ]
        ]
        .head(10)
        .to_string(index=False)
    )


# =========================================================
# 9. Save matched dataset
# =========================================================

output_columns = [
    "vehicle_id",
    "trip_id",
    "route_id",
    "route_short_name",
    "route_long_name",
    "trip_headsign",
    "direction_id",
    "latitude",
    "longitude",
    "timestamp",
    "current_stop_id",
    "current_stop_sequence",
    "stop_status",
    "update_age_seconds",
    "stale",
    "missing_essential_data",
    "invalid_coordinates",
    "valid"
]

for optional_column in ("current_stop_id", "current_stop_sequence", "stop_status"):
    if optional_column not in matched.columns:
        matched[optional_column] = None

matched[output_columns].to_csv(
    "data/central_sydney_buses_matched.csv",
    index=False
)


print("\nMatched data saved to:")
print("data/central_sydney_buses_matched.csv")
