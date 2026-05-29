#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC

"""Phase 2.5 via ttsim — Blackhole simulator, no hardware required.

Runs the same TTNN UNet denoising pipeline as generate_blackhole_v2.py but
against a ttsim Blackhole virtual device. Any Linux/x86_64 machine can run
this; no Tenstorrent silicon is needed.

ttsim is slower than silicon (roughly 10–100× depending on the operation) and
is intended for development, CI, and exploration — not production throughput.
At the default --steps 4 / --frames 2 it completes in reasonable wall-clock
time on a laptop.

Setup:
    # 1. Build tt-metal (one-time)
    git clone https://github.com/tenstorrent/tt-metal.git ~/tt-metal
    cd ~/tt-metal && ./build_metal.sh

    # 2. Download the ttsim Blackhole binary (v1.7.0 or latest)
    mkdir -p ~/sim
    wget -O ~/sim/libttsim_bh.so \\
        https://github.com/tenstorrent/ttsim/releases/download/v1.7.0/libttsim_bh.so
    cp $TT_METAL_HOME/tt_metal/soc_descriptors/blackhole_140_arch.yaml \\
        ~/sim/soc_descriptor.yaml

    # 3. Activate tt-metal Python env and install this project
    source ~/tt-metal/python_env/bin/activate
    pip install -e /path/to/tt-animatediff[dev]

    # 4. Download model weights
    hf download CompVis/stable-diffusion-v1-4

Usage:
    # Quick smoke test (2 frames, 4 steps — fits in ~10 minutes on a fast machine)
    TT_METAL_SIMULATOR=~/sim/libttsim_bh.so \\
    TT_METAL_SLOW_DISPATCH_MODE=1 \\
    TT_METAL_DISABLE_SFPLOADMACRO=1 \\
        python examples/generate_sim.py --frames 2 --steps 4

    # Longer run (8 frames, 25 steps — same as silicon default)
    TT_METAL_SIMULATOR=~/sim/libttsim_bh.so \\
    TT_METAL_SLOW_DISPATCH_MODE=1 \\
    TT_METAL_DISABLE_SFPLOADMACRO=1 \\
        python examples/generate_sim.py

    # Custom prompt
    TT_METAL_SIMULATOR=~/sim/libttsim_bh.so \\
    TT_METAL_SLOW_DISPATCH_MODE=1 \\
    TT_METAL_DISABLE_SFPLOADMACRO=1 \\
        python examples/generate_sim.py \\
            --prompt "neon city rain at midnight, cyberpunk aesthetic" \\
            --frames 4 --steps 8

Notes:
    - TT_METAL_SLOW_DISPATCH_MODE=1 is recommended; fast dispatch on the
      simulator has not been adequately characterized for run-to-run determinism.
    - TT_METAL_DISABLE_SFPLOADMACRO=1 is required — SFPLOADMACRO is not
      implemented in the ttsim SFPU.
    - The simulator is bit-exact with silicon for supported operations, so
      output GIFs are numerically identical to hardware runs on the same seed.
    - VAE decode runs on CPU as usual (not a simulator limitation).
"""

import argparse
import os
import sys
import time
from pathlib import Path

import torch

# ── simulator environment ──────────────────────────────────────────────────
# TT_METAL_SIMULATOR must be set before tt-metal loads its dispatch layer.
# Accept it from the environment (preferred — set in shell before invoking)
# or fall back to ~/sim/libttsim_bh.so.
_DEFAULT_SIM = Path.home() / "sim" / "libttsim_bh.so"
_SIM_SO = Path(os.environ.get("TT_METAL_SIMULATOR", str(_DEFAULT_SIM)))

if not os.environ.get("TT_METAL_SIMULATOR"):
    if not _SIM_SO.exists():
        print(
            f"ERROR: ttsim binary not found at {_SIM_SO}\n"
            "Download it from https://github.com/tenstorrent/ttsim/releases\n"
            "or set TT_METAL_SIMULATOR=/path/to/libttsim_bh.so",
            file=sys.stderr,
        )
        sys.exit(1)
    os.environ["TT_METAL_SIMULATOR"] = str(_SIM_SO)

# Simulator requires slow dispatch and SFPLOADMACRO disabled.
os.environ.setdefault("TT_METAL_SLOW_DISPATCH_MODE", "1")
os.environ.setdefault("TT_METAL_DISABLE_SFPLOADMACRO", "1")
os.environ.setdefault("TT_METAL_ARCH_NAME", "blackhole")

# ── project path ──────────────────────────────────────────────────────────
TT_METAL_PATH = Path.home() / "tt-metal"
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(TT_METAL_PATH))

from animatediff_ttnn.ttnn_pipeline import _ensure_tt_metal_path
from animatediff_ttnn.temporal_attention import generate_frames_temporal
from animatediff_ttnn.pipeline import export_gif


def setup_sim_device():
    """Open a simulated Blackhole device via ttsim.

    Equivalent to setup_blackhole() but skips the hwmon sentinel check
    (no /sys/class/hwmon on a virtual device) and opens exactly one chip —
    the simulator always presents a single virtual device.
    """
    _ensure_tt_metal_path()
    import ttnn
    from models.demos.wormhole.stable_diffusion.common import SD_L1_SMALL_SIZE

    # ttsim presents exactly one virtual chip — always device_id=0.
    return ttnn.open_mesh_device(
        mesh_shape=ttnn.MeshShape(1, 1),
        physical_device_ids=[0],
        l1_small_size=SD_L1_SMALL_SIZE,
    )


