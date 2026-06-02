#!/usr/bin/env python3
"""Submit an image-to-video job to SkyReels I2V server and print job ID."""
import sys, json, urllib.request, base64

prompt, image_path, seed = sys.argv[1:]
b64 = base64.b64encode(open(image_path, "rb").read()).decode()

payload = {
    "prompt": prompt,
    "num_inference_steps": 20,
    "seed": int(seed),
    "image_prompts": [{"image": b64, "frame_pos": 0}],
}
req = urllib.request.Request(
    "http://localhost:8000/v1/videos/generations/i2v",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=60) as r:
    d = json.loads(r.read())
    print(d.get("id", "ERROR:" + str(d)))
