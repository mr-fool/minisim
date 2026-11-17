"""Command line entry point:  python -m minisim SCENARIO [options]"""
import argparse
import json
from collections import Counter

from . import adaptors, plugins, render, scenario

HELP = """Commands (unit names are not case sensitive):
  move UNIT X Y [ALT]  send a unit to map position X,Y (km), optionally
                       changing altitude (km) on the way
  alt UNIT ALT    climb or descend to ALT km
  hold UNIT       stop and stay put
  free UNIT       cancel orders; the unit goes back to acting on its own
  fire on|off     allow / forbid all your units to shoot
  help            show this list
  quit            end the game
  (press Enter on an empty line to run the next turn)"""


def watch(sim, every):
    next_print, seen = 0.0, 0
    while sim.winner() is None:
        if sim.time >= next_print:
            print(render.ascii_map(sim))
            next_print += every
        sim.step()
        if len(sim.events) > seen:
            print(render.format_events(sim.events[seen:]))
            seen = len(sim.events)
    print(render.ascii_map(sim))


def monte_carlo(path, runs, seed):
    wins, losses = Counter(), Counter()
    for i in range(runs):
        sim = scenario.load(path, seed=seed + i, outputs=False)
        wins[sim.run()] += 1
        losses.update(sim.losses())
    print(f"{runs} runs of {sim.name}")
    for outcome, n in wins.most_common():
        print(f"  {outcome:<8} wins {n:4d}  ({100 * n / runs:.0f}%)")
    for side in sim.sides:
        print(f"  mean {side} losses: {losses[side] / runs:.2f}")


def play(sim, side, turn_s):
    if side not in sim.sides:
        raise SystemExit(f"no side called {side!r}; choose from {sim.sides}")
    mine = {p.name.lower(): p for p in sim.platforms.values() if p.side == side}
    print(HELP)
    seen = 0
    while sim.winner() is None:
        print()
        print(render.ascii_map(sim, viewer=side))
        news = [e for e in sim.events[seen:] if side in e[3]]
        seen = len(sim.events)
        if news:
            print(render.format_events(news))
        print(render.status(sim, side))
        while True:
            try:
                parts = input(f"[{side} t={sim.time:.0f}s] > ").split()
            except EOFError:
                return
            if not parts:
                break
            cmd = parts[0].lower()
            try:
                if cmd == "quit":
                    return
                elif cmd == "help":
                    print(HELP)
                elif cmd == "fire":
                    for p in mine.values():
                        p.weapons_free = parts[1].lower() == "on"
                    print("weapons", "free" if parts[1].lower() == "on" else "tight")
                elif cmd == "alt":
                    mine[parts[1].lower()].target_alt = float(parts[2])
                    print("ok")
                elif cmd in ("move", "hold", "free"):
                    unit = mine[parts[1].lower()]
                    if cmd == "move":
                        unit.order = ("move", (float(parts[2]), float(parts[3])))
                        if len(parts) > 4:
                            unit.target_alt = float(parts[4])
                    elif cmd == "hold":
                        unit.order = ("hold",)
                    else:
                        unit.order = None
                    print("ok")
                else:
                    print("unknown command; type help")
            except (KeyError, IndexError, ValueError):
                print("could not read that; type help")
        sim.run(until=sim.time + turn_s)
    print()
    print(render.ascii_map(sim))
    print(render.format_events([e for e in sim.events[seen:] if side in e[3]]))


def space_main(args):
    from . import space, space_render
    sim = space.load_space(args.scenario, seed=args.seed)
    if args.trade:
        if "trade" not in sim.spec:
            raise SystemExit("this scenario has no 'trade' section")
        print("\n".join(space.trade_study(sim.spec["trade"], sim.targets)))
        return
    print(space.report(sim.run()))
    for flag, fn in ((args.plot, space_render.plot_orbits),
                     (args.map, space_render.plot_groundtrack),
                     (args.rpo, space_render.plot_rpo)):
        if flag:
            print(f"saved {flag}" if fn(sim, flag) is not False
                  else f"nothing to draw for {flag}")
    for flag in ("play", "runs", "watch", "gif", "output"):
        if getattr(args, flag):
            print(f"note: --{flag} is not available for space scenarios")


def main():
    ap = argparse.ArgumentParser(prog="minisim", description=__doc__)
    ap.add_argument("scenario", help="path to a scenario JSON file")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--play", metavar="SIDE", help="command one side yourself")
    ap.add_argument("--turn", type=float, default=120,
                    help="seconds of simulated time per player turn (default 120)")
    ap.add_argument("--watch", type=float, metavar="SECONDS",
                    help="print the map every SECONDS of simulated time")
    ap.add_argument("--runs", type=int, help="run many times and report win rates")
    ap.add_argument("--plugin", action="append", default=[], metavar="FILE.py",
                    help="load a plug-in (repeatable)")
    ap.add_argument("--output", action="append", default=[], metavar="TYPE:TARGET",
                    help="attach an output adaptor, e.g. events_csv:events.csv "
                         "or udp_json:127.0.0.1:9999 (repeatable)")
    ap.add_argument("--replay", metavar="FILE.json",
                    help="record the run for the replay viewer (viewer.html)")
    ap.add_argument("--plot", metavar="FILE.png", help="save a 3D picture of all paths")
    ap.add_argument("--map", metavar="FILE.png",
                    help="save a top-down map with unit icons")
    ap.add_argument("--rpo", metavar="FILE.png",
                    help="space scenarios: save rendezvous / proximity plots")
    ap.add_argument("--trade", action="store_true",
                    help="space scenarios: run the constellation trade study")
    ap.add_argument("--gif", metavar="FILE.gif",
                    help="save an animated replay with unit icons")
    args = ap.parse_args()

    for path in args.plugin:
        plugins.load_plugin(path)
    with open(args.scenario) as f:
        if json.load(f).get("domain") == "space":
            return space_main(args)
    if args.runs:
        return monte_carlo(args.scenario, args.runs, args.seed)

    sim = scenario.load(args.scenario, seed=args.seed)
    if args.replay:
        args.output.append("replay:" + args.replay)
    for spec in args.output:
        kind, _, target = spec.partition(":")
        sim.observers.append(adaptors.make(kind, target=target))
    if args.play:
        play(sim, args.play, args.turn)
    elif args.watch:
        watch(sim, args.watch)
    else:
        sim.run()
        print(render.format_events(sim.events))
    sim.finish()
    result = sim.winner()
    print(f"\nResult at t={sim.time:.0f}s: {result or 'unfinished'}   "
          f"losses: {sim.losses()}")
    if args.plot:
        render.plot(sim, args.plot)
        print(f"saved {args.plot}")
    if args.map:
        render.map_picture(sim, args.map)
        print(f"saved {args.map}")
    if args.gif:
        render.gif(sim, args.gif)
        print(f"saved {args.gif}")


if __name__ == "__main__":
    main()
