"""A fake ``gozer`` executable for tests, driven by a JSON behaviour file.

``make_fake_gozer(dir, behaviour)`` writes ``<dir>/gozer`` (a Python script) and
``<dir>/behaviour.json``. The script never touches chips, /proc or the real gate.

Behaviour JSON::

    {"version": "gozer 0.3.4",
     "commands": {                      # key = argv with --json removed, joined by spaces
        "status": {"stdout": "...", "stderr": "", "exit": 0,
                   "delay": 0.0,        # sleep before answering
                   "hang": false}}}     # sleep forever instead of answering

A command matches by exact key, else by longest key that is a prefix of it.
Every invocation's argv is appended to ``calls.jsonl`` next to the script.
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

_SCRIPT = r'''#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\n")
b = json.load(open(os.path.join(here, "behaviour.json")))
if args == ["--version"]:
    print(b.get("version", "gozer 0.3.4")); sys.exit(0)
key = " ".join(a for a in args if a != "--json")
cmds = b.get("commands", {{}})
spec = cmds.get(key)
if spec is None:
    best = [k for k in cmds if key.startswith(k)]
    spec = cmds[max(best, key=len)] if best else {{"stderr": "fake gozer: no behaviour for " + key, "exit": 2}}
time.sleep(spec.get("delay", 0))
if spec.get("hang"):
    time.sleep(3600)
if "stdout" in spec:
    sys.stdout.write(spec["stdout"]); sys.stdout.flush()
if spec.get("stderr"):
    sys.stderr.write(spec["stderr"]); sys.stderr.flush()
sys.exit(spec.get("exit", 0))
'''


def make_fake_gozer(directory: Path, behaviour: dict) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "behaviour.json").write_text(json.dumps(behaviour))
    g = directory / "gozer"
    g.write_text(_SCRIPT.format(python=sys.executable))
    g.chmod(g.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return g


def calls(directory: Path) -> list[list[str]]:
    p = Path(directory) / "calls.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
