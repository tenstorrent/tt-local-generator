# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
#
# SkyReels-V2-I2V-14B-540P TTNN pipeline for Tenstorrent Blackhole.
#
# Architecture notes
# ------------------
# SkyReels-V2-I2V-14B-540P is stored as a raw WAN 2.1 checkpoint (not diffusers
# format).  This file provides:
#
#   1. _map_raw_wan_i2v_to_diffusers()  — key mapper from raw WAN 2.1 I2V keys
#      to the diffusers-compatible keys expected by TTNN WanTransformer3DModel.
#
#   2. SkyReelsI2VTTNNTransformer  — TTNN-backed drop-in for the transformer.
#      Uses model_type="i2v" (in_channels=36: 16 noisy + 20 image/mask).
#      Image conditioning flows through the 36-channel spatial concatenation
#      (VAE latents of the conditioning frame + binary mask).
#
#   3. SkyReelsI2VPipeline  — server-compatible wrapper that accepts an `image`
#      parameter and returns np.ndarray (1, T, H, W, C) in [0, 1].
#
# CLIP image cross-attention note
# --------------------------------
# The raw WAN 2.1 I2V model has `cross_attn.k_img / v_img` keys for CLIP-level
# image cross-attention.  The TTNN WanTransformer3DModel (Wan 2.2 T2V backbone)
# does NOT implement this path.  Those keys are silently dropped by the key
# mapper.  The image conditioning still works via the 36-channel spatial input
# (VAE-encoded conditioning frame concatenated with the noisy latent).
#
# Component loading
# -----------------
# Because the checkpoint has no diffusers subfolder structure:
#   - VAE:          load architecture from Wan2.2-T2V-A14B-Diffusers (same arch),
#                   replace weights from Wan2.1_VAE.pth
#   - T5 encoder:   load architecture from Wan2.2-T2V-A14B-Diffusers,
#                   replace weights from models_t5_umt5-xxl-enc-bf16.pth
#   - UMT5 tokenizer: loaded from google/umt5-xxl/ subfolder in the I2V checkpoint
#   - CLIP encoder: _DummyCLIPImageEncoder (returns zeros); TTNN transformer
#                   ignores encoder_hidden_states_image anyway
#   - Scheduler:    UniPCMultistepScheduler, flow_shift=5.0 (I2V default)
#
# Mount path (via run_docker_server.py tt_dit hotpatch mechanism, --dev-mode):
#   patches/tt_dit/pipelines/skyreels_v2/pipeline_skyreels_i2v.py
#   → ~/tt-metal/models/tt_dit/pipelines/skyreels_v2/pipeline_skyreels_i2v.py
#
# Python import path inside the container:
#   models.tt_dit.pipelines.skyreels_v2.pipeline_skyreels_i2v
#
# Hardware target: Tenstorrent Blackhole, 1×4 mesh (P150X4) or 2×2 mesh (P300X2).
#   sp_axis=0, tp_axis=1.  num_links=2.

import os
import re
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import torch

from models.tt_dit.models.transformers.wan2_2.transformer_wan import (
    WanTransformer3DModel as TTNNWanTransformer3DModel,
)
from models.tt_dit.models.vae.vae_wan2_1 import WanDecoder
from models.tt_dit.parallel.config import DiTParallelConfig, ParallelFactor, VaeHWParallelConfig
from models.tt_dit.parallel.manager import CCLManager
from models.tt_dit.utils import cache as _ttnn_cache
from models.tt_dit.utils.conv3d import conv_pad_height, conv_pad_in_channels
from models.tt_dit.utils.tensor import bf16_tensor, typed_tensor_2dshard

import ttnn


# ---------------------------------------------------------------------------
# WAN 2.2 A14B Diffusers checkpoint — used as architecture source for VAE/T5.
# The I2V-14B shares the same VAE and T5 text encoder architecture as WAN 2.2.
# These are pulled from the local HuggingFace cache (already downloaded).
# ---------------------------------------------------------------------------
_WAN22_ARCH_CHECKPOINT = "Wan-AI/Wan2.2-T2V-A14B-Diffusers"


# ---------------------------------------------------------------------------
# Key mapper: raw WAN 2.1 I2V  →  diffusers WAN (TTNN-compatible) format
# ---------------------------------------------------------------------------

