"""Output adaptors: observers that pass simulation data to other tools.

An adaptor is any object with on_start / on_step / on_event / on_end.
Built in:
  events_csv     every logged event as a CSV row
  positions_csv  every platform's position every `every` seconds
  jsonl          full state snapshots, one JSON object per line (replay file)
  udp_json       the same snapshots sent as UDP datagrams to host:port
  replay         one JSON file of the whole run for the replay viewer

udp_json is a simple stand-in for network interoperability. It is not DIS;
a DIS adaptor would follow the same pattern with real PDU encoding.

Plug-ins add their own with  minisim.adaptors.OBSERVERS["name"] = Class.
"""
import csv
import json
import socket


class Observer:
    def __init__(self, target=None, every=10.0, **options):
        self.target = target
        self.every = float(every)
        self._next = 0.0

    def due(self, sim):
        if sim.time >= self._next:
            self._next = sim.time + self.every
            return True
        return False

    def on_start(self, sim): pass
    def on_step(self, sim): pass
    def on_event(self, sim, event): pass
    def on_end(self, sim): pass


def snapshot(sim):
    """A plain-data picture of the whole simulation right now."""
    return {
        "time": sim.time,
        "platforms": [
            {"name": p.name, "side": p.side, "kind": p.kind, "domain": p.domain,
             "x": round(p.pos[0], 3), "y": round(p.pos[1], 3),
             "z": round(p.pos[2], 3), "fuel": p.fuel, "landed": p.landed,
             "heading": round(p.heading, 1), "hp": p.hp, "alive": p.alive,
             "ammo": [w.ammo for w in p.weapons], "activity": p.activity}
            for p in sim.platforms.values()],
    }


class _FileObserver(Observer):
    def on_start(self, sim):
        self.file = open(self.target, "w", newline="")

    def on_end(self, sim):
        if getattr(self, "file", None):
            self.file.close()


class EventsCsv(_FileObserver):
    def on_start(self, sim):
        super().on_start(sim)
        self.writer = csv.writer(self.file)
        self.writer.writerow(["time", "kind", "text", "sides"])

    def on_event(self, sim, event):
        if not hasattr(self, "writer"):      # event before the first step
            self.on_start(sim)
        t, kind, text, sides = event
        self.writer.writerow([t, kind, text, " ".join(sorted(sides))])


class PositionsCsv(_FileObserver):
    def on_start(self, sim):
        super().on_start(sim)
        self.writer = csv.writer(self.file)
        self.writer.writerow(["time", "name", "side", "x", "y", "z", "hp", "alive"])

    def on_step(self, sim):
        if self.due(sim):
            for p in sim.platforms.values():
                self.writer.writerow([sim.time, p.name, p.side, round(p.pos[0], 3),
                                      round(p.pos[1], 3), round(p.pos[2], 3),
                                      p.hp, int(p.alive)])


class Jsonl(_FileObserver):
    def on_step(self, sim):
        if self.due(sim):
            self.file.write(json.dumps(snapshot(sim)) + "\n")

    def on_end(self, sim):
        self.file.write(json.dumps(snapshot(sim)) + "\n")
        super().on_end(sim)


class UdpJson(Observer):
    """Send snapshots to host:port. target looks like '127.0.0.1:9999'."""

    def on_start(self, sim):
        host, _, port = self.target.rpartition(":")
        self.addr = (host or "127.0.0.1", int(port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def on_step(self, sim):
        if self.due(sim):
            self.sock.sendto(json.dumps(snapshot(sim)).encode(), self.addr)

    def on_end(self, sim):
        self.sock.sendto(json.dumps({"time": sim.time, "end": True,
                                     "winner": sim.winner()}).encode(), self.addr)
        self.sock.close()


class Replay(Observer):
    """Record a whole run for the replay viewer (viewer.html): positions every
    `every` seconds, who detected whom, shots, events, terrain and any
    satellites overhead. Writes one JSON file at the end; with no target the
    result is just kept in `.data`."""

    def on_start(self, sim):
        self.names = list(sim.platforms)
        self.index = {n: i for i, n in enumerate(self.names)}
        self.frames, self.seen = [], set()
        self.overhead = next((o for o in sim.observers if hasattr(o, "sats")), None)
        self.sat_names = [s["name"] for s in self.overhead.sats] if self.overhead else []
        self._frame(sim)
        self._next = self.every

    def _frame(self, sim):
        rows = []
        for p in sim.platforms.values():
            fuel = -1 if p.fuel is None else round(p.fuel / p.fuel_max, 3)
            rows.append([round(p.pos[0], 2), round(p.pos[1], 2), round(p.pos[2], 3),
                         round(p.heading, 1), int(p.alive), int(p.landed),
                         round(p.hp, 2), fuel])
        air, sat = [], []
        for observer, target in sorted(self.seen):
            if observer.startswith("sat:"):
                sat.append([self.sat_names.index(observer[4:]), self.index[target]])
            else:
                air.append([self.index[observer], self.index[target]])
        self.frames.append({"t": round(sim.time, 2), "p": rows, "d": air, "s": sat})
        self.seen = set()

    def on_step(self, sim):
        self.seen.update(sim.detections)
        if sim.time >= self._next:
            self._next = sim.time + self.every
            self._frame(sim)

    def on_end(self, sim):
        self.seen.update(sim.detections)
        if not self.frames or self.frames[-1]["t"] < sim.time:
            self._frame(sim)
        space = None
        if self.overhead:
            space = {"origin": {"lat": self.overhead.lat0, "lon": self.overhead.lon0},
                     "satellites": [
                         {"name": s["name"], "side": s["side"], "min_elev": s["min_elev"],
                          "orbit": {k: getattr(s["orbit"], k) for k in
                                    ("a", "e", "inc", "raan", "argp", "m0", "epoch")}}
                         for s in self.overhead.sats]}
        self.data = {
            "format": "minisim-replay-1", "name": sim.name,
            "width": sim.width, "height": sim.height, "end": round(sim.time, 2),
            "winner": sim.winner(), "losses": sim.losses(), "sides": sim.sides,
            "terrain": {"hills": [{"x": x, "y": y, "height_km": h, "radius_km": r}
                                  for x, y, h, r in sim.terrain.hills]},
            "platforms": [{"name": p.name, "side": p.side, "kind": p.kind,
                           "icon": p.icon, "domain": p.domain, "comms_km": p.comms_km,
                           "mobile": p.max_speed > 0}
                          for p in sim.platforms.values()],
            "frames": self.frames,
            "events": [[round(t, 1), kind, text] for t, kind, text, _ in sim.events],
            "shots": [[round(s["t0"], 1), round(s["t1"], 1), self.index[s["from"]],
                       self.index[s["to"]], s["result"]] for s in sim.shots],
            "space": space,
        }
        if self.target:
            with open(self.target, "w") as f:
                json.dump(self.data, f, separators=(",", ":"))


OBSERVERS = {"replay": Replay, "events_csv": EventsCsv, "positions_csv": PositionsCsv,
             "jsonl": Jsonl, "udp_json": UdpJson}


def make(kind, **options):
    if kind not in OBSERVERS:
        raise ValueError(f"unknown output type {kind!r}; known: {sorted(OBSERVERS)}")
    return OBSERVERS[kind](**options)
