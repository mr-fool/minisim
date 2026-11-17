"""Drive blue from Python with a random policy. Swap `policy` for your own
(or for a learning library) to do "minisim in Python" experiments.

Run from the project folder:   python examples/random_agent.py
"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from minisim.env import WargameEnv  # noqa: E402


def policy(obs, env, rng):
    """Each turn, send every living armed unit to a random point on blue's
    half of the map about one time in five."""
    actions = {}
    for unit in obs["own"]:
        if unit["alive"] and unit["ammo"] and rng.random() < 0.2:
            actions[unit["name"]] = ("move", (rng.uniform(20, env.sim.width / 2),
                                               rng.uniform(10, env.sim.height - 10)))
    return actions


def main(episodes=20):
    env = WargameEnv(os.path.join(ROOT, "scenarios", "border_patrol.json"), side="blue")
    rng = random.Random(0)
    totals, wins = [], 0
    for episode in range(episodes):
        obs, total, done = env.reset(seed=episode), 0.0, False
        while not done:
            obs, reward, done, info = env.step(policy(obs, env, rng))
            total += reward
        totals.append(total)
        wins += info["winner"] == "blue"
        print(f"episode {episode:2d}: reward {total:+5.1f}  winner {info['winner']}")
    print(f"\nmean reward {sum(totals) / len(totals):+.2f}, blue won {wins}/{episodes}")
    print(f"observation vector length: {len(env.vector())}")


if __name__ == "__main__":
    main()
