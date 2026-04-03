# tt-local-generator

A GTK4 desktop UI for generating videos and images with Tenstorrent hardware.

**Supported models:**

| Mode | Model | Hardware |
|------|-------|----------|
| Video | [Wan2.2-T2V-A14B-Diffusers](https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B-Diffusers) | P150x4 |
| Video | [Mochi-1-preview](https://huggingface.co/genmo/mochi-1-preview) | QB2 (P300x2) |
| Image | [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) | 4× p300c |
| Animate | [Wan2.2-I2V-A14B-Diffusers](https://huggingface.co/Wan-AI/Wan2.2-I2V-14B-720P-Diffusers) (animate a character from motion video) | CPU/CUDA (Phase 1) |

All inference runs via a local [tt-inference-server](https://github.com/tenstorrent/tt-inference-server) Docker container on port 8000.

---

## Features

### Generation
- **Video** — text-to-video with Wan2.2 or Mochi-1; choose model in the Video tab
- **Image** — text-to-image with FLUX.1-dev
- **Animate** — drive a character image with a motion video (Wan2.2-Animate-14B); choose Animation or Replacement mode
- **Seed image** — attach a reference image to guide Wan2.2's motion and composition
- **Style chips** — one-click prompt modifiers (camera moves, lighting, style, quality)
- **Prompt queue** — write the next prompt while a generation is running; jobs execute in sequence automatically
- **Queue persistence** — the queue is saved to disk on every change; if the app crashes or is restarted, pending items reload and generation resumes automatically
- **Disk space guard** — generation is blocked (with a status warning) when the output drive has less than 18 GB free; Attractor mode pauses and retries every 60 s

### Prompt generator
- **✨ Inspire me** — always works: three-tier system (algorithmic word banks → Markov chain → optional LLM polish)
- **Algo-only** mode (dot shows `⬤ algo only`) when the Qwen server is offline — still generates varied prompts
- **LLM polish** when `start_prompt_gen.sh` is running (dot turns green `⬤ ready`) — Qwen3-0.6B on CPU, port 8001
- **Seed-text inspire** — type a rough idea in the prompt box, click Inspire; LLM polishes it if available, returns unchanged otherwise

### Gallery & playback
- **Responsive gallery** — card grid re-flows automatically as the window resizes
- **Inline video player** — hover a card to preview; click for full detail panel
- **Full-size player** — F for true fullscreen, Space to play/pause, Esc to close
- **Trash / delete** — 🗑 on each card removes the generation from history and deletes files
- **Iterate** — ↺ re-populates the prompt panel from any past generation

### Attractor Mode
- **✦ Attractor** toolbar button opens a borderless kiosk window
- Loops through all media in the gallery with crossfades
- Continuously generates new prompts and queues new generations in the background
- Sidebar shows current prompt, model, and live status (`🔄 generating`, `⏳ N queued`, `⬤ idle`)
- Back-pressure prevents the queue from growing beyond 2 items

### Server & setup
- **Server control** — ▶ Start / ■ Stop the inference server from the UI; a pulsing progress bar and phase label show startup progress ("Docker container starting…" → "Loading model weights…" → "Server ready!"); expand "▸ Log" to see raw output
- **Context-aware start** — Video tab starts Wan2.2 or Mochi-1; Animate tab starts Wan2.2-Animate; Image tab starts FLUX
- **Generation history** — all outputs saved to `~/.local/share/tt-video-gen/` and reloaded on launch
- **Job recovery** — re-attach to server jobs that survived a UI crash; recovery works even while a queue is active (recovery jobs are inserted at the front of the queue)
- **App icon + desktop entry** — "TT Generator" in GNOME Activities / KDE launcher

---

## Quick start

```bash
# 1. One-shot setup (Ubuntu 24.04)
git clone https://github.com/tsingletaryTT/tt-local-generator.git ~/code/tt-local-generator
cd ~/code/tt-local-generator
./setup_ubuntu.sh

# 2. Launch the UI
/usr/bin/python3 main.py

# 3. Start a server (or use the ▶ Start button in the UI)
./start_wan.sh          # Wan2.2 video  — wait ~5 min for "Application startup complete"
./start_mochi.sh        # Mochi-1 video — requires QB2 / P300x2
./start_flux.sh         # FLUX image
./start_animate.sh      # Wan2.2-Animate character animation
./start_prompt_gen.sh   # Qwen3-0.6B prompt polish server (optional, CPU)

# Stop any server
./start_wan.sh --stop
```

See **[GUIDE.md](GUIDE.md)** for the full walkthrough: server setup, API tour, troubleshooting, chaining clips, prompt generation, and configuration reference.

For Mochi-1 on QB2 (P300x2) specifically, see **[docs/mochi-qb2-setup.md](docs/mochi-qb2-setup.md)**.

---

## Requirements

- Ubuntu 22.04+ (24.04 recommended)
- Tenstorrent accelerator — P150x4 for Wan2.2; QB2 (P300x2) for Mochi-1; 4× p300c for FLUX
- [tt-inference-server](https://github.com/tenstorrent/tt-inference-server) configured
- System `python3` with `python3-gi` (GTK4 bindings — **not pip-installable**, must be system packages)
- `ffmpeg`, GStreamer (`libgtk-4-media-gstreamer`, `gstreamer1.0-libav`)

Optional for LLM prompt polish:
```bash
pip install transformers torch accelerate  # ~1.2 GB model downloads on first start
```

---

## Architecture

### UI and generation

| File | Purpose |
|------|---------|
| `main.py` | `Gtk.Application` entry point |
| `main_window.py` | All GTK4 widgets: `MainWindow`, `ControlPanel`, `GenerationCard`, `GalleryWidget`, `DetailPanel` |
| `attractor.py` | `AttractorWindow` — kiosk loop, crossfade player, background generation |
| `worker.py` | `GenerationWorker` / `ImageGenerationWorker` — pure Python, no GUI imports |
| `api_client.py` | HTTP client for the inference server (port 8000) |
| `history_store.py` | Persistent JSON history + file path management |

### Prompt generation

| File | Purpose |
|------|---------|
| `generate_prompt.py` | Three-tier generator: algo → Markov → LLM polish; CLI and importable |
| `word_banks.py` | All word-bank lists + sampling helpers (subjects, actions, settings, etc.) |
| `prompt_client.py` | Thin wrapper — seed-text polish vs. full generation; no GTK deps |
| `prompt_server.py` | FastAPI server hosting Qwen3-0.6B on port 8001 |
| `start_prompt_gen.sh` | Start/stop the prompt server |
| `prompts/markov_seed.txt` | Seed corpus for the Markov chain (tagged by type) |
| `prompts/prompt_generator.md` | System prompt for interactive LLM use |

### Inference servers

| File | Purpose |
|------|---------|
| `start_wan.sh` | Wan2.2-T2V server (`--stop`, `--gui` flags) |
| `start_mochi.sh` | Mochi-1 server — applies QB2 hotpatches automatically |
| `start_flux.sh` | FLUX.1-dev image server (`--stop`, `--gui`, `--schnell` flags) |
| `start_animate.sh` | Wan2.2-Animate server (Phase 1: Diffusers CPU/CUDA path) |
| `patches/` | Hotpatch files applied by `start_mochi.sh` for QB2 compatibility |
| `apply_patches.sh` | Helper invoked by `start_mochi.sh` to patch files inside the container |

### Setup

| File | Purpose |
|------|---------|
| `setup_ubuntu.sh` | One-shot Ubuntu 24.04 dependency installer (Docker image, desktop entry) |
| `assets/` | `tenstorrent.png` icon, `ai.tenstorrent.tt-video-gen.desktop` |
| `requirements.txt` | pip-installable deps (`requests`; GTK4 bindings documented separately) |

---

## Prompt generator details

### Three-tier design

1. **Algorithmic** (always available) — `word_banks.py` has every category as a Python list; `generate_prompt.py` picks independently from each slot so diversity is guaranteed regardless of whether the LLM is running.

2. **Markov** (requires `markovify`) — trained on `prompts/markov_seed.txt`; produces novel sentence-level recombinations. Falls back to algorithmic if the corpus is too small.

3. **LLM polish** (requires `start_prompt_gen.sh` running) — sends the tier-1/2 slug to Qwen3-0.6B with a short polishing instruction. The LLM only improves fluency — element selection is already locked in. Falls back gracefully to the raw slug if the server is down.

### CLI usage

```bash
python3 generate_prompt.py                        # default: algo + LLM polish, video
python3 generate_prompt.py --type image --mode markov
python3 generate_prompt.py --count 5 --no-enhance  # algo only, no LLM, five prompts
python3 generate_prompt.py --raw                   # plain text (no JSON wrapper)
```

JSON output: `{"prompt": "...", "type": "video|image|animate", "source": "llm|markov|algo", "slug": "..."}`

### Starting the Qwen server

```bash
./start_prompt_gen.sh          # start; model (~1.2 GB) downloads on first run
./start_prompt_gen.sh --stop   # stop

curl -s http://localhost:8001/health  # → {"status":"ok","model_ready":true}
```

---

## License

Apache 2.0
