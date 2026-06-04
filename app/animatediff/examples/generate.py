#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC

"""AnimateDiff video generation — unified entry point.

Modes
-----
blackhole  (default) — TTNN UNet on Blackhole hardware + cross-frame temporal
                       attention. Most performant on silicon. ~15 s/frame.
cpu        — diffusers AnimateDiffPipeline with MotionAdapter, CPU only.
             Full AnimateDiff temporal attention. ~2 min/frame. No hardware.
sim        — Same as blackhole but against a ttsim virtual device.
             Any Linux/x86_64 machine; no silicon required.

Requirements
------------
All modes:
    pip install -e ".[dev]"
    hf download CompVis/stable-diffusion-v1-4

cpu mode also needs the motion adapter:
    hf download guoyww/animatediff-motion-adapter-v1-5-2

blackhole / sim modes also need tt-metal:
    source ~/tt-metal/python_env/bin/activate

sim mode also needs ttsim:
    mkdir -p ~/sim
    wget -O ~/sim/libttsim_bh.so \\
        https://github.com/tenstorrent/ttsim/releases/download/v1.7.0/libttsim_bh.so

Usage
-----
    # Blackhole hardware (default, most performant)
    python examples/generate.py --prompt "ocean waves, cinematic 4K" --frames 8

    # CPU only (no hardware required)
    python examples/generate.py --mode cpu --frames 16

    # ttsim simulator (no hardware, slower)
    python examples/generate.py --mode sim --frames 2 --steps 4
    python examples/generate.py --mode sim --sim ~/sim/libttsim_bh.so --frames 2

    # Disable temporal attention blending
    python examples/generate.py --temporal-alpha 0
"""

import argparse
import os
import sys
import time
from pathlib import Path

import torch

# ── parse args first — sim mode needs --sim before env bootstrap ───────────
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AnimateDiff — Blackhole TTNN UNet (default), CPU, or ttsim"
    )
    parser.add_argument(
        "--mode",
        choices=["blackhole", "cpu", "sim"],
        default="blackhole",
        help="Execution backend (default: blackhole)",
    )
    parser.add_argument(
        "--prompt",
        default="1939 World's Fair imagined from the year 2099, art deco spires at golden dusk, retro-futurist optimism, cinematic 4K",
    )
    parser.add_argument(
        "--negative-prompt", default="blurry, low quality", dest="negative_prompt"
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=None,
        help="Frames to generate (default: 16 for cpu, 8 for blackhole/sim)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help="Denoising steps (default: 25 for blackhole/cpu, 4 for sim)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        default=None,
        help="Output GIF path (default: output/<mode>.gif)",
    )
    parser.add_argument(
        "--temporal-alpha",
        type=float,
        default=0.35,
        dest="temporal_alpha",
        help="Cross-frame attention blend 0–1 (blackhole/sim only; default 0.35)",
    )
    parser.add_argument(
        "--sim",
        default=None,
        metavar="PATH",
        help="Path to libttsim_bh.so for sim mode (overrides TT_METAL_SIMULATOR)",
    )
    return parser

args = _build_parser().parse_args()

# Apply mode-specific defaults now that we know the mode
if args.frames is None:
    args.frames = 16 if args.mode == "cpu" else 8
if args.steps is None:
    args.steps = 4 if args.mode == "sim" else 25
if args.output is None:
    args.output = f"output/{args.mode}.gif"
if not 0.0 <= args.temporal_alpha <= 1.0:
    _build_parser().error(f"--temporal-alpha must be in [0, 1], got {args.temporal_alpha}")

# ── sim: resolve ttsim path and configure env before tt-metal loads ────────
if args.mode == "sim":
    _DEFAULT_SIM = Path.home() / "sim" / "libttsim_bh.so"
    if args.sim:
        _sim_so = Path(args.sim)
        os.environ["TT_METAL_SIMULATOR"] = str(_sim_so)
    elif os.environ.get("TT_METAL_SIMULATOR"):
        _sim_so = Path(os.environ["TT_METAL_SIMULATOR"])
    else:
        _sim_so = _DEFAULT_SIM
        if not _sim_so.exists():
            print(
                f"ERROR: ttsim binary not found at {_sim_so}\n"
                "Download it from https://github.com/tenstorrent/ttsim/releases\n"
                "or pass --sim /path/to/libttsim_bh.so",
                file=sys.stderr,
            )
            sys.exit(1)
        os.environ["TT_METAL_SIMULATOR"] = str(_sim_so)

    # Required simulator env before tt-metal dispatch initialises
    os.environ.setdefault("TT_METAL_SLOW_DISPATCH_MODE", "1")
    os.environ.setdefault("TT_METAL_DISABLE_SFPLOADMACRO", "1")
    os.environ.setdefault("TT_METAL_ARCH_NAME", "blackhole")

