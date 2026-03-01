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
    "bike": "cycling",
    "walking": "walking",
    "driving": "driving",
    "cycling": "cycling",
}

def get_mapbox_routes(
    *,
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    mode: str,
    max_routes: int,
) -> list[RouteCandidate]:
    if not settings.MAPBOX_TOKEN:
        raise ValueError("MAPBOX_TOKEN is missing")

    profile = _MODE_MAP.get(mode, "walking")
    coords = f"{start_lon},{start_lat};{end_lon},{end_lat}"
    url = f"https://api.mapbox.com/directions/v5/mapbox/{profile}/{coords}"

    params = {
        "access_token": settings.MAPBOX_TOKEN,
        "alternatives": "true",
        "geometries": "geojson",
        "overview": "full",
        "steps": "false",
    }

    r = requests.get(url, params=params, timeout=15)
    if r.status_code != 200:
        raise ValueError(f"Mapbox error: {r.status_code} {r.text[:300]}")

    data = r.json()
    routes = (data.get("routes") or [])[:max_routes]

    out: list[RouteCandidate] = []
    for i, rt in enumerate(routes):
        coords_list = (rt.get("geometry") or {}).get("coordinates") or []
        poly = [(p[1], p[0]) for p in coords_list]  # to (lat, lon)
        out.append(
            RouteCandidate(
                name="Default route" if i == 0 else f"Alternative {i}",
                distance_m=float(rt.get("distance", 0.0)),
                duration_s=float(rt.get("duration", 0.0)),
                polyline=poly,
                raw=rt,
            )
        )

    return out