"""Behaviours: small rules a platform checks in priority order every step.

The first *exclusive* behaviour whose ready() is true decides what the
platform does this step. Non-exclusive behaviours (Engage) run and then let
the list continue, so a unit can shoot and move in the same step.

Add your own by subclassing Behavior and registering it in REGISTRY.
"""
from .sim import dist, hdist

FRESH_S = 10.0   # a track must be this recent to shoot at


class Behavior:
    name = "behavior"
    exclusive = True

    def __init__(self, **params):
        # Parameters come from the scenario file, e.g.
        # {"name": "escort", "target": "Owl", "radius": 10}
        for key, value in params.items():
            setattr(self, key, value)

    def ready(self, p, sim):
        return True

    def run(self, p, sim):
        pass


class Engage(Behavior):
    """Fire a ready weapon at the nearest valid, recently seen hostile.

    A unit waits for its own shot at a target to land before firing at that
    target again, so it does not empty its magazine at one enemy.
    """
    name = "engage"
    exclusive = False

    def ready(self, p, sim):
        return p.weapons_free and p.has_ammo()

    def run(self, p, sim):
        tracks = p.hostile_tracks(sim, max_age=FRESH_S)
        for w in p.weapons:
            if w.ammo <= 0 or sim.time < w.ready_at:
                continue
            options = [t for t in tracks if t.domain in w.domains
                       and dist(p.pos, t.pos) <= w.range_km
                       and (p.name, t.target) not in sim.in_flight]
            if options:
                sim.fire(p, w, min(options, key=lambda t: dist(p.pos, t.pos)))
                tracks = [t for t in tracks if (p.name, t.target) not in sim.in_flight]


class Orders(Behavior):
    """Obey an order given by a human player."""
    name = "orders"

    def ready(self, p, sim):
        return p.order is not None

    def run(self, p, sim):
        if p.order[0] == "hold":
            p.dest = None
        elif p.order[0] == "move":
            p.dest = p.order[1]
            if hdist(p.pos, p.dest) < 0.5:
                p.order = None


class Retreat(Behavior):
    """Head home and land when out of ammunition, low on fuel or badly
    damaged. Units with a turnaround time refuel, rearm and take off again."""
    name = "retreat"

    def ready(self, p, sim):
        if p.max_speed <= 0:
            return False
        return ((p.armed and not p.has_ammo()) or p.hp <= 0.3 * p.max_hp
                or p.bingo())

    def run(self, p, sim):
        p.dest = p.home
        if hdist(p.pos, p.home) < 0.5:
            p.land(sim)


class Pursue(Behavior):
    """Close on the nearest hostile that one of our loaded weapons can hit.
    Optional parameter `domains` limits what is chased, e.g. ["air"]."""
    name = "pursue"
    domains = None

    def _targets(self, p, sim):
        domains = {d for w in p.weapons if w.ammo > 0 for d in w.domains}
        if self.domains:
            domains &= set(self.domains)
        return [t for t in p.hostile_tracks(sim) if t.domain in domains]

    def ready(self, p, sim):
        return p.max_speed > 0 and bool(self._targets(p, sim))

    def run(self, p, sim):
        p.dest = min(self._targets(p, sim), key=lambda t: dist(p.pos, t.pos)).pos


class Patrol(Behavior):
    """Loop through the platform's waypoints. A waypoint may be [x, y] or
    [x, y, altitude_km]."""
    name = "patrol"

    def ready(self, p, sim):
        return p.max_speed > 0 and bool(p.waypoints)

    def run(self, p, sim):
        wp = p.waypoints[p.wp_index]
        if hdist(p.pos, wp) < 0.5:
            p.wp_index = (p.wp_index + 1) % len(p.waypoints)
            wp = p.waypoints[p.wp_index]
        p.dest = wp
        if len(wp) > 2:
            p.target_alt = wp[2]


class OneWayAttack(Behavior):
    """For expendable attack drones: fly the waypoints in order (no loop),
    steer to a tracked hostile ground unit if one is within `seek_km`, and
    detonate on arrival. The drone is used up either way."""
    name = "one_way_attack"
    pk = 0.7
    damage = 2.0
    blast_km = 1.5
    seek_km = 30.0

    def ready(self, p, sim):
        return p.max_speed > 0

    def run(self, p, sim):
        near = [t for t in p.hostile_tracks(sim) if t.domain == "ground"
                and hdist(p.pos, t.pos) <= self.seek_km]
        if near:
            aim = min(near, key=lambda t: hdist(p.pos, t.pos)).pos[:2]
        elif p.waypoints:
            while (p.wp_index < len(p.waypoints) - 1
                   and hdist(p.pos, p.waypoints[p.wp_index]) < 0.5):
                p.wp_index += 1
            aim = p.waypoints[p.wp_index]
        else:
            p.dest = None
            return
        p.dest = aim
        if hdist(p.pos, aim) < 5:
            p.target_alt = 0.0                 # final dive
        if hdist(p.pos, aim) < 0.5:
            self.detonate(p, sim)

    def detonate(self, p, sim):
        p.alive = False
        victims = [o for o in sim.platforms.values()
                   if o.alive and o.side != p.side and o.domain == "ground"
                   and hdist(o.pos, p.pos) <= self.blast_km]
        if not victims:
            sim.log("STRIKE", f"{p.name} hits empty ground", {p.side})
            return
        target = min(victims, key=lambda o: hdist(o.pos, p.pos))
        sides = {p.side, target.side}
        if sim.rng.random() >= self.pk:
            sim.log("STRIKE", f"{p.name} misses {target.name}", sides)
            return
        target.hp -= self.damage
        if target.hp <= 0:
            target.alive = False
            sim.log("KILL", f"{p.name} destroys {target.name}", sides)
        else:
            sim.log("HIT", f"{p.name} damages {target.name} "
                           f"({target.hp:g}/{target.max_hp:g} hp)", sides)


class Hold(Behavior):
    name = "hold"

    def run(self, p, sim):
        p.dest = None


REGISTRY = {b.name: b for b in (Engage, Orders, Retreat, Pursue, Patrol,
                                   OneWayAttack, Hold)}
DEFAULT = ["engage", "orders", "retreat", "pursue", "patrol", "hold"]