def _map_raw_wan_i2v_to_diffusers(raw_sd: dict) -> dict:
    """
    Re-key a raw WAN 2.1 I2V state_dict to the diffusers key schema that
    TTNN WanTransformer3DModel.load_torch_state_dict() expects.

    Mapping summary
    ---------------
    Block-level (for each N in 0..num_layers-1):
      self_attn.q/k/v       →  attn1.to_q/to_k/to_v
      self_attn.o           →  attn1.to_out.0
      self_attn.norm_q/norm_k  →  attn1.norm_q/norm_k
      cross_attn.q/k/v      →  attn2.to_q/to_k/to_v
      cross_attn.o          →  attn2.to_out.0
      cross_attn.norm_q/norm_k →  attn2.norm_q/norm_k
      cross_attn.k_img/*    →  DROPPED (CLIP image cross-attn, not in TTNN)
      cross_attn.v_img/*    →  DROPPED
      cross_attn.norm_k_img/* →  DROPPED
      ffn.0                 →  ffn.net.0.proj
      ffn.2                 →  ffn.net.2
      modulation            →  scale_shift_table
      norm3                 →  norm2

    Top-level:
      text_embedding.0/2    →  condition_embedder.text_embedder.linear_1/2
      time_embedding.0/2    →  condition_embedder.time_embedder.linear_1/2
      time_projection.1     →  condition_embedder.time_proj
      head.head             →  proj_out
      head.modulation       →  scale_shift_table
      patch_embedding       →  patch_embedding  (unchanged)
      img_emb.proj.*        →  DROPPED (CLIP image embedding, not in TTNN)
    """
    # Prefixes to silently drop (CLIP image conditioning not supported in TTNN)
    SKIP_PATTERNS = (
        "cross_attn.k_img",
        "cross_attn.v_img",
        "cross_attn.norm_k_img",
        "img_emb.",
    )

    # Block-level renames: (raw_suffix_prefix, new_suffix_prefix) pairs.
    # Entries WITHOUT trailing '.' are matched exactly (e.g. "modulation").
    BLOCK_MAP = [
        ("self_attn.q.",        "attn1.to_q."),
        ("self_attn.k.",        "attn1.to_k."),
        ("self_attn.v.",        "attn1.to_v."),
        ("self_attn.o.",        "attn1.to_out.0."),
        ("self_attn.norm_q.",   "attn1.norm_q."),
        ("self_attn.norm_k.",   "attn1.norm_k."),
        ("cross_attn.q.",       "attn2.to_q."),
        ("cross_attn.k.",       "attn2.to_k."),
        ("cross_attn.v.",       "attn2.to_v."),
        ("cross_attn.o.",       "attn2.to_out.0."),
        ("cross_attn.norm_q.",  "attn2.norm_q."),
        ("cross_attn.norm_k.",  "attn2.norm_k."),
        ("ffn.0.",              "ffn.net.0.proj."),
        ("ffn.2.",              "ffn.net.2."),
        ("norm3.",              "norm2."),
        # Exact matches (no trailing dot):
        ("modulation",          "scale_shift_table"),
    ]

    # Top-level renames
    TOP_MAP = [
        ("text_embedding.0.",   "condition_embedder.text_embedder.linear_1."),
        ("text_embedding.2.",   "condition_embedder.text_embedder.linear_2."),
        ("time_embedding.0.",   "condition_embedder.time_embedder.linear_1."),
        ("time_embedding.2.",   "condition_embedder.time_embedder.linear_2."),
        ("time_projection.1.",  "condition_embedder.time_proj."),
        ("head.head.",          "proj_out."),
        ("patch_embedding.",    "patch_embedding."),
        # Exact matches:
        ("head.modulation",     "scale_shift_table"),
    ]

    block_re = re.compile(r"^(blocks\.\d+)\.")
    out: dict = {}

    for raw_key, val in raw_sd.items():
        # Skip CLIP-specific keys
        if any(pat in raw_key for pat in SKIP_PATTERNS):
            continue

        m = block_re.match(raw_key)
        if m:
            block_prefix = m.group(1)           # e.g. "blocks.0"
            suffix = raw_key[len(block_prefix) + 1:]  # e.g. "self_attn.q.weight"

            new_suffix = None
            for raw_pfx, new_pfx in BLOCK_MAP:
                if raw_pfx.endswith("."):
                    if suffix.startswith(raw_pfx):
                        new_suffix = new_pfx + suffix[len(raw_pfx):]
                        break
                else:
                    if suffix == raw_pfx:
                        new_suffix = new_pfx
                        break

            if new_suffix is None:
                # Pass through unknown keys (should not happen for valid checkpoints)
                new_suffix = suffix

            out[f"{block_prefix}.{new_suffix}"] = val

        else:
            # Top-level key
            new_key = None
            for raw_pfx, new_pfx in TOP_MAP:
                if raw_pfx.endswith("."):
                    if raw_key.startswith(raw_pfx):
                        new_key = new_pfx + raw_key[len(raw_pfx):]
                        break
                else:
                    if raw_key == raw_pfx:
                        new_key = new_pfx
                        break

            if new_key is not None:
                out[new_key] = val
            # Unknown top-level keys (shouldn't occur for valid checkpoints) are dropped

    return out


# ---------------------------------------------------------------------------
# Dummy CLIP image encoder
# ---------------------------------------------------------------------------

