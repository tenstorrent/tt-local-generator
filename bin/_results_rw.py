#!/usr/bin/env python3
"""Read/write workflow results.json. Called by run_worlds_fair_parallel.sh."""
import sys, json
from pathlib import Path

cmd = sys.argv[1]   # set_result | set_label | get_result

if cmd == "set_result":
    rj, node_id, key, value = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
    d = json.loads(Path(rj).read_text())
    d.setdefault(node_id, {})[key] = value
    Path(rj).write_text(json.dumps(d, indent=2))

elif cmd == "set_label":
    rj, node_id, label = sys.argv[2], sys.argv[3], sys.argv[4]
    d = json.loads(Path(rj).read_text())
    d.setdefault(node_id, {})["_label"] = label
    Path(rj).write_text(json.dumps(d, indent=2))

elif cmd == "get_result":
    rj, node_id, key = sys.argv[2], sys.argv[3], sys.argv[4]
    d = json.loads(Path(rj).read_text())
    print(d.get(node_id, {}).get(key, ""))
