# minisim

A small mission-level simulation and wargame framework in Python, in the
style of tools like AFSIM but far simpler. It covers ground, air and space. Platforms move in 3D over hilly
terrain, burn fuel, scan with sensors that hills, the horizon and jammers can
defeat, share tracks over comms, follow behaviour rules and shoot. Scenarios are JSON
files. No dependencies (matplotlib only for `--plot`). Python 3.9+.

Every number in the bundled scenario is invented for gameplay and does not
describe any real system.

## Run it

    python -m minisim scenarios/border_patrol.json                # run, print event log
    python -m minisim scenarios/border_patrol.json --watch 120    # print the map as it runs
    python -m minisim scenarios/border_patrol.json --play blue    # command blue yourself
    python -m minisim scenarios/border_patrol.json --runs 300     # win rates over many runs
    python -m minisim scenarios/border_patrol.json --plot out.png # 3D picture of terrain and paths

    python -m minisim scenarios/border_patrol.json --map map.png  # top-down map with unit icons
    python -m minisim scenarios/border_patrol.json --gif run.gif  # animated replay with icons

`--map` and `--gif` need matplotlib (and pillow for the GIF). `--seed N` changes the dice. The same seed always gives the same battle.

## Playing

In `--play` mode you see your own units and only the enemies your side is
currently tracking. Each turn is 120 simulated seconds (`--turn` changes it).
Units act on their own unless you give an order:

    move hawk1 120 150    go to x=120, y=150 (km)
    move hawk1 120 150 1  the same, descending to 1 km on the way
    alt owl 12            climb or descend to 12 km
    hold owl              stop and stay
    free hawk1            cancel the order
    fire off / fire on    hold or release weapons for all your units
    (empty line)          run the next turn

With both sides on autopilot, red (the defender) wins about half the time and
blue about a third, so blue needs your help.

## The 3D world

Positions are x, y and altitude, all in km. The text map is a top-down view
(`:` and `^` mark hills); the status table shows each unit's altitude and fuel.

- **Altitude:** aircraft climb and descend at a limited rate and never go
  below 150 m above the ground. Ground units sit on the terrain.
- **Terrain:** hills listed in the scenario file block line of sight. A
  simple radar-horizon rule also hides low targets at long range, so flying
  low behind a ridge keeps you unseen, and a sensor placed high sees further.
- **Fuel:** flying, loitering and especially climbing burn fuel. A unit that
  can only just get home turns back and lands. Units with a `turnaround_s`
  refuel, rearm and take off again; an aircraft that runs dry is lost.
- **Jamming:** a unit with a jammer shrinks the detection range of enemy
  sensors near it, more strongly the closer it is. Blue's `Crow` carries one.

## How it works

| File | What it holds |
|---|---|
| `minisim/sim.py` | `Simulation` loop, `Platform`, `Sensor`, `Weapon`, `Jammer`, `Track` |
| `minisim/terrain.py` | Hills and ground height |
| `minisim/icons.py` | Unit silhouettes for the map picture and GIF |
| `minisim/space.py` | Orbits, manoeuvres, space surveillance, conjunctions, debris |
| `minisim/space_render.py` | Orbit, ground-track and rendezvous pictures |
| `minisim/overhead.py` | Satellites passing over a surface scenario |
| `minisim/gui.py`, `gui.html` | minisim Studio: browser interface and its local server |
| `minisim/viewer.html` | minisim Replay: 3D playback of air and ground runs |
| `minisim/behaviors.py` | Engage, Orders, Retreat, Pursue, Patrol, OneWayAttack, Hold |
| `minisim/scenario.py` | JSON scenario loader |
| `minisim/render.py` | Text map, status table, 3D plot, icon map, GIF |
| `minisim/__main__.py` | Command line and the play loop |
| `minisim/plugins.py` | Plug-in loader |
| `minisim/adaptors.py` | Output adaptors (CSV, JSON lines, UDP) |
| `minisim/env.py` | `WargameEnv`, the reset/step interface for agents |
| `plugins/escort.py` | Example plug-in |
| `examples/random_agent.py` | Example agent driving blue from Python |

Each step (`dt` seconds) every living platform: scans (range, field of view,
line of sight, jamming), shares tracks with
friends in comms range, runs its behaviour list, moves. Shots are scheduled
events that resolve after their time of flight with a hit probability.

A side wins when the other has no armed platforms left. At the time limit the
side with fewer losses wins.

## Icons and the attack drone