class _DummyCLIPImageEncoder(torch.nn.Module):
    """
    Zero-output CLIP image encoder placeholder.

    The TTNN WAN I2V transformer ignores encoder_hidden_states_image (the CLIP
    path is not implemented in WanTransformer3DModel).  Image conditioning
    happens instead via the 36-channel spatial input (16 noisy latents +
    16 VAE-encoded conditioning frame + 4 mask channels).

    This placeholder satisfies the diffusers SkyReelsV2ImageToVideoPipeline's
    requirement for an image_encoder without loading the large OpenCLIP ViT-H/14
    model (≈1 GB weights that would only produce ignored outputs).

    The hidden_states[-2] shape (B, 257, 1280) matches ViT-H/14:
      257 = 1 CLS token + (224/14)^2 patch tokens
      1280 = ViT-H hidden dim
    """

    HIDDEN_DIM = 1280
    NUM_TOKENS = 257  # 1 + (224//14)^2

    def __init__(self):
        super().__init__()
        # projection_dim attr needed by diffusers pipeline internals
        self.config = type("_Cfg", (), {
            "hidden_size":     self.HIDDEN_DIM,
            "projection_dim":  self.HIDDEN_DIM,
        })()
        # Minimal param so .to(device) works; never actually used in a matmul
        self._sentinel = torch.nn.Parameter(torch.zeros(1), requires_grad=False)

    @property
    def dtype(self):
        return self._sentinel.dtype

    def forward(self, pixel_values=None, output_hidden_states=False, **kwargs):
        B = 1 if pixel_values is None else pixel_values.shape[0]
        device = self._sentinel.device
        dtype  = self._sentinel.dtype
        zeros = torch.zeros(B, self.NUM_TOKENS, self.HIDDEN_DIM,
                            device=device, dtype=dtype)
        if output_hidden_states:
            class _Out:
                def __init__(self, h):
                    # hidden_states[-2] is what encode_image() reads
                    self.hidden_states = [h, h]
                    self.image_embeds  = h
            return _Out(zeros)
        class _Out:
            def __init__(self, h):
                self.image_embeds = h
        return _Out(zeros)


# ---------------------------------------------------------------------------
# Transformer config stub
# ---------------------------------------------------------------------------

class _TransformerConfigI2V:
    """
    Minimal config object read by SkyReelsV2ImageToVideoPipeline at runtime.

    Fields:
      in_channels:          36 (16 noisy + 16 image latent + 4 mask)
      num_frame_per_block:  1  (no causal AR masking)
      patch_size:           (1, 2, 2)
    """

    def __init__(self):
        self.in_channels = 36
        self.num_frame_per_block = 1
        self.patch_size = (1, 2, 2)


# ---------------------------------------------------------------------------
# SkyReelsI2VTTNNTransformer
# ---------------------------------------------------------------------------