# ── project / tt-metal paths ───────────────────────────────────────────────
TT_METAL_PATH = Path.home() / "tt-metal"
sys.path.insert(0, str(Path(__file__).parent.parent))
if args.mode in ("blackhole", "sim"):
    sys.path.insert(0, str(TT_METAL_PATH))


# ══════════════════════════════════════════════════════════════════════════
# Shared helpers (blackhole + sim share load_sd14_ttnn / encode_prompt)
# ══════════════════════════════════════════════════════════════════════════

def load_sd14_ttnn(device):
    """Load SD 1.4 TTNN UNet onto device; return (ttnn_model, torch_vae, config, time_proj).

    VAE stays on CPU — TTNN VAE conv_out OOMs on Blackhole's L1 grid
    (Wormhole-targeted kernel; no Blackhole-native VAE decoder yet).
    """
    from diffusers import AutoencoderKL, UNet2DConditionModel
    from ttnn.model_preprocessing import preprocess_model_parameters
    from models.demos.vision.generative.stable_diffusion.wormhole.custom_preprocessing import custom_preprocessor
    from models.demos.vision.generative.stable_diffusion.wormhole.tt.ttnn_functional_unet_2d_condition_model_new_conv import (
        UNet2DConditionModel as UNet2D,
    )

    print("  Loading PyTorch VAE (CPU decode)...")
    torch_vae = AutoencoderKL.from_pretrained("CompVis/stable-diffusion-v1-4", subfolder="vae")
    torch_vae.eval()

    print("  Loading PyTorch UNet (config + time_proj)...")
    torch_unet = UNet2DConditionModel.from_pretrained("CompVis/stable-diffusion-v1-4", subfolder="unet")

    print("  Building TTNN UNet (~2-3 min first run, cached after)...")
    parameters = preprocess_model_parameters(
        initialize_model=lambda: torch_unet,
        custom_preprocessor=custom_preprocessor,
        device=device,
    )
    ttnn_model = UNet2D(device, parameters, 2, 64, 64)
    return ttnn_model, torch_vae, torch_unet.config, torch_unet.time_proj


def encode_prompt(prompt: str, negative_prompt: str = "") -> torch.Tensor:
    """Encode text prompt pair to (2, 96, 768) CLIP embeddings.

    Pads 77 → 96 tokens to match TTNN UNet's expected sequence length.
    """
    from transformers import CLIPTokenizer, CLIPTextModel

    tokenizer = CLIPTokenizer.from_pretrained("CompVis/stable-diffusion-v1-4", subfolder="tokenizer")
    text_encoder = CLIPTextModel.from_pretrained("CompVis/stable-diffusion-v1-4", subfolder="text_encoder")
    text_encoder.eval()

    def encode(text):
        tokens = tokenizer(
            text, padding="max_length", max_length=tokenizer.model_max_length,
            truncation=True, return_tensors="pt",
        )
        with torch.no_grad():
            embeds = text_encoder(tokens.input_ids)[0]
        return torch.nn.functional.pad(embeds, (0, 0, 0, 19))  # 77 → 96 tokens

    return torch.cat([encode(negative_prompt), encode(prompt)], dim=0)  # (2, 96, 768)


# ══════════════════════════════════════════════════════════════════════════
# Mode: cpu
# ══════════════════════════════════════════════════════════════════════════

