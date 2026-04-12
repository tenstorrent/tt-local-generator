# app/generation_config.py
"""
Pure generation configuration tables — no GTK imports.

CLIP_LENGTH_FRAMES: maps (model_key, slot_name) -> frame_count
  model_key: "wan2" | "skyreels"
  slot_name: "short" | "standard" | "long" | "extended"
  Valid Wan2.2 counts follow 4k+1: 33, 49, 65, 81, 97, 121, 193...
  Valid SkyReels counts follow (N-1)%4==0: 9, 33, 65, 97...

MODELS_WITH_FIXED_FRAMES: models whose frame count is hard-coded in the runner
  and cannot be overridden via num_frames in the request.

CLIP_SLOTS: ordered list of slot names for display
QUALITY_PRESETS: ordered list of (slot_name, steps, label) tuples
"""

CLIP_LENGTH_FRAMES: dict[tuple[str, str], int] = {
    ("wan2",      "short"):    49,
    ("wan2",      "standard"): 81,
    ("wan2",      "long"):     121,
    ("wan2",      "extended"): 193,
    ("skyreels",  "short"):    9,
    ("skyreels",  "standard"): 33,
    ("skyreels",  "long"):     65,
    ("skyreels",  "extended"): 97,
}

# Models where the runner ignores num_frames -- show a locked single button.
# Value is the hard-coded frame count so the UI can display it.
MODELS_WITH_FIXED_FRAMES: dict[str, int] = {
    "mochi": 168,   # TTMochi1Runner hard-codes num_frames=168; TODO: parameterise
}

CLIP_SLOTS: list[str] = ["short", "standard", "long", "extended"]

# (slot_name, inference_steps, display_label)
QUALITY_PRESETS: list[tuple[str, int, str]] = [
    ("fast",      10, "Fast"),
    ("standard",  30, "Standard"),
    ("cinematic", 40, "Cinematic"),
]

# Seconds per frame at 24 fps (used for display labels)
_FPS = 24


def clip_frames(model_key: str, slot: str) -> "int | None":
    """Return frame count for (model_key, slot), or None if model uses fixed frames.

    Returns the standard-slot value if slot is unrecognised.
    Returns None for models in MODELS_WITH_FIXED_FRAMES (use their fixed count instead).
    Returns None for unknown model keys (key absent from both CLIP_LENGTH_FRAMES and
    MODELS_WITH_FIXED_FRAMES) — callers should treat this the same as a fixed-frame model
    and fall back to a sensible default.
    """
    if model_key in MODELS_WITH_FIXED_FRAMES:
        return None
    frames = CLIP_LENGTH_FRAMES.get((model_key, slot))
    if frames is None:
        frames = CLIP_LENGTH_FRAMES.get((model_key, "standard"))
    return frames


def quality_steps(slot: str) -> int:
    """Return inference step count for a quality slot name. Defaults to standard steps."""
    for name, steps, _ in QUALITY_PRESETS:
        if name == slot:
            return steps
    # Fall back to standard preset steps so this stays in sync if QUALITY_PRESETS changes.
    for name, steps, _ in QUALITY_PRESETS:
        if name == "standard":
            return steps
    return 30  # should never reach here if QUALITY_PRESETS is well-formed


def slot_for_steps(steps: int) -> "str | None":
    """Return the quality slot name for an exact step count, or None if no match."""
    for name, s, _ in QUALITY_PRESETS:
        if s == steps:
            return name
    return None


def clip_label(model_key: str, slot: str) -> str:
    """Human-readable sublabel for a CLIP LENGTH button, e.g. '3.4 s · 81 f'."""
    frames = clip_frames(model_key, slot)
    if frames is None:
        fixed = MODELS_WITH_FIXED_FRAMES.get(model_key, 0)
        return f"{fixed / _FPS:.1f} s · {fixed} f  (fixed)"
    secs = frames / _FPS
    return f"{secs:.1f} s · {frames} f"
