# Getting Started on Quietbox 2 — Time to Fun

**Goal:** Clone → install → download models → generate your first video → watch TT-TV.

**Hardware:** Quietbox 2 (QB2) — 2× P300 cards = 4 Blackhole chips in a (2,2) mesh.

**OS:** Ubuntu 24.04 (Noble). You have the Tenstorrent PPA and an active `~/.tenstorrent-venv`.

---

## Time Budget

Here is an honest accounting of where the time goes on a fresh install.
Most of it is one-time downloads you can walk away from.

| Phase | Time | One-time? |
|---|---|---|
| Clone repos + system packages | ~5 min | No (fast) |
| Docker image pull (~29 GB) | 30–60 min | **One-time** |
| Wan2.2 weight download (~118 GB) | ~64 min | **One-time** |
| Configure `.env` + apply patches | ~3 min | No (fast) |
| Server warmup (TT compile + warmup inference) | ~9 min | No, every cold start |
| **First video generation** | **~6 min** | — |
| **Click "Watch TT-TV"** | **instant** | — |

**Time from a fully prepped machine (Docker + weights cached) to first video:** ~20 minutes.

**Time from a completely fresh install:** ~2–2.5 hours, but you are watching a progress bar
for most of it.

---

## Step 0 — Verify your baseline

Before starting, confirm you have the Tenstorrent driver stack and venv:

```bash
tt-smi -s | python3 -c "import sys,json; d=json.load(sys.stdin); print('Chips:', len(d['device_info']))"
# → Chips: 4  (2 P300 cards × 2 chips each)

ls ~/.tenstorrent-venv/bin/python3
# → /home/ttuser/.tenstorrent-venv/bin/python3  (has torch + transformers)
```

If `tt-smi` is missing, run the Tenstorrent PPA installer first — that is outside the scope
of this guide.

---

## Step 1 — Clone the repo and set up the vendored inference server

> **Time: ~2 minutes** (network-dependent; only the files at the pinned commit are fetched)

```bash
mkdir -p ~/code
cd ~/code

# The app
git clone git@github.com:tsingletaryTT/tt-local-generator.git
cd tt-local-generator

# Fetch the pinned tt-inference-server shallow clone into vendor/
./bin/setup_vendor.sh
```

`setup_vendor.sh` reads the pinned commit SHA from `vendor/VENDOR_SHA`, creates
`vendor/tt-inference-server/` as a shallow clone at exactly that commit, and does
nothing else — it never touches `~/code/tt-inference-server` or any system paths.
Re-running it is always safe; it is a no-op if the checkout is already at the right SHA.

If QB2 has a `~/code/tt-inference-server` dev checkout you want to patch instead
(not recommended for normal use), pass `--dev` to `apply_patches.sh` later.

---

## Step 2 — System packages

> **Time: ~3 minutes** (apt cache update dominates)

GTK4 Python bindings (`python3-gi`) **must** come from apt, not pip. They are invisible
inside virtual environments. Always use `/usr/bin/python3` to run the UI.

```bash
sudo apt-get update
sudo apt-get install -y \
    python3-gi python3-gi-cairo gir1.2-gtk-4.0 \
    libgtk-4-media-gstreamer \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-libav \
    ffmpeg \
    curl git ca-certificates gnupg
```

Then the pip-only extras (install to the system interpreter, not the venv):

```bash
pip3 install --break-system-packages requests markovify huggingface_hub
```

`markovify` is optional — the prompt generator degrades gracefully to pure algorithmic
mode if it is absent. Install it anyway; it makes the prompts noticeably more varied.

---

## Step 3 — Docker CE

> **Time: ~5 minutes** (skip if already installed)

The inference server runs inside a Docker container. Ubuntu ships with Docker snap
by default; you want the official Docker CE packages instead (the snap version has
filesystem restrictions that break the model bind-mounts).

```bash
# Check if Docker CE is already installed
docker version 2>/dev/null | grep -q 'Docker Engine' && echo "Already installed — skip to Step 4"
```

If you need to install:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

# Add yourself to the docker group (no sudo required later)
sudo usermod -aG docker "$USER"
newgrp docker
```

Verify:

```bash
docker run --rm hello-world
```

---

## Step 4 — Pull the Docker image

> **Time: 30–60 minutes** on a typical connection (one-time only)
>
> The image is ~29 GB uncompressed. Pull it now and go make coffee.

```bash
docker pull ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34
```

If your internet connection is unreliable, you can also load from the Git LFS archive that
ships inside the repo (it is ~7.4 GB compressed — still large, but survivable on a flaky link):

```bash
cd ~/code/tt-local-generator
git lfs pull          # fetches docker/*.tar.gz
docker load -i docker/tt-media-inference-server-0.11.1-bac8b34.tar.gz
```

Verify the image landed:

```bash
docker image ls | grep tt-media-inference-server
# → tt-media-inference-server   0.11.1-bac8b34  ...  29.7 GB
```

---

## Step 5 — Download Wan2.2 weights

> **Time: ~64 minutes** (one-time only — measured on QB2, 2026-03-30)
>
> The model is 118 GB across 34 safetensors shards. Once it is in
> `~/.cache/huggingface/hub/`, subsequent server starts find it in under a second.

You will need a [HuggingFace account](https://huggingface.co/) and a read token.
The Wan2.2 model is public (no gated access required).

```bash
# Set your token for this session
export HF_TOKEN=hf_YOUR_TOKEN_HERE

