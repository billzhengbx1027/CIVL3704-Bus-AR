import time
from datetime import datetime

from transit_pipeline import build_snapshot, fetch_all
from train_static import (METRO_STATIC, TRAIN_STATIC, download_metro_static,
                          download_train_static)


POLL_INTERVAL_SECONDS = 60


def run_pipeline_cycle():
    """Run one complete Central Sydney multimodal data cycle."""

    print("\n============================================")
    print("Pipeline cycle:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
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

    print("\n[1/2] Fetching Vehicle Positions and Trip Updates...")
    fetch_all()

    print("\n[2/2] Matching static GTFS and calculating next-stop delays...")
    build_snapshot()

    print("\nPipeline cycle completed successfully.")


def main():
    print("Starting the Central Sydney bus, train, metro and light rail pipeline.")
    print(f"Cycle interval: {POLL_INTERVAL_SECONDS} seconds")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            cycle_started_at = time.time()

            try:
                run_pipeline_cycle()
            except Exception as error:
                print("Pipeline cycle failed:", error)

            elapsed = time.time() - cycle_started_at
            wait_time = max(0, POLL_INTERVAL_SECONDS - elapsed)

            if wait_time > 0:
                print(f"\nNext cycle in {wait_time:.0f} seconds...")
                time.sleep(wait_time)
            else:
                print("\nCycle took longer than 60 seconds; restarting now...")

    except KeyboardInterrupt:
        print("\nPipeline stopped.")


if __name__ == "__main__":
    main()
