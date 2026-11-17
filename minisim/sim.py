"""Core engine: platforms, sensors, weapons, jammers, tracks and the main loop.

Units: kilometres, seconds, degrees. Positions are (x, y, z) with z the
height above sea level. All numbers in the bundled scenarios are invented
for gameplay.
"""
from __future__ import annotations

import heapq
import itertools
import math
import random
from dataclasses import dataclass

from .terrain import Terrain

CLEARANCE_KM = 0.15      # aircraft never fly lower than this above the ground
MAST_KM = 0.03           # antenna height added to both ends of a sight line
HORIZON_K = 130.0        # radar horizon: d_km = K * (sqrt(h1_km) + sqrt(h2_km))
CLIMB_COST = 5.0         # climbing 1 km burns as much fuel as flying this many km
LOITER = 0.5             # holding in the air burns fuel like flying at this speed fraction


def hdist(a, b):
    """Horizontal distance."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def dist(a, b):
    """3D distance (falls back to horizontal if a point has no height)."""
    dz = a[2] - b[2] if len(a) > 2 and len(b) > 2 else 0.0
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + dz * dz)


def bearing(a, b):
    """Compass-free bearing in degrees from a to b (0 = +x, counter-clockwise)."""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


@dataclass
class Track:
    """What one platform believes about another platform."""
    target: str
    side: str
    kind: str
    domain: str
    pos: tuple
    time: float


@dataclass
class Sensor:
    name: str = "sensor"
    range_km: float = 50.0
    domains: tuple = ("air", "ground", "surface")
    p_detect: float = 0.9      # chance per scan when target is in coverage
    scan_s: float = 4.0        # seconds between scans
    fov_deg: float = 360.0     # centred on the platform heading
    next_scan: float = 0.0


@dataclass
class Weapon:
    name: str = "weapon"
    range_km: float = 20.0
    speed_kms: float = 1.0     # used for time of flight
    pk: float = 0.5            # probability of a hit
    damage: float = 1.0
    ammo: int = 4
    reload_s: float = 10.0
    domains: tuple = ("air",)
    ready_at: float = 0.0
    capacity: int = -1         # filled in from ammo; used when rearming

    def __post_init__(self):
        if self.capacity < 0:
            self.capacity = self.ammo


@dataclass
class Jammer:
    """Shrinks the detection range of hostile sensors near it.

    A sensor at distance d < range_km keeps a fraction
    1 - strength * (1 - d / range_km) of its normal range.
    """
    name: str = "jammer"
    range_km: float = 80.0
    strength: float = 0.6
    on: bool = True


class Platform:
    def __init__(self, name, side, kind="unit", domain="air", pos=(0, 0),
                 max_speed=0.0, hp=1.0, signature=1.0, comms_km=0.0,
                 symbol="u", icon=None, expendable=False, waypoints=None, home=None, altitude=None,
                 climb_rate=0.05, fuel=None, burn_per_km=0.0, turnaround_s=None):
        self.name = name
        self.side = side
        self.kind = kind
        self.domain = domain
        self.pos = tuple(pos)               # z is filled in by Simulation.add
        self.heading = 0.0
        self.max_speed = max_speed          # km/s over the ground
        self.climb_rate = climb_rate        # km/s
        self.altitude = altitude            # cruise height, km (air units)
        self.target_alt = altitude
        self.hp = self.max_hp = hp
        self.signature = signature          # scales the range it is seen at
        self.comms_km = comms_km
        self.symbol = symbol
        self.icon = icon or kind            # silhouette name, see icons.py
        self.expendable = expendable        # one-way units: not counted as losses
        self.waypoints = [tuple(w) for w in (waypoints or [])]
        self.wp_index = 0
        self.home = tuple(home) if home else tuple(pos)[:2]
        self.fuel = self.fuel_max = fuel    # None = unlimited
        self.burn_per_km = burn_per_km
        self.turnaround_s = turnaround_s    # time on the ground to refuel and rearm
        self.landed = False
        self.landed_at = 0.0
        self.sensors = []
        self.weapons = []
        self.jammers = []
        self.behaviors = []
        self.tracks = {}                    # target name -> Track
        self.dest = None
        self.order = None                   # player order: ("move", (x, y)) / ("hold",)
        self.weapons_free = True
        self.alive = True
        self.activity = "idle"

    # ---- helpers -------------------------------------------------------
    @property
    def armed(self):
        return bool(self.weapons) or self.expendable

    def has_ammo(self):
        return any(w.ammo > 0 for w in self.weapons)

    def bingo(self):
        """True when there is only just enough fuel left to get home."""
        if self.fuel is None:
            return False
        return self.fuel <= 1.3 * self.burn_per_km * hdist(self.pos, self.home) + \
            0.02 * self.fuel_max

    def hostile_tracks(self, sim, max_age=None):
        out = []
        for t in self.tracks.values():
            tgt = sim.platforms.get(t.target)
            if t.side == self.side or tgt is None or not tgt.alive:
                continue
            if max_age is not None and sim.time - t.time > max_age:
                continue
            out.append(t)
        return out

    # ---- per-step phases -------------------------------------------------
    def sense(self, sim):
        jam = None
        for s in self.sensors:
            if sim.time < s.next_scan:
                continue
            s.next_scan = sim.time + s.scan_s
            if jam is None:
                jam = sim.jam_factor(self)
            for other in sim.platforms.values():
                if other is self or not other.alive or other.side == self.side:
                    continue
                if other.domain not in s.domains:
                    continue
                if dist(self.pos, other.pos) > s.range_km * other.signature * jam:
                    continue
                if s.fov_deg < 360:
                    off = (bearing(self.pos, other.pos) - self.heading + 180) % 360 - 180
                    if abs(off) > s.fov_deg / 2:
                        continue
                if not sim.line_of_sight(self.pos, other.pos):
                    continue
                if sim.rng.random() > s.p_detect:
                    continue
                if other.name not in self.tracks:
                    sim.log("DETECT", f"{self.name} detects {other.name}",
                            {self.side})
                self.tracks[other.name] = Track(other.name, other.side, other.kind,
                                                other.domain, other.pos, sim.time)
                sim.detections.append((self.name, other.name))
        # forget stale tracks
        for name in [n for n, t in self.tracks.items()
                     if sim.time - t.time > sim.track_timeout]:
            del self.tracks[name]

    def think(self, sim):
        if self.landed:
            serviceable = (self.turnaround_s is not None
                           and self.hp > 0.3 * self.max_hp)
            if serviceable and sim.time - self.landed_at >= self.turnaround_s:
                self.fuel = self.fuel_max
                for w in self.weapons:
                    w.ammo = w.capacity
                self.landed = False
                sim.log("READY", f"{self.name} takes off refuelled and rearmed",
                        {self.side})
            else:
                self.activity = "landed"
                return
        for b in self.behaviors:
            if b.ready(self, sim):
                b.run(self, sim)
                if b.exclusive:
                    self.activity = b.name
                    return
        self.activity = "idle"

    def land(self, sim):
        self.landed = True
        self.landed_at = sim.time
        self.dest = None
        self.pos = (self.pos[0], self.pos[1], sim.terrain.height(*self.pos[:2]))
        sim.log("LAND", f"{self.name} lands at base", {self.side})

    def move(self, sim):
        if self.landed or self.max_speed <= 0:
            return
        x, y, z = self.pos
        step = 0.0
        if self.dest is not None:
            d = hdist(self.pos, self.dest)
            if d > 1e-6:
                self.heading = bearing(self.pos, self.dest)
                step = min(d, self.max_speed * sim.dt)
                x = min(max(x + (self.dest[0] - x) / d * step, 0), sim.width)
                y = min(max(y + (self.dest[1] - y) / d * step, 0), sim.height)
        climbed = 0.0
        if self.domain == "air":
            floor = sim.terrain.height(x, y) + CLEARANCE_KM
            want = max(self.target_alt, floor)
            limit = self.climb_rate * sim.dt
            dz = max(-limit, min(limit, want - z))
            new_z = max(z + dz, floor)          # never fly into the ground
            climbed = max(new_z - z, 0.0)
            z = new_z
        else:
            z = sim.terrain.height(x, y)
        self.pos = (x, y, z)
        if self.fuel is not None:
            flown = step if step > 0 else LOITER * self.max_speed * sim.dt
            self.fuel -= self.burn_per_km * (flown + CLIMB_COST * climbed)
            if self.fuel <= 0:
                self.fuel = 0.0
                self.alive = False
                sim.log("LOST", f"{self.name} runs out of fuel", {self.side})


class Simulation:
    def __init__(self, width=300, height=200, dt=2.0, end_time=3600,
                 seed=None, name="scenario", terrain=None):
        self.name = name
        self.width, self.height = width, height
        self.dt = dt
        self.end_time = end_time
        self.time = 0.0
        self.rng = random.Random(seed)
        self.terrain = terrain or Terrain()
        self.platforms = {}
        self.events = []                 # (time, kind, text, sides involved)
        self.history = {}                # name -> list of positions (for plots)
        self.track_timeout = 60.0
        self.observers = []              # output adaptors, see adaptors.py
        self.detections = []             # (observer, target) seen during this step
        self.shots = []                  # every shot fired, for replays
        self._started = self._finished = False
        self.in_flight = set()           # (shooter name, target name)
        self._queue = []
        self._counter = itertools.count()

    # ---- setup -------------------------------------------------------------
    def add(self, platform):
        if platform.name in self.platforms:
            raise ValueError(f"duplicate platform name: {platform.name}")
        p = platform
        ground = self.terrain.height(p.pos[0], p.pos[1])
        if p.domain == "air":
            if p.altitude is None:
                p.altitude = p.pos[2] if len(p.pos) > 2 else 8.0
            p.target_alt = p.altitude
            z = p.pos[2] if len(p.pos) > 2 else p.altitude
            z = max(z, ground + CLEARANCE_KM)
        else:
            z = ground
        p.pos = (p.pos[0], p.pos[1], z)
        self.platforms[p.name] = p
        self.history[p.name] = [p.pos]
        return p

    @property
    def sides(self):
        return sorted({p.side for p in self.platforms.values()})

    # ---- environment ---------------------------------------------------------
    def line_of_sight(self, a, b):
        """Clear if within the radar horizon and no terrain blocks the line."""
        d = hdist(a, b)
        za, zb = max(a[2], 0) + MAST_KM, max(b[2], 0) + MAST_KM
        if d > HORIZON_K * (math.sqrt(za) + math.sqrt(zb)):
            return False
        if not self.terrain.hills:
            return True
        n = max(2, int(d / 2))
        for i in range(1, n):
            f = i / n
            x, y = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
            if self.terrain.height(x, y) > za + (zb - za) * f:
                return False
        return True

    def jam_factor(self, platform):
        """Fraction of normal sensor range `platform` keeps under hostile jamming."""
        factor = 1.0
        for other in self.platforms.values():
            if not other.alive or other.landed or other.side == platform.side:
                continue
            for j in other.jammers:
                d = dist(platform.pos, other.pos)
                if j.on and d < j.range_km:
                    factor = min(factor, 1 - j.strength * (1 - d / j.range_km))
        return factor

    # ---- events --------------------------------------------------------------
    def log(self, kind, text, sides=()):
        event = (self.time, kind, text, set(sides))
        self.events.append(event)
        for o in self.observers:
            o.on_event(self, event)

    def schedule(self, delay, fn):
        heapq.heappush(self._queue, (self.time + delay, next(self._counter), fn))

    def fire(self, shooter, weapon, track):
        weapon.ammo -= 1
        weapon.ready_at = self.time + weapon.reload_s
        tof = dist(shooter.pos, track.pos) / weapon.speed_kms
        self.in_flight.add((shooter.name, track.target))
        shot = {"t0": self.time, "t1": self.time + tof, "from": shooter.name,
                "to": track.target, "weapon": weapon.name, "result": "in flight"}
        self.shots.append(shot)
        self.log("FIRE", f"{shooter.name} fires {weapon.name} at {track.target}",
                 {shooter.side, track.side})
        self.schedule(tof, lambda s=shooter, w=weapon, t=track.target:
                      self._resolve(s, w, t, shot))

    def _resolve(self, shooter, weapon, target_name, shot=None):
        target = self.platforms[target_name]
        self.in_flight.discard((shooter.name, target_name))
        sides = {shooter.side, target.side}
        before = (target.alive, target.hp)
        self._apply_hit(shooter, weapon, target, sides)
        if shot is not None:
            shot["result"] = ("kill" if before[0] and not target.alive
                              else "hit" if target.hp < before[1] else "miss")

    def _apply_hit(self, shooter, weapon, target, sides):
        if not target.alive:
            self.log("MISS", f"{weapon.name} from {shooter.name} finds "
                             f"{target.name} already destroyed", sides)
        elif self.rng.random() < weapon.pk:
            target.hp -= weapon.damage
            if target.hp <= 0:
                target.alive = False
                self.log("KILL", f"{shooter.name} destroys {target.name}", sides)
            else:
                self.log("HIT", f"{shooter.name} damages {target.name} "
                                f"({target.hp:g}/{target.max_hp:g} hp)", sides)
        else:
            self.log("MISS", f"{weapon.name} from {shooter.name} misses "
                             f"{target.name}", sides)

    # ---- main loop -------------------------------------------------------------
    def _share_tracks(self, alive):
        net = [p for p in alive if p.comms_km > 0]
        for a in net:
            for b in net:
                if a is b or a.side != b.side:
                    continue
                if dist(a.pos, b.pos) > min(a.comms_km, b.comms_km):
                    continue
                for name, t in a.tracks.items():
                    mine = b.tracks.get(name)
                    if mine is None or mine.time < t.time:
                        b.tracks[name] = t

    def step(self):
        if not self._started:
            self._started = True
            for o in self.observers:
                o.on_start(self)
        self.detections = []
        alive = [p for p in self.platforms.values() if p.alive]
        for p in alive:
            p.sense(self)
        self._share_tracks(alive)
        for p in alive:
            p.think(self)
        for p in alive:
            p.move(self)
        self.time += self.dt
        while self._queue and self._queue[0][0] <= self.time:
            _, _, fn = heapq.heappop(self._queue)
            fn()
        for p in alive:
            self.history[p.name].append(p.pos)
        for o in self.observers:
            o.on_step(self)

    def finish(self):
        """Tell output adaptors the run is over (closes files and sockets)."""
        if not self._finished:
            self._finished = True
            for o in self.observers:
                o.on_end(self)

    def losses(self):
        out = {s: 0 for s in self.sides}
        for p in self.platforms.values():
            if not p.alive and not p.expendable:
                out[p.side] += 1
        return out

    def winner(self):
        """Return a side name, 'draw', or None while the fight is still on."""
        fighting = {p.side for p in self.platforms.values() if p.alive and p.armed}
        if len(fighting) == 1:
            return next(iter(fighting))
        if not fighting:
            return "draw"
        if self.time >= self.end_time:
            losses = self.losses()
            best = min(losses.values())
            leaders = [s for s, n in losses.items() if n == best]
            return leaders[0] if len(leaders) == 1 else "draw"
        return None

    def run(self, until=None):
        stop = self.end_time if until is None else min(until, self.end_time)
        while self.time < stop and self.winner() is None:
            self.step()
        return self.winner()
