import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from delay_processing import main as process_delay
from gtfs_realtime import fetch_realtime_buses
from gtfs_trip_updates import fetch_trip_updates


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_SCRIPT = PROJECT_ROOT / "src" / "gtfs_static.py"
POLL_INTERVAL_SECONDS = 30


def run_static_matching():
    """Run the existing Static GTFS matching script once."""

    subprocess.run(
        [sys.executable, str(STATIC_SCRIPT)],
        cwd=PROJECT_ROOT,
        check=True,
    )


def run_pipeline_cycle():
    """Run one complete Central Sydney delay-data cycle."""

    print("\n============================================")
    print("Pipeline cycle:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("============================================")

    print("\n[1/4] Fetching Vehicle Positions...")
    fetch_realtime_buses()

    print("\n[2/4] Matching Static GTFS...")
    run_static_matching()

    print("\n[3/4] Fetching Trip Updates...")
    fetch_trip_updates()

    print("\n[4/4] Calculating Central Sydney delays...")
    process_delay()

    print("\nPipeline cycle completed successfully.")


def main():
    print("Starting the Central Sydney bus-delay pipeline.")
    print(f"Cycle interval: {POLL_INTERVAL_SECONDS} seconds")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            cycle_started_at = time.time()

            try:
                run_pipeline_cycle()
            except subprocess.CalledProcessError as error:
                print("A pipeline script failed:", error)
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