# Download Wan2.2 (~118 GB, ~64 min on QB2 hardware)
huggingface-cli login --token "$HF_TOKEN"
huggingface-cli download Wan-AI/Wan2.2-T2V-A14B-Diffusers

# Download Qwen3-0.6B for the prompt server (~1.2 GB, ~2 min)
huggingface-cli download Qwen/Qwen3-0.6B
```

Both downloads are resumable — if they are interrupted, just re-run the same command.

Check what you have:

```bash
ls ~/.cache/huggingface/hub/ | grep -E "Wan-AI|Qwen"
# → models--Qwen--Qwen3-0.6B
# → models--Wan-AI--Wan2.2-T2V-A14B-Diffusers
```

---

## Step 6 — Configure `.env`

> **Time: ~2 minutes**

The inference server container reads secrets and paths from a `.env` file at the root
of the `tt-inference-server` checkout — which after Step 1 lives at
`vendor/tt-inference-server/.env`.

```bash
# The repo ships an example; copy and fill it in
cp ~/code/tt-local-generator/.env.example \
   ~/code/tt-local-generator/vendor/tt-inference-server/.env

$EDITOR ~/code/tt-local-generator/vendor/tt-inference-server/.env
```

Set these three values:

```bash
# Your HuggingFace read token (same one you used in Step 5)
HF_TOKEN=hf_YOUR_TOKEN_HERE

# A random secret for the server's internal JWT auth.
# Anything ≥32 characters works. Generate one:
#   python3 -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET=paste_your_random_string_here

