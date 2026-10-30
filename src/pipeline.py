import time
from datetime import datetime

from transit_pipeline import build_snapshot, fetch_all
from train_static import (METRO_STATIC, TRAIN_STATIC, download_metro_static,
                          download_train_static)


# Vehicle Positions are small and move the map; Trip Updates are large (about
# 120,000 stop rows for buses) and change less often, so they are fetched less
# often. 15 s / 60 s uses about 43,000 TfNSW requests a day across the 6 feeds.
POSITIONS_INTERVAL_SECONDS = 15
UPDATES_INTERVAL_SECONDS = 60


def run_pipeline_cycle(include_updates: bool = True, updates_received_at: int | None = None):
    """Run one Central Sydney multimodal data cycle."""

    print("\n============================================")
    print("Pipeline cycle:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          "(positions + trip updates)" if include_updates else "(positions only)")
    print("============================================")

    for label, root, download in (("Sydney Trains", TRAIN_STATIC, download_train_static),
                                  ("Metro", METRO_STATIC, download_metro_static)):
        trips = root / "trips.txt"
        if not trips.exists() or time.time() - trips.stat().st_mtime > 24 * 3600:
            try:
                print(f"Refreshing {label} static GTFS...")
                download()
            except Exception as error:
                print(f"{label} static GTFS unavailable:", error)

    print("\n[1/2] Fetching Vehicle Positions" + (" and Trip Updates..." if include_updates else "..."))
    fetch_all(include_updates=include_updates)

    print("\n[2/2] Matching static GTFS and calculating next-stop delays...")
    build_snapshot(updates_received_at=updates_received_at)

    print("\nPipeline cycle completed successfully.")


def main():
    print("Starting the Central Sydney bus, train, metro and light rail pipeline.")
    print(f"Vehicle positions every {POSITIONS_INTERVAL_SECONDS} seconds, "
          f"trip updates every {UPDATES_INTERVAL_SECONDS} seconds")
    print("Press Ctrl+C to stop.")

    last_updates_at = None
    try:
        while True:
            cycle_started_at = time.time()
            include_updates = (last_updates_at is None
                               or cycle_started_at - last_updates_at >= UPDATES_INTERVAL_SECONDS - 1)

            try:
                run_pipeline_cycle(include_updates,
                                   int(cycle_started_at if include_updates else last_updates_at))
                if include_updates:
                    last_updates_at = cycle_started_at
            except Exception as error:
                print("Pipeline cycle failed:", error)

            elapsed = time.time() - cycle_started_at
            wait_time = max(0, POSITIONS_INTERVAL_SECONDS - elapsed)

            if wait_time > 0:
                print(f"\nNext cycle in {wait_time:.0f} seconds...")
                time.sleep(wait_time)
            else:
                print(f"\nCycle took longer than {POSITIONS_INTERVAL_SECONDS} seconds; restarting now...")

    except KeyboardInterrupt:
        print("\nPipeline stopped.")


if __name__ == "__main__":
    main()
