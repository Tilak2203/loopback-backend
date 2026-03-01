import requests
from dataclasses import dataclass
from typing import Any
from loopback.config import settings

@dataclass(frozen=True)
class RouteCandidate:
    name: str
    distance_m: float
    duration_s: float
    polyline: list[tuple[float, float]]  # (lat, lon)
    raw: dict[str, Any]

_MODE_MAP = {
    "walk": "walking",
    "drive": "driving",
    "bike": "bicycling",
    "walking": "walking",
    "driving": "driving",
    "cycling": "bicycling",
    "bicycling": "bicycling",
}


def _decode_polyline(encoded: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lon = 0

    while index < len(encoded):
        result = 0
        shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        d_lat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += d_lat

        result = 0
        shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        d_lon = ~(result >> 1) if (result & 1) else (result >> 1)
        lon += d_lon

        points.append((lat / 1e5, lon / 1e5))

    return points

def get_mapbox_routes(
    *,
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    mode: str,
    max_routes: int,
) -> list[RouteCandidate]:
    if not settings.GOOGLE_MAPS_API_KEY:
        raise ValueError("GOOGLE_MAPS_API_KEY is missing")

    travel_mode = _MODE_MAP.get(mode, "walking")
    url = "https://maps.googleapis.com/maps/api/directions/json"

    params = {
        "origin": f"{start_lat},{start_lon}",
        "destination": f"{end_lat},{end_lon}",
        "mode": travel_mode,
        "alternatives": "true",
        "key": settings.GOOGLE_MAPS_API_KEY,
    }

    r = requests.get(url, params=params, timeout=15)
    if r.status_code != 200:
        raise ValueError(f"Google Maps error: {r.status_code} {r.text[:300]}")

    data = r.json()
    status = data.get("status")
    if status != "OK":
        message = data.get("error_message") or status or "Unknown Google Maps error"
        raise ValueError(f"Google Directions error: {message}")

    routes = (data.get("routes") or [])[:max_routes]

    out: list[RouteCandidate] = []
    for i, rt in enumerate(routes):
        overview_polyline = (rt.get("overview_polyline") or {}).get("points") or ""
        poly = _decode_polyline(overview_polyline) if overview_polyline else []
        leg = (rt.get("legs") or [{}])[0]
        out.append(
            RouteCandidate(
                name="Default route" if i == 0 else f"Alternative {i}",
                distance_m=float((leg.get("distance") or {}).get("value", 0.0)),
                duration_s=float((leg.get("duration") or {}).get("value", 0.0)),
                polyline=poly,
                raw=rt,
            )
        )

    return out