## tt-local-generator — install guide

Two paths: **`.deb` package** (recommended for Ubuntu 24.04 with TT hardware)
or **git clone** (any platform, remote-client use, or development).

---

## Path A — `.deb` package (Ubuntu 24.04, recommended)

### Before you install

Log in to HuggingFace first. The model packages read the saved token automatically so you won't be prompted during install.

```bash
pip install huggingface_hub
huggingface-cli login          # paste your token; saves to ~/.cache/huggingface/token
```

### Install

```bash
# 1. Main app (sets up Docker repo, /opt/tenstorrent/models, docker group, etc.)
sudo apt install ./tt-local-generator_*.deb

# 2. Models (downloads happen at install time — no apply_patches.sh needed)
sudo apt install ./tt-model-qwen3_*.deb          # ~1.2 GB — prompt server
sudo apt install ./tt-model-wan2-t2v_*.deb       # ~118 GB — primary video model
```

Other available model packages:
- `tt-model-flux_*.deb` — FLUX.1-dev (~34 GB, image generation, gated model)
- `tt-model-mochi_*.deb` — Mochi-1 (~20 GB)
- `tt-model-animate_*.deb` — Wan2.2-Animate-14B (~30 GB, character animation)
- `tt-model-skyreels-t2v_*.deb` — SkyReels-V2-DF-1.3B (~12 GB, Blackhole)
- `tt-model-skyreels-i2v_*.deb` — SkyReels-V2-I2V-14B (~58 GB, Blackhole)
- `tt-local-generator-models-all_*.deb` — meta-package that installs all of the above

Or re-run a download manually at any time:
```bash
tt-local-gen-download-model --repo Wan-AI/Wan2.2-T2V-A14B-Diffusers
tt-local-gen-download-model --repo Qwen/Qwen3-0.6B
```

### First launch

```bash
newgrp docker          # activate docker group without logging out (or just relog)
tt-local-gen
```

Click **Servers ▸ Start** in the app. The status bar tracks startup progress
live (~5 min on first run after weights are cached). The prompt server starts
automatically in the background.

> **Note:** `bin/quickstart.sh` is not needed for `.deb` installs. The
> `postinst` script already handles everything quickstart does — Python deps,
> vendor clone, patches, `.env`, and model download. Running quickstart on a
> deb install would attempt to re-clone and re-patch files that are already
> managed by the package. If you hit post-install problems, check
> `/tmp/tt_prompt_gen.log` and re-run `tt-local-gen-download-model` instead.

---

## Path B — git clone (any platform / dev use)

Clone destination `~/code/tt-local-generator` is expected by all scripts.

### Recommended: single-command setup

```bash
git clone https://github.com/tenstorrent/tt-local-generator.git ~/code/tt-local-generator
cd ~/code/tt-local-generator
./bin/quickstart.sh
```

`quickstart.sh` runs and validates every setup step in order:

| Step | What it does |
|------|-------------|
| 1 | Python deps — `torch`, `transformers`, `fastapi`, `uvicorn`, `markovify` |
| 2 | Vendor clone — `vendor/tt-inference-server` at the pinned SHA |
| 3 | Vendor `.env` — seeds `JWT_SECRET`; warns if it looks like a placeholder |
| 4 | Patches — hotpatches applied to the vendor tree (idempotent) |
| 5 | GTK4/PyGObject — informational check; GUI won't open without it |
| 6 | Prompt server — starts Qwen3-0.6B on CPU (port 8001) |
| 7 | Validation — sends a live inference request to confirm the model responds |

If any step fails and the prompt server is running, a **"Qwen suggests:"** box
prints targeted fix commands automatically. Pass `--no-assist` to skip it.

```bash
./bin/quickstart.sh            # full check-and-fix + start + validate
./bin/quickstart.sh --status   # checks only, no side effects
./bin/quickstart.sh --no-assist  # no Qwen remediation advice on failure
```

Steps 2 and 4 must be re-run any time `vendor/VENDOR_SHA` changes (after
pulling a commit that bumps the pinned server version). Re-running
`quickstart.sh` handles both automatically.

### Manual step-by-step

```bash
# 1. Clone
git clone https://github.com/tenstorrent/tt-local-generator.git ~/code/tt-local-generator
cd ~/code/tt-local-generator

# 2. Install system dependencies
./bin/setup_ubuntu.sh    # Ubuntu — GTK4, GStreamer, Docker
# ./bin/setup_macos.sh   # macOS  — Homebrew (remote-client mode only)

# 3. Set up the vendored inference server
./bin/setup_vendor.sh    # shallow-clone vendor/tt-inference-server at pinned SHA

# 4. Apply patches to the vendored server (required before first start_*.sh)
./bin/apply_patches.sh   # HF_HOME mount, SkyReels model specs, device config

# 5. Launch
./tt-gen
```

Model weights are not downloaded automatically in clone mode. Pre-download with:
```bash
huggingface-cli download Wan-AI/Wan2.2-T2V-A14B-Diffusers   # ~118 GB
huggingface-cli download Qwen/Qwen3-0.6B                      # ~1.2 GB
```

Or start the app and let `start_wan_qb2.sh` pull weights inside the container
(slower, requires network during first run).