class SkyReelsI2VTTNNTransformer(torch.nn.Module):
    """
    TTNN-accelerated transformer for SkyReels-V2-I2V-14B-540P.

    Wraps WanTransformer3DModel (models.tt_dit) configured as:
      model_type="i2v"  in_channels=36  dim=5120  num_heads=40  num_layers=40

    Weight loading:
      The raw WAN 2.1 I2V checkpoint is sharded across 14 safetensors files.
      _map_raw_wan_i2v_to_diffusers() converts keys to the diffusers schema
      consumed by TTNN load_torch_state_dict().
      I2V-specific CLIP keys (k_img, v_img, norm_k_img, img_emb) are dropped
      because the TTNN model does not implement that cross-attention path.

    Forward contract:
      The diffusers SkyReelsV2ImageToVideoPipeline concatenates noisy latents
      (16ch) with the conditioning (16ch image latent + 4ch mask = 20ch) before
      calling the transformer, yielding hidden_states with 36 channels.
      This wrapper splits that back into spatial (16ch) and y (20ch), which is
      what WanTransformer3DModel.__call__(spatial, ..., y=y) expects for i2v.

      encoder_hidden_states_image (CLIP features) is received but silently
      ignored — the TTNN model does not implement CLIP cross-attention.
    """

    # SkyReels-V2-I2V-14B architecture (from Skywork/SkyReels-V2-I2V-14B-540P/config.json)
    NUM_HEADS   = 40
    DIM         = 5120       # 40 heads × 128 head_dim
    FFN_DIM     = 13824
    NUM_LAYERS  = 40
    TEXT_DIM    = 4096       # UMT5 hidden size
    FREQ_DIM    = 256
    PATCH_SIZE  = (1, 2, 2)
    # in_channels = 36 (set automatically by model_type="i2v")

    def __init__(
        self,
        mesh_device: ttnn.MeshDevice,
        parallel_config: DiTParallelConfig,
        ccl_manager: CCLManager,
        is_fsdp: bool = False,
    ):
        super().__init__()
        self.mesh_device = mesh_device
        self.parallel_config = parallel_config

        # Build WAN TTNN model in I2V mode.
        # model_type="i2v" sets in_channels=36 automatically inside the TTNN model.
        self.ttnn_model = TTNNWanTransformer3DModel(
            patch_size=self.PATCH_SIZE,
            num_heads=self.NUM_HEADS,
            dim=self.DIM,
            in_channels=36,          # I2V: 16 noisy + 16 img latent + 4 mask
            out_channels=16,
            text_dim=self.TEXT_DIM,
            freq_dim=self.FREQ_DIM,
            ffn_dim=self.FFN_DIM,
            num_layers=self.NUM_LAYERS,
            cross_attn_norm=True,
            eps=1e-6,
            rope_max_seq_len=1024,
            mesh_device=mesh_device,
            ccl_manager=ccl_manager,
            parallel_config=parallel_config,
            is_fsdp=is_fsdp,
            model_type="i2v",
        )

        self.config = _TransformerConfigI2V()
        self._weights_loaded = False

    @property
    def dtype(self) -> torch.dtype:
        return torch.bfloat16

    @contextmanager
    def cache_context(self, cache_name: str):
        """
        No-op — SkyReelsV2ImageToVideoPipeline wraps transformer calls in
        cache_context() for KV-cache sharing between CFG passes.  Our TTNN
        backend has no KV caching.
        """
        yield

    def _set_ar_attention(self, causal_block_size: int):
        """Called by the DF pipeline to set causal block size.  No-op here."""
        self.config.num_frame_per_block = causal_block_size

    def load_i2v_weights(self, checkpoint_path: str):
        """
        Load transformer weights from a raw WAN 2.1 I2V checkpoint directory.

        The checkpoint is sharded across model-XXXXX-of-00014.safetensors files.
        All shards are merged into a single state_dict, re-keyed via
        _map_raw_wan_i2v_to_diffusers(), then loaded into the TTNN model.

        Args:
            checkpoint_path: Local directory (or HF cache path) containing
                             model.safetensors.index.json and all shard files.
        """
        from safetensors.torch import load_file

        index_path = Path(checkpoint_path) / "model.safetensors.index.json"
        if not index_path.exists():
            raise FileNotFoundError(
                f"I2V checkpoint index not found at {index_path}. "
                f"Expected raw WAN 2.1 format with model.safetensors.index.json."
            )

        import json
        with open(index_path) as f:
            index = json.load(f)

        # Collect unique shard file names
        shard_files = sorted(set(index["weight_map"].values()))
        print(f"[SkyReelsI2V] Loading transformer shards ({len(shard_files)} files) ...")

        raw_sd: dict = {}
        for shard_name in shard_files:
            shard_path = Path(checkpoint_path) / shard_name
            shard = load_file(str(shard_path), device="cpu")
            raw_sd.update(shard)
            del shard  # free as soon as merged

        print(f"[SkyReelsI2V] Raw checkpoint: {len(raw_sd)} tensors. Remapping keys ...")
        diffusers_sd = _map_raw_wan_i2v_to_diffusers(raw_sd)
        del raw_sd

        print(f"[SkyReelsI2V] After key mapping: {len(diffusers_sd)} tensors. "
              "Loading into TTNN model ...")
        self.ttnn_model.load_torch_state_dict(diffusers_sd)

        self._weights_loaded = True
        print("[SkyReelsI2V] TTNN I2V weights loaded.")

    def forward(
        self,
        hidden_states: torch.Tensor,
        timestep: torch.Tensor,
        encoder_hidden_states: torch.Tensor,
        encoder_hidden_states_image=None,   # CLIP features — silently ignored
        enable_diffusion_forcing: bool = False,
        fps=None,
        return_dict: bool = True,
        attention_kwargs=None,
    ):
        """
        Drop-in for SkyReelsV2Transformer3DModel.forward() in I2V mode.

        hidden_states:          (B, 36, F, H, W) — already concatenated by pipeline:
                                  ch 0:15  = noisy video latents
                                  ch 16:31 = VAE-encoded conditioning frame (repeated)
                                  ch 32:35 = binary mask (4ch)
        timestep:               (B,)       — denoising timestep
        encoder_hidden_states:  (B, L, 4096)  — UMT5 text encoder output
        encoder_hidden_states_image:  ignored (CLIP features, TTNN has no CLIP path)
        """
        if not self._weights_loaded:
            raise RuntimeError("Call load_i2v_weights() before forward().")

        # Split 36-channel input back into spatial (16ch) and y (20ch).
        # The TTNN WanTransformer3DModel for i2v expects these separately
        # and concatenates them internally before patch embedding.
        spatial = hidden_states[:, :16, ...]   # noisy video latents
        y       = hidden_states[:, 16:, ...]   # image conditioning (16ch) + mask (4ch)

        # Convert text encoder output to TTNN 4D: (B, L, D) → (1, B, L, D)
        enc_4d = encoder_hidden_states.unsqueeze(0).to(torch.bfloat16)
        tt_prompt = bf16_tensor(enc_4d, device=self.mesh_device)

        noise_pred = self.ttnn_model(
            spatial=spatial,
            prompt=tt_prompt,
            timestep=timestep,
            y=y,
        )

        if return_dict:
            return {"sample": noise_pred}
        return (noise_pred,)