def run_cpu():
    from animatediff_ttnn.pipeline import create_animatediff_pipeline, generate, export_gif

    print("AnimateDiff — CPU mode (diffusers AnimateDiffPipeline + MotionAdapter)")
    print(f"  Prompt  : {args.prompt}")
    print(f"  Frames  : {args.frames}  Steps: {args.steps}  Seed: {args.seed}")
    print()

    print("Loading pipeline (first run downloads ~4.7 GB)...")
    t0 = time.time()
    pipe = create_animatediff_pipeline()
    print(f"  Loaded in {time.time() - t0:.1f}s\n")

    print(f"Generating {args.frames} frames...")
    t1 = time.time()
    frames = generate(
        pipe, args.prompt,
        negative_prompt=args.negative_prompt,
        num_frames=args.frames,
        num_inference_steps=args.steps,
        seed=args.seed,
    )
    elapsed = time.time() - t1
    print(f"  Done in {elapsed:.1f}s ({elapsed / args.frames:.1f}s/frame)\n")

    export_gif(frames, args.output)
    print(f"Saved {len(frames)} frames → {args.output}")
    print("\nNote: MotionAdapter injected temporal attention into every UNet block.")
    print("      Each denoising step attends across all frames simultaneously.")


# ══════════════════════════════════════════════════════════════════════════
# Mode: blackhole / sim
# ══════════════════════════════════════════════════════════════════════════

def _open_device():
    """Open a MeshDevice — real Blackhole or ttsim virtual device."""
    from animatediff_ttnn.ttnn_pipeline import setup_blackhole, _ensure_tt_metal_path
    import ttnn
    from models.demos.vision.generative.stable_diffusion.wormhole.common import SD_L1_SMALL_SIZE

    if args.mode == "sim":
        _ensure_tt_metal_path()
        return ttnn.open_mesh_device(
            mesh_shape=ttnn.MeshShape(1, 1),
            physical_device_ids=[0],
            l1_small_size=SD_L1_SMALL_SIZE,
        )
    else:
        # SD 1.4 TTNN UNet (Wormhole-targeted) uses ttnn.to_torch() without a
        # mesh_composer, which crashes if tensor is sharded across >1 chip.
        return setup_blackhole(device_ids=[0])


def run_ttnn():
    from animatediff_ttnn.temporal_attention import generate_frames_temporal
    from animatediff_ttnn.pipeline import export_gif

    backend = "ttsim simulator" if args.mode == "sim" else "Blackhole hardware"
    print(f"AnimateDiff — {backend} (TTNN UNet + cross-frame temporal attention)")
    if args.mode == "sim":
        print(f"  Simulator      : {os.environ.get('TT_METAL_SIMULATOR', '?')}")
    print(f"  Prompt         : {args.prompt}")
    print(f"  Frames         : {args.frames}  Steps: {args.steps}  Seed: {args.seed}")
    print(f"  Temporal alpha : {args.temporal_alpha}")
    if args.mode == "sim":
        print(f"\n  Note: ttsim is 10–100× slower than silicon.")
    print()

    print(f"Opening {'simulated ' if args.mode == 'sim' else ''}Blackhole device...")
    device = _open_device()
    print()

    try:
        print("Loading SD 1.4 models...")
        t0 = time.time()
        ttnn_model, torch_vae, config, torch_time_proj = load_sd14_ttnn(device)
        print(f"  Loaded in {time.time() - t0:.1f}s\n")

        print("Encoding prompts with CLIP...")
        text_embeddings = encode_prompt(args.prompt, args.negative_prompt)
        print(f"  Embeddings: {text_embeddings.shape}\n")

        print(f"Generating {args.frames} frame(s)...")
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
        print(f"  Done in {elapsed:.1f}s ({elapsed / args.frames:.1f}s/frame)\n")
    finally:
        import ttnn
        ttnn.close_mesh_device(device)
        print("Device closed.\n")

    export_gif(frames, args.output)
    print(f"Saved {len(frames)} frame(s) → {args.output}")
    print(f"\nBackend: TTNN UNet spatial denoising on {backend}")
    print(f"         Cross-frame temporal attention (alpha={args.temporal_alpha}): CPU")
    print(f"         VAE decode: CPU (TTNN VAE conv_out OOMs on Blackhole)")


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

def main():
    if args.mode == "cpu":
        run_cpu()
    else:
        run_ttnn()


if __name__ == "__main__":
    main()
