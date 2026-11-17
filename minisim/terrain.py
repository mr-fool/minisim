"""Terrain: smooth hills on a flat plain. Heights are km above sea level.

Scenario file:
    "terrain": {"hills": [{"x": 170, "y": 150, "height_km": 1.8, "radius_km": 14}]}
"""
import math


class Terrain:
    def __init__(self, hills=()):
        self.hills = [(h["x"], h["y"], h["height_km"], h["radius_km"]) for h in hills]

    def height(self, x, y):
        total = 0.0
        for hx, hy, hh, hr in self.hills:
            d2 = (x - hx) ** 2 + (y - hy) ** 2
            if d2 < 9 * hr * hr:
                total += hh * math.exp(-d2 / (2 * hr * hr))
        return total
