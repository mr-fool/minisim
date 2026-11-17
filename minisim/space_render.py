"""Pictures for the space domain (need matplotlib): 3D orbits around the
Earth, ground tracks, and rendezvous / proximity-operations plots."""
import math

from .space import RE, cross, dot, norm, sub, subpoint, unit

SIDE_COLORS = {"blue": "tab:blue", "red": "tab:red", "none": "0.45"}


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _color(side):
    return SIDE_COLORS.get(side, "tab:green")


def plot_orbits(sim, path):
    """Earth, every live object's orbit, and its position now."""
    import numpy as np
    plt = _plt()
    fig = plt.figure(figsize=(10, 10))
    ax = fig.add_subplot(projection="3d")
    u, v = np.meshgrid(np.linspace(0, 2 * np.pi, 40), np.linspace(0, np.pi, 20))
    ax.plot_surface(RE * np.cos(u) * np.sin(v), RE * np.sin(u) * np.sin(v),
                    RE * np.cos(v), color="lightsteelblue", alpha=0.5, linewidth=0)
    reach = RE
    for o in sim.objects.values():
        if not o.alive:
            continue
        now = o.orbit.state(sim.time)[0]
        reach = max(reach, o.orbit.a * (1 + o.orbit.e))
        if o.kind == "debris":
            ax.scatter(*[[c] for c in now], color="0.3", s=5)
            continue
        pts = [o.orbit.state(sim.time + o.orbit.period * k / 120)[0] for k in range(121)]
        ax.plot(*zip(*pts), color=_color(o.side), lw=0.8, alpha=0.6)
        ax.scatter(*[[c] for c in now], color=_color(o.side), s=22)
        ax.text(now[0], now[1], now[2], " " + o.name, fontsize=6)
    for axis in (ax.set_xlim, ax.set_ylim, ax.set_zlim):
        axis(-reach, reach)
    ax.set_box_aspect((1, 1, 1))
    ax.set_axis_off()
    ax.set_title(f"{sim.name}  t={sim.time:.0f}s  "
                 "(blue / red sides, grey = unowned, dots = debris)")
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def plot_groundtrack(sim, path):
    """Ground tracks over the run, with stations and target areas."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(12, 6))
    names = [n for n, o in sim.objects.items() if o.kind != "debris"]
    for name in names:
        side = sim.objects[name].side
        lats, lons = [], []
        for t, states in sim.history:
            if name not in states:
                continue
            lat, lon = subpoint(states[name][0], t)
            if lons and abs(lon - lons[-1]) > 180:      # wrapped round the map edge
                ax.plot(lons, lats, color=_color(side), lw=0.6, alpha=0.6)
                lats, lons = [], []
            lats.append(lat)
            lons.append(lon)
        ax.plot(lons, lats, color=_color(side), lw=0.6, alpha=0.6)
        if lons:
            ax.plot(lons[-1], lats[-1], "o", color=_color(side), ms=4)
            ax.annotate(name, (lons[-1], lats[-1]), fontsize=6, xytext=(3, 3),
                        textcoords="offset points")
    for st in sim.stations.values():
        ax.plot(st.lon, st.lat, "^", color=_color(st.side), ms=9, mec="black")
        ax.annotate(st.name, (st.lon, st.lat), fontsize=7, xytext=(5, -9),
                    textcoords="offset points")
    for tgt in sim.targets:
        ax.plot(tgt["lon"], tgt["lat"], "*", color="goldenrod", ms=13, mec="black")
        ax.annotate(tgt["name"], (tgt["lon"], tgt["lat"]), fontsize=7,
                    xytext=(6, 5), textcoords="offset points")
    ax.set_xlim(-180, 180)
    ax.set_ylim(-90, 90)
    ax.set_xticks(range(-180, 181, 30))
    ax.set_yticks(range(-90, 91, 30))
    ax.grid(alpha=0.3)
    ax.set_xlabel("longitude (no coastlines are drawn)")
    ax.set_ylabel("latitude")
    ax.set_title(f"{sim.name}: ground tracks (triangles = stations, stars = targets)")
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def plot_rpo(sim, path):
    """For each rendezvous: range against time, and the chaser's path in the
    target's own frame (in-track against radial)."""
    pairs = [(o.name, o.rpo_target) for o in sim.objects.values() if o.rpo_target]
    if not pairs:
        return False
    plt = _plt()
    fig, axes = plt.subplots(len(pairs), 2, figsize=(12, 4.5 * len(pairs)),
                             squeeze=False)
    for row, (chaser, target) in zip(axes, pairs):
        ts, rng, intrack, radial = [], [], [], []
        for t, states in sim.history:
            if chaser not in states or target not in states:
                continue
            (rc, _), (rt, vt) = states[chaser], states[target]
            rel = sub(rc, rt)
            rhat = unit(rt)
            ihat = cross(unit(cross(rt, vt)), rhat)
            ts.append(t / 60)
            rng.append(norm(rel))
            radial.append(dot(rel, rhat))
            intrack.append(dot(rel, ihat))
        row[0].semilogy(ts, rng, color="tab:red")
        row[0].set_xlabel("time (min)")
        row[0].set_ylabel("range (km)")
        row[0].set_title(f"{chaser} range to {target}")
        row[0].grid(alpha=0.3, which="both")
        close = [k for k, d in enumerate(rng) if d < 400]
        if close:
            row[1].plot([intrack[k] for k in close], [radial[k] for k in close],
                        color="tab:red", lw=1)
            row[1].plot(intrack[close[-1]], radial[close[-1]], "o", color="tab:red")
        row[1].plot(0, 0, "s", color="tab:blue", ms=8)
        row[1].annotate(target, (0, 0), fontsize=8, xytext=(6, 6),
                        textcoords="offset points")
        row[1].set_xlabel("in-track (km, + = ahead of target)")
        row[1].set_ylabel("radial (km, + = above)")
        row[1].set_title(f"{chaser} approach in {target}'s frame (last 400 km)")
        row[1].grid(alpha=0.3)
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return True
