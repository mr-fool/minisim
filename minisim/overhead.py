"""Multi-domain link: satellites passing over a flat-world scenario.

Add to a surface/air scenario file:

    "space": {
      "origin": {"lat": 35.0, "lon": 45.0},        where the map centre sits on Earth
      "satellites": [
        {"name": "Argus", "side": "blue",
         "orbit": {"alt_km": 700, "inc_deg": 60, "pass_at_s": 600},
         "sensor": {"min_elev_deg": 40, "domains": ["ground"],
                    "p_detect": 0.7, "scan_s": 10}}]}

While a satellite is high enough above a hostile unit it can detect it, and
the track goes straight to every friendly unit that has comms. "pass_at_s"
builds an orbit that flies over the map centre at that time; the usual orbit
forms (see space.py) work too.
"""
import math

from .adaptors import Observer
from .sim import Track
from .space import Orbit, circular_through, elevation_deg, ground_eci

KM_PER_DEG = 111.2


class Overhead(Observer):
    def __init__(self, spec, sim):
        super().__init__()
        self.lat0, self.lon0 = spec["origin"]["lat"], spec["origin"]["lon"]
        self.sats = []
        for s in spec.get("satellites", []):
            o = s["orbit"]
            if "pass_at_s" in o:
                over = ground_eci(self.lat0, self.lon0, o["pass_at_s"], o["alt_km"])
                orbit = circular_through(over, o["pass_at_s"], o["inc_deg"])
            else:
                orbit = Orbit.from_spec(o)
            sensor = s.get("sensor", {})
            self.sats.append({
                "name": s["name"], "side": s["side"], "orbit": orbit,
                "min_elev": sensor.get("min_elev_deg", 30.0),
                "domains": tuple(sensor.get("domains", ["ground"])),
                "p_detect": sensor.get("p_detect", 0.8),
                "scan_s": sensor.get("scan_s", 10.0),
                "next": 0.0, "up": False, "seen": {}})

    def latlon(self, sim, pos):
        lat = self.lat0 + (pos[1] - sim.height / 2) / KM_PER_DEG
        lon = self.lon0 + (pos[0] - sim.width / 2) / (
            KM_PER_DEG * math.cos(math.radians(self.lat0)))
        return lat, lon

    def on_step(self, sim):
        t = sim.time
        for sat in self.sats:
            if t < sat["next"]:
                continue
            sat["next"] = t + sat["scan_s"]
            above = sat["orbit"].state(t)[0]
            centre = ground_eci(self.lat0, self.lon0, t)
            up = elevation_deg(centre, above) >= sat["min_elev"]
            if up != sat["up"]:
                sat["up"] = up
                sim.log("PASS", f"satellite {sat['name']} "
                                f"{'is overhead' if up else 'has passed out of view'}",
                        {sat["side"]})
            if not up:
                continue
            friends = [p for p in sim.platforms.values()
                       if p.alive and p.side == sat["side"] and p.comms_km > 0]
            for p in sim.platforms.values():
                if (not p.alive or p.side == sat["side"]
                        or p.domain not in sat["domains"]):
                    continue
                lat, lon = self.latlon(sim, p.pos)
                here = ground_eci(lat, lon, t, p.pos[2])
                if elevation_deg(here, above) < sat["min_elev"]:
                    continue
                if sim.rng.random() > sat["p_detect"]:
                    continue
                if t - sat["seen"].get(p.name, -1e9) > 60:
                    sim.log("DETECT", f"satellite {sat['name']} detects {p.name}",
                            {sat["side"]})
                sat["seen"][p.name] = t
                sim.detections.append(("sat:" + sat["name"], p.name))
                track = Track(p.name, p.side, p.kind, p.domain, p.pos, t)
                for f in friends:
                    f.tracks[p.name] = track
