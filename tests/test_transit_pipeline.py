"""Check multimodal GTFS parsing and rail schedule fallback."""

import csv
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
from google.transit import gtfs_realtime_pb2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import transit_pipeline
import visualization
from transit_modes import Feed


class TransitPipelineTests(unittest.TestCase):
    def test_position_freshness_uses_each_feeds_receive_time(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "data") as directory:
            root = Path(directory)
            clock = [1000]
            positions = gtfs_realtime_pb2.FeedMessage()
            positions.header.gtfs_realtime_version = "2.0"
            vehicle = positions.entity.add().vehicle
            vehicle.position.latitude = -33.88
            vehicle.position.longitude = 151.20
            vehicle.timestamp = 1120
            updates = gtfs_realtime_pb2.FeedMessage()
            updates.header.gtfs_realtime_version = "2.0"

            def delayed_feed(url, _api_key):
                clock[0] = 1130 if url == "positions" else 1131
                return positions if url == "positions" else updates

            with patch.object(transit_pipeline, "FEEDS", (Feed("innerwest", "light_rail", "positions", "updates"),)), \
                 patch.object(transit_pipeline, "_feed", side_effect=delayed_feed), \
                 patch.object(transit_pipeline, "time", SimpleNamespace(time=lambda: clock[0])), \
                 patch.object(transit_pipeline, "POSITIONS_FILE", root / "positions.csv"), \
                 patch.object(transit_pipeline, "UPDATES_FILE", root / "updates.csv"), \
                 patch.object(transit_pipeline, "FEED_STATUS_FILE", root / "status.json"):
                transit_pipeline.fetch_all(api_key="test")
                with (root / "positions.csv").open(encoding="utf-8", newline="") as source:
                    row = next(csv.DictReader(source))
            self.assertEqual(row["valid"], "True")

    def test_vehicle_and_update_parsing_keeps_mode(self):
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.header.gtfs_realtime_version = "2.0"
        feed.header.timestamp = 1000
        entity = feed.entity.add()
        entity.id = "vehicle-1"
        entity.vehicle.trip.trip_id = "rail-trip"
        entity.vehicle.vehicle.id = "set-1"
        entity.vehicle.position.latitude = -33.88
        entity.vehicle.position.longitude = 151.20
        entity.vehicle.timestamp = 990
        positions = transit_pipeline.parse_positions(feed, "train", "sydneytrains", 1000)
        self.assertEqual(positions[0]["mode"], "train")
        self.assertEqual(positions[0]["trip_id"], "rail-trip")
        self.assertTrue(positions[0]["valid"])

        update_entity = feed.entity.add()
        update = update_entity.trip_update
        update.trip.trip_id = "rail-trip"
        stop = update.stop_time_update.add()
        stop.stop_id = "station-1"
        stop.departure.delay = 120
        updates = transit_pipeline.parse_updates(feed, "train", "sydneytrains")
        self.assertEqual(updates[0]["trip_update_timestamp"], 1000)
        self.assertEqual(updates[0]["delay_seconds"], 120)
        self.assertEqual(updates[0]["mode"], "train")

    def test_rail_delay_only_update_uses_scheduled_stop_time(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "data") as directory:
            root = Path(directory)
            with (root / "stop_times.txt").open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=["trip_id", "stop_id", "stop_sequence",
                                                           "arrival_time", "departure_time"])
                writer.writeheader()
                writer.writerow({"trip_id": "rail-trip", "stop_id": "station-1",
                                 "stop_sequence": "1", "arrival_time": "10:04:00",
                                 "departure_time": "10:05:00"})
            now = int(datetime(2026, 10, 23, 10, 0, tzinfo=ZoneInfo("Australia/Sydney")).timestamp())
            for mode in ("train", "metro"):
                with self.subTest(mode=mode):
                    updates = pd.DataFrame([{
                        "mode": mode, "trip_id": "rail-trip", "stop_id": "station-1",
                        "stop_sequence": 1, "arrival_time": float("nan"), "departure_time": float("nan"),
                        "delay_seconds": 120.0, "delay_event_type": "departure", "start_date": "",
                    }])
                    vehicles = pd.DataFrame([{"mode": mode, "trip_id": "rail-trip"}])
                    transit_pipeline._fill_rail_predictions(updates, vehicles, now, mode, root)
                    self.assertEqual(updates.iloc[0]["departure_time"], now + 7 * 60)

    def test_missing_feed_reuses_only_recent_positions_as_stale(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "data") as directory:
            positions = Path(directory) / "positions.csv"
            with positions.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=["feed", "timestamp", "valid"])
                writer.writeheader()
                writer.writerows([
                    {"feed": "innerwest", "timestamp": 820, "valid": "True"},
                    {"feed": "innerwest", "timestamp": 390, "valid": "True"},
                    {"feed": "metro", "timestamp": 900, "valid": "True"},
                ])
            with patch.object(transit_pipeline, "POSITIONS_FILE", positions):
                cached = transit_pipeline._cached_positions(1000, "innerwest")
            self.assertEqual(len(cached), 1)
            self.assertEqual(cached[0]["timestamp"], "820")
            self.assertIs(cached[0]["valid"], False)

    def test_recent_stale_light_rail_is_visible_but_old_position_expires(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "data") as directory:
            root = Path(directory)
            now = int(datetime.now(ZoneInfo("Australia/Sydney")).timestamp())
            rows = [
                {"mode": "light_rail", "feed": "innerwest", "vehicle_id": "recent",
                 "trip_id": "L1-recent", "route_short_name": "L1", "latitude": -33.88,
                 "longitude": 151.20, "timestamp": now - 180, "valid": False},
                {"mode": "light_rail", "feed": "innerwest", "vehicle_id": "expired",
                 "trip_id": "L1-expired", "route_short_name": "L1", "latitude": -33.88,
                 "longitude": 151.20, "timestamp": now - 660, "valid": False},
            ]
            for row in rows:
                row["snapshot_time"] = datetime.fromtimestamp(now, ZoneInfo("Australia/Sydney")).isoformat()
            snapshot_file = root / "vehicles.csv"
            pd.DataFrame(rows).to_csv(snapshot_file, index=False)
            with patch.object(visualization, "stop_lookup", return_value={}), \
                 patch.object(visualization, "FEED_STATUS_FILE", root / "missing.json"):
                snapshot = visualization.load_vehicle_snapshot(snapshot_file)
            self.assertEqual([vehicle["vehicle_id"] for vehicle in snapshot["vehicles"]], ["recent"])
            self.assertIs(snapshot["vehicles"][0]["valid"], False)


if __name__ == "__main__":
    unittest.main()