# ---------------------------------------------------------------------------
# SkyReelsI2VPipeline — server-compatible wrapper
# ---------------------------------------------------------------------------

class SkyReelsI2VPipeline:
    """
    TT-hardware SkyReels-V2-I2V-14B-540P image-to-video pipeline.

    Wraps SkyReelsI2VTTNNTransformer inside a diffusers
    SkyReelsV2ImageToVideoPipeline.  Exposes create_pipeline() and __call__()
    matching the WanPipeline interface so TTSkyReelsI2VRunner follows the same
    pattern as TTSkyReelsRunner.

    The `image` parameter to __call__() accepts:
      - PIL.Image.Image
      - numpy array (H, W, C) or (H, W) in uint8 or float32
      - str / pathlib.Path (file path)
      - None (uses a black frame — for warmup only)
    """

    CHECKPOINT = "Skywork/SkyReels-V2-I2V-14B-540P"

    def __init__(
        self,
        diffusers_pipe,
        mesh_device: ttnn.MeshDevice,
        tt_vae: WanDecoder,
        vae_parallel_config: VaeHWParallelConfig,
        vae_ccl_manager: CCLManager,
        checkpoint_path: str,
    ):
        self._pipe = diffusers_pipe
        self.mesh_device = mesh_device
        self.tt_vae = tt_vae
        self.vae_parallel_config = vae_parallel_config
        self.vae_ccl_manager = vae_ccl_manager
        self._checkpoint_path = checkpoint_path

    @staticmethod
    def create_pipeline(
        mesh_device: ttnn.MeshDevice,
        checkpoint_name: str = None,
    ) -> "SkyReelsI2VPipeline":
        """
        Create a SkyReelsI2VPipeline backed by TTNN WAN I2V transformer.

        Args:
            mesh_device:     Already-opened ttnn.MeshDevice (managed by runner).
                             Fabric initialization has already been done by
                             TTSkyReelsI2VRunner._configure_fabric before this.
            checkpoint_name: HF repo ID or local path to the I2V-14B checkpoint.
                             Can be "Skywork/SkyReels-V2-I2V-14B-540P" (HF) or
                             a local cache path.  Defaults to MODEL_WEIGHTS_DIR.
        """
        if checkpoint_name is None:
            checkpoint_name = os.environ.get(
                "MODEL_WEIGHTS_DIR", SkyReelsI2VPipeline.CHECKPOINT
            )

        # Resolve to a local filesystem path (handles HF repo ID or local path)
        checkpoint_path = _resolve_checkpoint_path(checkpoint_name)

        mesh_shape = tuple(mesh_device.shape)  # e.g. (1, 4) or (2, 2)
        tp_factor = mesh_shape[1]
        sp_factor = mesh_shape[0]

        parallel_config = DiTParallelConfig(
            cfg_parallel=None,
            tensor_parallel=ParallelFactor(factor=tp_factor, mesh_axis=1),
            sequence_parallel=ParallelFactor(factor=sp_factor, mesh_axis=0),
        )

        ccl_manager = CCLManager(
            mesh_device=mesh_device,
            num_links=2,
            topology=ttnn.Topology.Linear,
        )

        print(f"[SkyReelsI2V] Building TTNN I2V transformer (mesh={mesh_shape}) ...")
        ttnn_transformer = SkyReelsI2VTTNNTransformer(
            mesh_device=mesh_device,
            parallel_config=parallel_config,
            ccl_manager=ccl_manager,
            is_fsdp=False,
        )
        ttnn_transformer.load_i2v_weights(checkpoint_path)

        print("[SkyReelsI2V] Loading diffusers pipeline components ...")
        diffusers_pipe = _build_diffusers_i2v_pipeline(
            checkpoint_path=checkpoint_path,
            ttnn_transformer=ttnn_transformer,
        )

        # ── TTNN VAE decoder ─────────────────────────────────────────────────
        # SkyReels-V2-I2V-14B shares the same VAE architecture as WAN 2.2 T2V.
        # Use the same WanDecoder that WanPipeline uses, with weights already
        # loaded into diffusers_pipe.vae (replaced from Wan2.1_VAE.pth above).
        # The VAE runs on TTNN rather than CPU, eliminating the multi-minute
        # CPU decode that follows the diffusion steps.
        print("[SkyReelsI2V] Building TTNN VAE decoder ...")
        vae_ccl_manager = CCLManager(
            mesh_device=mesh_device,
            num_links=2,
            topology=ttnn.Topology.Linear,
        )
        # tp_axis=1, sp_axis=0 — matches Blackhole mesh convention used by the
        # transformer (DiTParallelConfig above uses the same axis assignment).
        vae_parallel_config = VaeHWParallelConfig(
            height_parallel=ParallelFactor(factor=tp_factor, mesh_axis=1),
            width_parallel=ParallelFactor(factor=sp_factor, mesh_axis=0),
        )
        vae_cfg = diffusers_pipe.vae.config
        tt_vae = WanDecoder(
            base_dim=vae_cfg.base_dim,
            z_dim=vae_cfg.z_dim,
            dim_mult=vae_cfg.dim_mult,
            num_res_blocks=vae_cfg.num_res_blocks,
            attn_scales=vae_cfg.attn_scales,
            temperal_downsample=vae_cfg.temperal_downsample,
            out_channels=vae_cfg.out_channels,
            is_residual=vae_cfg.is_residual,
            mesh_device=mesh_device,
            ccl_manager=vae_ccl_manager,
            parallel_config=vae_parallel_config,
            dtype=ttnn.bfloat16,
        )

        print("[SkyReelsI2V] I2V pipeline ready.")
        return SkyReelsI2VPipeline(
            diffusers_pipe,
            mesh_device,
            tt_vae,
            vae_parallel_config,
            vae_ccl_manager,
            checkpoint_path,
        )

    def _prepare_vae(self) -> None:
        """Load the TTNN VAE weights onto the mesh device (cached after first load)."""
        _ttnn_cache.load_model(
            self.tt_vae,
            model_name=os.path.basename(self._checkpoint_path),
            subfolder="vae",
            parallel_config=self.vae_parallel_config,
            mesh_shape=tuple(self.mesh_device.shape),
            get_torch_state_dict=lambda: self._pipe.vae.state_dict(),
        )

    def _decode_latents_ttnn(self, latents: torch.Tensor) -> np.ndarray:
        """
        Decode video latents with the TTNN WanDecoder.

        Mirrors the VAE decode block in WanPipeline.__call__() exactly:
          1. Denormalise using vae.config.latents_mean / latents_std.
          2. Permute BCTHW → BTHWC.
          3. Pad channels and height for TTNN parallelism.
          4. Shard across the mesh and run the TTNN decoder.
          5. Gather back to host, strip padding, postprocess.

        Args:
            latents: (B, 16, T_lat, H_lat, W_lat) tensor in scheduler latent space.

        Returns:
            (1, T, H, W, C) float32 numpy array with pixel values in [0, 1].
        """
        vae_cfg = self._pipe.vae.config

        # Denormalise: scheduler latents → VAE input space
        latents = latents.to(self._pipe.vae.dtype)
        latents_mean = (
            torch.tensor(vae_cfg.latents_mean)
            .view(1, vae_cfg.z_dim, 1, 1, 1)
            .to(latents.device, latents.dtype)
        )
        latents_std = (
            1.0
            / torch.tensor(vae_cfg.latents_std)
            .view(1, vae_cfg.z_dim, 1, 1, 1)
            .to(latents.device, latents.dtype)
        )
        latents = latents / latents_std + latents_mean

        # Load TTNN VAE weights (no-op after first call, served from disk cache)
        self._prepare_vae()

        # Shard and dispatch to TTNN (same logic as WanPipeline decode block)
        tt_latents_BTHWC = latents.permute(0, 2, 3, 4, 1)          # BCTHW → BTHWC
        tt_latents_BTHWC = conv_pad_in_channels(tt_latents_BTHWC)
        tt_latents_BTHWC, logical_h = conv_pad_height(
            tt_latents_BTHWC, self.vae_parallel_config.height_parallel.factor
        )
        tt_latents_BTHWC = typed_tensor_2dshard(
            tt_latents_BTHWC,
            self.mesh_device,
            layout=ttnn.ROW_MAJOR_LAYOUT,
            shard_mapping={
                self.vae_parallel_config.height_parallel.mesh_axis: 2,
                self.vae_parallel_config.width_parallel.mesh_axis: 3,
            },
            dtype=self.tt_vae.dtype,
        )

        tt_video_BCTHW, new_logical_h = self.tt_vae(tt_latents_BTHWC, logical_h)

        # Gather shards back to host and strip height padding
        concat_dims = [None, None]
        concat_dims[self.vae_parallel_config.height_parallel.mesh_axis] = 3
        concat_dims[self.vae_parallel_config.width_parallel.mesh_axis] = 4
        video_torch = self.vae_ccl_manager.device_to_host(tt_video_BCTHW, concat_dims)
        video_torch = video_torch[:, :, :, :new_logical_h, :]       # (B, C, T, H, W)

        # Postprocess via the diffusers pipeline's VideoProcessor, then convert
        # to (1, T, H, W, C) numpy in [0, 1] — same output contract as the
        # previous CPU VAE path.
        video = self._pipe.video_processor.postprocess_video(video_torch, output_type="pil")
        frames_pil = video[0]  # list of T PIL Images
        frames_np = np.stack(
            [np.array(f, dtype=np.float32) / 255.0 for f in frames_pil]
        )  # (T, H, W, C)
        return frames_np[np.newaxis]  # (1, T, H, W, C)

    def __call__(
        self,
        prompt: str,
        image=None,
        negative_prompt: str = "",
        height: int = 544,
        width: int = 960,
        num_frames: int = 97,
        num_inference_steps: int = 20,
        guidance_scale: float = 5.0,
        seed: int = 0,
        **kwargs,
    ) -> np.ndarray:
        """
        Run I2V denoising and return video frames as a numpy array.

        Returns:
            numpy array of shape (1, T, H, W, C) with pixel values in [0, 1].

        Args:
            prompt:               Text prompt describing the video.
            image:                Conditioning image for I2V.  If None (warmup),
                                  a black frame of the target resolution is used.
            negative_prompt:      Negative guidance text.
            height / width:       Output resolution in pixels.
            num_frames:           Number of output frames. Must satisfy (N-1)%4==0.
            num_inference_steps:  Denoising steps.
            guidance_scale:       CFG scale (5.0 is SkyReels I2V default).
            seed:                 Random seed.
        """
        from PIL import Image

        # Build conditioning image — use black frame if none provided (warmup)
        if image is None:
            cond_image = Image.fromarray(
                np.zeros((height, width, 3), dtype=np.uint8)
            )
        elif isinstance(image, (str, Path)):
            cond_image = Image.open(image).convert("RGB")
        elif isinstance(image, np.ndarray):
            if image.dtype != np.uint8:
                image = (image * 255).clip(0, 255).astype(np.uint8)
            cond_image = Image.fromarray(image).convert("RGB")
        else:
            # Assume PIL.Image or anything the pipeline can accept
            cond_image = image

        generator = torch.Generator().manual_seed(seed)

        # Run the diffusion loop only — VAE decode happens on TTNN below.
        output = self._pipe(
            image=cond_image,
            prompt=prompt,
            negative_prompt=negative_prompt if negative_prompt else None,
            height=height,
            width=width,
            num_frames=num_frames,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            generator=generator,
            output_type="latent",
        )

        # output.frames is (B, 16, T_lat, H_lat, W_lat) in latent space.
        # Decode to pixel space on TTNN hardware instead of on CPU.
        return self._decode_latents_ttnn(output.frames)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_checkpoint_path(checkpoint_name: str) -> str:
    """
    Return a local filesystem path for the checkpoint.

    If checkpoint_name is already a local directory, return it as-is.
    If it looks like a HuggingFace repo ID (e.g. "Skywork/SkyReels-V2-I2V-14B-540P"),
    resolve it via the HF cache.
    """
    p = Path(checkpoint_name)
    if p.exists() and p.is_dir():
        return str(p)

    # Resolve from HF hub cache
    try:
        from huggingface_hub import snapshot_download
        return snapshot_download(
            repo_id=checkpoint_name,
            local_files_only=True,   # Use cached version only (no internet needed)
        )
    except Exception:
        # Fall back: try raw path
        return checkpoint_name


