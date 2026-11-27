"""Coordinate conversion for OpenStreetMap data rendered on the GCJ-02 base map."""

import math

AXIS = 6378245.0
ECCENTRICITY = 0.006693421622965943


def to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    x, y = lng - 105, lat - 35
    common = (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    dlat = (-100 + 2*x + 3*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x)) + common
            + (20*math.sin(y*math.pi) + 40*math.sin(y*math.pi/3)) * 2/3
            + (160*math.sin(y*math.pi/12) + 320*math.sin(y*math.pi/30)) * 2/3)
    dlng = (300 + x + 2*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x)) + common
            + (20*math.sin(x*math.pi) + 40*math.sin(x*math.pi/3)) * 2/3
            + (150*math.sin(x*math.pi/12) + 300*math.sin(x*math.pi/30)) * 2/3)
    latitude = math.radians(lat)
    correction = 1 - ECCENTRICITY * math.sin(latitude)**2
    return (
        lng + math.degrees(dlng * math.sqrt(correction) / (AXIS * math.cos(latitude))),
        lat + math.degrees(dlat * correction**1.5 / (AXIS * (1 - ECCENTRICITY))),
    )


def approximate_wgs84(lng: float, lat: float) -> tuple[float, float]:
    x, y = lng, lat
    for _ in range(3):
        actual_x, actual_y = to_gcj02(x, y)
        x += lng - actual_x
        y += lat - actual_y
    return x, y
