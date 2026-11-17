"""minisim Studio and Replay: browser interfaces for minisim.

    python -m minisim.gui            # then open http://127.0.0.1:8765
    python -m minisim.gui --port 9000

Studio (/) builds and runs space scenarios. Replay (/viewer) runs an air and
ground scenario from the scenarios folder and plays it back in 3D.

The page (gui.html) has a globe, a constellation maker, the scenario file as
editable text, a platform list and an output pane. Its Run button sends the
scenario here and shows the result. The server only listens on this computer.
The globe's coastlines need an internet connection the first time (the page
loads its map library from a public CDN).
"""
import argparse
import json
import os
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import adaptors, scenario, space

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "gui.html")
VIEWER = os.path.join(HERE, "viewer.html")


def surface_scenarios():
    """Air and ground scenario files: {file name: path}."""
    found = {}
    for folder in (os.path.join(os.getcwd(), "scenarios"),
                   os.path.join(os.path.dirname(HERE), "scenarios")):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not name.endswith(".json") or name in found:
                continue
            try:
                with open(path) as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and "platforms" in data:
                found[name] = path
    return found


def run_replay(name, seed):
    """Run one of the listed scenarios and return its replay as JSON text."""
    path = surface_scenarios()[name]              # KeyError for anything unlisted
    sim = scenario.load(path, seed=seed, outputs=False)
    recorder = adaptors.Replay()
    sim.observers.append(recorder)
    sim.run()
    sim.finish()
    return json.dumps(recorder.data, separators=(",", ":"))
MAX_BODY = 2_000_000


def run_scenario(text):
    """Run scenario JSON text through the space engine; return the report."""
    json.loads(text)                                   # fail early on bad JSON
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        sim = space.load_space(path, seed=1)
        return space.report(sim.run())
    finally:
        os.unlink(path)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, kind):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html", "/viewer"):
            with open(VIEWER if self.path == "/viewer" else PAGE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/scenarios":
            self._send(200, json.dumps(list(surface_scenarios())).encode(),
                       "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path not in ("/api/run", "/api/replay"):
            return self._send(404, b"not found", "text/plain")
        size = int(self.headers.get("Content-Length", 0))
        if size > MAX_BODY:
            return self._send(413, b"scenario too large", "text/plain")
        body = self.rfile.read(size).decode("utf-8")
        try:
            if self.path == "/api/replay":
                ask = json.loads(body)
                return self._send(200, run_replay(ask["scenario"],
                                                  int(ask.get("seed", 1))).encode(),
                                  "application/json")
            result = {"output": run_scenario(body)}
        except Exception as err:                       # report any scenario problem
            result = {"error": f"{type(err).__name__}: {err}"}
        self._send(200, json.dumps(result).encode(), "application/json")

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser(prog="minisim.gui", description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"minisim Studio is running at {url}  (Ctrl+C to stop)")
    print(f"minisim Replay is at {url}/viewer")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
