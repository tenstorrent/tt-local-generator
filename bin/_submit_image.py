#!/usr/bin/env python3
"""Submit an image generation job to FLUX server and print job ID."""
import sys, json, urllib.request, urllib.error

prompt, seed = sys.argv[1:]
payload = {"prompt": prompt, "width": 1024, "height": 1024,
           "num_inference_steps": 4, "seed": int(seed)}
req = urllib.request.Request(
    "http://localhost:8000/v1/images/generations",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
        print(d.get("id", "ERROR:" + str(d)))
except Exception as e:
    print(f"ERROR:{e}")
