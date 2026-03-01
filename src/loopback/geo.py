from math import radians, cos, sin, asin, sqrt
import geohash2

def to_geohash(lat: float, lon: float, precision: int) -> str:
    return geohash2.encode(lat, lon, precision=precision)

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return R * c