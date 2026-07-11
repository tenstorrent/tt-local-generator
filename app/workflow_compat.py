# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Workflow compatibility layer.

Maps ComfyUI node class_types to tt-local-generator equivalents.
validate_spec() runs a preflight check before a pipeline run, returning
a ValidationResult with warnings (skippable nodes) and blocking errors
(unknown required nodes).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Compatibility map ─────────────────────────────────────────────────────────
#
# Three tiers:
#   ttlg == class_type  → exact native node, runs as-is
#   ttlg is a str       → mapped to a different TTLG node (with input translation)
#   ttlg is None        → node is skippable (omit with warning)
#
# optional=True  → safe to skip; pipeline continues without this node's output
# optional=False → required; if ttlg is None the pipeline cannot run

COMPATIBILITY_MAP: dict[str, dict] = {
    # ── Tier 1: Native tt-local-generator nodes ───────────────────────────────
    "TTLGTextToImage":      {"ttlg": "TTLGTextToImage",      "optional": False},
    "TTLGImageToVideo":     {"ttlg": "TTLGImageToVideo",     "optional": False},
    "TTLGGenerateText":     {"ttlg": "TTLGGenerateText",     "optional": False},
    "TTLGCaptionImage":     {"ttlg": "TTLGCaptionImage",     "optional": True},
    "TTLGRemoveBackground": {"ttlg": "TTLGRemoveBackground", "optional": True},
    "TTLGEstimateDepth":    {"ttlg": "TTLGEstimateDepth",    "optional": True},
    "TTLGPromptCompose":    {"ttlg": "TTLGPromptCompose",    "optional": False},
    "TTLGAddToPlaylist":    {"ttlg": "TTLGAddToPlaylist",    "optional": False},
    "TTLGComposite":        {"ttlg": "TTLGComposite",        "optional": True},
    "TTLGSVGRender":        {"ttlg": "TTLGSVGRender",        "optional": True},

    # TTLGArtgenGenerate — runs one artgen plugin (verse/palette/ansi/codeart/…)
    # selected by inputs.plugin. Marked optional=True because a pipeline can
    # still be useful without a generative-art step; validate_spec additionally
    # checks that inputs.plugin names a registered artgen generator (see the
    # plugin-name check below, in validate_spec).
    #
    # Output keys produced by this node (consumed downstream by node inputs
    # that reference "<node_id>.<key>"):
    #   artifact_path — always present; path to the raw generated artifact
    #                   (the file the plugin's output_ext implies, e.g. .txt/.svg/.ans)
    #   png_path      — present only when the plugin's output_ext is a raster
    #                   image extension (e.g. palette/landscape/skyline SVGs
    #                   rendered to PNG); absent for text-only plugins
    #   text          — present only when the plugin's output_ext is a text
    #                   extension (.txt/.ans/.md/…); the artifact's raw string
    #                   content, for nodes that want to consume it directly
    #                   (e.g. TTLGPromptCompose) without reading the file back
    "TTLGArtgenGenerate": {"ttlg": "TTLGArtgenGenerate", "optional": True},

    # TTLGAnimateDiff — Wan2.2-Animate-14B character animation to GIF.
    #
    # Output keys:
    #   gif_path — path to the rendered GIF
    #
    # Recognized inputs:
    #   prompt             — text guidance (optional; style only)
    #   frames             — frame count
    #   steps              — diffusion steps
    #   seed               — RNG seed (single-chip / base seed for multichip)
    #   negative_prompt    — negative guidance text
    #   multichip_mode     — how multiple chips split/collaborate on the run
    #   per_chip_prompts   — list of per-chip prompt overrides (multichip)
    #   seed_spread        — per-chip seed offset strategy (multichip)
    #   ramp               — enable prompt/seed ramping across the frame range
    #   ramp_lo            — ramp start value
    #   ramp_hi            — ramp end value
    #   stitch_order       — order chip outputs are stitched into the final GIF
    #   prompt_schedule    — list of (frame, prompt) pairs for time-varying prompts
    #   loop               — whether the output GIF loops
    "TTLGAnimateDiff": {"ttlg": "TTLGAnimateDiff", "optional": True},

    # ── Tier 2: Mapped ComfyUI standard nodes ─────────────────────────────────
    "KSampler": {
        "ttlg": "TTLGTextToImage", "optional": False,
        "note": "KSampler mapped to TTLGTextToImage — seed/steps/cfg adapted",
    },
    "KSamplerAdvanced": {
        "ttlg": "TTLGTextToImage", "optional": False,
        "note": "KSamplerAdvanced mapped to TTLGTextToImage",
    },
    "CLIPTextEncode": {
        "ttlg": "TTLGPromptCompose", "optional": True,
        "note": "prompt text passed through directly",
    },
    "VAEDecode": {
        "ttlg": None, "optional": True,
        "note": "VAE decode is internal to the TTNN pipeline — node skipped",
    },
    "VAEEncode": {
        "ttlg": None, "optional": True,
        "note": "VAE encode is internal — node skipped",
    },
    "LoadImage": {
        "ttlg": None, "optional": True,
        "note": "use input_image param on the job instead",
    },
    "SaveImage": {
        "ttlg": "TTLGAddToPlaylist", "optional": True,
        "note": "mapped to TTLGAddToPlaylist",
    },

    # ── Tier 3: Skippable — not supported, safe to omit ───────────────────────
    "ControlNetApply": {
        "ttlg": None, "optional": True,
        "note": "ControlNet not supported — node skipped, base model used",
    },
    "ControlNetLoader": {
        "ttlg": None, "optional": True,
        "note": "ControlNet not supported",
    },
    "IPAdapterApply": {
        "ttlg": None, "optional": True,
        "note": "IP-Adapter not supported — node skipped",
    },
    "UpscaleImage": {
        "ttlg": None, "optional": True,
        "note": "upscaling not supported — original resolution kept",
    },
    "ImageScale": {
        "ttlg": None, "optional": True,
        "note": "image scaling not supported — original size kept",
    },
    "LoraLoader": {
        "ttlg": None, "optional": True,
        "note": "LoRA not supported — base model weights used",
    },
    "CheckpointLoaderSimple": {
        "ttlg": None, "optional": True,
        "note": "checkpoint loading handled by tt-inference-server — node skipped",
    },
}


