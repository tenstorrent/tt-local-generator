# tt-local-generator

A GTK4 desktop UI for generating videos and images with Tenstorrent hardware.

**Supported models:**

| Mode | Model | Hardware |
|------|-------|----------|
| Video | [Wan2.2-T2V-A14B-Diffusers](https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B-Diffusers) | QB2 (P300x2) |
| Video | [Mochi-1-preview](https://huggingface.co/genmo/mochi-1-preview) | QB2 (P300x2) |
| Video | [SkyReels-V2-I2V-14B-540P](https://huggingface.co/Skywork/SkyReels-V2-I2V-14B-540P) | Blackhole (P300X2) — image-to-video |
| Image | [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) | 4× p300c |
| Animate | [Wan2.2-I2V-A14B-Diffusers](https://huggingface.co/Wan-AI/Wan2.2-I2V-14B-720P-Diffusers) | CPU/CUDA (Phase 1) |

All inference runs via a local [tt-inference-server](https://github.com/tenstorrent/tt-inference-server) Docker container on port 8000.

---

## Directory layout

```
tt-local-generator/
  app/                   ← all Python source files
    main.py              ← Gtk.Application entry point
    main_window.py       ← all GTK4 widgets
    worker.py            ← background generation (no GTK imports)
    api_client.py        ← HTTP client for inference server
    server_manager.py    ← start/stop/health for all managed services
    history_store.py     ← persistent JSON history
    generate_prompt.py   ← three-tier prompt generator CLI
    prompt_client.py     ← thin wrapper: polish vs. full generation
    prompt_server.py     ← FastAPI server (Qwen3-0.6B, port 8001)
    word_banks.py        ← prompt word banks + sampling helpers
    attractor.py         ← AttractorWindow — kiosk loop
    chip_config.py       ← chip definitions from config/
    app_settings.py      ← persistent settings
    assets/              ← tenstorrent.png icon, .desktop entry
    prompts/             ← Markov seed corpus, system prompt
    config/              ← prompt_chips.yaml
  bin/                   ← shell scripts
    start_wan_qb2.sh     ← Wan2.2 on P300x2 (default)
    start_wan.sh         ← Wan2.2 on P150x4
    start_mochi.sh       ← Mochi-1 on QB2
    start_skyreels_i2v.sh← SkyReels-V2-I2V-14B-540P (Blackhole P300X2) — I2V, --stop/--restart
    start_flux.sh        ← FLUX.1-dev image server
    start_animate.sh     ← Wan2.2-Animate (CPU/CUDA Phase 1)
    start_prompt_gen.sh  ← Qwen3-0.6B prompt server (CPU, port 8001)
    apply_patches.sh     ← apply patches/ to vendor/ or ~/code/tt-inference-server
    best_experience_services.sh  ← start Wan2.2 + prompt server together
  patches/               ← hotpatch files applied by apply_patches.sh
    media_server_config/ ← constants.py, runner, and domain overrides
    tt_dit/              ← pipeline fixes incl. SkyReels-V2 (dev_mode only)
  vendor/                ← shallow clone of tt-inference-server (gitignored)
    tt-inference-server/ ← pinned to VENDOR_SHA, patched by apply_patches.sh
    VENDOR_SHA           ← SHA of the vendored commit
  docker/                ← Docker image archive (Git LFS)
    tt-media-inference-server-0.11.1-bac8b34.tar.gz
    README.md
  tests/                 ← pytest test suite (83 tests)
  tt-gen                 ← launcher: starts the GUI
  tt-ctl                 ← CLI: status, history, start/stop services
  requirements.txt
```

---

## Quick start

```bash
# 1. Clone and set up
git clone https://github.com/tsingletaryTT/tt-local-generator.git ~/code/tt-local-generator
cd ~/code/tt-local-generator
./setup_ubuntu.sh

# 2. Apply patches to the vendored tt-inference-server
./bin/apply_patches.sh

# 3. Start the inference server (QB2 / P300x2)
./bin/start_wan_qb2.sh         # Wan2.2 — wait ~5 min for "Application startup complete"

# 4. Launch the UI
./tt-gen

# Or start everything at once
./bin/best_experience_services.sh
```

### Alternate start (P150x4)

```bash
./bin/start_wan.sh             # Wan2.2 on P150x4
```

### SkyReels I2V (image-to-video, Blackhole P300X2)

```bash
./bin/start_skyreels_i2v.sh                  # start — weight loading takes 30–60 min
./bin/start_skyreels_i2v.sh --stop           # stop running container
./bin/start_skyreels_i2v.sh --restart        # stop + start fresh (useful after a crash)
./bin/start_skyreels_i2v.sh --device p150x4  # target P150X4 mesh instead
```

> **Before first run:** download weights (~58 GB) and apply patches:
> ```bash
> huggingface-cli download Skywork/SkyReels-V2-I2V-14B-540P --local-dir-use-symlinks False
> huggingface-cli download Wan-AI/Wan2.2-T2V-A14B-Diffusers   --local-dir-use-symlinks False
> ./bin/apply_patches.sh
> ```

### Stop any server

```bash
./bin/start_wan_qb2.sh --stop
./bin/start_wan.sh --stop
./bin/start_skyreels_i2v.sh --stop
```

---

## CLI tool (`tt-ctl`)

```bash
# Overview
./tt-ctl status           # server health, queue depth, history summary (newest + oldest)
./tt-ctl servers          # live status of every managed service
./tt-ctl server           # server detail + active job list
./tt-ctl history          # 10 most recent generations (newest first)
./tt-ctl history 25       # show last 25 instead
./tt-ctl recover          # list server jobs not in local history

# Run a generation now (blocking)
./tt-ctl run "a red fox"
./tt-ctl run "a red fox" --steps 50 --seed 42 --model mochi
./tt-ctl run "a red fox" --neg "blurry, bad quality"
# --model choices: video | image | animate | skyreels | mochi

# Service management
./tt-ctl start wan2.2         # start the Wan2.2 server (non-blocking by default)
./tt-ctl start prompt-server  # start the Qwen prompt server
./tt-ctl start all            # start the recommended set (wan2.2 + prompt-server)
./tt-ctl stop  wan2.2
./tt-ctl restart wan2.2
./tt-ctl start wan2.2 --blocking   # wait for the script to exit
# Service keys: wan2.2  mochi  skyreels  flux  animate  prompt-server  all

# Queue management
./tt-ctl queue                          # list pending items
./tt-ctl queue add "prompt text"        # append a prompt (model: video)
./tt-ctl queue add "prompt" --model image --steps 50 --seed 7 --neg "blurry"
./tt-ctl queue clear                    # empty the queue
./tt-ctl queue run                      # drain and execute the queue (blocking)
./tt-ctl queue run --dry-run            # preview without running

# Prompt generation (algorithmic / Markov / Qwen-guided)
./tt-ctl generate                       # generate 1 prompt and run it
./tt-ctl generate "mountain landscape"  # guided by a theme (Qwen polishes if up)
./tt-ctl generate --count 5             # generate and run 5 prompts
./tt-ctl generate --type image --mode markov --no-enhance
./tt-ctl generate --count 3 --seed 10 --steps 40 --queue-only
# --type choices: video | image | skyreels | animate
# --mode choices: algo (default) | markov
# --queue-only: stage prompts in queue.json without running them
```

---

## Features

### Generating

- **Text-to-video with Wan2.2** — 5-second 720p clips from a text prompt. The server warms in
  under 5 minutes on QB2 after the first cold load; browse your existing gallery while it
  starts — no idle waiting before you get value.
- **Text-to-image with FLUX.1-dev** — high-quality still images on the same hardware.
- **Animate** — bring any character image to life: supply a motion video and a character PNG,
  and Wan2.2-Animate-14B drives the character through the motion pattern.
- **Seed image** — attach a reference photo or frame to guide color palette, composition, and
  style continuity between clips.
- **Prompt queue** — write the next prompt while a generation runs; the queue drains
  automatically so the GPU stays busy. Survives crashes and restarts.
- **Disk space guard** — generation pauses automatically when free space drops below 18 GB.

### Writing prompts

- **✨ Inspire me** — one click to a polished, varied prompt every time. Three-tier system:
  algorithmic word-bank sampling → Markov chain → Qwen3-0.6B LLM polish (when the prompt
  server is running). Seed it with your own rough idea; works entirely offline in algo mode.
- **Style chips** — one-click modifiers for camera moves, lighting, mood, and quality,
  appended to your prompt without retyping anything.

### Browsing and organizing

- **Persistent gallery** — every generation saved to `~/.local/share/tt-video-gen/` with a
  metadata sidecar (prompt, seed, steps, generation time). Nothing lost across restarts.
- **Responsive card grid** — hover to preview video inline; click for the full detail panel.
  Re-flows automatically as the window resizes.
- **Full-screen player** — `F` to go fullscreen, `Space` to pause, `Esc` to close.
- **Iterate / remix** — ↺ repopulates the prompt panel from any past generation so you can
  tweak one parameter and re-run without retyping the whole prompt.
- **Playlists** — create named collections and add videos or images from the gallery.
  Each playlist doubles as a TT-TV channel.
- **Trash / delete** — 🗑 removes a card from history and deletes the files in one step.

### TT-TV (Attractor / Kiosk Mode)

- **Looping media player** — borderless kiosk window that cycles your generated media with
  channel-change flash transitions and a broadcast-style lower-third HUD (prompt, model,
  pool size). Newly finished generations appear on-screen within a few slots of completing.
- **Channels** — switch between playlists inside TT-TV without leaving fullscreen; each
  channel remembers its own auto-generate setting.
- **Audience prompt input** — a sidebar entry lets anyone type a prompt that jumps to the
  front of the generation queue — live crowd participation during a demo.
- **Auto-generate loop** — TT-TV continuously writes prompts and queues new generations so
  the pool grows on its own. Toggle the auto-gen switch to pause it; playback keeps running.

### Server and hardware

- **Context-aware server start** — the Servers ▾ toolbar dropdown launches the right server
  for the active tab (Wan2.2 for Video, FLUX for Image, Wan2.2-Animate for Animate).
- **Server log panel** — pulsing progress bar and phase label during startup; expand "▸ Log"
  for raw container output.
- **Job recovery** — ⟳ Recover re-attaches to any server jobs that survived a UI restart.
- **Live chip telemetry** — the status bar shows AI clock (MHz), temperature, and total power
  draw from `tt-smi` in real time.
- **CLI companion (`tt-ctl`)** — `./tt-ctl run "a prompt"` submits a job from the terminal;
  `./tt-ctl status` shows server health, chip temps, and recent history. Scriptable.

---

## Requirements

- Ubuntu 22.04+ (24.04 recommended)
- Tenstorrent QB2 (P300x2) for Wan2.2 and Mochi-1; 4× p300c for FLUX
- Docker
- System `python3` with `python3-gi` (GTK4 bindings — not pip-installable)
- `ffmpeg`, GStreamer (`libgtk-4-media-gstreamer`, `gstreamer1.0-libav`)

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 ffmpeg \
    libgtk-4-media-gstreamer gstreamer1.0-libav
```

Optional for LLM prompt polish:
```bash
pip install -r requirements.txt   # requests, markovify, pytest
# Qwen model (~1.2 GB) downloads automatically on first ./bin/start_prompt_gen.sh
```

---

## Prompt generator (CLI)

```bash
cd app/
python3 generate_prompt.py                          # algo + LLM polish, video
python3 generate_prompt.py --type image --mode markov
python3 generate_prompt.py --count 5 --no-enhance  # algo only, five prompts
python3 generate_prompt.py --raw                    # plain text (no JSON wrapper)
```

JSON output: `{"prompt": "...", "type": "video|image|animate|skyreels", "source": "llm|markov|algo", "slug": "..."}`

```bash
./bin/start_prompt_gen.sh          # start Qwen server (port 8001)
./bin/start_prompt_gen.sh --stop   # stop
curl -s http://localhost:8001/health  # → {"status":"ok","model_ready":true}
```

---

## Vendored `tt-inference-server`

The repo includes a shallow clone of `tt-inference-server` at a known-good commit:

```bash
cat vendor/VENDOR_SHA          # pinned SHA
./bin/apply_patches.sh         # apply patches/ to vendor/
```

Patches are applied in seven steps:
1. Copy `patches/tt_dit/` into `vendor/` (dev_mode pipeline fixes, incl. SkyReels-V2-I2V)
2. Patch `run_docker_server.py` with dev_mode bind-mounts
3. Copy `patches/media_server_config/` into `vendor/` (constants, runner, and domain overrides)
4. Patch `run_docker_server.py` with unconditional `constants.py` bind-mount
5. Inject `HF_HOME` bind-mount block into `run_docker_server.py`
6. Inject SkyReels T2V `ModelSpecTemplate` into `workflows/model_spec.py`
7. Inject SkyReels I2V `ModelSpecTemplate` into `workflows/model_spec.py`

The `.env` in `vendor/tt-inference-server/` controls Docker container environment (including `HF_HUB_OFFLINE=1` and `TT_DIT_CACHE_DIR` for weight caching across restarts).

---

## Running tests

```bash
/usr/bin/python3 -m pytest tests/ -q   # 107 tests, all should pass
```

---

## License

Apache 2.0
