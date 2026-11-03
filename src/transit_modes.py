"""TfNSW feeds and route types used by the Central Sydney map."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Feed:
    name: str
    mode: str
    vehicle_url: str
    updates_url: str


FEEDS = (
    Feed("buses", "bus",
         "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/buses",
         "https://api.transport.nsw.gov.au/v1/gtfs/realtime/buses"),
    Feed("sydneytrains", "train",
         "https://api.transport.nsw.gov.au/v2/gtfs/vehiclepos/sydneytrains",
         "https://api.transport.nsw.gov.au/v2/gtfs/realtime/sydneytrains"),
    Feed("metro", "metro",
         "https://api.transport.nsw.gov.au/v2/gtfs/vehiclepos/metro",
         "https://api.transport.nsw.gov.au/v2/gtfs/realtime/metro"),
    Feed("innerwest", "light_rail",
         "https://api.transport.nsw.gov.au/v2/gtfs/vehiclepos/lightrail/innerwest",
         "https://api.transport.nsw.gov.au/v2/gtfs/realtime/lightrail/innerwest"),
    Feed("cbdandsoutheast", "light_rail",
         "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/lightrail/cbdandsoutheast",
         "https://api.transport.nsw.gov.au/v1/gtfs/realtime/lightrail/cbdandsoutheast"),
    Feed("parramatta", "light_rail",
         "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/lightrail/parramatta",
         "https://api.transport.nsw.gov.au/v1/gtfs/realtime/lightrail/parramatta"),
    # The v2 ferry endpoints return empty feeds; v1 carries Sydney Ferries.
    Feed("sydneyferries", "ferry",
         "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/ferries/sydneyferries",
         "https://api.transport.nsw.gov.au/v1/gtfs/realtime/ferries/sydneyferries"),
)

MODES = ("bus", "train", "metro", "light_rail", "ferry")
POSITION_DISPLAY_SECONDS = 10 * 60


def mode_for_route_type(route_type: str) -> str | None:
    """Interpret both basic and extended GTFS route_type values."""
    try:
        value = int(route_type)
    except (TypeError, ValueError):
        return None
    if value == 3 or 700 <= value <= 716:
        return "bus"
    if value == 2 or 100 <= value <= 117:
        return "train"
    if value == 1 or 400 <= value <= 405:
        return "metro"
    if value == 0 or 900 <= value <= 906:
        return "light_rail"
    if value == 4 or 1000 <= value <= 1021 or value == 1200:
        return "ferry"
    return None
