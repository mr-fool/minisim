"""Text map, status table, and optional matplotlib pictures: a 3D track
plot, a top-down map with unit icons, and an animated GIF replay."""
import math

from . import icons

COLS, ROWS = 72, 24


def visible_tracks(sim, side):
    """Newest track per hostile target held by any living platform on `side`."""
    best = {}
    for p in sim.platforms.values():
        if p.side != side or not p.alive:
            continue
        for t in p.hostile_tracks(sim):
            if t.target not in best or best[t.target].time < t.time:
                best[t.target] = t
    return best


def ascii_map(sim, viewer=None):
    """Draw the world. With `viewer` set, enemies appear only where tracked."""
    def ground(r, c):
        h = sim.terrain.height((c + 0.5) / COLS * sim.width,
                               (1 - (r + 0.5) / ROWS) * sim.height)
        if h > 1.0:
            return "^"
        if h > 0.4:
            return ":"
        return "." if (r % 4 == 0 and c % 8 == 0) else " "

    grid = [[ground(r, c) for c in range(COLS)] for r in range(ROWS)]
    first = sim.sides[0]

    def put(pos, ch):
        c = min(COLS - 1, int(pos[0] / sim.width * COLS))
        r = min(ROWS - 1, int((1 - pos[1] / sim.height) * ROWS))
        grid[r][c] = ch

    def glyph(p_side, symbol):
        return symbol.upper() if p_side == first else symbol.lower()

    for p in sim.platforms.values():
        if not p.alive:
            continue
        if viewer is None or p.side == viewer:
            put(p.pos, glyph(p.side, p.symbol))
    if viewer is not None:
        for t in visible_tracks(sim, viewer).values():
            put(t.pos, glyph(t.side, sim.platforms[t.target].symbol))
    border = "+" + "-" * COLS + "+"
    lines = [border] + ["|" + "".join(row) + "|" for row in grid] + [border]
    lines.append(f"t={sim.time:.0f}s   {first.upper()} = capitals, others lower case"
                 f"   map {sim.width}x{sim.height} km   : ^ = hills")
    return "\n".join(lines)


def status(sim, side):
    rows = [f"{'unit':<10}{'type':<10}{'pos':<10}{'alt km':<8}{'fuel':<6}"
            f"{'hp':<7}{'ammo':<8}doing"]
    for p in sim.platforms.values():
        if p.side != side:
            continue
        if not p.alive:
            rows.append(f"{p.name:<10}{p.kind:<10}destroyed")
            continue
        ammo = "/".join(str(w.ammo) for w in p.weapons) or "-"
        pos = f"{p.pos[0]:.0f},{p.pos[1]:.0f}"
        fuel = "-" if p.fuel is None else f"{100 * p.fuel / p.fuel_max:.0f}%"
        rows.append(f"{p.name:<10}{p.kind:<10}{pos:<10}{p.pos[2]:<8.1f}{fuel:<6}"
                    f"{p.hp:g}/{p.max_hp:<5g}{ammo:<8}{p.activity}")
    return "\n".join(rows)


def format_events(events):
    return "\n".join(f"  [{t:6.0f}s] {kind:<6} {text}" for t, kind, text, _ in events)