def load_sd14_ttnn(device):
    """Load SD 1.4 TTNN UNet onto the simulated device."""
    from diffusers import AutoencoderKL, UNet2DConditionModel
    from ttnn.model_preprocessing import preprocess_model_parameters
    from models.demos.wormhole.stable_diffusion.custom_preprocessing import custom_preprocessor
    from models.demos.wormhole.stable_diffusion.tt.ttnn_functional_unet_2d_condition_model_new_conv import (
        UNet2DConditionModel as UNet2D,
    )

    print("  Loading PyTorch VAE (CPU decode)...")
    torch_vae = AutoencoderKL.from_pretrained(
        "CompVis/stable-diffusion-v1-4", subfolder="vae"
    )
    torch_vae.eval()

    print("  Loading PyTorch UNet (for config and time_proj)...")
    torch_unet = UNet2DConditionModel.from_pretrained(
        "CompVis/stable-diffusion-v1-4", subfolder="unet"
    )

    print("  Building TTNN UNet on simulator (slower than silicon — please wait)...")
    parameters = preprocess_model_parameters(
        initialize_model=lambda: torch_unet,
        custom_preprocessor=custom_preprocessor,
        device=device,
    )
    ttnn_model = UNet2D(device, parameters, 2, 64, 64)

    return ttnn_model, torch_vae, torch_unet.config, torch_unet.time_proj


def encode_prompt(prompt: str, negative_prompt: str = "") -> torch.Tensor:
    """Encode text + negative prompt to (2, 96, 768) CLIP embeddings."""
    from transformers import CLIPTokenizer, CLIPTextModel

    tokenizer = CLIPTokenizer.from_pretrained(
        "CompVis/stable-diffusion-v1-4", subfolder="tokenizer"
    )
    text_encoder = CLIPTextModel.from_pretrained(
        "CompVis/stable-diffusion-v1-4", subfolder="text_encoder"
    )
    text_encoder.eval()

    def encode(text):
        tokens = tokenizer(
            text,
            padding="max_length",
            max_length=tokenizer.model_max_length,
            truncation=True,
            return_tensors="pt",
        )
        with torch.no_grad():
            embeds = text_encoder(tokens.input_ids)[0]
        return torch.nn.functional.pad(embeds, (0, 0, 0, 19))  # 77 → 96 tokens

    return torch.cat([encode(negative_prompt), encode(prompt)], dim=0)  # (2, 96, 768)


def main():
    parser = argparse.ArgumentParser(
        description="AnimateDiff Phase 2.5 on ttsim Blackhole simulator"
    )
    parser.add_argument(
        "--prompt",
        default="neon city rain at midnight, cyberpunk aesthetic, cinematic",
    )
    parser.add_argument(
        "--negative-prompt", default="blurry, low quality", dest="negative_prompt"
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=2,
        help="Frames to generate. Default 2 for simulator (use 8 for silicon).",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=4,
        help="Denoising steps. Default 4 for simulator (use 25 for silicon).",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="output/sim.gif")
    parser.add_argument(
        "--temporal-alpha",
        type=float,
        default=0.35,
        dest="temporal_alpha",
        help="Cross-frame attention blend (0–1, default 0.35)",
    )
    parser.add_argument(
        "--sim",
        default=None,
        metavar="PATH",
        help="Path to libttsim_bh.so (overrides TT_METAL_SIMULATOR env var)",
    )
    args = parser.parse_args()

    if args.sim:
        os.environ["TT_METAL_SIMULATOR"] = args.sim

    sim_path = os.environ.get("TT_METAL_SIMULATOR", "")
    print("AnimateDiff Phase 2.5 — ttsim Blackhole simulator")
    print(f"  Simulator      : {sim_path}")
    print(f"  Prompt         : {args.prompt}")
    print(f"  Frames         : {args.frames}  Steps: {args.steps}  Seed: {args.seed}")
    print(f"  Temporal alpha : {args.temporal_alpha}")
    print()
    print(
        "Note: ttsim is 10-100× slower than silicon. For a smoke test, "
        f"{args.frames} frame(s) × {args.steps} step(s) is recommended."
    )
    print()

    print("Opening simulated Blackhole device...")
    device = setup_sim_device()
    print()

    try:
        print("Loading SD 1.4 models onto simulator...")
        t0 = time.time()
        ttnn_model, torch_vae, config, torch_time_proj = load_sd14_ttnn(device)
        print(f"  Models loaded in {time.time() - t0:.1f}s")
        print()

        print("Encoding prompts with CLIP (CPU)...")
        text_embeddings = encode_prompt(args.prompt, args.negative_prompt)
        print(f"  Embeddings shape: {text_embeddings.shape}")
        print()

        print(
            f"Generating {args.frames} frame(s) with temporal attention on simulator..."
        )
        t1 = time.time()
        frames = generate_frames_temporal(
            device=device,
            ttnn_model=ttnn_model,
            torch_vae=torch_vae,
            config=config,
            torch_time_proj=torch_time_proj,
            text_embeddings=text_embeddings,
            num_frames=args.frames,
            num_steps=args.steps,
            seed=args.seed,
            temporal_alpha=args.temporal_alpha,
        )
        elapsed = time.time() - t1
        print(f"  Generated in {elapsed:.1f}s ({elapsed / args.frames:.1f}s/frame)")
        print()
    finally:
        import ttnn
        ttnn.close_mesh_device(device)
        print("Device closed.")
        print()

    export_gif(frames, args.output)
    print(f"Saved {len(frames)} frame(s) → {args.output}")
    print()
    print("Backend breakdown:")
    print(f"  TTNN UNet spatial denoising : ttsim (bit-exact with Blackhole silicon)")
    print(f"  Cross-frame temporal attention (alpha={args.temporal_alpha}): CPU")
    print(f"  VAE decode                  : CPU")


if __name__ == "__main__":
    main()