The map picture and GIF draw each unit as a silhouette: `fighter`, `drone`
(delta wing), `scout`, `jammer`, `radar`, `launcher`. A template chooses one
with `"icon": "drone"`; by default the icon matches the unit's `kind`. Add
your own shape in `minisim/icons.py` or from a plug-in via
`minisim.icons.ICONS`. The text map still uses letters (`d` for drones).

The `drone` template is a slow, low-flying, hard-to-see one-way attack drone.
Its `one_way_attack` behaviour flies its waypoints in order, steers to a
tracked enemy ground unit within 30 km, and is used up when it arrives.
Drones are marked `"expendable": true`, so they do not count as losses. Blue
has four (`Wasp1` to `Wasp4`) aimed at red's launchers and radar.

## minisim Studio (the interface)

    python -m minisim.gui        # opens http://127.0.0.1:8765 in your browser

A workbench for space scenarios: a globe you can rotate and zoom, with orbits
and moving satellites; a constellation maker (planes, satellites per plane,
altitude or revs per day, inclination, RAAN and anomaly spacing) that
previews as you type; the scenario file as editable text that stays in step
with the globe; scenario and platform lists; and an output pane. **Run
coverage** sends the scenario to the Python engine and prints its report.
**Download** saves the scenario file, which also runs from the command line.

**Satellite and mission planner:** New satellite (or click one in the list)
opens a panel for its starting orbit, fuel budget and a mission sequence. Each
step waits for a point on the orbit (now, periapsis, apoapsis, a node) and
then burns: set the far apsis, circularize, set inclination, or a plain
prograde burn. Every step is verified as you type (PASS or FAIL, delta-V,
time, resulting orbit) and its orbit is drawn on the globe in the step's
colour. The demo's `Upper-stage` goes from a 200 km parking orbit to
geostationary orbit in three burns. The same steps work in scenario files
under an object's `"mission"` key and are flown by the Python engine.

A `rendezvous` step sends the satellite to a standoff point behind another
satellite in a chosen transfer time (two burns). The demo's `Chaser` closes on
`Target` this way. The target is assumed not to manoeuvre.

