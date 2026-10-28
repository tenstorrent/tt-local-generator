"""A fake ``tt`` executable for tests, driven by a JSON behaviour file.

``make_fake_tt(dir, behaviour)`` writes ``<dir>/tt`` (a Python script) and
``<dir>/behaviour.json``. The script never touches docker, gozer or hardware.

Behaviour JSON::

    {"version": "tt 1.2.0",              # --version stdout
     "commands": {                        # key = argv with --json removed, joined by spaces
        "model ps": {"stdout": "...", "stderr": "", "exit": 0,
                     "delay": 0.0,           # sleep before answering
                     "lines": ["a", "b"],    # alternative to stdout: one per line
                     "line_delay": 0.0,      # sleep after each line
                     "hang": false}}}        # sleep forever after output

A command matches by exact key, else by longest key that is a prefix of it.
Every invocation's argv is appended to ``calls.jsonl`` next to the script.
"""
from __future__ import annotations

import json
import os
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
    print(b.get("version", "tt 1.2.0")); sys.exit(0)
key = " ".join(a for a in args if a not in ("--json",))
cmds = b.get("commands", {{}})
spec = cmds.get(key)
if spec is None:
    best = [k for k in cmds if key.startswith(k)]
    spec = cmds[max(best, key=len)] if best else {{"stderr": "fake tt: no behaviour for " + key, "exit": 2}}
time.sleep(spec.get("delay", 0))
if "stdout" in spec:
    sys.stdout.write(spec["stdout"]); sys.stdout.flush()
for ln in spec.get("lines", []):
    print(ln, flush=True)
    time.sleep(spec.get("line_delay", 0))
if spec.get("stderr"):
    sys.stderr.write(spec["stderr"]); sys.stderr.flush()
if spec.get("hang"):
    time.sleep(3600)
sys.exit(spec.get("exit", 0))
'''


def make_fake_tt(directory: Path, behaviour: dict) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "behaviour.json").write_text(json.dumps(behaviour))
    tt = directory / "tt"
    tt.write_text(_SCRIPT.format(python=sys.executable))
    tt.chmod(tt.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return tt


def calls(directory: Path) -> list[list[str]]:
    p = Path(directory) / "calls.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
