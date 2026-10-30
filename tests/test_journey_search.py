"""Checks that direct routes and arrivals follow GTFS stop order and live data."""

import csv
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from journey_search import RouteIndex, find_places, search_area_journey, search_journey


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


class JourneySearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1] / "data"
        )
        root = Path(self.directory.name)
        self.static = root / "static"
        self.static.mkdir()
        write_csv(self.static / "stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon"], [
            {"stop_id": id_, "stop_name": name, "stop_lat": lat, "stop_lon": lon}
            for id_, name, lat, lon in [
                ("A", "Alpha", -33.8700, 151.2000),
                ("B", "Bravo", -33.8703, 151.2002),
                ("C", "Charlie", -33.8750, 151.2050),
            ]
        ])
        write_csv(self.static / "routes.txt", ["route_id", "route_short_name", "route_long_name", "route_type"], [
            {"route_id": "BUS", "route_short_name": "42", "route_long_name": "Alpha to Charlie", "route_type": "700"},
            {"route_id": "TRAIN", "route_short_name": "T1", "route_long_name": "Rail", "route_type": "2"},
            {"route_id": "LIGHT", "route_short_name": "L1", "route_long_name": "Light rail", "route_type": "900"},
            {"route_id": "METRO", "route_short_name": "M1", "route_long_name": "Metro", "route_type": "401"},
        ])
        write_csv(self.static / "trips.txt", ["trip_id", "route_id", "trip_headsign"], [
            {"trip_id": trip, "route_id": "TRAIN" if trip == "rail" else "LIGHT" if trip == "tram" else "METRO" if trip == "metro" else "BUS", "trip_headsign": "Charlie"}
            for trip in ["forward", "reverse", "no_pickup", "no_dropoff", "rail", "tram", "metro"]
        ])
        rows = []
        for trip, stops in [
            ("forward", ["A", "B", "C"]), ("reverse", ["C", "B", "A"]),
            ("no_pickup", ["A", "B", "C"]), ("no_dropoff", ["A", "B", "C"]),
            ("rail", ["A", "B", "C"]), ("tram", ["A", "B", "C"]),
            ("metro", ["A", "B", "C"]),
        ]:
            for sequence, stop in enumerate(stops, 1):
                rows.append({"trip_id": trip, "stop_id": stop, "stop_sequence": sequence,
                             "pickup_type": "1" if trip == "no_pickup" and stop == "A" else "0",
                             "drop_off_type": "1" if trip == "no_dropoff" and stop == "C" else "0"})
        write_csv(self.static / "stop_times.txt",
                  ["trip_id", "stop_id", "stop_sequence", "pickup_type", "drop_off_type"], rows)
        self.index = RouteIndex(self.static, root / "routes.sqlite")
        self.index.build()
        self.updates = root / "updates.csv"

    def tearDown(self):
        self.directory.cleanup()

    def test_direct_route_respects_order_and_boarding_rules(self):
        self.assertEqual([trip["trip_id"] for trip in self.index.matching_trips("A", "C", "bus")], ["forward"])
        self.assertEqual([trip["trip_id"] for trip in self.index.matching_trips("C", "A", "bus")], ["reverse"])
        self.assertEqual(self.index.find_stops("Al")[0]["id"], "A")

    def test_arrivals_require_fresh_bus_and_stop_prediction(self):
        now = time.time()
        write_csv(self.updates, ["trip_id", "stop_id", "trip_update_timestamp", "arrival_time", "departure_time"], [
            {"trip_id": "forward", "stop_id": "A", "trip_update_timestamp": now - 5,
             "arrival_time": now + 180, "departure_time": now + 200},
        ])
        snapshot = {"source": "live", "snapshot_time": datetime.fromtimestamp(now - 5).astimezone().isoformat(),
                    "buses": [{"trip_id": "forward", "vehicle_id": "BUS-1", "timestamp": now - 5,
                               "latitude": -33.87, "longitude": 151.20}]}
        result = search_journey(self.index, "A", "C", snapshot, self.updates, mode="bus", now=now)
        self.assertEqual(result["routes"][0]["buses"][0]["vehicle_id"], "BUS-1")
        self.assertEqual(result["routes"][0]["buses"][0]["minutes"], 3)
        snapshot["buses"][0]["valid"] = False
        self.assertEqual(search_journey(self.index, "A", "C", snapshot, self.updates,
                                        mode="bus", now=now)["routes"][0]["buses"], [])
        snapshot["buses"][0].pop("valid")
        self.assertEqual(search_journey(self.index, "C", "A", snapshot, self.updates, mode="bus", now=now)["routes"][0]["buses"], [])
        snapshot["snapshot_time"] = datetime.fromtimestamp(now - 300).astimezone().isoformat()
        self.assertEqual(search_journey(self.index, "A", "C", snapshot, self.updates, mode="bus", now=now)["routes"][0]["buses"], [])

    def test_area_search_uses_nearby_stops_and_names_boarding_points(self):
        result = search_area_journey(
            self.index, "@-33.8703,151.2002", "Charlie",
            {"source": "demo", "snapshot_time": None, "buses": []}, self.updates, mode="bus")
        self.assertEqual(result["origin_stop_count"], 2)
        self.assertEqual(result["radius_metres"], 250)
        self.assertEqual(len(result["routes"]), 1)
        self.assertEqual(result["routes"][0]["boarding_stop"]["id"], "B")
        self.assertEqual(result["routes"][0]["alighting_stop"]["id"], "C")
        self.assertIn("Broadway", [place["name"] for place in find_places(self.index, "Broad")])

    def test_area_arrival_uses_actual_boarding_stop_prediction(self):
        now = time.time()
        write_csv(self.updates, ["trip_id", "stop_id", "trip_update_timestamp", "arrival_time", "departure_time"], [
            {"trip_id": "forward", "stop_id": "B", "trip_update_timestamp": now - 5,
             "arrival_time": now + 180, "departure_time": now + 200},
        ])
        snapshot = {"source": "live", "snapshot_time": datetime.fromtimestamp(now - 5).astimezone().isoformat(),
                    "buses": [{"trip_id": "forward", "vehicle_id": "BUS-1", "timestamp": now - 5,
                               "latitude": -33.87, "longitude": 151.20}]}
        result = search_area_journey(self.index, "@-33.8703,151.2002", "Charlie",
                                     snapshot, self.updates, mode="bus", now=now)
        self.assertEqual(result["routes"][0]["buses"][0]["boarding_stop"]["id"], "B")
        self.assertEqual(result["routes"][0]["buses"][0]["minutes"], 3)

    def test_rail_modes_and_updates_stay_in_their_modes(self):
        now = time.time()
        rows = [
            {"mode": "train", "trip_id": "rail", "stop_id": "A", "trip_update_timestamp": now - 5,
             "arrival_time": now + 180, "departure_time": now + 200},
            {"mode": "light_rail", "trip_id": "tram", "stop_id": "A", "trip_update_timestamp": now - 5,
             "arrival_time": now + 240, "departure_time": now + 260},
            {"mode": "metro", "trip_id": "metro", "stop_id": "A", "trip_update_timestamp": now - 5,
             "arrival_time": now + 300, "departure_time": now + 320},
        ]
        write_csv(self.updates, ["mode", "trip_id", "stop_id", "trip_update_timestamp", "arrival_time", "departure_time"], rows)
        snapshot = {"source": "live", "snapshot_time": datetime.fromtimestamp(now - 5).astimezone().isoformat(),
                    "vehicles": [
                        {"mode": "train", "trip_id": "rail", "vehicle_id": "T-1", "timestamp": now - 5},
                        {"mode": "light_rail", "trip_id": "tram", "vehicle_id": "L-1", "timestamp": now - 5},
                        {"mode": "metro", "trip_id": "metro", "vehicle_id": "M-1", "timestamp": now - 5},
                    ]}
        trains = search_journey(self.index, "A", "C", snapshot, self.updates, mode="train", now=now)
        trams = search_journey(self.index, "A", "C", snapshot, self.updates, mode="light_rail", now=now)
        metros = search_journey(self.index, "A", "C", snapshot, self.updates, mode="metro", now=now)
        self.assertEqual([(route["route"], route["mode"]) for route in trains["routes"]], [("T1", "train")])
        self.assertEqual(trains["routes"][0]["buses"][0]["vehicle_id"], "T-1")
        self.assertEqual([(route["route"], route["mode"]) for route in trams["routes"]], [("L1", "light_rail")])
        self.assertEqual(trams["routes"][0]["buses"][0]["vehicle_id"], "L-1")
        self.assertEqual([(route["route"], route["mode"]) for route in metros["routes"]], [("M1", "metro")])
        self.assertEqual(metros["routes"][0]["buses"][0]["vehicle_id"], "M-1")


if __name__ == "__main__":
    unittest.main()
