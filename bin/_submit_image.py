#!/usr/bin/env python3
"""Submit an image generation job to FLUX server.

FLUX returns the image synchronously as base64 in {"images": [...], "generation_time": N}.
This script saves the image to the output path and prints "DONE" on success,
or "ERROR:..." on failure.

Usage: python3 _submit_image.py <prompt> <seed> <output_path>
"""
import sys, json, base64, urllib.request, urllib.error
from pathlib import Path

prompt, seed, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
payload = {"prompt": prompt, "width": 1024, "height": 1024,
           "num_inference_steps": 4, "seed": int(seed)}
req = urllib.request.Request(
    "http://localhost:8000/v1/images/generations",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
        if "images" in d and d["images"]:
            img_data = base64.b64decode(d["images"][0])
            Path(out_path).write_bytes(img_data)
            print("DONE")
        elif "id" in d:
            # async server — print job ID for polling
            print(d["id"])
        else:
            print(f"ERROR:unexpected response keys {list(d.keys())}")
except urllib.error.HTTPError as e:
    body = e.read().decode()[:200]
    print(f"ERROR:HTTP {e.code}: {body}")
except Exception as e:
    print(f"ERROR:{e}")