def _build_diffusers_i2v_pipeline(checkpoint_path: str, ttnn_transformer):
    """
    Assemble a SkyReelsV2ImageToVideoPipeline from checkpoint components.

    Component loading strategy:
      - UMT5 tokenizer:    google/umt5-xxl/ subfolder in the I2V checkpoint
      - T5 text encoder:   architecture from WAN 2.2 A14B diffusers (same arch),
                           weights from models_t5_umt5-xxl-enc-bf16.pth
      - VAE:               architecture from WAN 2.2 A14B diffusers (same arch),
                           weights from Wan2.1_VAE.pth
      - CLIP encoder:      _DummyCLIPImageEncoder (returns zeros; TTNN ignores it)
      - CLIPProcessor:     CLIPImageProcessor with standard ViT-H/14 preprocessing
      - Scheduler:         UniPCMultistepScheduler, flow_shift=5.0
    """
    from diffusers import AutoencoderKLWan, UniPCMultistepScheduler
    from diffusers.image_processor import VaeImageProcessor
    from diffusers.pipelines.skyreels_v2.pipeline_skyreels_v2_i2v import (
        SkyReelsV2ImageToVideoPipeline,
    )
    from transformers import AutoTokenizer, CLIPImageProcessor, UMT5EncoderModel

    checkpoint_path = Path(checkpoint_path)

    # ── Tokenizer (from subfolder in the I2V checkpoint) ──────────────────
    tokenizer_dir = checkpoint_path / "google" / "umt5-xxl"
    if not tokenizer_dir.exists():
        # Fall back to the canonical HF checkpoint
        tokenizer_path = "google/umt5-xxl"
        print(f"[SkyReelsI2V] Tokenizer subfolder missing; using '{tokenizer_path}' from HF cache")
    else:
        tokenizer_path = str(tokenizer_dir)
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)

    # ── T5 text encoder ────────────────────────────────────────────────────
    # Load architecture from WAN 2.2 (same UMT5-XXL architecture), then
    # replace weights with the I2V checkpoint's T5 weights.
    print(f"[SkyReelsI2V] Loading T5 text encoder architecture from {_WAN22_ARCH_CHECKPOINT} ...")
    text_encoder = UMT5EncoderModel.from_pretrained(
        _WAN22_ARCH_CHECKPOINT,
        subfolder="text_encoder",
        torch_dtype=torch.bfloat16,
    )
    t5_pth = checkpoint_path / "models_t5_umt5-xxl-enc-bf16.pth"
    if t5_pth.exists():
        print(f"[SkyReelsI2V] Loading T5 weights from {t5_pth} ...")
        t5_state = torch.load(str(t5_pth), map_location="cpu", weights_only=True)
        text_encoder.load_state_dict(t5_state, strict=False)
        del t5_state
    else:
        print(f"[SkyReelsI2V] WARNING: {t5_pth} not found; using WAN 2.2 T5 weights")

    # ── VAE ─────────────────────────────────────────────────────────────────
    print(f"[SkyReelsI2V] Loading VAE architecture from {_WAN22_ARCH_CHECKPOINT} ...")
    vae = AutoencoderKLWan.from_pretrained(
        _WAN22_ARCH_CHECKPOINT,
        subfolder="vae",
        torch_dtype=torch.float32,
    )
    vae_pth = checkpoint_path / "Wan2.1_VAE.pth"
    if vae_pth.exists():
        print(f"[SkyReelsI2V] Loading VAE weights from {vae_pth} ...")
        vae_state = torch.load(str(vae_pth), map_location="cpu", weights_only=True)
        vae.load_state_dict(vae_state, strict=False)
        del vae_state
    else:
        print(f"[SkyReelsI2V] WARNING: {vae_pth} not found; using WAN 2.2 VAE weights")

    # ── Scheduler ───────────────────────────────────────────────────────────
    # Borrow the scheduler config from WAN 2.2 then override flow_shift.
    # I2V uses flow_shift=5.0 (T2V uses 8.0).
    scheduler_base = UniPCMultistepScheduler.from_pretrained(
        _WAN22_ARCH_CHECKPOINT, subfolder="scheduler"
    )
    scheduler = UniPCMultistepScheduler.from_config(
        scheduler_base.config, flow_shift=5.0
    )

    # ── CLIP image encoder (dummy) ───────────────────────────────────────────
    # The TTNN transformer ignores encoder_hidden_states_image; image conditioning
    # flows through the 36-channel spatial concatenation instead.
    image_encoder = _DummyCLIPImageEncoder()

    # ── CLIPImageProcessor ───────────────────────────────────────────────────
    # Standard ViT-H/14 preprocessing (224×224, CLIP normalization).
    # resize_mode="shortest_edge" then center-crop.
    image_processor = CLIPImageProcessor(
        size={"shortest_edge": 224},
        crop_size={"height": 224, "width": 224},
        do_center_crop=True,
        do_normalize=True,
        image_mean=[0.48145466, 0.4578275,  0.40821073],
        image_std= [0.26862954, 0.26130258, 0.27577711],
        do_resize=True,
        resample=3,           # BICUBIC
    )

    # ── Assemble pipeline ────────────────────────────────────────────────────
    pipe = SkyReelsV2ImageToVideoPipeline(
        tokenizer=tokenizer,
        text_encoder=text_encoder,
        image_encoder=image_encoder,
        image_processor=image_processor,
        transformer=ttnn_transformer,
        vae=vae,
        scheduler=scheduler,
    )

    # Compatibility shim: _execution_device is a read-only property in this
    # diffusers version whose getter fails because our TTNN transformer has no
    # standard PyTorch parameters for device inference.  Patch the class-level
    # property to always return CPU — all non-TTNN components (T5, VAE) run on
    # CPU, and TTNN manages the chip device itself.
    try:
        _ = pipe._execution_device
    except AttributeError:
        SkyReelsV2ImageToVideoPipeline._execution_device = property(
            lambda self: torch.device("cpu")
        )

    return pipe
