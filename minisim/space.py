"""Space domain: orbits, manoeuvres, space surveillance, conjunctions, debris.

Earth-centred inertial frame, kilometres and seconds. Orbits are pure
two-body (Kepler) ellipses: no drag, no J2, no Sun or Moon. The Sun direction
is fixed. Everything in the bundled scenarios is notional.

Main pieces
  Orbit          Keplerian orbit; state(t), from_state(r, v, t), from_tle(...)
  lambert        two-point transfer solver used for rendezvous and tracking
  SpaceObject    satellite, rocket body or debris fragment
  Station        ground site with radar / optical sensors
  SpaceSim       the loop: mission sequences, sensing + fusion, conjunction
                 screening, collisions and debris, avoidance burns
  walker, coverage, trade_study    constellation design helpers
"""
from __future__ import annotations

import json
import math
import os
import random
from dataclasses import dataclass, field

MU = 398600.4418          # km^3/s^2
RE = 6378.137             # km
OMEGA_E = 7.2921159e-5    # rad/s, Earth rotation
ATMOSPHERE_KM = 120.0     # an orbit dipping below this is treated as decayed


# ---- small vector helpers (tuples) ---------------------------------------------
def add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def scale(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def norm(a): return math.sqrt(dot(a, a))
def unit(a): return scale(a, 1.0 / norm(a))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


# ---- orbits ----------------------------------------------------------------------
class Orbit:
    """Elliptical Keplerian orbit. Angles in radians; m0 is the mean anomaly
    at time `epoch`."""

    def __init__(self, a, e=0.0, inc=0.0, raan=0.0, argp=0.0, m0=0.0, epoch=0.0):
        if not (a > 0 and 0 <= e < 1):
            raise ValueError("only closed (elliptical) orbits are supported")
        self.a, self.e, self.inc = a, e, inc
        self.raan, self.argp, self.m0, self.epoch = raan, argp, m0, epoch
        self.n = math.sqrt(MU / a ** 3)
        co, so = math.cos(raan), math.sin(raan)
        cw, sw = math.cos(argp), math.sin(argp)
        ci, si = math.cos(inc), math.sin(inc)
        self._p = (co * cw - so * sw * ci, so * cw + co * sw * ci, sw * si)
        self._q = (-co * sw - so * cw * ci, -so * sw + co * cw * ci, cw * si)

    period = property(lambda self: 2 * math.pi / self.n)
    perigee_alt = property(lambda self: self.a * (1 - self.e) - RE)
    apogee_alt = property(lambda self: self.a * (1 + self.e) - RE)

    def state(self, t):
        """Position and velocity (km, km/s) at time t."""
        m = (self.m0 + self.n * (t - self.epoch)) % (2 * math.pi)
        ecc = m if self.e < 0.8 else math.pi
        for _ in range(30):
            step = (ecc - self.e * math.sin(ecc) - m) / (1 - self.e * math.cos(ecc))
            ecc -= step
            if abs(step) < 1e-12:
                break
        ce, se = math.cos(ecc), math.sin(ecc)
        b = math.sqrt(1 - self.e ** 2)
        xp, yp = self.a * (ce - self.e), self.a * b * se
        rmag = self.a * (1 - self.e * ce)
        k = math.sqrt(MU * self.a) / rmag
        vx, vy = -k * se, k * b * ce
        p, q = self._p, self._q
        return ((p[0] * xp + q[0] * yp, p[1] * xp + q[1] * yp, p[2] * xp + q[2] * yp),
                (p[0] * vx + q[0] * vy, p[1] * vx + q[1] * vy, p[2] * vx + q[2] * vy))

    def shifted(self, along_track_km):
        """The same orbit with the object moved along its path (negative = behind)."""
        return Orbit(self.a, self.e, self.inc, self.raan, self.argp,
                     self.m0 + along_track_km / self.a, self.epoch)

    @classmethod
    def circular(cls, alt_km, inc_deg=0.0, raan_deg=0.0, u_deg=0.0, epoch=0.0):
        return cls(RE + alt_km, 0.0, math.radians(inc_deg), math.radians(raan_deg),
                   0.0, math.radians(u_deg), epoch)

    @classmethod
    def from_state(cls, r, v, t=0.0):
        rm, vm = norm(r), norm(v)
        a = 1.0 / (2.0 / rm - vm * vm / MU)
        h = cross(r, v)
        hhat = unit(h)
        evec = scale(sub(scale(r, vm * vm - MU / rm), scale(v, dot(r, v))), 1 / MU)
        e = norm(evec)
        if a <= 0 or e >= 1:
            raise ValueError("escape trajectory")
        inc = math.acos(max(-1.0, min(1.0, hhat[2])))
        node = (-h[1], h[0], 0.0)
        if norm(node) < 1e-9 * norm(h):
            node, raan = (1.0, 0.0, 0.0), 0.0
        else:
            node = unit(node)
            raan = math.atan2(node[1], node[0])

        def angle(frm, to):
            return math.atan2(dot(cross(frm, to), hhat), dot(frm, to))

        if e < 1e-9:
            argp, nu = 0.0, angle(node, r)
        else:
            argp, nu = angle(node, evec), angle(evec, r)
        ecc = 2 * math.atan2(math.sqrt(1 - e) * math.sin(nu / 2),
                             math.sqrt(1 + e) * math.cos(nu / 2))
        return cls(a, e, inc, raan, argp, ecc - e * math.sin(ecc), t)

    @classmethod
    def from_tle(cls, line1, line2):
        """Read the mean elements of a two-line element set. The TLE epoch is
        treated as t = 0 and motion is two-body, so this is not SGP4."""
        inc = math.radians(float(line2[8:16]))
        raan = math.radians(float(line2[17:25]))
        e = float("0." + line2[26:33].strip())
        argp = math.radians(float(line2[34:42]))
        m0 = math.radians(float(line2[43:51]))
        n = float(line2[52:63]) * 2 * math.pi / 86400.0
        return cls((MU / n ** 2) ** (1 / 3), e, inc, raan, argp, m0, 0.0)

    @classmethod
    def from_spec(cls, spec):
        """Build from a scenario dictionary (see README for the forms)."""
        if "tle" in spec:
            return cls.from_tle(*spec["tle"])
        if "alt_km" in spec:
            return cls.circular(spec["alt_km"], spec.get("inc_deg", 0.0),
                                spec.get("raan_deg", 0.0), spec.get("u_deg", 0.0))
        return cls(spec["a_km"], spec.get("e", 0.0), math.radians(spec.get("inc_deg", 0)),
                   math.radians(spec.get("raan_deg", 0)),
                   math.radians(spec.get("argp_deg", 0)), math.radians(spec.get("m_deg", 0)))


def circular_through(point, t, inc_deg, miss_km=0.0):
    """A circular orbit of the given inclination that passes (ascending)
    `miss_km` above the inertial point `point` at time t."""
    r = norm(point)
    lat = math.asin(point[2] / r)
    inc = math.radians(inc_deg)
    if abs(math.sin(lat)) > abs(math.sin(inc)):
        raise ValueError("inclination too low to reach that latitude")
    u = math.asin(math.sin(lat) / math.sin(inc))
    raan = math.atan2(point[1], point[0]) - math.atan2(math.cos(inc) * math.sin(u),
                                                       math.cos(u))
    return Orbit(r + miss_km, 0.0, inc, raan, 0.0, u, t)


# ---- mission planning: steps that wait for a point on the orbit -------------------
WHEN = ("now", "periapsis", "apoapsis", "ascending_node", "descending_node")


def time_to(orbit, t, when):
    """Seconds after t until the orbit next reaches the named point.
    Points that do not exist (apsides of a circle, nodes of an equatorial
    orbit) count as 'now'."""
    if when == "now":
        return 0.0
    if when in ("periapsis", "apoapsis"):
        if orbit.e < 1e-6:
            return 0.0
        nu = 0.0 if when == "periapsis" else math.pi
    else:
        if abs(math.sin(orbit.inc)) < 1e-6:
            return 0.0
        nu = -orbit.argp if when == "ascending_node" else math.pi - orbit.argp
    ecc = 2 * math.atan2(math.sqrt(1 - orbit.e) * math.sin(nu / 2),
                         math.sqrt(1 + orbit.e) * math.cos(nu / 2))
    target = ecc - orbit.e * math.sin(ecc)
    now = orbit.m0 + orbit.n * (t - orbit.epoch)
    gap = (target - now) % (2 * math.pi)
    if gap > 2 * math.pi - 1e-6:
        gap = 0.0
    return gap / orbit.n


def plan_mission(orbit, steps, t0=0.0, budget_kms=None, targets=None):
    """Work through mission steps in order. Each step waits for its "when"
    point, then burns. Returns one row per burn:
    {step (index), at, dv (vector), dv_kms, label, orbit (after), ok, why}.

    Steps:  {"do": "tangent", "apsis_alt_km": X}       set the far apsis to X
            {"do": "circularize"}                       make the orbit circular here
            {"do": "change_inclination", "inc_deg": X}  best done at a node
            {"do": "burn", "prograde_ms": X}            speed up (+) or slow down (-)
            {"do": "rendezvous", "target": name, "tof_s": X, "standoff_km": Y}
                two burns: depart, then match orbits Y km behind the target
    each with an optional "when" (see WHEN; default "now"). `targets` maps
    names to orbits for rendezvous; the target is assumed not to manoeuvre.
    """
    rows, state = [], {"orbit": orbit, "spent": 0.0}

    def burn(index, t, r, v, new_v, label):
        dv = sub(new_v, v)
        state["spent"] += norm(dv)
        row = {"step": index, "at": t, "dv": dv, "dv_kms": norm(dv), "label": label,
               "orbit": None, "ok": False, "why": "", "pos": r}
        rows.append(row)
        try:
            new = Orbit.from_state(r, new_v, t)
        except ValueError:
            row["why"] = "leaves Earth orbit"
            return False
        row["orbit"] = state["orbit"] = new
        if new.perigee_alt < ATMOSPHERE_KM:
            row["why"] = "dips into the atmosphere"
        elif budget_kms is not None and state["spent"] > budget_kms + 1e-9:
            row["why"] = "not enough fuel"
        else:
            row["ok"] = True
        return row["ok"]

    def fail(index, t, r, label, why):
        rows.append({"step": index, "at": t, "dv": (0.0, 0.0, 0.0), "dv_kms": 0.0,
                     "label": label, "orbit": None, "ok": False, "why": why, "pos": r})

    t = t0
    for index, step in enumerate(steps):
        orbit = state["orbit"]
        t += time_to(orbit, t, step.get("when", "now"))
        r, v = orbit.state(t)
        rm = norm(r)
        rhat = unit(r)
        that = cross(unit(cross(r, v)), rhat)          # horizontal, along travel
        do = step["do"]
        if do == "tangent":
            a = (rm + RE + step["apsis_alt_km"]) / 2
            ok = burn(index, t, r, v, scale(that, math.sqrt(MU * (2 / rm - 1 / a))),
                      f"set far apsis to {step['apsis_alt_km']:g} km")
        elif do == "circularize":
            ok = burn(index, t, r, v, scale(that, math.sqrt(MU / rm)), "circularize")
        elif do == "change_inclination":
            delta = math.radians(step["inc_deg"]) - orbit.inc
            theta = delta if v[2] >= 0 else -delta
            vr = dot(v, rhat)
            vt = sub(v, scale(rhat, vr))
            new_v = add(add(scale(vt, math.cos(theta)),
                            scale(cross(rhat, vt), math.sin(theta))), scale(rhat, vr))
            ok = burn(index, t, r, v, new_v,
                      f"change inclination to {step['inc_deg']:g} deg")
        elif do == "burn":
            ok = burn(index, t, r, v, add(v, scale(unit(v), step["prograde_ms"] / 1000.0)),
                      f"burn {step['prograde_ms']:+g} m/s")
        elif do == "rendezvous":
            name, tof = step["target"], step["tof_s"]
            target = (targets or {}).get(name)
            if target is None:
                fail(index, t, r, f"depart for {name}", "unknown target")
                break
            aim = target.shifted(-step.get("standoff_km", 1.0))
            r2, v2 = aim.state(t + tof)
            best = None
            for long_way in (False, True):
                sol = lambert(r, r2, tof, long_way)
                if sol:
                    cost = norm(sub(sol[0], v)) + norm(sub(v2, sol[1]))
                    if best is None or cost < best[0]:
                        best = (cost, sol[0])
            if best is None:
                fail(index, t, r, f"depart for {name}", "no transfer in that time")
                break
            ok = burn(index, t, r, v, best[1], f"depart for {name}")
            if ok:
                t += tof
                rc, vc = state["orbit"].state(t)
                ok = burn(index, t, rc, vc, v2, f"match orbit with {name}")
        else:
            raise ValueError(f"unknown mission step {do!r}")
        if not ok:
            break
    return rows


def _stumpff(z):
    if z > 1e-8:
        s = math.sqrt(z)
        return (1 - math.cos(s)) / z, (s - math.sin(s)) / s ** 3
    if z < -1e-8:
        s = math.sqrt(-z)
        return (math.cosh(s) - 1) / -z, (math.sinh(s) - s) / s ** 3
    return 0.5, 1.0 / 6.0


def lambert(r1, r2, tof, long_way=False):
    """Velocities at r1 and r2 for a single-revolution transfer taking `tof`
    seconds. Returns (v1, v2) or None if there is no solution."""
    r1n, r2n = norm(r1), norm(r2)
    cosd = max(-1.0, min(1.0, dot(r1, r2) / (r1n * r2n)))
    d = math.acos(cosd)
    if long_way:
        d = 2 * math.pi - d
    if abs(math.sin(d)) < 1e-9:
        return None
    big_a = math.sin(d) * math.sqrt(r1n * r2n / (1 - math.cos(d)))

    def y(z):
        c, s = _stumpff(z)
        return r1n + r2n + big_a * (z * s - 1) / math.sqrt(c)

    def f(z):
        c, s = _stumpff(z)
        yy = y(z)
        return (yy / c) ** 1.5 * s + big_a * math.sqrt(yy) - math.sqrt(MU) * tof

    lo, hi = -40.0, 4 * math.pi ** 2 - 1e-6
    while y(lo) <= 0 and lo < hi:
        lo += 0.05
    if lo >= hi or f(lo) > 0 or f(hi) < 0:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if y(mid) <= 0 or f(mid) < 0:
            lo = mid
        else:
            hi = mid
    yy = y(0.5 * (lo + hi))
    ff, g, gdot = 1 - yy / r1n, big_a * math.sqrt(yy / MU), 1 - yy / r2n
    return (scale(sub(r2, scale(r1, ff)), 1 / g),
            scale(sub(scale(r2, gdot), r1), 1 / g))


# ---- Earth geometry --------------------------------------------------------------
def ground_eci(lat_deg, lon_deg, t, alt_km=0.0):
    lat = math.radians(lat_deg)
    lam = math.radians(lon_deg) + OMEGA_E * t
    r = RE + alt_km
    return (r * math.cos(lat) * math.cos(lam), r * math.cos(lat) * math.sin(lam),
            r * math.sin(lat))


def elevation_deg(ground, sat):
    rho = sub(sat, ground)
    return math.degrees(math.asin(dot(rho, ground) / (norm(rho) * norm(ground))))


def subpoint(r, t):
    """Latitude and longitude (degrees) of the point under an object."""
    lat = math.degrees(math.asin(r[2] / norm(r)))
    lon = math.degrees(math.atan2(r[1], r[0]) - OMEGA_E * t)
    return lat, (lon + 180) % 360 - 180


def earth_blocks(a, b, margin_km=50.0):
    """True if the straight line from a to b passes through the Earth."""
    ab = sub(b, a)
    k = max(0.0, min(1.0, -dot(a, ab) / dot(ab, ab)))
    return norm(add(a, scale(ab, k))) < RE + margin_km


def closest_approach(ra, va, rb, vb, dt):
    """Straight-line closest approach over the next dt seconds.
    Returns (seconds from now, distance); the time is not clamped."""
    r, v = sub(ra, rb), sub(va, vb)
    vv = dot(v, v)
    tau = -dot(r, v) / vv if vv > 1e-12 else 0.0
    k = max(0.0, min(dt, tau))
    return tau, norm(add(r, scale(v, k)))


# ---- objects ---------------------------------------------------------------------
@dataclass
class SpaceSensor:
    kind: str = "radar"            # radar | optical | space
    range_km: float = 3000.0
    min_elev_deg: float = 10.0     # ground sensors only
    sigma_km: float = 0.1          # position noise of one observation
    p_detect: float = 0.9


@dataclass
class SpaceObject:
    name: str
    side: str = "none"
    orbit: Orbit = None
    kind: str = "satellite"        # satellite | rocket body | debris
    signature: float = 1.0         # scales the range it can be seen at
    dv: float = 0.0                # manoeuvre budget, km/s
    avoid: bool = False            # dodge predicted close approaches
    sensors: list = field(default_factory=list)
    payload: dict = None           # e.g. {"type": "isr", "min_elev_deg": 15}
    sequence: list = field(default_factory=list)   # mission sequence
    alive: bool = True
    rpo_target: str = None
    crosslink_km: float = 0.0      # relay link range to other satellites; 0 = none
    mission: list = field(default_factory=list)    # condition-based steps
    mission_start_s: float = 0.0


@dataclass
class Station:
    name: str
    side: str
    lat: float
    lon: float
    sensors: list = field(default_factory=list)


@dataclass
class SpaceTrack:
    """One side's fused picture of one object."""
    target: str
    pos: tuple = None
    sigma: float = 0.0
    time: float = -1e9
    sources: set = field(default_factory=set)
    fixes: list = field(default_factory=list)      # (time, pos), thinned
    orbit: Orbit = None                            # estimated orbit, if any

    def perceived(self, t):
        return self.orbit.state(t)[0] if self.orbit else self.pos


class SpaceSim:
    def __init__(self, dt=10.0, end_time=7200.0, seed=None, name="space scenario",
                 conjunction_km=10.0, collision_km=0.1, debris_per_object=8,
                 scan_s=20.0, screen_s=300.0, screen_horizon_s=3000.0,
                 avoid_km=2.0, avoid_dv_kms=0.001, sun=(1.0, 0.0, 0.0),
                 conjunctions=True):
        self.name, self.dt, self.end_time = name, dt, end_time
        self.rng = random.Random(seed)
        self.conjunction_km, self.collision_km = conjunction_km, collision_km
        self.debris_per_object = debris_per_object
        self.scan_s, self.screen_s = scan_s, screen_s
        self.screen_horizon_s, self.avoid_km = screen_horizon_s, avoid_km
        self.avoid_dv_kms = avoid_dv_kms
        self.sun = unit(sun)
        self.conjunctions = conjunctions   # False skips close-approach screening
        self.time = 0.0
        self.objects, self.stations, self.targets = {}, {}, []
        self.events = []                 # (time, kind, text)
        self.tracks = {}                 # side -> {target name: SpaceTrack}
        self.stats = {}                  # side -> {target: [scans, tracked, err_sum, err_n]}
        self.history = []                # (time, {name: (r, v)}) for non-debris
        self.record_s = 30.0
        self._next_scan = self._next_screen = self._next_record = 0.0
        self._prox_logged = set()

    # ---- setup / logging ---------------------------------------------------------
    def add(self, obj):
        if obj.name in self.objects:
            raise ValueError(f"duplicate object name: {obj.name}")
        obj.sequence = sorted(obj.sequence, key=lambda i: i["at"])
        self.objects[obj.name] = obj
        return obj

    def log(self, kind, text, t=None):
        self.events.append((self.time if t is None else t, kind, text))

    def alive(self):
        return [o for o in self.objects.values() if o.alive]

    # ---- manoeuvres and mission sequences ------------------------------------------
    def _burn(self, obj, at, dv, label):
        size = norm(dv)
        if size > obj.dv + 1e-12:
            self.log("ABORT", f"{obj.name} lacks fuel for {label} "
                              f"({size * 1000:.1f} m/s needed, "
                              f"{obj.dv * 1000:.1f} left)", at)
            return False
        r, v = obj.orbit.state(at)
        try:
            new = Orbit.from_state(r, add(v, dv), at)
        except ValueError:
            self.log("ABORT", f"{obj.name}: {label} would leave Earth orbit", at)
            return False
        obj.dv -= size
        obj.orbit = new
        self.log("BURN", f"{obj.name} {label}: {size * 1000:.1f} m/s -> "
                         f"{new.perigee_alt:.0f} x {new.apogee_alt:.0f} km", at)
        if new.perigee_alt < ATMOSPHERE_KM:
            obj.alive = False
            self.log("DECAY", f"{obj.name} re-enters the atmosphere", at)
        return True

    def _execute(self, obj, item):
        at, do = item["at"], item["do"]
        r, v = obj.orbit.state(at)
        if do == "note":
            self.log("ABORT", f"{obj.name}: {item['label']}", at)
        elif do == "burn_vec":
            self._burn(obj, at, tuple(item["dv"]), item.get("label", "burn"))
        elif do == "burn":
            dv = add(add(scale(unit(v), item.get("prograde", 0.0)),
                         scale(unit(r), item.get("radial", 0.0))),
                     scale(unit(cross(r, v)), item.get("normal", 0.0)))
            self._burn(obj, at, dv, item.get("label", "burn"))
        elif do == "hohmann":
            r1, r2 = norm(r), RE + item["alt_km"]
            dv1 = math.sqrt(MU / r1) * (math.sqrt(2 * r2 / (r1 + r2)) - 1)
            dv2 = math.sqrt(MU / r2) * (1 - math.sqrt(2 * r1 / (r1 + r2)))
            if self._burn(obj, at, scale(unit(v), dv1), "transfer burn"):
                tof = math.pi * math.sqrt(((r1 + r2) / 2) ** 3 / MU)
                obj.sequence.append({"at": at + tof, "do": "burn", "prograde": dv2,
                                     "label": "circularise"})
        elif do == "rendezvous":
            target = self.objects[item["target"]]
            standoff, tof = item.get("standoff_km", 1.0), item["tof_s"]
            r2, v2 = target.orbit.shifted(-standoff).state(at + tof)
            best = None
            for long_way in (False, True):
                sol = lambert(r, r2, tof, long_way)
                if sol:
                    cost = norm(sub(sol[0], v)) + norm(sub(v2, sol[1]))
                    if best is None or cost < best[0]:
                        best = (cost, sol[0])
            if best is None:
                self.log("ABORT", f"{obj.name}: no transfer to {target.name}", at)
            elif self._burn(obj, at, sub(best[1], v), f"departs for {target.name}"):
                obj.rpo_target = target.name
                obj.sequence.append({"at": at + tof, "do": "match",
                                     "target": target.name, "standoff_km": standoff})
        elif do == "match":
            target = self.objects[item["target"]]
            _, v2 = target.orbit.shifted(-item.get("standoff_km", 1.0)).state(at)
            if self._burn(obj, at, sub(v2, v), f"matches orbit with {target.name}"):
                gap = norm(sub(obj.orbit.state(at)[0], target.orbit.state(at)[0]))
                self.log("ARRIVE", f"{obj.name} holds {gap:.1f} km from "
                                   f"{target.name}", at)
        else:
            raise ValueError(f"unknown mission step {do!r}")

    def _run_sequences(self):
        for obj in self.alive():
            while obj.alive and obj.sequence:
                obj.sequence.sort(key=lambda i: i["at"])
                if obj.sequence[0]["at"] > self.time:
                    break
                self._execute(obj, obj.sequence.pop(0))

    # ---- sensing and fusion ----------------------------------------------------------
    def _sunlit(self, r):
        along = dot(r, self.sun)
        return along > 0 or norm(sub(r, scale(self.sun, along))) > RE

    def _observe(self, sensor, at, is_ground, target, pos):
        """Return a noisy position if this sensor sees the target, else None."""
        rng = norm(sub(pos, at))
        if rng > sensor.range_km * target.signature:
            return None
        if is_ground:
            if elevation_deg(at, pos) < sensor.min_elev_deg:
                return None
            if sensor.kind == "optical" and (dot(at, self.sun) > 0
                                             or not self._sunlit(pos)):
                return None       # needs a dark site and a sunlit target
        elif earth_blocks(at, pos):
            return None
        if self.rng.random() > sensor.p_detect:
            return None
        g = self.rng.gauss
        return (pos[0] + g(0, sensor.sigma_km), pos[1] + g(0, sensor.sigma_km),
                pos[2] + g(0, sensor.sigma_km))

    def _scan(self, states):
        t = self.time
        lookers = {}       # side -> [(sensor, position, is_ground)]
        for st in self.stations.values():
            here = ground_eci(st.lat, st.lon, t)
            lookers.setdefault(st.side, []).extend((s, here, True) for s in st.sensors)
        for o in self.alive():
            if o.sensors:
                lookers.setdefault(o.side, []).extend(
                    (s, states[o.name][0], False) for s in o.sensors)
        for side, sensors in lookers.items():
            tracks = self.tracks.setdefault(side, {})
            stats = self.stats.setdefault(side, {})
            for target in self.alive():
                if target.side == side:
                    continue
                truth = states[target.name][0]
                obs = []
                for sensor, at, is_ground in sensors:
                    seen = self._observe(sensor, at, is_ground, target, truth)
                    if seen:
                        obs.append((seen, sensor))
                if obs:
                    self._fuse(tracks, target.name, obs, side)
                row = stats.setdefault(target.name, [0, 0, 0.0, 0])
                row[0] += 1
                trk = tracks.get(target.name)
                # custody: a fresh fix, or an orbit estimate to coast on
                if trk and t - trk.time <= (300 if trk.orbit else 0):
                    row[1] += 1
                    row[2] += norm(sub(trk.perceived(t), truth))
                    row[3] += 1

    def _fuse(self, tracks, name, obs, side):
        """Combine this scan's observations (weighted by accuracy) into the
        track, then re-estimate the orbit from two fixes at least two minutes apart."""
        t = self.time
        weights = [1 / s.sigma_km ** 2 for _, s in obs]
        total = sum(weights)
        pos = tuple(sum(w * p[i] for (p, _), w in zip(obs, weights)) / total
                    for i in range(3))
        trk = tracks.get(name)
        if trk is None or t - trk.time > 900:
            self.log("TRACK", f"{side} acquires {name} "
                              f"({'+'.join(sorted({s.kind for _, s in obs}))})")
            trk = tracks[name] = SpaceTrack(name)
        trk.pos, trk.sigma, trk.time = pos, math.sqrt(1 / total), t
        trk.sources |= {s.kind for _, s in obs}
        if not trk.fixes or t - trk.fixes[-1][0] >= 60:
            trk.fixes.append((t, pos))
        trk.fixes = [f for f in trk.fixes if t - f[0] <= 1500]
        t0, p0 = trk.fixes[0]
        if t - t0 >= 120:
            sol = lambert(p0, pos, t - t0)
            if sol:
                try:
                    trk.orbit = Orbit.from_state(p0, sol[0], t0)
                except ValueError:
                    pass

    # ---- conjunctions, collisions, debris, avoidance -------------------------------------
    def _conjunctions(self, states):
        objs = self.alive()
        reach = (16.0 * self.dt + self.conjunction_km) ** 2
        hits = []
        for i, a in enumerate(objs):
            ra, va = states[a.name]
            for b in objs[i + 1:]:
                rb, vb = states[b.name]
                d = sub(ra, rb)
                if dot(d, d) > reach:
                    continue
                tau, miss = closest_approach(ra, va, rb, vb, self.dt)
                slow = norm(sub(va, vb)) < 0.005
                if slow:                       # co-orbiting: proximity, not a pass
                    pair = (a.name, b.name)
                    if (a.side != b.side and miss < self.conjunction_km
                            and pair not in self._prox_logged):
                        self._prox_logged.add(pair)
                        self.log("PROXIMITY", f"{a.name} and {b.name} are "
                                              f"{miss:.1f} km apart and co-orbiting")
                    continue
                if 0 <= tau < self.dt and miss < self.conjunction_km:
                    hits.append((self.time + tau, a, b, miss))
        for tca, a, b, miss in hits:
            if not (a.alive and b.alive):
                continue
            if miss < self.collision_km:
                self._collide(a, b, tca)
            else:
                self.log("CONJUNCTION", f"{a.name} passes {b.name} at "
                                        f"{miss:.2f} km", tca)

    def _collide(self, a, b, tca):
        self.log("COLLISION", f"{a.name} and {b.name} collide", tca)
        made = 0
        for parent in (a, b):
            r, v = parent.orbit.state(tca)
            parent.alive = False
            for k in range(self.debris_per_object):
                kick = tuple(self.rng.gauss(0, 0.15) for _ in range(3))
                try:
                    orbit = Orbit.from_state(r, add(v, kick), tca)
                except ValueError:
                    continue
                if orbit.perigee_alt < ATMOSPHERE_KM:
                    continue
                self.add(SpaceObject(f"{parent.name} frag {k + 1}", "none", orbit,
                                     kind="debris", signature=0.3))
                made += 1
        self.log("DEBRIS", f"{made} trackable fragments created", tca)

    def predict(self, obj, horizon=None, step=10.0):
        """Close approaches of `obj` with other objects over the horizon,
        as (time, other, miss_km) for misses under the avoidance threshold."""
        horizon = horizon or self.screen_horizon_s
        lo = obj.orbit.a * (1 - obj.orbit.e) - self.avoid_km
        hi = obj.orbit.a * (1 + obj.orbit.e) + self.avoid_km
        times = [self.time + k * step for k in range(int(horizon / step) + 1)]
        mine = [obj.orbit.state(t) for t in times]
        found = []
        for other in self.alive():
            if (other is obj or other.side == obj.side
                    or other.name == obj.rpo_target or other.rpo_target == obj.name):
                continue
            oo = other.orbit
            if oo.a * (1 + oo.e) < lo or oo.a * (1 - oo.e) > hi:
                continue                   # altitude bands never overlap
            for t, (ra, va) in zip(times, mine):
                rb, vb = oo.state(t)
                tau, miss = closest_approach(ra, va, rb, vb, step)
                if 0 <= tau < step and miss < self.avoid_km:
                    found.append((t + tau, other, miss))
        return found

    @staticmethod
    def _miss_near(orbit_a, orbit_b, tca, span=180.0, step=2.0):
        best, t = float("inf"), tca - span
        while t < tca + span:
            ra, va = orbit_a.state(t)
            rb, vb = orbit_b.state(t)
            best = min(best, closest_approach(ra, va, rb, vb, step)[1])
            t += step
        return best

    def _avoid(self):
        for obj in self.alive():
            if not obj.avoid or obj.dv < self.avoid_dv_kms:
                continue
            for tca, other, miss in self.predict(obj):
                if tca - self.time < 120:
                    continue
                r, v = obj.orbit.state(self.time)
                options = []
                for sign in (1, -1):
                    trial = Orbit.from_state(
                        r, add(v, scale(unit(v), sign * self.avoid_dv_kms)), self.time)
                    options.append((self._miss_near(trial, other.orbit, tca), sign))
                new_miss, sign = max(options)
                self.log("WARNING", f"{obj.name}: predicted pass of {other.name} at "
                                    f"{miss:.2f} km in {tca - self.time:.0f} s")
                if self._burn(obj, self.time, scale(unit(v), sign * self.avoid_dv_kms),
                              "avoidance burn"):
                    self.log("AVOID", f"{obj.name} dodges {other.name}: miss "
                                      f"{miss:.2f} km -> {new_miss:.1f} km")
                break                       # one burn per screening

    # ---- main loop -----------------------------------------------------------------------
    def step(self):
        self._run_sequences()
        states = {o.name: o.orbit.state(self.time) for o in self.alive()}
        if self.time >= self._next_record:
            self._next_record = self.time + self.record_s
            self.history.append((self.time, {n: s for n, s in states.items()
                                             if self.objects[n].kind != "debris"}))
        if self.time >= self._next_scan:
            self._next_scan = self.time + self.scan_s
            self._scan(states)
        if self.time >= self._next_screen:
            self._next_screen = self.time + self.screen_s
            before = {o.name: o.orbit for o in self.alive()}
            self._avoid()
            for o in self.alive():          # refresh anything that just burned
                if o.orbit is not before.get(o.name):
                    states[o.name] = o.orbit.state(self.time)
        if self.conjunctions:
            self._conjunctions(states)
        self.time += self.dt

    def run(self):
        while self.time < self.end_time:
            self.step()
        return self

    # ---- reports ---------------------------------------------------------------------------
    def tracking_report(self):
        lines = []
        for side, rows in sorted(self.stats.items()):
            lines.append(f"Space surveillance picture for {side}:")
            lines.append(f"  {'object':<20}{'custody':>8}{'mean error':>12}  sensors")
            debris = [0, 0]
            for name, (scans, tracked, err, n) in rows.items():
                if self.objects[name].kind == "debris":
                    debris[0] += scans
                    debris[1] += tracked
                    continue
                trk = self.tracks[side].get(name)
                srcs = "+".join(sorted(trk.sources)) if trk else "never seen"
                err_s = f"{err / n:9.2f} km" if n else "           -"
                lines.append(f"  {name:<20}{100 * tracked / scans:7.0f}%{err_s}  {srcs}")
            if debris[0]:
                lines.append(f"  {'(debris, all)':<20}"
                             f"{100 * debris[1] / debris[0]:7.0f}%")
        return "\n".join(lines)

    def coverage_report(self, step=60.0, duration=None):
        if not self.targets:
            return ""
        lines = ["Ground coverage (share of time in view, longest gap):"]
        for side in sorted({o.side for o in self.objects.values() if o.payload}):
            sats = [(o.orbit, o.payload.get("min_elev_deg", 10.0))
                    for o in self.alive() if o.side == side and o.payload]
            for tgt in self.targets:
                pct, gap = coverage(sats, tgt["lat"], tgt["lon"],
                                    duration or self.end_time, step)
                lines.append(f"  {side:<6}{tgt['name']:<16}{pct:5.0f}%  "
                             f"gap {gap / 60:5.1f} min")
        return "\n".join(lines)


def report(sim):
    """The text summary of a finished run: events, tracking, coverage."""
    lines = []
    for t, kind, text in sorted(sim.events, key=lambda e: e[0]):
        if kind == "TRACK" and " frag " in text:
            continue                       # keep the log readable
        lines.append(f"  [{t:6.0f}s] {kind:<12}{text}")
    live = sim.alive()
    lines.append(f"\n{sim.name} at t={sim.time:.0f}s: "
                 f"{sum(o.kind != 'debris' for o in live)} objects and "
                 f"{sum(o.kind == 'debris' for o in live)} debris fragments in orbit")
    for part in (sim.tracking_report(), sim.coverage_report(), comms_report(sim)):
        if part:
            lines.append("\n" + part)
    return "\n".join(lines)


# ---- constellation design ------------------------------------------------------------
def walker(sats, planes, phasing, alt_km, inc_deg, raan0_deg=0.0,
           raan_range_deg=360.0, anomaly0_deg=0.0, anomaly_step_deg=None):
    """Orbits of a Walker-style constellation (sats total, evenly spread).

    Planes are spread over `raan_range_deg` (360 for a delta pattern, 180 for
    a star). Each plane's satellites are offset from the previous plane's by
    `anomaly_step_deg`, or by the Walker phasing number if that is not given.
    """
    per_plane = max(1, sats // planes)
    step = 360.0 * phasing / sats if anomaly_step_deg is None else anomaly_step_deg
    out = []
    for p in range(planes):
        for s in range(per_plane):
            out.append(Orbit.circular(alt_km, inc_deg,
                                      raan0_deg + raan_range_deg * p / planes,
                                      anomaly0_deg + 360.0 * s / per_plane + step * p))
    return out


def coverage(sats, lat, lon, duration, step=60.0):
    """sats: [(orbit, min_elev_deg)]. Returns (% of time covered, longest gap s)."""
    covered = gap = worst = 0
    steps = int(duration / step)
    for k in range(steps):
        t = k * step
        g = ground_eci(lat, lon, t)
        if any(elevation_deg(g, o.state(t)[0]) >= elev for o, elev in sats):
            covered += 1
            gap = 0
        else:
            gap += 1
            worst = max(worst, gap)
    return 100.0 * covered / max(steps, 1), worst * step


def trade_study(spec, targets):
    """Compare constellation designs. Returns printable lines."""
    duration, step = spec.get("duration_s", 86400), spec.get("step_s", 60)
    elev = spec.get("min_elev_deg", 10.0)
    lines = [f"Constellation trade study over {duration / 3600:.0f} h "
             f"(min elevation {elev:g} deg)",
             f"  {'design':<16}{'sats':>5}{'alt km':>8}{'inc':>6}"
             f"{'mean cover':>12}{'worst gap':>11}"]
    for d in spec["designs"]:
        orbits = walker(d["sats"], d["planes"], d.get("phasing", 1),
                        d["alt_km"], d["inc_deg"])
        res = [coverage([(o, elev) for o in orbits], t["lat"], t["lon"], duration, step)
               for t in targets]
        lines.append(f"  {d['name']:<16}{d['sats']:>5}{d['alt_km']:>8g}{d['inc_deg']:>6g}"
                     f"{sum(r[0] for r in res) / len(res):>11.0f}%"
                     f"{max(r[1] for r in res) / 60:>8.0f} min")
    return lines


# ---- scenario loading ------------------------------------------------------------------
def _sensors(specs):
    return [SpaceSensor(**s) for s in specs or []]


def load_space(path, seed=None):
    with open(path) as f:
        data = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    sim = SpaceSim(dt=data.get("dt", 10.0), end_time=data.get("end_time", 7200.0),
                   seed=seed, name=data.get("name", path), **data.get("settings", {}))
    sim.spec = data
    catalogue = {}
    if "catalogue" in data:
        with open(os.path.join(base, data["catalogue"])) as f:
            catalogue = {c["name"]: c for c in json.load(f)["objects"]}
    deferred = []
    for spec in data.get("objects", []):
        if "from_catalogue" in spec:
            entry = dict(catalogue[spec["from_catalogue"]])
            entry.update({k: v for k, v in spec.items() if k != "from_catalogue"})
            spec = entry
        if "intercept" in spec.get("orbit", {}):
            deferred.append(spec)           # needs the other object to exist first
            continue
        sim.add(_object(spec, Orbit.from_spec(spec["orbit"])))
    for spec in deferred:
        hit = spec["orbit"]["intercept"]
        point = sim.objects[hit["with"]].orbit.state(hit["at_s"])[0]
        sim.add(_object(spec, circular_through(point, hit["at_s"], hit["inc_deg"],
                                               hit.get("miss_km", 0.0))))
    for c in data.get("constellations", []):
        orbits = walker(c["sats"], c["planes"], c.get("phasing", 1), c["alt_km"],
                        c["inc_deg"], c.get("raan0_deg", 0.0),
                        c.get("raan_range_deg", 360.0), c.get("anomaly0_deg", 0.0),
                        c.get("anomaly_step_deg"))
        for k, orbit in enumerate(orbits, 1):
            sim.add(SpaceObject(f"{c['name']}-{k}", c["side"], orbit,
                                crosslink_km=c.get("crosslink_km", 0.0),
                                payload=c.get("payload"),
                                sensors=_sensors(c.get("sensors")),
                                dv=c.get("dv_kms", 0.0), avoid=c.get("avoid", False)))
    for s in data.get("stations", []):
        sim.stations[s["name"]] = Station(s["name"], s["side"], s["lat"], s["lon"],
                                          _sensors(s.get("sensors")))
    sim.targets = data.get("targets", [])
    sim.links = data.get("links", [])
    start = {name: o.orbit for name, o in sim.objects.items()}
    for obj in sim.objects.values():           # plan condition-based missions
        for row in plan_mission(obj.orbit, obj.mission, obj.mission_start_s,
                                targets=start):
            if row["orbit"] is None:
                obj.sequence.append({"at": row["at"], "do": "note",
                                     "label": f"{row['label']}: {row['why']}"})
            else:
                obj.sequence.append({"at": row["at"], "do": "burn_vec",
                                     "dv": row["dv"], "label": row["label"]})
        obj.sequence.sort(key=lambda i: i["at"])
    return sim


def _object(spec, orbit):
    if "shape" in spec:
        spec = dict(spec, signature=signature_from_shape(spec["shape"]))
    return SpaceObject(spec["name"], spec.get("side", "none"), orbit,
                       kind=spec.get("kind", "satellite"),
                       signature=spec.get("signature", 1.0), dv=spec.get("dv_kms", 0.0),
                       avoid=spec.get("avoid", False),
                       sensors=_sensors(spec.get("sensors")),
                       payload=spec.get("payload"),
                       sequence=[dict(i) for i in spec.get("sequence", [])],
                       crosslink_km=spec.get("crosslink_km", 0.0),
                       mission=spec.get("mission", []),
                       mission_start_s=spec.get("mission_start_s", 0.0))


REF_AREA_M2 = 20.0


def signature_from_shape(shape):
    """Visibility multiplier from a simple body: a box, sun-tracking solar
    panels and an antenna. Uses the average area presented to a viewer,
    relative to a 20 m^2 reference; detection range scales with its square
    root. {"body_m": [x, y, z], "panels_m2": A, "antenna_m2": B}"""
    x, y, z = shape.get("body_m", [1.0, 1.0, 1.0])
    area = ((x * y + y * z + z * x) / 2 + 0.5 * shape.get("panels_m2", 0.0)
            + 0.5 * shape.get("antenna_m2", 0.0))
    return math.sqrt(area / REF_AREA_M2)


LIGHT_KM_PER_MS = 299.792458


def route(nodes, a, b):
    """Shortest relay path from ground point a to ground point b.
    nodes: [(position, crosslink_km, min_elev_deg)]. Returns (km, [indices])
    or None. Up/down links need the elevation; crosslinks need range and a
    line of sight clear of the Earth."""
    n = len(nodes)
    dist = [float("inf")] * n
    prev = [-1] * n
    done = [False] * n
    for i, (pos, _, elev) in enumerate(nodes):
        if elevation_deg(a, pos) >= elev:
            dist[i] = norm(sub(pos, a))
    best, last = float("inf"), -1
    while True:
        i = min((k for k in range(n) if not done[k]), key=dist.__getitem__, default=-1)
        if i < 0 or dist[i] >= best:
            break
        done[i] = True
        pos, reach, elev = nodes[i]
        if elevation_deg(b, pos) >= elev and dist[i] + norm(sub(pos, b)) < best:
            best, last = dist[i] + norm(sub(pos, b)), i
        for j in range(n):
            if done[j]:
                continue
            d = norm(sub(nodes[j][0], pos))
            if (d <= min(reach, nodes[j][1]) and dist[i] + d < dist[j]
                    and not earth_blocks(pos, nodes[j][0], 80.0)):
                dist[j], prev[j] = dist[i] + d, i
    if last < 0:
        return None
    path = [last]
    while prev[path[-1]] >= 0:
        path.append(prev[path[-1]])
    return best, path[::-1]


def comms_report(sim, step=900.0, duration=86400.0):
    """For each link in the scenario: how often a relay path exists between
    the two ground sites, and its typical delay and number of satellites."""
    links = getattr(sim, "links", [])
    relays = [o for o in sim.alive() if o.crosslink_km > 0]
    if not links:
        return ""
    sites = {t["name"]: t for t in sim.targets}
    tally = {i: [0, 0.0, 0] for i in range(len(links))}
    steps = int(duration / step)
    for k in range(steps):
        t = k * step
        nodes = [(o.orbit.state(t)[0], o.crosslink_km,
                  (o.payload or {}).get("min_elev_deg", 10.0)) for o in relays]
        for i, link in enumerate(links):
            a, b = sites[link["from"]], sites[link["to"]]
            found = route(nodes, ground_eci(a["lat"], a["lon"], t),
                          ground_eci(b["lat"], b["lon"], t))
            if found:
                tally[i][0] += 1
                tally[i][1] += found[0] / LIGHT_KM_PER_MS
                tally[i][2] += len(found[1])
    lines = [f"Communications over {duration / 3600:.0f} h "
             "(time connected, mean delay, mean satellites in the path):"]
    for i, link in enumerate(links):
        up, delay, hops = tally[i]
        tail = (f"{delay / up:6.1f} ms  {hops / up:4.1f} sats" if up else "  no path")
        lines.append(f"  {link['from']} <-> {link['to']:<12}{100 * up / max(steps, 1):5.0f}%  {tail}")
    return "\n".join(lines)