# ── Validation result ─────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Result of a preflight spec validation.

    ok=True means the pipeline can run (possibly with skipped nodes).
    ok=False means a required node is unknown — the pipeline cannot run.
    """
    ok: bool = True
    warnings: list[str] = field(default_factory=list)   # skippable unknown nodes
    mappings: list[str] = field(default_factory=list)   # nodes substituted
    blocking: list[str] = field(default_factory=list)   # unknown required nodes

    def summary(self) -> str:
        parts = []
        if self.blocking:
            parts.append(
                f"❌ {len(self.blocking)} required node(s) not supported: "
                + ", ".join(self.blocking)
            )
        if self.warnings:
            parts.append(
                f"⚠️ {len(self.warnings)} optional node(s) will be skipped: "
                + ", ".join(self.warnings)
            )
        if self.mappings:
            parts.append(
                f"↔ {len(self.mappings)} node(s) mapped to TTLG equivalents: "
                + ", ".join(self.mappings)
            )
        if not parts:
            return "✅ All nodes supported."
        return "\n".join(parts)


# ── validate_spec ─────────────────────────────────────────────────────────────

def validate_spec(spec_path: str) -> ValidationResult:
    """Run a preflight check on a workflow spec file.

    Returns a ValidationResult indicating whether the spec can run,
    which nodes will be skipped (with warnings), and which nodes block
    the run entirely (unknown + required).
    """
    result = ValidationResult()

    try:
        data = json.loads(Path(spec_path).read_text())
    except Exception as e:
        result.ok = False
        result.blocking.append(f"Cannot read spec: {e}")
        return result

    # Best-effort load of the registered artgen plugin names, used below to
    # validate TTLGArtgenGenerate's inputs.plugin. If artgen can't be imported
    # (e.g. validate_spec is run outside the full app environment) or its
    # generator registry is empty, skip the plugin check entirely rather than
    # false-rejecting an otherwise-valid spec.
    try:
        from artgen import all_names as _artgen_all_names
        known_artgen_plugins = set(_artgen_all_names())
    except Exception:
        known_artgen_plugins = set()

    for node_id, node in data.items():
        if node_id.startswith("_") or not isinstance(node, dict):
            continue

        class_type = node.get("class_type", "")
        if not class_type:
            continue

        if class_type == "TTLGArtgenGenerate":
            plugin = (node.get("inputs") or {}).get("plugin")
            if not plugin:
                result.ok = False
                result.blocking.append(
                    f"TTLGArtgenGenerate (node {node_id}) — missing required 'plugin' input"
                )
            elif known_artgen_plugins and plugin not in known_artgen_plugins:
                result.ok = False
                result.blocking.append(
                    f"TTLGArtgenGenerate plugin '{plugin}' (node {node_id}) — unknown artgen plugin"
                )

        entry = COMPATIBILITY_MAP.get(class_type)
        is_required = node.get("_required", False)

        if entry is None:
            # Unknown node type
            if is_required:
                result.ok = False
                result.blocking.append(f"{class_type} (node {node_id})")
            else:
                result.warnings.append(
                    f"{class_type} (node {node_id}) — unknown type, will be skipped"
                )
        elif entry["ttlg"] is None:
            # Known skippable
            if not entry.get("optional", True):
                result.ok = False
                result.blocking.append(
                    f"{class_type} (node {node_id}) — {entry.get('note','not supported')}"
                )
            else:
                result.warnings.append(
                    f"{class_type} (node {node_id}) — {entry.get('note','skipped')}"
                )
        elif entry["ttlg"] != class_type:
            # Mapped to different type
            result.mappings.append(
                f"{class_type} → {entry['ttlg']} (node {node_id})"
            )
        # else: exact native match — no action needed

    return result
