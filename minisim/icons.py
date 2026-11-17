"""Unit icons: simple top-down silhouettes for the map picture and GIF.

Each icon is one or more polygons in a box from -1 to 1. Aircraft point
along +x and are rotated to their heading; ground icons stay upright.
A template picks one with  "icon": "drone".  Add your own with
    minisim.icons.ICONS["name"] = ([polygon, ...], rotates)
where a polygon is a list of (x, y) points.
"""
import math


def _mirror(half):
    """Build a symmetric outline from its upper half (nose to tail)."""
    return half + [(x, -y) for x, y in reversed(half)]


def _arc(cx, cy, r, a0, a1, n=10):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)))
            for i in range(n + 1)]


ICONS = {
    # swept-wing jet
    "fighter": ([_mirror([(1.0, 0), (0.25, 0.14), (-0.3, 0.75), (-0.55, 0.75),
                          (-0.35, 0.14), (-0.8, 0.12), (-1.0, 0.42), (-1.0, 0)])], True),
    # delta-wing one-way attack drone with wingtip fins
    "drone": ([_mirror([(1.0, 0), (0.5, 0.12), (-0.55, 0.9), (-0.6, 1.0),
                        (-0.95, 1.0), (-0.9, 0.9), (-0.85, 0.12), (-1.0, 0.1),
                        (-1.0, 0)])], True),
    # long straight wings
    "scout": ([_mirror([(1.0, 0), (0.85, 0.1), (0.2, 0.1), (0.1, 1.0), (-0.15, 1.0),
                        (-0.1, 0.1), (-0.8, 0.08), (-0.9, 0.4), (-1.0, 0.4),
                        (-1.0, 0)]),
               _arc(-0.05, 0, 0.3, 0, 360, 16)], True),
    # swept wings with pods at the tips
    "jammer": ([_mirror([(1.0, 0), (0.7, 0.12), (0.15, 0.12), (-0.25, 0.8),
                         (0.05, 0.85), (0.05, 1.0), (-0.6, 1.0), (-0.6, 0.85),
                         (-0.3, 0.12), (-0.85, 0.1), (-1.0, 0.38), (-1.0, 0)])], True),
    # mast with a dish
    "radar": ([[(-0.55, -1.0), (0.55, -1.0), (0.1, -0.05), (-0.1, -0.05)],
               _arc(0, 0.25, 0.75, 200, 380, 12) + [(0.12, 0.42), (0.5, 0.95),
                                                    (0.38, 1.0), (0.0, 0.5)]], False),
    # vehicle with raised launch rails
    "launcher": ([[(-1.0, -0.75), (1.0, -0.75), (1.0, -0.25), (-1.0, -0.25)],
                  [(-0.85, -0.2), (-0.55, -0.2), (0.95, 0.75), (0.75, 1.0)],
                  _arc(-0.6, -0.8, 0.22, 0, 360, 10),
                  _arc(0.6, -0.8, 0.22, 0, 360, 10)], False),
    "default": ([[(1.0, 0), (0, 0.8), (-1.0, 0), (0, -0.8)]], False),
}


def marker(name, heading_deg=0.0):
    """Return a matplotlib Path for the icon, rotated to the heading."""
    from matplotlib.path import Path
    from matplotlib.transforms import Affine2D

    polygons, rotates = ICONS.get(name, ICONS["default"])
    verts, codes = [], []
    for poly in polygons:
        verts += list(poly) + [poly[0]]
        codes += [Path.MOVETO] + [Path.LINETO] * (len(poly) - 1) + [Path.CLOSEPOLY]
    path = Path(verts, codes)
    if rotates:
        path = path.transformed(Affine2D().rotate_deg(heading_deg))
    return path