**Shape and visibility:** the planner also takes a body size, solar panel
area and antenna area, shows a rotating outline, and derives the satellite's
visibility (the `signature` the engine's sensors use) from the average area it
presents. In a scenario file this is an object's `"shape"` key.

**Communication links:** give a constellation a crosslink range, then use New
link to join two ground sites. The globe draws the current relay path in
white and the list shows its satellites and delay; Run reports the share of
time connected, mean delay and mean satellites in the path over 24 hours
(`"links"` and `"crosslink_km"` in a scenario file).

**Relative geometry:** pick two satellites to see radial, in-track and
cross-track separation and range, live, with the last hour's path of one in
the other's frame.

The page is `minisim/gui.html`. Its coastlines and fonts load from public
CDNs, so the globe is plain without an internet connection. It covers space
scenarios only. The air and ground game is played in the terminal and watched
in minisim Replay.

## minisim Replay (results viewer)

Plays back an air and ground run in 3D. With `python -m minisim.gui` running,
open http://127.0.0.1:8765/viewer, pick a scenario and seed, and press **Run
and replay**. Or record a run from the command line and open the file in the
page:

    python -m minisim scenarios/overwatch.json --seed 3 --replay run.json

- **Battle area:** terrain, units as icons at their altitude, comms links
  (blue), detections (yellow dashes), satellite detections (violet dots going
  up) and shots (red). Drag to rotate, scroll to zoom, click a unit for its
  details. The legend switches each kind of line on or off.
- **Orbit view:** for scenarios with satellites overhead, the globe shows the
  orbit, the satellite, the battle area and the line between them during a pass.
- **Follow cameras:** four views, each tethered to a unit or satellite you pick.
- **Timeline:** play, pause, scrub and speed; recent events show in the corner.

Height is exaggerated four times so altitude is readable. The page is
`minisim/viewer.html` and opens with a recorded sample run.

## Space domain

`scenarios/orbital_watch.json` is a space scenario (`"domain": "space"` in the
file). It runs with the same command and prints an event log, each side's
space-surveillance picture and ground coverage:

    python -m minisim scenarios/orbital_watch.json
    python -m minisim scenarios/orbital_watch.json --plot orbits.png   # 3D orbits round the Earth
    python -m minisim scenarios/orbital_watch.json --map tracks.png    # ground tracks
    python -m minisim scenarios/orbital_watch.json --rpo rpo.png       # rendezvous plots
    python -m minisim scenarios/orbital_watch.json --trade             # constellation trade study

What it models (`minisim/space.py`):

- **Orbits:** two-body Kepler orbits in an Earth-centred frame. Objects are
  given as circular altitude and inclination, full elements, or a two-line
  element set, or pulled by name from a catalogue file
  (`scenarios/catalogue.json`, all notional).
- **Constellations:** Walker patterns from a few numbers; coverage of target
  areas (share of time in view, longest gap); `--trade` compares designs.
- **Mission sequences:** timed steps per satellite: `burn`, `hohmann` (raise or
  lower a circular orbit), `rendezvous` (a two-burn transfer to a standoff
  point behind a target, solved with Lambert's method) with a fuel budget.
  A `mission` list gives condition-based steps instead (see the Studio section).
  In the scenario `Courier` goes from a 300 km parking orbit to 850 km and
  `Prowler` closes on `Sentinel` to 20 km, then 2 km.
- **Space surveillance:** ground radars (range and elevation limits) and
  optical sites (dark site, sunlit target), plus sensors on satellites. Each
  scan's observations are fused by accuracy into one track per object, and an
  orbit is estimated from two fixes so the track can coast between passes.
  The report shows custody and mean error against the truth.
- **Conjunctions, collisions, debris:** every pair is screened each step;
  close passes are logged, hits destroy both objects and create fragments
  that are then tracked and screened like anything else.
- **Avoidance:** a satellite with `"avoid": true` looks ahead, and makes a
  small burn when a pass inside 2 km is predicted.

**Space with air and ground:** a surface scenario can have satellites pass
overhead (`"space"` key, see `minisim/overhead.py`). `scenarios/overwatch.json`
is Border Patrol plus a blue imaging satellite: during its pass it finds red's
ground sites and every blue unit with comms gets the tracks, so they show up
on your map in `--play`.

Limits: links are geometric only (range, elevation, line of sight), with no
data rates, antennas pointing or interference; visibility from shape is an
average, not dependent on viewing angle or lighting; no drag, J2, Sun or Moon gravity; a fixed Sun direction; TLEs are read
as plain elements (not SGP4); avoidance uses true orbits, not the tracked
ones; ground-track pictures have no coastlines.

## Plug-ins

A plug-in is a Python file with a `register(api)` function. It can add
behaviours, output types, and its own scenario keys, which the scenario file
can then use by name. `plugins/escort.py` is a working example, used by
`scenarios/escort_mission.json`:

```json
"plugins": ["../plugins/escort.py"],
"behaviors": ["engage", "orders", "retreat",
              {"name": "escort", "target": "Owl", "radius": 12},
              "patrol", "hold"]
```

Behaviours run in the order listed; entries can carry parameters as above.
Load a plug-in from the command line with `--plugin FILE.py`. Plug-ins are
ordinary code, so only load ones you trust.

New unit types need no code: add a template to the scenario JSON (speed in
km/s, sensors, weapons, optional `behaviors`).

## Output adaptors

Adaptors pass simulation data to other tools. Attach them in the scenario
(`"outputs": [{"type": "events_csv", "target": "events.csv"}]`) or on the
command line:

    python -m minisim scenarios/border_patrol.json \
        --output events_csv:events.csv --output positions_csv:pos.csv \
        --output jsonl:replay.jsonl --output udp_json:127.0.0.1:9999

| Type | Sends |
|---|---|
| `events_csv` | every event (detections, shots, kills) |
| `positions_csv` | every platform's position every 10 s |
| `jsonl` | full state snapshots, one JSON object per line |
| `udp_json` | the same snapshots as UDP datagrams to a host and port |

`udp_json` is a simple stand-in for network interoperability; it is not DIS.
Write your own by subclassing `minisim.Observer` (see `KillLog` in the
example plug-in). File targets are relative to where you run the command.

## Driving it from Python

```python
import minisim
sim = minisim.load("scenarios/border_patrol.json", seed=7)
print(sim.run(), sim.losses())
```

For agents and machine learning, `WargameEnv` gives a reset/step loop with
observations limited to what your side can see, and a reward of enemy losses
minus own losses (plus or minus 5 for a win or loss):

```python
env = minisim.WargameEnv("scenarios/border_patrol.json", side="blue")
obs = env.reset(seed=1)
obs, reward, done, info = env.step({"Hawk1": ("move", (120, 150))})
numbers = env.vector(obs)      # fixed-length list for a learning library
```

`python examples/random_agent.py` runs 20 games with a random policy.

## Not modelled

Weapon fly-out (shots are a delay plus a dice roll, with no line-of-sight
check at launch), speed or fuel burn that varies with altitude, weather, smart enemy tactics, real DIS networking and SysML import. Each is a reasonable next addition.
