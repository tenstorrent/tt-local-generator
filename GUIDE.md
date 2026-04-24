# Generating Videos with Wan2.2 on Tenstorrent Hardware
### A complete walkthrough: setup → server → API → UI

This guide documents exactly how we got Wan2.2-T2V-A14B-Diffusers running on
a Tenstorrent P150x4 board, including every timeout, workaround, and error we
hit along the way. It then tours the API and explains how to use this UI wrapper
to interact with it.

> **Other models:** For Mochi-1 on QB2 (P300x2 / Blackhole), see
> [docs/mochi-qb2-setup.md](docs/mochi-qb2-setup.md).
> For a feature overview of the full UI, see [README.md](README.md).

---

## Contents

1. [What You're Actually Running](#1-what-youre-actually-running)
2. [Prerequisites and Hardware](#2-prerequisites-and-hardware)
3. [Setup on a Fresh Ubuntu 24.04 Machine](#3-setup-on-a-fresh-ubuntu-2404-machine)
4. [Pre-downloading the Model Weights](#4-pre-downloading-the-model-weights)
5. [Starting the Inference Server — What Actually Happens](#5-starting-the-inference-server--what-actually-happens)
6. [Problems We Hit and How We Fixed Them](#6-problems-we-hit-and-how-we-fixed-them)
7. [A Tour of the Wan2.2 API](#7-a-tour-of-the-wan22-api)
8. [Installing and Running the UI](#8-installing-and-running-the-ui)
9. [Running on Fewer Chips](#9-running-on-fewer-chips)
10. [Chaining Generations into Longer Videos](#10-chaining-generations-into-longer-videos)
11. [Adding a Prompt Generator](#11-adding-a-prompt-generator)
12. [How Configuration Options Affect Output](#12-how-configuration-options-affect-output)
13. [Running the GUI Remotely (macOS or another Linux machine)](#13-running-the-gui-remotely-macos-or-another-linux-machine)

---

## 1. What You're Actually Running

**Wan2.2-T2V-A14B-Diffusers** is Wan-AI's 14-billion-parameter text-to-video
diffusion model. It generates 5-second, 720p video clips from a text prompt.
The reference implementation is on HuggingFace as
[Wan-AI/Wan2.2-T2V-A14B-Diffusers](https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B-Diffusers).

Tenstorrent's **tt-inference-server** wraps this model in a production-style
async HTTP server backed by a Docker container. The container:

- Runs the model on Tenstorrent Tensix accelerator chips (not CUDA)
- Exposes an OpenAI-compatible REST API on port 8000
- Handles job queuing, status polling, and video download

The stack looks like:

```
your app / this UI
        │  HTTP (port 8000)
        ▼
  Docker container
  ├── FastAPI server  (tt-inference-server)
  ├── Wan2.2 model   (loaded into TT DRAM on startup)
  └── TT device      (P150x4 — 4 Wormhole chips, ~128 GB DRAM)
```

The model takes **approximately 5 minutes to load** into device DRAM on first
start. Subsequent starts are faster (~2 min) because the container is already
pulled. Generation of a single 5-second clip at 20 steps takes roughly 3–8
minutes depending on load.

---

## 2. Prerequisites and Hardware

### Minimum hardware

| Component | Minimum | Used in this walkthrough |
|---|---|---|
| Tenstorrent accelerator | n150 (1 chip) | **P150x4** (4 chips) |
| Host RAM | 16 GB | 64 GB |
| Host CPU | Any x86_64 | AMD EPYC |
| Host storage | 200 GB free | NVMe SSD |
| OS | Ubuntu 22.04+ | **Ubuntu 24.04 LTS** |

### Software requirements

- Docker CE (not the Ubuntu snap)
- `tt-smi` and TT-KMD kernel module (installed separately from
  [tt-kmd](https://github.com/tenstorrent/tt-kmd))
- Python 3.10+ (system python3 — GTK4 bindings are system-only)
- `ffmpeg` for thumbnail extraction

---

## 3. Setup on a Fresh Ubuntu 24.04 Machine

Run the included setup script:

```bash
cd ~/code/tt-local-generator
./bin/setup_ubuntu.sh
```

This installs:

- **Docker CE** (from Docker's official apt repo — the Ubuntu snap version
  does not support GPU/device passthrough correctly)
- **GTK4 bindings** (`python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-4.0`)
  as system packages. These are **not pip-installable** — they must be
  installed system-wide and accessed via `/usr/bin/python3`.
- **GStreamer plugins** (`libgtk-4-media-gstreamer`, `gstreamer1.0-libav`)
  for inline video playback in the UI
- **ffmpeg** for thumbnail extraction
- Clones `tt-inference-server` and `tt-local-generator` to `~/code/`
- Creates `~/.env` from the template

After the script, create your secrets:

```bash
# Edit and set these two values
nano ~/code/tt-inference-server/.env
```

Minimum `.env` contents:

```ini
JWT_SECRET=replace-me-with-a-long-random-string
AUTHORIZATION_TOKEN=replace-me-with-another-long-random-string
```

> **Why two secrets?** `JWT_SECRET` is the server's internal signing key used
> to authenticate Docker container registration. `AUTHORIZATION_TOKEN` (also
> called `API_KEY`) is the Bearer token your API clients send with requests.
> They can be the same value, but keeping them separate is better practice.

Generate a suitable random value:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 4. Pre-downloading the Model Weights

The Wan2.2-T2V-A14B-Diffusers model is **~118 GB**. The Docker container has a
**1200-second (20-minute) startup timeout**. On a fast connection (1 Gbps+)
you might squeeze a download in under the timeout, but it's safer to
pre-download to the host's HuggingFace cache and mount it read-only into
the container.

```bash
pip3 install --break-system-packages huggingface_hub

# This downloads to ~/.cache/huggingface/hub/models--Wan-AI--Wan2.2-T2V-A14B-Diffusers/
huggingface-cli download Wan-AI/Wan2.2-T2V-A14B-Diffusers
```

The `--host-hf-cache` flag in `start_wan.sh` mounts `~/.cache/huggingface`
into the container at the same path, so the model loads from local NVMe
(seconds) rather than the internet.

### Why the subdirectory layout matters

Wan2.2 stores its weights in **subdirectories** of the HuggingFace snapshot:

```
models--Wan-AI--Wan2.2-T2V-A14B-Diffusers/
└── snapshots/
    └── <hash>/
        ├── transformer/          ← diffusion transformer weights
        │   ├── config.json
        │   └── diffusion_pytorch_model-00001-of-00007.safetensors
        │   └── … (7 shards)
        ├── tokenizer/
        ├── vae/
        ├── text_encoder/
        └── scheduler/
```

An older version of `setup_host.py` in tt-inference-server globbed the
snapshot **root** for `model*.safetensors`. Because Wan2.2 puts its weights in
`transformer/`, that glob always found nothing and fell through to an
interactive "how do you want to provide the model?" prompt. We bypass this
entirely by setting `MODEL_SOURCE=huggingface` (see next section).

---

## 5. Starting the Inference Server — What Actually Happens

The startup process is orchestrated by `run.py` in tt-inference-server:

```
run.py
  │
  ├─ calls setup_host.py        (validates model source, checks weights)
  │                              ← MODEL_SOURCE=huggingface skips interactive prompts
  │
  ├─ pulls Docker image if not cached
  │   ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34
  │
  ├─ docker run ... (with Tenstorrent device bind-mounts, port 8000,
  │                   --host-hf-cache mounted, JWT_SECRET injected)
  │
  └─ exits with code 0          ← this is normal; Docker is now running
                                   in the background
```

After `run.py` exits, the container boots independently and loads the model
into device DRAM. This takes approximately:

| Phase | Log signal | Time |
|---|---|---|
| Container startup | `Workflow PID …` | ~30 s |
| Python/FastAPI imports | `─── tailing …` | ~1 min |
| FastAPI ready (model still loading) | `Application startup complete` | ~2 min |
| Model shards loading into TT DRAM | (progress in log) | ~3–4 min |
| **Workers warmed up — server ready** | **`All devices are warmed up`** | **~5 min total** |

Use `start_wan.sh` to watch this happen:

```bash
cd ~/code/tt-local-generator
./start_wan.sh
```

The script tails the container log automatically. Watch for:

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

**The server is NOT ready** when `Application startup complete` appears —
that only means the FastAPI process is up. The model workers continue loading
for several more minutes. The real readiness signal is:

```
All devices are warmed up
```

in the container log, or `"model_ready": true` in the `/tt-liveness` JSON:

```bash
curl -s http://localhost:8000/tt-liveness | python3 -m json.tool
# → { "status": "alive", "model_ready": true, ... }
```

The first `POST /v1/videos/generations` will return 405 until `model_ready`
is true. The UI polls `/tt-liveness` and turns the status indicator teal when
the server is actually ready to accept jobs.

### Monitoring the running server

```bash
# Check if Docker container is running
docker ps --filter "ancestor=ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34"

# Tail the log from a running server
tail -f ~/code/tt-inference-server/workflow_logs/docker_server/media_*_Wan2.2*.log

# Check Tenstorrent chip utilization
tt-smi -s    # snapshot mode — outputs JSON, avoids TUI

# Health check
curl http://localhost:8000/tt-liveness

# Stop the server
docker stop $(docker ps -q --filter "ancestor=ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34")
```

---

## 6. Problems We Hit and How We Fixed Them

### 6.1  `ModuleNotFoundError: No module named 'benchmarking'`

**Symptom:** Running `python3 -m workflows.run_workflows` failed immediately.

**Cause:** `workflows/run_workflows.py` imports `benchmarking`, which isn't on
the Python path when invoked as a module.

**Fix:** Use the actual entry point:
```bash
python3 run.py --model Wan2.2-T2V-A14B-Diffusers ...
```

### 6.2  Interactive `EOFError` from `setup_host.py`

**Symptom:** When running `run.py` in the background (`&`), the process
immediately died with `EOFError` because `setup_host.py` tried to `input()`
for the model source and JWT secret, but stdin was a pipe, not a terminal.

**Fix:** Set two environment variables before running `run.py`:
```bash
MODEL_SOURCE=huggingface JWT_SECRET="$JWT_SECRET" python3 run.py ...
```

`MODEL_SOURCE=huggingface` tells `setup_host.py` to skip the interactive
"how do you want to provide the model?" prompt entirely.
`JWT_SECRET` is read from `.env` and injected so the script never prompts
for it either.

### 6.3  `start_wan.sh` reported "Workflow process exited unexpectedly" (false alarm)

**Symptom:** Script printed `ERROR: Workflow process exited unexpectedly` even
though the server was actually running fine.

**Cause:** `run.py` **intentionally** exits with code 0 after handing off to
Docker. The script was treating any exit as an error.

**Fix:** Changed the check from `if [[ $? -ne 0 ]]` to `wait $PID; EXIT=$?;
if [[ $EXIT -ne 0 ]]`. Now only a non-zero exit code is treated as an error.

### 6.4  Log file not found after `run.py` exits

**Symptom:** `start_wan.sh` couldn't find the new log file because it picked up
an old log from a previous run.

**Fix:** Record the start timestamp before launching `run.py`:
```bash
START_TS=$(date +%s)
```
Then filter log files by mtime:
```bash
ls -t "$LOG_DIR"/*.log | while read -r f; do
    mtime=$(stat -c %Y "$f"); [[ $mtime -ge $START_TS ]] && echo "$f" && break
done
```

### 6.5  Container startup timeout (1200 s) on first run

**Symptom:** The container exited with a timeout error when the model weights
needed to be downloaded.

**Cause:** The container has a hard 1200-second startup timeout. Downloading
118 GB over a typical connection takes longer.

**Fix:** Pre-download with `huggingface-cli download Wan-AI/Wan2.2-T2V-A14B-Diffusers`
before starting the server. The `--host-hf-cache` flag mounts the local cache
into the container so the model loads from disk (a few seconds per shard) rather
than the network.

### 6.6  ffmpeg blocking on terminal input

**Symptom:** The thumbnail extraction subprocess would hang indefinitely waiting
for the user to press `[q]` to quit ffmpeg. The UI froze.

**Cause:** ffmpeg inherited the terminal's stdin and waited for user input when
it detected an interactive terminal.

**Fix:** Pass `stdin=subprocess.DEVNULL` to `subprocess.run()`. Also add
`-update 1` to ffmpeg flags to suppress the "output is a sequence of images"
warning that appeared without it:
```python
subprocess.run(
    ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-q:v", "2", "-update", "1", thumb_path],
    stdin=subprocess.DEVNULL,
    capture_output=True,
    timeout=30,
)
```

---

## 7. A Tour of the Wan2.2 API

The server exposes an HTTP API on port 8000. All endpoints require a Bearer
token in the `Authorization` header:

```
Authorization: Bearer <AUTHORIZATION_TOKEN from .env>
```

### 7.1  Health check

```http
GET /tt-liveness
```

Returns `200 OK` as soon as FastAPI is up. Note that the model worker may still
be loading at this point (see section 5).

```bash
curl http://localhost:8000/tt-liveness
# → HTTP 200
```

### 7.2  Submit a generation job

```http
POST /v1/videos/generations
Content-Type: application/json
Authorization: Bearer <token>

{
  "prompt": "a cinematic shot of a red sports car driving through a rainy city at night",
  "negative_prompt": "blurry, low quality, watermark",
  "num_inference_steps": 20,
  "seed": 42
}
```

**Response `202 Accepted`:**
```json
{
  "id": "3f7a9b2c-e1d4-4f8a-9c0b-1234567890ab",
  "status": "queued",
  "model": "Wan2.2-T2V-A14B-Diffusers",
  "created_at": "2025-07-15T14:32:00Z"
}
```

**Parameters:**

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `prompt` | string | ✓ | — | Text description of the video. More detail → better results. |
| `negative_prompt` | string | — | null | What to avoid. "blurry, watermark" is a good baseline. |
| `num_inference_steps` | int | — | 20 | Range: 12–50. More steps = sharper, slower. See section 12. |
| `seed` | int | — | random | Fix the seed to reproduce results exactly. |
| `guidance_scale` | float | — | 5.0 | Classifier-free guidance strength. Higher = closer adherence to prompt; above ~7 can cause oversaturation. |

**Returns `405 Method Not Allowed`** if the model worker is still loading.
Poll and retry.

### 7.3  Poll job status

```http
GET /v1/videos/generations/{job_id}
Authorization: Bearer <token>
```

**Response:**
```json
{
  "id": "3f7a9b2c-...",
  "status": "in_progress",
  "progress": 0.45,
  "model": "Wan2.2-T2V-A14B-Diffusers",
  "created_at": "2025-07-15T14:32:00Z",
  "request_parameters": {
    "prompt": "...",
    "num_inference_steps": 20,
    "seed": 42
  }
}
```

**Status values:**

| Status | Meaning |
|---|---|
| `queued` | Job waiting in server queue |
| `in_progress` | Model is generating frames |
| `completed` | Video is ready to download |
| `failed` | Generation failed (see `error` field) |
| `cancelled` | Job was cancelled |

Poll every 3–5 seconds. A 20-step generation runs for 3–8 minutes on P150x4.

### 7.4  Download the video

```http
GET /v1/videos/generations/{job_id}/download
Authorization: Bearer <token>
```

Returns the raw MP4 as a binary stream. Use streaming to avoid loading the
whole file into memory:

```python
import requests

resp = requests.get(
    f"http://localhost:8000/v1/videos/generations/{job_id}/download",
    headers={"Authorization": f"Bearer {token}"},
    stream=True,
    timeout=120,
)
resp.raise_for_status()

with open("output.mp4", "wb") as f:
    for chunk in resp.iter_content(chunk_size=65536):
        f.write(chunk)
```

### 7.5  List all jobs

```http
GET /v1/videos/jobs
Authorization: Bearer <token>
```

Returns a JSON array of all jobs the server knows about, including completed
and failed ones. Useful for recovery after a crash.

### 7.6  Complete Python example (no UI)

```python
#!/usr/bin/env python3
"""Minimal example: generate a video and save it."""
import time
import requests

BASE_URL = "http://localhost:8000"
TOKEN    = "your-authorization-token"

def headers():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# 1. Submit
resp = requests.post(f"{BASE_URL}/v1/videos/generations", headers=headers(), json={
    "prompt": "a golden retriever running on a beach at sunset",
    "num_inference_steps": 20,
    "seed": 1234,
})
resp.raise_for_status()
job_id = resp.json()["id"]
print(f"Job submitted: {job_id}")

# 2. Poll
while True:
    time.sleep(5)
    status_resp = requests.get(f"{BASE_URL}/v1/videos/generations/{job_id}", headers=headers())
    status_resp.raise_for_status()
    data = status_resp.json()
    print(f"  Status: {data['status']}  progress: {data.get('progress', '?')}")
    if data["status"] == "completed":
        break
    if data["status"] in ("failed", "cancelled"):
        raise RuntimeError(f"Job {data['status']}: {data.get('error')}")

# 3. Download
dl = requests.get(f"{BASE_URL}/v1/videos/generations/{job_id}/download",
                  headers=headers(), stream=True, timeout=120)
dl.raise_for_status()
with open("output.mp4", "wb") as f:
    for chunk in dl.iter_content(chunk_size=65536):
        f.write(chunk)
print("Saved: output.mp4")
```

---

## 8. Installing and Running the UI

### 8.1  Prerequisites

The UI requires:
- `/usr/bin/python3` with `python3-gi` (GTK4 bindings) — **must be system
  python3, not a venv** (GTK bindings are system packages and are invisible
  inside virtualenvs)
- `python3-requests`
- GStreamer for inline video: `libgtk-4-media-gstreamer gstreamer1.0-libav`
- `ffmpeg` for thumbnail generation
- The inference server running and healthy on `http://localhost:8000`

If you ran `setup_ubuntu.sh`, all of these are already installed.

### 8.2  Clone and run

```bash
git clone https://github.com/tenstorrent/tt-local-generator.git ~/code/tt-local-generator
cd ~/code/tt-local-generator

# Always use the system python3, not a venv interpreter
/usr/bin/python3 main.py
# Or use the launcher:
./tt-gen
```

To point at a different server (e.g., remote or non-default port):

```bash
./tt-gen --server http://192.168.1.50:8000
```

The `--server` flag sets the inference server address. The prompt server (port 8001)
and inventory server (port 8002) are automatically derived from the same hostname.

### 8.3  UI walkthrough

```
┌──────────────────────┬────────────────────────────────────────┐
│  TT VIDEO GENERATOR  │  Gallery (scrollable grid of cards)    │
│                      │                                         │
│  PROMPT              │  ┌──────────┐  ┌──────────┐            │
│  ┌────────────────┐  │  │ thumbnail│  │ thumbnail│            │
│  │ Describe the   │  │  │ prompt…  │  │ prompt…  │            │
│  │ video…         │  │  │ 14:32    │  │ 14:28    │            │
│  └────────────────┘  │  │[▶][💾][↺]│  │[▶][💾][↺]│            │
│                      │  └──────────┘  └──────────┘            │
│  NEGATIVE PROMPT     │                                         │
│  ┌────────────────┐  │                                         │
│  │                │  │                                         │
│  └────────────────┘  │                                         │
│                      │                                         │
│  Steps (12–50): 20   │                                         │
│  Seed (−1=rand): −1  │                                         │
│                      │                                         │
│  SEED IMAGE          │                                         │
│  [none] [Browse…]    │                                         │
│         [Clear]      │                                         │
│                      │                                         │
│  ⬤ Server ready     │                                         │
│                      │                                         │
│  [  Generate  ]      │                                         │
│  [✕ Cancel    ]      │                                         │
│  [⟳ Recover   ]      │                                         │
│                      │                                         │
│  QUEUED PROMPTS      │                                         │
│  1. a red car…  [×]  │                                         │
│  2. ocean waves [×]  │                                         │
├──────────────────────┴────────────────────────────────────────┤
│ Status: Generating… 42s (in_progress)                          │
└────────────────────────────────────────────────────────────────┘
```

**Button behavior:**
- **Generate** (server idle): submits immediately, clears prompt, shows a
  pending card with the prompt text and elapsed timer
- **+ Add to Queue** (server busy): queues the prompt, clears the field so
  you can write the next one; runs automatically when the current job finishes
- **▶ Play**: switches the card's thumbnail to an inline video player
- **💾 Save**: opens a file-chooser to export the MP4
- **↺ Iterate**: repopulates the prompt and negative prompt fields with the
  values from this generation, so you can tweak and re-run
- **⟳ Recover Jobs**: scans the server for jobs not in local history (useful
  after a crash or restart)

**Keyboard shortcut:** Press `Enter` in the prompt field to submit (if the
server is ready) or queue (if busy). Actually, just click the button — the
prompt field is a multiline `TextView`, so `Enter` inserts a newline. The
button is the intended trigger.

### 8.4  Where your videos are stored

```
~/.local/share/tt-video-gen/
├── videos/           ← MP4 files (kept forever — manage manually)
│   ├── gen_3f7a9b2c.mp4
│   └── gen_3f7a9b2c.txt  ← metadata sidecar (prompt, seed, steps, duration)
├── thumbnails/       ← first-frame JPEGs for the gallery
│   ├── gen_3f7a9b2c.jpg
│   └── seed_3f7a9b2c.png  ← saved copy of any seed image
└── history.json      ← generation index (id, prompt, paths, created_at, duration_s)
```

The sidecar `.txt` file alongside each MP4 contains:
```
prompt: a golden retriever running on a beach at sunset
steps: 20
seed: 1234
generated: 2025-07-15T14:32:00
duration_s: 312.4
```

---

## 9. Running on Fewer Chips

### Supported configurations

| Device flag | Chips | TT DRAM | Notes |
|---|---|---|---|
| `p150x4` | 4 × Wormhole P150 | ~128 GB | Fastest; full model in DRAM |
| `p150x2` | 2 × Wormhole P150 | ~64 GB | Slower; may require weight quantization |
| `t3k` | 1 × T3000 (4 chips) | ~32 GB | May require `--impl` flag changes |
| `n150` | 1 × Wormhole N150 | ~12 GB | Not enough DRAM for 14B; requires 4-bit quant |

### Trying `p150x2`

Change the device flag in `start_wan.sh`:

```bash
python3 run.py \
    --model Wan2.2-T2V-A14B-Diffusers \
    --workflow server \
    --tt-device p150x2 \      # ← changed
    --impl tt-transformers \
    --engine media \
    --docker-server \
    --override-docker-image "$DOCKER_IMAGE" \
    --host-hf-cache "$HF_CACHE"
```

**Expected effect:** Generation time roughly doubles (fewer chips = fewer
parallel operations). Memory pressure increases. If the model OOMs, you may
need to reduce `num_inference_steps` or enable weight quantization.

### Fewer chips: what breaks first

The Wan2.2 transformer is a 14B-parameter model with ~28 GB of bfloat16
weights. It is sharded across chips during inference. With fewer chips:

1. **4→2 chips:** Usually works, ~2× slower per step, higher per-chip memory
   pressure. Quality unaffected.
2. **4→1 chip (N150):** 12 GB DRAM is insufficient for bfloat16 weights.
   Requires INT8 or INT4 quantization, which reduces video quality noticeably
   (softer textures, more compression artifacts).
3. **OOM errors:** Manifest as the model worker crashing with a memory
   allocation error in the container log. Reduce `num_inference_steps` first
   as a quick test; if that doesn't help, you need quantization.

### Checking what's available

```bash
tt-smi -s | python3 -c "
import json, sys
data = json.load(sys.stdin)
for dev in data.get('device_info', []):
    print(dev.get('board_type'), dev.get('dram_size'))
"
```

---

## 10. Chaining Generations into Longer Videos

Wan2.2 generates 5-second clips. To build a longer video, you chain clips:

### Strategy 1: Sequential prompts with seed image continuity

Generate clip 1, extract the last frame, use it as the seed image for clip 2.
This gives rough visual continuity (same style/subject), though motion doesn't
carry over seamlessly.

```bash
# Extract last frame of clip 1
ffmpeg -sseof -0.1 -i clip1.mp4 -vframes 1 -q:v 2 last_frame.jpg

# Submit clip 2 with seed image
curl -X POST http://localhost:8000/v1/videos/generations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "continuation of the scene, camera panning left",
    "seed_image_path": "last_frame.jpg",
    "num_inference_steps": 20
  }'
```

The UI supports this directly: use the **Seed Image** field in the control
panel to attach a frame, then submit.

### Strategy 2: Post-processing concatenation

Generate clips independently with related prompts, then concatenate:

```bash
# Concatenate MP4s with ffmpeg (re-encodes for clean cut)
ffmpeg \
  -i clip1.mp4 -i clip2.mp4 -i clip3.mp4 \
  -filter_complex "[0:v][1:v][2:v]concat=n=3:v=1:a=0[out]" \
  -map "[out]" \
  output_15s.mp4
```

**For smoother transitions** use a crossfade:

```bash
# Crossfade between clip1 and clip2 (0.5s overlap at clip1's end)
ffmpeg \
  -i clip1.mp4 -i clip2.mp4 \
  -filter_complex "
    [0:v]trim=0:4.5,setpts=PTS-STARTPTS[v0];
    [1:v]trim=0:5,setpts=PTS-STARTPTS[v1];
    [v0][v1]xfade=transition=fade:duration=0.5:offset=4[out]
  " \
  -map "[out]" \
  output_9s_crossfade.mp4
```

### Strategy 3: Prompt a narrative arc

Write your prompts as a narrative sequence — same subject, evolving scene:

```
Clip 1: "a red sports car parked in a dark garage, dramatic lighting"
Clip 2: "the red sports car's engine starts, headlights on, fog machine"
Clip 3: "the red sports car accelerates onto a rain-slicked city street at night"
Clip 4: "the red sports car drifting around a corner, sparks flying"
```

Use the queue in the UI to line them all up and walk away.

---

## 11. Adding a Prompt Generator

You can pre-generate a list of prompts using an LLM and feed them to the queue,
or build an interactive prompt helper.

### Simple CLI prompt expander

```python
#!/usr/bin/env python3
"""
prompt_gen.py — Expand a bare idea into a detailed Wan2.2 video prompt.
Uses the Anthropic API (or any OpenAI-compatible endpoint).

Usage:
    python3 prompt_gen.py "a city at night"
"""
import sys
import anthropic

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

idea = " ".join(sys.argv[1:]) or "something beautiful"

system = """You are a cinematographer describing video shots for a text-to-video
diffusion model. Convert the user's idea into a detailed, vivid video prompt.
Include: subject, action, camera angle, lighting, mood, time of day, weather.
Keep it to 1-3 sentences, present tense, cinematic language.
Output ONLY the prompt, nothing else."""

msg = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=200,
    system=system,
    messages=[{"role": "user", "content": idea}],
)
print(msg.content[0].text)
```

```bash
python3 prompt_gen.py "a city at night"
# → "A sweeping crane shot descends over a rain-soaked Tokyo skyline at midnight,
#    neon signs reflecting off wet asphalt as umbrellas drift through crowded
#    crosswalks below, the scene drenched in magenta and cyan light."
```

Pipe directly into the queue:

```bash
python3 prompt_gen.py "a city at night" | xclip -selection clipboard
# then paste into the UI prompt field
```

### Batch prompt generation script

```python
#!/usr/bin/env python3
"""
batch_gen.py — Generate and submit multiple videos automatically.

Combines prompt generation (via LLM) with direct API calls.
Saves output to ~/Videos/batch_<timestamp>/

Usage:
    python3 batch_gen.py ideas.txt
"""
import time, json, requests, anthropic
from pathlib import Path
from datetime import datetime

SERVER  = "http://localhost:8000"
TOKEN   = open(Path.home() / "code/tt-inference-server/.env").read()
# (parse TOKEN properly from .env in production)
OUT_DIR = Path.home() / "Videos" / f"batch_{datetime.now():%Y%m%d_%H%M%S}"
OUT_DIR.mkdir(parents=True)

client = anthropic.Anthropic()
ideas  = Path("ideas.txt").read_text().splitlines()

def expand(idea):
    """Expand a short idea into a full video prompt."""
    msg = client.messages.create(
        model="claude-opus-4-6", max_tokens=200,
        system="Convert the idea into a vivid 1-3 sentence video prompt. Output ONLY the prompt.",
        messages=[{"role": "user", "content": idea}],
    )
    return msg.content[0].text.strip()

def submit(prompt):
    r = requests.post(f"{SERVER}/v1/videos/generations",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"prompt": prompt, "num_inference_steps": 20})
    r.raise_for_status()
    return r.json()["id"]

def wait_and_download(job_id, out_path):
    while True:
        time.sleep(5)
        r = requests.get(f"{SERVER}/v1/videos/generations/{job_id}",
            headers={"Authorization": f"Bearer {TOKEN}"})
        r.raise_for_status()
        s = r.json()["status"]
        print(f"  {job_id[:8]} → {s}")
        if s == "completed": break
        if s in ("failed", "cancelled"):
            print(f"  FAILED: {r.json().get('error')}")
            return
    dl = requests.get(f"{SERVER}/v1/videos/generations/{job_id}/download",
        headers={"Authorization": f"Bearer {TOKEN}"}, stream=True, timeout=120)
    dl.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in dl.iter_content(65536): f.write(chunk)
    print(f"  Saved: {out_path}")

for i, idea in enumerate(ideas):
    if not idea.strip(): continue
    print(f"\n[{i+1}/{len(ideas)}] {idea}")
    prompt = expand(idea)
    print(f"  Prompt: {prompt}")
    job_id = submit(prompt)
    wait_and_download(job_id, OUT_DIR / f"{i+1:03d}_{job_id[:8]}.mp4")
```

---

## 12. How Configuration Options Affect Output

### `num_inference_steps` (12–50)

This is the single most impactful parameter.

| Steps | Time (P150x4) | Quality | Best use |
|---|---|---|---|
| 12 | ~2–3 min | Rough, blurry, unstable motion | Quick iteration, idea testing |
| 20 | ~4–5 min | Good balance | **Default; most generations** |
| 30 | ~6–8 min | Sharper edges, better motion coherence | Final outputs, important prompts |
| 50 | ~10–14 min | Marginal improvement over 30 | Rarely worth it |

**Theoretical explanation:** Each step refines the noisy latent by applying the
diffusion transformer once. Early steps establish global structure (composition,
main subject); later steps add fine-grained detail and sharpening. Diminishing
returns set in after ~30 steps for most prompts.

### `seed`

- **−1 (random):** Different video every time from the same prompt. Good for
  exploring a concept.
- **Fixed seed:** Reproducible result. Same prompt + same seed → same video
  (assuming same model version). Use this to isolate the effect of changing
  other parameters.
- **Fixed seed + varied prompt:** Good for ablation studies — you can see
  exactly how adding "at sunset" changes the result vs. the identical base.

### `negative_prompt`

Tells the model what to push away from during sampling. Common useful values:

```
blurry, out of focus, watermark, text, logo, low quality
```

For motion:
```
still image, static, frozen, no movement
```

For style:
```
cartoon, anime, painting, sketch, illustration
```

**Theoretical explanation:** In classifier-free guidance (CFG), the model
generates two predictions per step: one conditioned on the positive prompt, one
on the negative. The final update is:

```
output = uncond + cfg_scale × (cond − uncond)
```

The `guidance_scale` parameter (default 5.0) controls this. The UI exposes
it in the Advanced Settings panel. Higher values = stronger adherence to the
prompt, but over-saturation and artifacts appear above ~7 for Wan2.2.

### Prompt quality

The biggest lever of all. Tips derived from working with this model:

1. **Be specific about camera movement:**
   *"slow dolly in"*, *"aerial drone shot descending"*, *"static wide shot"*
   
2. **Specify lighting explicitly:**
   *"golden hour", "harsh noon sun", "neon-lit", "moonlight filtering through clouds"*

3. **Describe motion of subjects:**
   *"running at full speed", "slowly turning its head", "waves crashing"*
   The model struggles with implicit motion — name it.

4. **Short prompts → abstract/dreamlike results.** Longer, detailed prompts
   → more literal interpretation but occasionally overcrowded composition.

5. **Negative prompt is not a reliable brake.** It reduces probability of
   certain features but doesn't eliminate them. If you need "no people" and
   people keep appearing, rewrite the positive prompt to describe a scene
   that naturally excludes them.

### Seed image

An optional reference image that conditions the generation. The model's
image encoder processes it and biases the latent initialization.

**Effect:** Similar color palette and rough composition to the reference,
particularly in the first few seconds. Motion and fine details are still
generated from the prompt.

**Best use:** Visual style transfer (*"in the style of this concept art"*),
approximate color grading continuity between clips, or anchoring a subject's
appearance.

**Limitation:** The model was not trained on paired (image→video) data the
same way as a true image-to-video model. Results are stylistically influenced
but not strictly consistent. For true I2V generation, a dedicated model like
Wan2.2-I2V would be more appropriate.

---

---

## 13. Running the GUI Remotely (macOS or another Linux machine)

The GUI is a pure HTTP client — it never imports any TT hardware libraries. This
means it runs on any machine that can reach the inference server over the network,
including a macOS laptop or a second Ubuntu workstation. No Tenstorrent hardware is
required on the client machine.

### 13.1  What runs where

```
TT machine (Ubuntu, Blackhole/QB2)          Client machine (macOS or Linux laptop)
────────────────────────────────────        ──────────────────────────────────────
Docker container (port 8000)          HTTP  ./tt-gen --server http://tt-machine:8000
Qwen prompt server (port 8001)  ◄──────────
tt-ctl serve-inventory (port 8002)
```

### 13.2  Start the inventory server on the TT machine

The inventory server exposes your local video library (metadata + media files) over
HTTP so the remote GUI can browse and download it:

```bash
# On the TT machine:
./tt-ctl serve-inventory               # listens on 0.0.0.0:8002
./tt-ctl serve-inventory --port 9000   # custom port

# Verify it is up:
curl http://localhost:8002/inventory/health
# → {"status": "ok", "records": 47}
```

Keep this running in a tmux pane or screen session alongside the inference server.

### 13.3  Launch the GUI on the client machine

```bash
./tt-gen --server http://tt-machine:8000
```

Replace `tt-machine` with the hostname or IP of your TT machine. The GUI
automatically derives:
- Prompt server: `http://tt-machine:8001`
- Inventory server: `http://tt-machine:8002`

On startup the app:
1. Fetches `http://tt-machine:8002/inventory/records` — the full video library
2. Downloads thumbnails eagerly to `~/.local/share/tt-video-gen/remote-cache/tt-machine/`
3. Merges remote records into the gallery (shown with their original prompts and metadata)

### 13.4  Downloading videos from the remote library

Click any remote card to open the detail panel. Two buttons appear:

- **"Download from Remote Library"** — streams the MP4 from the inventory server
  directly to `~/.local/share/tt-video-gen/videos/` on the client. Progress is
  shown in the status bar. Once downloaded the video plays inline.
- **"⧉ Open externally"** — opens the video URL in the system player
  (`open` on macOS, `xdg-open` on Linux) without downloading it first. Useful
  when you just want to watch without storing a local copy.

### 13.5  Generating videos from the remote machine

The "Generate" button works identically when pointing at a remote server — it
submits to the inference server on the TT machine and polls for completion.
Finished videos are downloaded automatically and added to the local gallery.

### 13.6  macOS setup

Install the required GTK4 stack via Homebrew:

```bash
./bin/setup_macos.sh
```

Or manually:

```bash
brew install python@3.12 pygobject3 gtk4 gdk-pixbuf pango \
    gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav ffmpeg
pip3 install requests markovify pyyaml
```

> **Note on GStreamer on macOS:** inline video hover-previews work when the
> gst-libav plugin is installed. If you see blank video widgets, try
> `brew reinstall gst-libav` and restart the app. The "⧉ Open externally"
> button is always available as a fallback.

### 13.7  Firewall / port access

The client needs TCP access to three ports on the TT machine:

| Port | Service | Required |
|------|---------|---------|
| 8000 | Inference server | Yes |
| 8001 | Prompt server (Qwen) | No — prompt generation degrades gracefully |
| 8002 | Inventory server | No — remote gallery is unavailable without it |

On UFW:
```bash
sudo ufw allow 8000/tcp
sudo ufw allow 8001/tcp
sudo ufw allow 8002/tcp
```

---

*Document based on operational experience with Wan2.2-T2V-A14B-Diffusers on
Tenstorrent P150x4, tt-inference-server commit bac8b34, Ubuntu 24.04, March 2026.*
