"""Example plug-in: adds an "escort" behaviour and a "kill_log" output.

Use it from a scenario file:

    "plugins": ["../plugins/escort.py"],
    ...
    "behaviors": ["engage", "orders", "retreat",
                  {"name": "escort", "target": "Owl", "radius": 12},
                  "patrol", "hold"]
"""
from minisim.adaptors import Observer
from minisim.behaviors import Behavior
from minisim.sim import dist


class Escort(Behavior):
    """Stay within `radius` km of a friendly platform while it is alive.

    Hostiles that come within `guard_km` of the escorted platform are chased;
    anything further away is ignored, so the escort does not wander off.
    """
    name = "escort"
    target = None
    radius = 10.0
    guard_km = 50.0

    def ready(self, p, sim):
        ward = sim.platforms.get(self.target)
        return ward is not None and ward.alive and p.max_speed > 0

    def run(self, p, sim):
        ward = sim.platforms[self.target]
        threats = [t for t in p.hostile_tracks(sim)
                   if dist(t.pos, ward.pos) <= self.guard_km
                   and any(w.ammo > 0 and t.domain in w.domains for w in p.weapons)]
        if threats:
            p.dest = min(threats, key=lambda t: dist(t.pos, ward.pos)).pos
        elif dist(p.pos, ward.pos) > self.radius:
            p.dest = ward.pos
        else:
            p.dest = None


class KillLog(Observer):
    """Write one line per destroyed platform to a text file."""

    def on_start(self, sim):
        self.file = open(self.target, "w")

    def on_event(self, sim, event):
        t, kind, text, _ = event
        if kind == "KILL":
            self.file.write(f"{t:.0f}s {text}\n")

    def on_end(self, sim):
        self.file.close()


def register(api):
    api.behavior(Escort)
    api.output("kill_log", KillLog)
