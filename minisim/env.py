"""A reinforcement-learning style wrapper: drive one side from Python code.

    env = WargameEnv("scenarios/border_patrol.json", side="blue")
    obs = env.reset(seed=1)
    while True:
        obs, reward, done, info = env.step({"Hawk1": ("move", (120, 150))})
        if done:
            break

The interface follows the familiar reset/step shape used by Gym-type
libraries but has no dependencies. `vector(obs)` gives a fixed-length list
of numbers for feeding a learning library.
"""
from . import render, scenario

WIN_BONUS = 5.0


class WargameEnv:
    def __init__(self, scenario_path, side="blue", turn_s=60.0):
        self.path = scenario_path
        self.side = side
        self.turn_s = turn_s
        self.sim = None

    # ---- core API ---------------------------------------------------------
    def reset(self, seed=None):
        self.sim = scenario.load(self.path, seed=seed, outputs=False)
        if self.side not in self.sim.sides:
            raise ValueError(f"no side {self.side!r} in {self.sim.sides}")
        self.units = [p.name for p in self.sim.platforms.values()
                      if p.side == self.side]
        self.enemies = [p.name for p in self.sim.platforms.values()
                        if p.side != self.side]
        self._losses = self.sim.losses()
        return self.observe()

    def step(self, actions=None):
        """actions: {unit name: ("move", (x, y)) | ("move", (x, y, alt_km))
        | ("alt", alt_km) | ("hold",) | None}.

        None hands the unit back to its own behaviours. Units left out of the
        dict keep whatever order they already had.
        """
        for name, order in (actions or {}).items():
            unit = self.sim.platforms[name]
            if unit.side != self.side:
                raise ValueError(f"{name} is not on side {self.side}")
            if order and order[0] == "alt":
                unit.target_alt = float(order[1])
                continue
            if order and order[0] == "move" and len(order[1]) > 2:
                unit.target_alt = float(order[1][2])
                order = ("move", tuple(order[1][:2]))
            unit.order = order
        self.sim.run(until=self.sim.time + self.turn_s)

        losses = self.sim.losses()
        own = losses[self.side] - self._losses[self.side]
        enemy = sum(n - self._losses[s] for s, n in losses.items() if s != self.side)
        self._losses = losses
        reward = float(enemy - own)
        winner = self.sim.winner()
        done = winner is not None
        if done and winner != "draw":
            reward += WIN_BONUS if winner == self.side else -WIN_BONUS
        return self.observe(), reward, done, {"winner": winner, "losses": losses}

    # ---- observations -------------------------------------------------------
    def observe(self):
        """What this side knows: its own units and its current enemy tracks."""
        sim = self.sim
        own = [{"name": p.name, "kind": p.kind, "x": p.pos[0], "y": p.pos[1],
                "z": p.pos[2], "landed": p.landed,
                "fuel": 1.0 if p.fuel is None else p.fuel / p.fuel_max,
                "hp": p.hp, "alive": p.alive, "ammo": [w.ammo for w in p.weapons]}
               for p in (sim.platforms[n] for n in self.units)]
        tracks = [{"name": t.target, "kind": t.kind, "x": t.pos[0], "y": t.pos[1],
                   "z": t.pos[2], "age": sim.time - t.time}
                  for t in render.visible_tracks(sim, self.side).values()]
        return {"time": sim.time, "own": own, "tracks": tracks}

    def vector(self, obs=None):
        """Fixed-length numeric form: per own unit [alive, x, y, z, ammo, fuel],
        then per enemy [tracked, x, y, z]. x and y are scaled to 0..1, z is
        divided by 15 km, fuel is the fraction remaining."""
        obs = obs or self.observe()
        w, h = self.sim.width, self.sim.height
        out = []
        for u in obs["own"]:
            out += [float(u["alive"]), u["x"] / w, u["y"] / h, u["z"] / 15,
                    float(sum(u["ammo"])), u["fuel"]]
        seen = {t["name"]: t for t in obs["tracks"]}
        for name in self.enemies:
            t = seen.get(name)
            out += ([1.0, t["x"] / w, t["y"] / h, t["z"] / 15] if t
                    else [0.0, 0.0, 0.0, 0.0])
        return out