def plot(sim, path):
    """Save a 3D picture of the terrain and every platform's path.
    Needs matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    colors = dict(zip(sim.sides, ["tab:blue", "tab:red", "tab:green", "tab:orange"]))
    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(projection="3d")
    top = max(max(pos[2] for pos in h) for h in sim.history.values())
    if sim.terrain.hills:
        xs = np.linspace(0, sim.width, 70)
        ys = np.linspace(0, sim.height, 50)
        gx, gy = np.meshgrid(xs, ys)
        gz = np.vectorize(sim.terrain.height)(gx, gy)
        ax.plot_surface(gx, gy, gz, cmap="summer", alpha=0.55, linewidth=0,
                        vmin=0, vmax=max(2.5, float(gz.max())))
        top = max(top, float(gz.max()))
    for p in sim.platforms.values():
        xs, ys, zs = zip(*sim.history[p.name])
        c = colors[p.side]
        ax.plot(xs, ys, zs, color=c, lw=1, alpha=0.7)
        ax.scatter([xs[-1]], [ys[-1]], [zs[-1]], color=c, s=30,
                   marker="o" if p.alive else "x")
        ax.text(xs[-1], ys[-1], zs[-1] + 0.3, p.name, fontsize=7)
    ax.set_xlim(0, sim.width)
    ax.set_ylim(0, sim.height)
    ax.set_zlim(0, max(top * 1.1, 1))
    ax.set_box_aspect((sim.width, sim.height, 0.3 * sim.height))
    ax.set_xlabel("x km")
    ax.set_ylabel("y km")
    ax.set_zlabel("altitude km")
    ax.view_init(elev=28, azim=-62)
    ax.set_title(f"{sim.name}  (o = alive, x = destroyed)  t={sim.time:.0f}s")
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


# ---- top-down map picture and animated replay, with unit icons ---------------

def _heading_at(points, i):
    """Direction of travel at history index i, from the last real movement."""
    for k in range(min(i, len(points) - 1), 0, -1):
        a, b = points[k - 1], points[k]
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) > 1e-9:
            return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    return 0.0


def _map_axes(sim):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(11, 11 * sim.height / sim.width + 0.6))
    if sim.terrain.hills:
        xs = np.linspace(0, sim.width, 150)
        ys = np.linspace(0, sim.height, 100)
        gx, gy = np.meshgrid(xs, ys)
        gz = np.vectorize(sim.terrain.height)(gx, gy)
        ax.contourf(gx, gy, gz, levels=[0.2, 0.5, 0.8, 1.1, 1.4, 1.7, 5],
                    cmap="YlOrBr", alpha=0.45, vmin=-0.5, vmax=3)
    ax.set_xlim(0, sim.width)
    ax.set_ylim(0, sim.height)
    ax.set_aspect("equal")
    ax.set_xlabel("x km")
    ax.set_ylabel("y km")
    ax.grid(alpha=0.25)
    return fig, ax, plt


def _draw_units(ax, sim, index=None, trail=150):
    """Draw every unit as its icon at history index `index` (None = now).
    Returns the artists so an animation can remove them again."""
    colors = dict(zip(sim.sides, ["tab:blue", "tab:red", "tab:green", "tab:orange"]))
    artists = []
    for p in sim.platforms.values():
        pts = sim.history[p.name]
        i = len(pts) - 1 if index is None else min(index, len(pts) - 1)
        dead = (not p.alive) and i == len(pts) - 1
        x, y = pts[i][0], pts[i][1]
        c = colors[p.side]
        if p.max_speed > 0 and i > 0:
            seg = pts[max(0, i - trail):i + 1]
            artists += ax.plot([q[0] for q in seg], [q[1] for q in seg],
                               color=c, lw=0.8, alpha=0.35)
        if dead:
            artists.append(ax.scatter([x], [y], marker="x", s=45, color=c, alpha=0.45))
        else:
            artists.append(ax.scatter(
                [x], [y], marker=icons.marker(p.icon, _heading_at(pts, i)),
                s=420, color=c, edgecolors="black", linewidths=0.4, zorder=3))
            artists.append(ax.annotate(p.name, (x, y), fontsize=7, xytext=(7, 7),
                                       textcoords="offset points", zorder=4))
    return artists


def map_picture(sim, path):
    """Save a top-down map with unit icons. Needs matplotlib."""
    fig, ax, plt = _map_axes(sim)
    _draw_units(ax, sim)
    ax.set_title(f"{sim.name}  t={sim.time:.0f}s  (x = destroyed)")
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def gif(sim, path, every_s=20.0, fps=12):
    """Save an animated replay of the run. Needs matplotlib and pillow."""
    from matplotlib.animation import FuncAnimation, PillowWriter

    fig, ax, plt = _map_axes(sim)
    stride = max(1, int(round(every_s / sim.dt)))
    last = max(len(h) for h in sim.history.values()) - 1
    frames = list(range(0, last, stride)) + [last] * (fps * 2)   # hold the ending
    state = {"artists": []}

    def draw(i):
        for a in state["artists"]:
            a.remove()
        state["artists"] = _draw_units(ax, sim, index=i)
        ax.set_title(f"{sim.name}  t={i * sim.dt:.0f}s  (x = destroyed)")

    FuncAnimation(fig, draw, frames=frames).save(path, writer=PillowWriter(fps=fps),
                                                 dpi=70)
    plt.close(fig)