# Cache directory for compiled TT tensor weights.
# Without this, Wan2.2 re-converts PyTorch weights from scratch every cold start (~3 min extra).
# With it, the second start and beyond are ~3 min faster.
TT_DIT_CACHE_DIR=/home/container_app_user/cache_root/tt_dit_cache
```

The `HF_HOME` bind-mount (so the container sees your `~/.cache/huggingface` weights without
re-downloading them) is wired in automatically by `apply_patches.sh` in the next step —
you do not set it in `.env` yourself.

---

## Step 7 — Apply patches

> **Time: ~5 seconds**

The inference server might need seven targeted modifications before it will run correctly
on QB2's P300x2 topology. If these don't apply cleanly, chances you are you are working from a different version than we expect. One day hopefully these won't be needed for a good time.

A single script handles all of them:

```bash
cd ~/code/tt-local-generator
./bin/apply_patches.sh
```

With no arguments it patches `vendor/tt-inference-server` (set up in Step 1).
If `vendor/` somehow isn't there yet, it will call `setup_vendor.sh` automatically first.
Pass `--dev` to patch `~/code/tt-inference-server` instead (your dev checkout only).

What the 7 steps do:

1. **DiT patch files** — copies `patches/tt_dit/*.py` (DiT pipeline hotfixes) into
   the server's `patches/tt_dit/` directory.
2. **tt_dit bind-mount** — injects a Docker bind-mount block into `run_docker_server.py`
   so those hotfixes overlay the pipeline code inside the container (dev_mode only).
3. **Device config files** — copies `patches/media_server_config/constants.py` into
   the server's `patches/media_server_config/`. This overrides request timeouts and
   registers the P300x2 mesh dimensions.
4. **media_server_config bind-mount** — injects an unconditional bind-mount block so
   the config overrides are applied on every server start (no `--dev-mode` needed).
5. **HF_HOME bind-mount** — injects a Docker volume entry that mounts your
   `~/.cache/huggingface` read-only as `~/hf_home_cache` inside the container and
   sets `HF_HOME` accordingly. This is what prevents the container from attempting to
   download the 118 GB Wan2.2 weights on every start.
6. **SkyReels T2V model spec** — injects the `SkyReels-V2-DF-1.3B-540P` entry into
   `model_spec.py` (used if you switch to SkyReels mode).
7. **SkyReels I2V model spec** — injects the `SkyReels-V2-I2V-14B-540P` entry.

The script is idempotent — each step checks before modifying and backs up files it
changes. Safe to re-run after a `git pull` on the server tree.

Verify:

```bash
grep -q 'hf_home_cache' ~/code/tt-local-generator/vendor/tt-inference-server/workflows/run_docker_server.py \
    && echo "Patches applied OK"
```

---

## Step 8 — Start services

> **Time to launch: ~10 seconds**
> **Time until ready: ~9 minutes** (server warmup — measured on QB2, April 2026)

Start both services in the background with one command:

```bash
cd ~/code/tt-local-generator
./bin/best_experience_services.sh start
```

This launches:
- **Wan2.2 inference server** on port 8000 — runs on the 4 Blackhole chips
- **Qwen3-0.6B prompt server** on port 8001 — runs on CPU (~2.9 GB RAM)

Both start in non-blocking (`--gui`) mode; output goes to log files you can tail separately.

### Watch the warmup

The inference server goes through two phases before it accepts requests:

**Phase 1 — Container init and model loading** (~15 seconds):
```bash
tail -f ~/code/tt-local-generator/vendor/tt-inference-server/workflow_logs/docker_server/media_*_Wan2.2*.log
```
You will see lines like:
```
[server] Loading model...
[server] HF_HOME=/root/hf_home_cache  weights found in cache
[server] Application startup complete.   ← HTTP server is up (t+6s)
```

**Phase 2 — TT compilation and warmup inference** (~9 minutes):
```
[pipeline] Tracing DiT graph on P300x2 mesh...
[pipeline] Running warmup inference...      ← the warmup pass alone takes ~3.5 min
[pipeline] All devices are warmed up and ready.   ← YOU CAN NOW GENERATE
```

Real measurement: 525 seconds (8 min 45 s) from container start to "all devices ready"
(QB2, April 2026). The script message says "~5 min" — ignore it; the 9-minute figure
is from live logs.

The server is technically reachable at port 8000 after phase 1, but any request
made before the warmup completes will queue and wait. The GUI health indicator
(traffic light in the toolbar) turns green only when `model_ready=true` comes back
from the `/health` endpoint — at the end of phase 2.

On subsequent starts (with `TT_DIT_CACHE_DIR` set), the TT compilation is skipped
because the compiled weights are cached. Warmup drops to ~5–6 minutes.

Prompt server ready check:

```bash
curl -s http://localhost:8001/health | python3 -m json.tool
# → { "status": "ok", "model_ready": true }
```

---

## Step 9 — Launch the app

```bash
cd ~/code/tt-local-generator
./tt-gen
```

`tt-gen` is a thin launcher that ensures `/usr/bin/python3` is used (not a venv),
clears `GTK_MODULES` to prevent KDE/GTK4 segfaults, and raises the file-descriptor
limit before handing off to `app/main.py`.

You will see the main window with:
- A toolbar showing server health (green = ready, amber = warming up, red = down)
- A text prompt area
- The **Servers ▾** dropdown for starting/stopping services from the GUI

If the health indicator is still amber, wait for "All devices are warmed up and ready"
in the log before clicking Generate.

---

## Step 10 — Your first video

> **Time: ~5–7 minutes per video at steady state**
>
> Real measurements on QB2 (P300x2, 2026-04):
> - Warmup inference: 209 seconds
> - First external request: ~320 seconds
> - Steady-state range: 300–420 seconds (median ~370 s)

1. Click the **✨** button (or type your own prompt) to generate a starting prompt.
   The "✨ Generate prompt" button sends a request to the Qwen3-0.6B prompt server,
   which polishes a randomly assembled scene description. Results arrive in ~3 seconds.

2. Leave the defaults (**30 steps**, **seed: random**) for your first run.

3. Click **Generate**.

A pending card appears in the history panel showing an elapsed timer. The generation
is running on the TT hardware — you will not feel any load on the CPU.

When the video completes, it appears inline in the history panel. Hover to preview.
Click to open the full detail view with playback controls.

---

## Step 11 — Turn on TT-TV

Once you have at least one video in history, the **📺 Watch TT-TV** button in the
top-right toolbar becomes active. Click it.

TT-TV opens a borderless fullscreen window that:
- Cycles through your entire generated media library
- Shows a lower-third HUD with the prompt, model, pool size, and generation time
- Has a collapsible right sidebar with a channel selector, audience prompt input,
  and an auto-generate toggle

Enable **Auto-generate** in the sidebar to put the machine in self-sustaining
"attractor mode" — it continuously writes prompts and queues new generations,
growing the library while playing back what it already has. A disk space guard
pauses generation if free space drops below 18 GB.

On a machine with a pool of 187+ videos (a couple of sessions of generation),
TT-TV feels like a live generative art installation. Leave it running on a monitor
while you work.

---

## CLI quick reference

Once everything is running you can also drive the whole system from the terminal
without opening the GUI:

```bash
# Server status
./tt-ctl servers                          # live health of all managed services

# Start / stop
./tt-ctl start wan2.2                     # non-blocking
./tt-ctl start all                        # wan2.2 + prompt-server
./tt-ctl stop wan2.2

# Watch logs
./tt-ctl logs wan2.2 -f                   # tail the Wan2.2 server log
./tt-ctl logs prompt-server               # tail /tmp/tt_prompt_gen.log

# Generate from the CLI
./tt-ctl run "a red fox running through snow"
./tt-ctl run "close-up portrait" --steps 50 --seed 42

# History
./tt-ctl history 25                       # last 25 generation records

# Remote gallery (expose on port 8002 for a Mac client)
./tt-ctl serve-inventory
```

On a remote Mac (same LAN, running `tt-gen` pointed at QB2):
```bash
./tt-gen --server http://quietbox:8000
```

---

## Troubleshooting

### Health indicator stays amber forever

The warmup genuinely takes ~9 minutes on a cold start. Wait for:
```
[pipeline] All devices are warmed up and ready.
```
in the Wan2.2 log. If it never appears, check for errors:

```bash
./tt-ctl logs wan2.2 | grep -E "ERROR|assert|Traceback"
```

Common culprit: `apply_patches.sh` was not run, so the P300x2 device config
is missing and the container fails the topology assertion at startup.

### "Application startup complete" appears but requests time out

The HTTP server comes up 6 seconds into the start — but the model is still
loading. Any request made before the warmup finishes will sit in the queue.
The GUI correctly gates on `model_ready=true`; the CLI `./tt-ctl run` does not
wait — pipe it after confirming `./tt-ctl servers` shows green.

### Prompt generator shows "algo only" (no LLM polish)

The Qwen3-0.6B prompt server is not running. Start it:
```bash
./bin/start_prompt_gen.sh
curl -s http://localhost:8001/health
```
If you just started it, wait ~30 seconds for the model to load before the
health check reports `"model_ready": true`.

### Video plays in gallery but hover preview is blank (macOS remote client)

`libmedia-gstreamer.dylib` is missing from the Homebrew GTK4 bottle — the
backend was compiled out. Fix:
```bash
brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav
brew install --build-from-source gtk4
```

### Stale `.pyc` cache (line numbers in tracebacks don't match source)

```bash
find ~/code/tt-local-generator/app -name "*.pyc" -delete
find ~/code/tt-local-generator/app -name "__pycache__" -type d -exec rm -rf {} +
```

### Multiple `./tt-gen` invocations don't open new windows

The app uses `Gio.ApplicationFlags.NON_UNIQUE` so each launch should be
independent. If a stale process is holding a socket:
```bash
pgrep -a python3 | grep main.py
kill <pid>
./tt-gen
```

---

## What's next

Once you have the basic loop working, explore:

- **SkyReels-V2** — faster 540P video on Blackhole, good for rapid iteration:
  `./bin/start_skyreels.sh`
- **FLUX.1-dev** — high-quality static images:
  `./bin/start_flux.sh`
- **Wan2.2-Animate** — character animation from a motion reference video:
  `./bin/start_animate.sh`
- **Prompt corpus** — grow `app/prompts/markov_output.txt` by appending good
  prompts in `video|your prompt here` format; the Markov model is rebuilt
  fresh on every call so additions take effect immediately.
- **Batch generation** — `./tt-ctl queue add "prompt"` + `./tt-ctl queue run`
  to queue multiple jobs and drain them overnight.
- **KDE snapshot** — configure your desktop exactly how you want, then snapshot
  it: `~/tt-home/kde-snapshot/kde-snapshot.sh snapshot my-setup`

---

## Bonus: Bringing Up More Models — The WAN2.2 Family

If you got Wan2.2 running you are closer to many other models than you think.
This section explains why, documents what it actually takes to bring up a new one, and names
the models worth trying. It also covers TT-Lang and TT-Forge so you understand where the
TTNN inference stack sits in the larger Tenstorrent software ecosystem.

### The family tree

Every video model currently running on QB2 shares the same core building block:
**`WanTransformerBlock`** — the TTNN-accelerated attention + FFN unit implemented in
`tt-metal/models/tt_dit/models/transformers/wan2_2/transformer_wan.py` inside the
container image. When weight keys map to this block, a model is runnable with minimal
effort. When they don't, you are in new-territory work.

Here is the complete family as it stands on QB2:

| Model | HF checkpoint | Mode | Params | QB2 speed | Start script |
|---|---|---|---|---|---|
| Wan2.2-T2V-A14B | `Wan-AI/Wan2.2-T2V-A14B-Diffusers` | Text → Video | 14B | ~370 s/clip | `start_wan_qb2.sh` |
| Wan2.2-Animate-14B | `Wan-AI/Wan2.2-Animate-14B-Diffusers` | Video + Image → Video | 14B | ~156 s/clip | `start_animate.sh` |
| SkyReels-V2-DF-1.3B | `Skywork/SkyReels-V2-DF-1.3B-540P-Diffusers` | Text → Video | 1.3B | ~28 s/clip | `start_skyreels.sh` |
| SkyReels-V2-I2V-14B | `Skywork/SkyReels-V2-I2V-14B-540P` | Image + Text → Video | 14B | ~300 s/clip | `start_skyreels_i2v.sh` |
| Mochi-1-preview | `genmo/mochi-1-preview` | Text → Video | — | see docs | `start_mochi.sh` |

All of these share the same Docker image (`0.11.1-bac8b34`), the same `apply_patches.sh`
setup step, and the same overall start/stop/health pattern.

### Models worth trying next

These are real checkpoints that share the WAN transformer architecture and have a realistic
chance of running on QB2 with the existing TTNN pipeline code. None of them have been
attempted on this machine yet — they are uninvestigated leads:

**`Wan-AI/Wan2.2-I2V-14B-480P`** and **`Wan2.2-I2V-14B-720P`**
Wan's own image-to-video variants at two resolutions. Same 14B architecture as T2V but with
36 input channels (16 noisy + 16 VAE-encoded conditioning frame + 4 mask). The SkyReels I2V
bring-up work already built and tested the 36-channel path — these should be close to
drop-in. The 720P variant pushes sequence length significantly; expect longer warmup and
possibly OOM at high frame counts.

**`Skywork/SkyReels-V2-T2V-14B-540P`**
The 14B text-to-video sibling of the 1.3B that is already working. Same SkyReels checkpoint
format, same transformer architecture, 10× the parameters. Weight keys are identical to the
1.3B — just more layers and wider hidden dims. Expect 14B-scale warmup (~8–9 min) and
generation times similar to Wan2.2.

**`Wan-AI/Wan2.1-T2V-14B`**
The original WAN 2.1 in raw (non-diffusers) format. The key naming convention differs from
diffusers: `self_attn.q.weight` instead of `attn1.to_q.weight`, etc. The SkyReels I2V work
already wrote a `_map_raw_wan_i2v_to_diffusers()` key mapper for this exact format — the
same mapper, or a variant of it, should cover WAN 2.1 T2V. Interesting primarily as a
historical baseline, not a quality improvement over Wan2.2.

### What it takes to wire up a new model

SkyReels-V2-I2V-14B took two sessions and required fixing six separate bugs before it
produced a single frame. That is the realistic baseline for "new model, existing architecture."
Here is the general pattern:

**1 — Verify weight key compatibility**

```bash
# On the host (not inside Docker), with the weights already downloaded:
python3 - <<'EOF'
from safetensors import safe_open
import os, glob

ckpt_dir = os.path.expanduser("~/.cache/huggingface/hub/models--Skywork--SkyReels-V2-I2V-14B-540P/snapshots/latest/")
files = glob.glob(f"{ckpt_dir}/*.safetensors")[:1]
with safe_open(files[0], framework="pt") as f:
    for k in list(f.keys())[:20]:
        print(k)
EOF
```

Compare the key names against what `transformer_wan.py` expects. WAN2.2-family keys look
like `attn1.to_q.weight`, `scale_shift_table`, `ffn.net.0.proj.weight`. Raw WAN 2.1 format
uses `self_attn.q.weight`, `modulation`, `ffn.0.weight`. Both are mappable to the TTNN
model; the second requires a key-rename function. Extra keys from fine-tuning (e.g., SkyReels
FPS conditioning: `fps_embedding.*`) are silently dropped by `strict=False` and do not
require any architecture change.

**2 — Determine in_channels**

- `model_type="t2v"` → 16 channels (pure denoising)
- `model_type="i2v"` → 36 channels (16 noisy + 16 VAE latent + 4 mask)

Check the checkpoint's config file (`config.json` or `transformer/config.json`):
```bash
cat ~/.cache/huggingface/hub/.../transformer/config.json | python3 -m json.tool | grep in_channels
```

**3 — Write a pipeline hotpatch** (`patches/tt_dit/pipelines/<model>/pipeline_<model>.py`)

The pipeline file needs: a `TTNNTransformer` class wrapping the existing
`WanTransformer3DModel` TTNN model, a `cache_context(name)` no-op context manager, a
`_set_ar_attention(n)` no-op, a `Pipeline.create_pipeline()` factory, and a `__call__()`
that returns `np.ndarray` in shape `(1, T, H, W, C)` in [0, 1].

Look at `patches/tt_dit/pipelines/skyreels_v2/pipeline_skyreels.py` (T2V) and
`pipeline_skyreels_i2v.py` (I2V) as direct templates. They are well-commented because
they were written iteratively with an AI assistant, meaning every non-obvious decision has
an explanation in the code.

**4 — Write a runner hotpatch** (`patches/media_server_config/tt_model_runners/<model>_runner.py`)

Subclass `BaseMetalDeviceRunner` directly — **not** `TTDiTRunner`. The `TTDiTRunner`
subclass looks at `dit_runner_log_map` at module import time using the `MODEL_RUNNER`
environment variable as a key. If your model's runner string is not already in that dict
(which it won't be — it's compiled into the image), the import fails with a `KeyError`
before your code ever runs. `BaseMetalDeviceRunner` does not have this problem.

Set warmup timeout generously: 3600 s for sub-5B models, 5400 s for 14B models.
The 2-step warmup run does the TTNN kernel compilation for the full sequence length —
it is always slow the first time even with `TT_DIT_CACHE_DIR` set.

**5 — Update `patches/media_server_config/config/constants.py`**

Add entries to `SupportedModels`, `ModelNames`, `ModelRunners`, and `ModelConfigs`.
The `ModelConfigs` entry needs the mesh shape and a generous `inference_timeout_s` —
Wan2.2 at 500 steps can take over 700 seconds; anything under 900 will timeout on
long runs.

**6 — Register the runner in `runner_fabric.py`**

The patched `runner_fabric.py` replaces the image's version entirely (it is in
`patches/media_server_config/`, not `patches/tt_dit/`). Add your model's `ModelRunners`
key to the `AVAILABLE_RUNNERS` dict using a lazy import via `__import__()` so the import
does not happen at module load time.

**7 — Inject a `model_spec.py` entry**

Add a `ModelSpecTemplate` with `DeviceModelSpec` entries for `DeviceTypes.P150X4` and
`DeviceTypes.P300X2`. Set `min_disk_gb` conservatively — it gates whether `run.py` will
even attempt the start.

**8 — Write a start script**

Copy `bin/start_skyreels.sh` as a template. Pass `--dev-mode` to `python3 run.py` so the
`patches/tt_dit/` pipeline files are bind-mounted. Without `--dev-mode`, only the
`patches/media_server_config/` files are active (the constants and runner registration),
and the container will try to load the original upstream pipeline instead of yours.

### The bugs you will hit (and how to handle them)

These are the actual categories of failure encountered across three model bring-ups
(Mochi, SkyReels-1.3B, SkyReels-I2V-14B). They arrive in roughly this order:

**The board needs a reset after a previous crash**

Symptom: container starts, then `llrt.cpp` throws a hardware-level timeout waiting for
an ethernet core to become active again. This is not a software bug — it is the chip
still in a bad state from a prior OOM or abnormal exit.

```bash
tt-smi --reset       # full board reset; takes ~30 seconds
# then retry the server start
```

Check the board is clean before debugging any other failure:
```bash
tt-smi -s | python3 -c "import sys,json; d=json.load(sys.stdin); [print(x['status']) for x in d['device_info']]"
# all entries should be "idle" or "available"
```

**The Docker image does not know about your model**

Symptom: `run.py` exits cleanly with code 0 but no Docker container starts, or a container
starts but immediately exits because no runner is registered.

These failures are silent. The tell is `docker ps` showing nothing, or `docker logs` of the
last container showing `KeyError: 'tt-your-model'` at import time.

The fix is `patches/media_server_config/constants.py` + `runner_fabric.py` updates.
Always check `docker logs $(docker ps -lq)` first when a server start seems to do nothing.

**Mesh topology assertions fail silently**

Symptom: container starts, model loads, warmup inference begins, then crashes with
`AssertionError` somewhere deep in the pipeline.

The pipeline files inside the image were written for specific mesh shapes (usually `(2,4)`
or `(4,8)` for larger machines). A QB2's `(2,2)` shape is often simply not in the
assertion list, even when the underlying `device_configs[(2,2)]` dict entry is already
complete and correct.

Fix pattern:
```python
# Original (blocks (2,2)):
assert tuple(mesh_device.shape) in [(2, 4), (4, 8)]

# Fix:
assert tuple(mesh_device.shape) in [(2, 2), (2, 4), (4, 8)]
```

This fix goes in `patches/tt_dit/` and requires `--dev-mode`. See `docs/mochi-qb2-setup.md`
for the full Mochi example. The SkyReels pipeline avoided this class of bug entirely by
deriving the parallel config from `mesh_device.shape` dynamically rather than looking it
up in a fixed dict.

**`constants.py` is missing a symbol that `settings.py` imports**

Symptom: `ImportError: cannot import name 'SDXL_VALID_IMAGE_RESOLUTIONS' from 'config.constants'`.

The patched `constants.py` replaces the image's version entirely. If you forget to carry
forward any symbol that `settings.py` (or any other server file) imports from `constants.py`,
every server start will fail at this import. The fix is to diff the image's original
`constants.py` (extractable from the container) against your patch and ensure you have not
dropped any exported names.

```bash
# Extract the original constants.py from the container:
docker run --rm --entrypoint cat \
    ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34 \
    /home/container_app_user/tt-metal/server/config/constants.py \
    > /tmp/constants_original.py

diff /tmp/constants_original.py \
    ~/code/tt-local-generator/patches/media_server_config/config/constants.py | less
```

**The pipeline has no `cache_context` method**

Symptom: `AttributeError: 'TTNNWanTransformer3DModel' object has no attribute 'cache_context'`.

The diffusers pipeline wraps the transformer in `with transformer.cache_context("cond"):` /
`with transformer.cache_context("uncond"):`. The TTNN model does not implement this —
it has no PyTorch KV cache. The fix is a no-op:

```python
from contextlib import contextmanager

class TTNNTransformer:
    @contextmanager
    def cache_context(self, name: str):
        yield   # TTNN has no KV cache; this is a no-op
```

**`_execution_device` property crashes on a non-PyTorch model**

Symptom: `AttributeError: '_execution_device'` during pipeline call setup.

The diffusers `Pipeline` base class tries to infer the compute device by inspecting
PyTorch model parameters. The TTNN transformer has none. Monkey-patch it:

```python
from diffusers import SkyReelsV2ImageToVideoPipeline
try:
    _ = pipe._execution_device
except AttributeError:
    SkyReelsV2ImageToVideoPipeline._execution_device = property(
        lambda self: torch.device("cpu")
    )
```

**Python scoping: `UnboundLocalError` after `from module import x`**

A subtle one. Inside a function body, if you write `from module import x` conditionally,
Python marks `x` as a local variable for the *entire function scope*. Any reference to `x`
before the import line (e.g., in an `if` branch) raises `UnboundLocalError` even though `x`
is defined at module level.

This bit the SkyReels runner during bring-up. The fix is to always import at the top of the
function body, or use `importlib.import_module()` inline.

### Working with an AI assistant on model bring-up

An AI assistant (including Claude) can be genuinely useful for this work, but there are
specific places where it will confidently give you wrong answers. Know the failure modes:

**Trust it for: structure and pattern**
The scaffolding for a new pipeline file, runner file, and constants.py entries is highly
formulaic. An AI can generate correct first drafts of all of it if you give it existing
working examples as context. Feed it `pipeline_skyreels.py` and `skyreels_runner.py` and
say "write one for model X with these differences" — the result will be 80–90% correct.

**Verify before trusting: weight key names**
AI models hallucinate checkpoint key names with high confidence. Always run the actual
inspection script above. Key names differ between diffusers format, raw WAN 2.1 format, and
each model's fine-tuning additions. This is the #1 source of bugs that look correct in the
code but fail at `load_torch_state_dict()` with `unexpected key` or `missing key` errors.

**Never trust it for: exact Python version and import behavior**
The `UnboundLocalError` scoping bug above is the classic example. AI assistants often
miss Python's lexical scoping rule for function-local names. Similarly, "import at module
load time vs import at function call time" bugs are easy to generate and hard to spot in
review. Always check: does importing this file cause a KeyError or AttributeError even
before a server request arrives?

**Trust but verify: mesh shape assumptions**
If you ask an AI to "make this work on QB2," it may add `(2,2)` to an assertion list but
miss that the corresponding `device_configs[(2,2)]` dict needs to be populated too, or vice
versa. Check both sides of every topology-gated branch.

**Always verify with `docker logs`**
A server that "starts" but does nothing is the silent failure mode. Always:
```bash
# While starting a new model for the first time:
docker logs -f $(docker ps -lq)
```
The container logs and the workflow log file (`workflow_logs/docker_server/media_*.log`) are
two separate streams. The former shows import errors and container-level failures; the latter
shows server-level activity after the container is up. Both matter.

### Reality check on the effort involved

Mochi required 2 hotpatches (mesh assertion + VAE config) — a few hours.
SkyReels-1.3B required 6 bugs fixed across constants, runner registration, model ID matching,
an import scoping bug, a missing server state branch, and a `cache_context` no-op — about
two sessions with an AI assistant.
SkyReels-I2V-14B additionally required a 300-line key mapper for the raw WAN 2.1 checkpoint
format and a workaround for the `_execution_device` crash — one more session.

None of these required modifying the TTNN model code or rebuilding the Docker image.
That is the leverage the hotpatch system gives you: you are operating at the Python
orchestration layer, not the kernel or compiler layer.

The uninvestigated models listed above — Wan2.2-I2V, SkyReels-14B T2V — should be
closer to the Mochi end of that spectrum than the SkyReels-I2V end, because the 36-channel
I2V path and the WAN checkpoint formats are already implemented.

---

## Prompting Wan2.2 — Getting Good Results

Getting the hardware running is the hard part. Getting *interesting* results out of it is
the fun part. This section covers the generation parameters, the three-tier prompt pipeline
that runs alongside the model, and how to push further into automated generation.

### Generation parameters

The main window exposes the controls that matter most. Here is what each one does:

**Steps** (default: 30)
The number of denoising iterations. Wan2.2 at 30 steps produces good results; 50 steps
gives visibly better temporal coherence and detail at the cost of ~60% more generation
time. Values below 20 produce noticeably degraded motion. There is no benefit beyond 80.

```
20 steps: ~250 s  — fast iteration, slightly rough
30 steps: ~370 s  — default, good quality
50 steps: ~600 s  — best quality, use for keepers
```

**Seed** (default: random)
Fix the seed to reproduce a result exactly or to run small variations on a prompt.
Leave it random for TT-TV's auto-generate loop (you want variety in the pool).

To find the seed of a video you already generated: click it in the history panel, open
the detail view — seed, steps, and model are all shown and copyable.

**Model selector**
Switch between Wan2.2, SkyReels-1.3B, and others from the toolbar. Each model requires
its own server to be running (`./bin/start_wan_qb2.sh`, `./bin/start_skyreels.sh`).
SkyReels-1.3B is useful for rapid prompting experiments: at ~28 s per clip you can
iterate a dozen times while Wan2.2 is doing one pass.

### What Wan2.2 responds to

Wan2.2 is a text-to-video model trained on cinematic footage. It responds best to
prompts that describe a **single contained action in a single location** — the way a
director would describe one shot, not one film. The model generates 4–6 second clips;
anything that implies narrative arc or scene change is either ignored or creates
incoherent motion.

Things that work well:
- Specific subjects doing one concrete thing ("a crane lowers a steel beam onto a dock")
- Camera language ("rack focus from foreground rust to distant ship, golden hour")
- Named visual styles or directors ("in the style of a late 90s Tarkovsky wide")
- Unusual specificity ("a woman in a yellow raincoat closing a 1968 station wagon door in light rain")
- Texture and material descriptors ("oxidized copper, soft overcast light, macro lens")

Things that work less well:
- Abstract concepts without visual anchor ("the feeling of loss")
- More than one subject doing more than one thing
- Any text or legible writing in the frame
- Rapid cuts or scene transitions (the model does not do editing)
- Arbitrary combinations of unrelated visual elements

**The most important prompt rule: describe what is in the frame, not what you feel about it.**
"Melancholy" is invisible. "A woman looking out a rain-streaked window, forehead against
the glass" is not.

### The three-tier prompt system

The **✨ Generate prompt** button in the toolbar is wired to a local pipeline that runs
entirely on your CPU, independent of the video server. It has three tiers that activate
in sequence:

**Tier 1 — Algorithmic** (always available)
`app/word_banks.py` contains dozens of curated lists: subjects drawn from literary
traditions (Steinbeck working-class figures, PKD suburban uncanny, Octavia Butler near-future
characters, Brautigan pastoral strangeness), plus camera moves, settings, moods, lighting,
and director styles. The generator samples independently from each list, assembles a slug
like `a cannery worker hosing down a concrete floor at dawn, tracking shot, diffuse marine layer`,
and hands it to the next tier. Because sampling is independent, unusual combinations
arise naturally — and unusual beats generic every time.

**Tier 2 — Markov chain** (requires `markovify`, enabled with `--mode markov`)
A bigram model trained on `app/prompts/markov_seed.txt` (the curated seed corpus)
plus anything you have accumulated in `app/prompts/markov_output.txt`. It recombines
phrases at the sentence level rather than the word level, producing outputs like
`a jalopy loaded with everything a family owns, springs showing, push-in on a burning doorway`
— phrases that would not come from pure random sampling because they carry syntactic rhythm
from the training corpus. The model is rebuilt fresh on every call, so any new lines you
append to `markov_output.txt` are effective immediately.

**Tier 3 — LLM polish** (requires the prompt server on port 8001)
The slug from tier 1 or 2 is sent to Qwen3-0.6B with a tightly scoped system prompt:
*rewrite as one vivid sentence, keep every element, add nothing, cut filler, hard limit 25 words*.
The LLM's job is fluency, not selection. The randomness is already locked in by tiers 1/2;
the LLM just makes the output sound like something a human would write on a shot list.
At 19 tok/s on the Ryzen 7 9700X it returns in about 3 seconds.

If the prompt server is not running, tier 3 is silently skipped and the tier 1/2 slug is
used directly. The result is slightly rougher but completely usable.

### Prompt CLI — useful invocations

```bash
cd ~/code/tt-local-generator

# Default: algo slug + LLM polish, video type
python3 app/generate_prompt.py

# Five prompts, Markov mode, no polish (fast, shows raw recombinations)
python3 app/generate_prompt.py --count 5 --mode markov --no-enhance

# Always use a specific director style
python3 app/generate_prompt.py --director "Andrei Tarkovsky"

# Raise director-style probability (default 33% → 80%)
python3 app/generate_prompt.py --director-prob 0.8

# SkyReels-specific prompt bank (cinematic nature/atmosphere optimized)
python3 app/generate_prompt.py --type skyreels

# Commercial product spot bank
python3 app/generate_prompt.py --type commercial

# Plain text output — pipe directly to tt-ctl
python3 app/generate_prompt.py --raw | xargs -I{} ./tt-ctl run "{}"
```

### Growing the corpus

`app/prompts/markov_output.txt` is gitignored — it accumulates machine-specific
good outputs. Append any prompt that produced a result worth revisiting:

```bash
echo "video|a woman in a yellow raincoat closing a 1968 station wagon door in light rain" \
    >> app/prompts/markov_output.txt
```

The tagged format (`video|`, `image|`, `animate|`, `skyreels|`) routes lines into
the correct prompt-type pool. Untagged lines go into every pool. The Markov model
becomes noticeably more interesting once the output file has 30–40 entries.

`app/prompts/markov_seed.txt` is checked into git and contains the curated starting
corpus. Edit it directly to permanently change the baseline style.

### Toward automated generation

The prompt pipeline is already wired into TT-TV's auto-generate loop — enable it in
the TT-TV sidebar and the system runs indefinitely without human input. But there is
more you can do from the CLI:

**Batch overnight with a theme**

The `guided_generate()` function in `generate_prompt.py` takes a theme string and has
the LLM write a full prompt around it, rather than just polishing an algorithmic slug:

```bash
# Generate 20 prompts on a theme and queue them all
for i in $(seq 20); do
    python3 app/generate_prompt.py --raw
done | while read prompt; do
    ./tt-ctl queue add "$prompt"
done
./tt-ctl queue run   # drain overnight
```

**Seed-locked variation set**

Pick a prompt and seed you like, then run it at increasing step counts to see how
quality scales — useful for knowing when 30 steps is enough vs when you need 50:

```bash
PROMPT="a crane lowers a steel beam onto a dock, late afternoon"
for steps in 20 30 50; do
    ./tt-ctl run "$PROMPT" --steps $steps --seed 42
done
```

**Director style sweep**

Sample one subject across multiple director styles to see which aesthetic matches it:

```bash
SUBJECT="an old man feeding pigeons in an empty plaza"
for director in "Tarkovsky" "Wong Kar-wai" "Jacques Demy" "early Kubrick"; do
    python3 app/generate_prompt.py --raw --director "$director" \
        | sed "s/^/$SUBJECT, /" \
        | xargs -I{} ./tt-ctl run "{}"
done
```

**What's further out**

The prompt server (`app/prompt_server.py`) exposes a full OpenAI-compatible chat
completions endpoint on port 8001. Anything that can POST to that API can participate
in generation workflows: shell scripts, notebooks, small Python tools, or a properly
prompted Claude session driving `tt-ctl` directly.

The history (`~/.local/share/tt-video-gen/history.json`) is plain JSON with every
generation's prompt, seed, steps, model, duration, and file path. Mining it for
patterns — which subjects generated longest, which prompts the auto-loop favored,
which step counts clustered — is a natural next project.
