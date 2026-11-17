"""Build a Simulation from a JSON scenario file.

Top-level keys: name, world, dt, end_time, templates, platforms, and
optionally
  "plugins": ["../plugins/escort.py"]                  paths relative to the file
  "outputs": [{"type": "events_csv", "target": "events.csv"}]
  "terrain": {"hills": [{"x": 170, "y": 150, "height_km": 1.8, "radius_km": 14}]}
  "space": {...}      satellites passing overhead, see overhead.py
Positions and waypoints are [x, y] or [x, y, altitude_km].
A file with "domain": "space" is a space scenario and is read by space.py.
Entries in a "behaviors" list are either a name or an object with
parameters:  {"name": "escort", "target": "Owl", "radius": 12}
"""
import json
import os

from . import adaptors, plugins
from .behaviors import DEFAULT, REGISTRY
from .sim import Jammer, Platform, Sensor, Simulation, Weapon
from .terrain import Terrain

PLATFORM_KEYS = ("kind", "domain", "max_speed", "hp", "signature", "comms_km",
                 "symbol", "icon", "expendable", "waypoints", "home", "altitude", "climb_rate", "fuel",
                 "burn_per_km", "turnaround_s")


def _behavior(entry):
    if isinstance(entry, str):
        entry = {"name": entry}
    params = dict(entry)
    name = params.pop("name")
    if name not in REGISTRY:
        raise ValueError(f"unknown behavior {name!r}; known: {sorted(REGISTRY)}. "
                         "Is the plug-in that defines it listed under 'plugins'?")
    return REGISTRY[name](**params)


def load(path, seed=None, outputs=True):
    with open(path) as f:
        data = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    for ref in data.get("plugins", []):
        plugins.load_plugin(ref, base_dir=base)

    world = data.get("world", {})
    sim = Simulation(width=world.get("width", 300), height=world.get("height", 200),
                     dt=data.get("dt", 2.0), end_time=data.get("end_time", 3600),
                     seed=seed, name=data.get("name", path),
                     terrain=Terrain(data.get("terrain", {}).get("hills", [])))
    templates = data.get("templates", {})
    for spec in data["platforms"]:
        merged = dict(templates.get(spec.get("template"), {}))
        merged.update(spec)
        merged.setdefault("kind", spec.get("template", "unit"))
        p = Platform(merged["name"], merged["side"], pos=merged["pos"],
                     **{k: merged[k] for k in PLATFORM_KEYS if k in merged})
        for s in merged.get("sensors", []):
            s = dict(s)
            if "domains" in s:
                s["domains"] = tuple(s["domains"])
            p.sensors.append(Sensor(**s))
        for w in merged.get("weapons", []):
            w = dict(w)
            if "domains" in w:
                w["domains"] = tuple(w["domains"])
            p.weapons.append(Weapon(**w))
        for j in merged.get("jammers", []):
            p.jammers.append(Jammer(**j))
        p.behaviors = [_behavior(b) for b in merged.get("behaviors", DEFAULT)]
        sim.add(p)

    if outputs:
        for spec in data.get("outputs", []):
            spec = dict(spec)
            sim.observers.append(adaptors.make(spec.pop("type"), **spec))
    if "space" in data:
        from .overhead import Overhead
        sim.observers.insert(0, Overhead(data["space"], sim))   # before any outputs
    for hook in plugins.LOAD_HOOKS:
        hook(sim, data)
    return sim
