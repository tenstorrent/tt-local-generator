#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Prompt generation client — three-tier system backed by generate_prompt.py.

Tier 1 (always):          Algorithmic assembly from word_banks.py
Tier 2 (with markovify):  Markov chain trained on prompts/markov_seed.txt
Tier 3 (Qwen server up):  LLM polishes the algo/markov slug for natural flow

check_health() still reports whether the Qwen LLM server is available, which
the UI uses to show the enhanced/algo-only dot state.  generate_prompt() never
raises due to the LLM being down — algo/markov always produces a result.

This module has no GTK dependencies and is safe to import without a display.

Functions:
    check_health(base_url) -> bool
    generate_prompt(source, seed_text, system_prompt, base_url, max_tokens) -> str
"""
import json as _json
import re as _re
import urllib.request
import urllib.error

_DEFAULT_URL = "http://127.0.0.1:8001"


def configure_llm_url(base_url: str) -> None:
    """
    Patch the module-level LLM URL constants in generate_prompt so that
    _llm_available() and _llm_polish() hit the correct host.

    Called once at startup when --server points at a non-local machine.
    Must be called before any generate_prompt() or check_health() call.
    """
    import generate_prompt as _gp
    base = base_url.rstrip("/")
    _gp.LLM_URL        = f"{base}/v1/chat/completions"
    _gp.LLM_HEALTH_URL = f"{base}/health"


def check_health(base_url: str = _DEFAULT_URL) -> bool:
    """
    Return True if the Qwen LLM server is up and the model is loaded.

    Used by the health-poll worker to drive the inspire dot colour:
        True  → green "ready" (full three-tier generation available)
        False → muted "algo only" (algo/markov still works, LLM not available)

    Calls GET /health and checks the model_ready field.  Returns False on
    any network error, non-200 status, or missing/false model_ready field.
    """
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=3) as r:
            return bool(_json.loads(r.read()).get("model_ready"))
    except Exception:
        return False


def generate_prompt(
    source: str,
    seed_text: str = "",
    system_prompt: str = "",   # kept for backward compat; generate_prompt.py uses its own
    base_url: str = _DEFAULT_URL,
    max_tokens: int = 150,
) -> str:
    """
    Generate a cinematic prompt using the three-tier system in generate_prompt.py.

    When seed_text is provided (inspire mode with existing text in the box):
        - If the LLM is available: polish the seed text with the LLM and return it.
        - If the LLM is down or polish fails: fall through to fresh generation
          (same as empty seed).  Returning the seed unchanged is never useful —
          the user already has it and the button would appear to do nothing.

    When seed_text is empty (attractor mode, or inspire with empty box):
        - Run algo → markov → optional LLM polish automatically.
        - Returns the best tier available; never raises on LLM unavailability.

    Args:
        source:        "video", "image", or "animate"
        seed_text:     Text to polish (inspire mode).  Empty = fully random.
        system_prompt: Ignored — generate_prompt.py uses focused polishing prompts.
        base_url:      Unused — generate_prompt.py reads LLM_URL directly.
        max_tokens:    Unused — generate_prompt.py uses its own token limits.

    Returns:
        Prompt string, always non-empty.

    Raises:
        RuntimeError: Only if word_banks.py or generate_prompt.py cannot be
                      imported (i.e. the package itself is broken).
    """
    if source in _MUSIC_SOURCES:
        return _generate_music_text(source, seed_text, max_tokens)

    import generate_prompt as _gp  # noqa: PLC0415 (lazy import — no GTK needed at module load)

    if seed_text.strip():
        # Inspire mode: user provided seed — try to polish it with the LLM
        if _gp._llm_available():
            polished = _gp._llm_polish(seed_text.strip(), source)
            if polished:
                return polished
        # LLM unavailable or polish returned nothing — fall through to fresh
        # generation.  Returning the seed unchanged is useless (user already has
        # it) and confusing (button flashes with no visible effect).

    # No seed, or seed-mode fallback — three-tier generation: algo → markov → LLM.
    # Pass director style settings so prompt style preferences are respected.
    try:
        from app_settings import settings as _settings
        director_prob = float(_settings.get("director_style_prob"))
        director_pin = str(_settings.get("director_pin") or "")
    except Exception:
        director_prob = 0.33
        director_pin = ""
    result = _gp.generate(
        prompt_type=source, mode="markov", enhance=True,
        director_prob=director_prob, director_pin=director_pin,
    )
    return result["prompt"]


def llm_polish_or_none(source: str, seed_text: str) -> "str | None":
    """Polish *seed_text* into a prompt for *source* USING THE LLM ONLY.

    Returns the polished string, or None when the prompt LLM isn't reachable
    or produces nothing — so callers can fall back to a deterministic literal
    (e.g. palette_prompt.literal_prompt) instead of `generate_prompt`'s
    seed-ignoring algorithmic fallback. Never raises.
    """
    if source in _MUSIC_SOURCES:
        try:
            return _music_llm_text(source, seed_text.strip()) if seed_text.strip() else None
        except Exception:
            return None
    try:
        import generate_prompt as _gp  # lazy: no GTK/network at import
        if not seed_text.strip() or not _gp._llm_available():
            return None
        polished = _gp._llm_polish(seed_text.strip(), source)
        return polished or None
    except Exception:
        return None


# ── Music: style prompts and lyrics ───────────────────────────────────────────
#
# Lyrics need a real chat model; Qwen3-0.6B on the prompt server is too small to
# rely on.  So these use artgen.detect_artgen_endpoint(), which prefers the
# artgen chat model (any port, including tt-model-manager containers) and only
# then falls back to the prompt server on :8001.  With nothing reachable the
# LLM helpers return None and generate_prompt() falls back to a local result.

_MUSIC_SOURCES = ("music", "lyrics")

_MUSIC_SYSTEM = (
    "You write style prompts for an AI music generator. Given an idea, answer with "
    "one line of comma-separated tags: genre, mood, instruments, tempo, era. "
    "Under 30 words. No preamble, no quotes, no explanation."
)

_LYRICS_SYSTEM = (
    "You write song lyrics for an AI music generator. Put each structure tag on its "
    "own line, in square brackets: [Verse], [Chorus], [Bridge]. Use [Verse 1] and "
    "[Verse 2] if there are two verses. Write a short song: two verses and a chorus, "
    "four lines each, plain language, a clear rhyme or rhythm. Output only the lyrics "
    "with their tags. No title, no preamble, no explanation."
)

# Used only when no LLM is reachable and the caller wants a fresh style prompt.
_MUSIC_FALLBACK = (
    "synthwave, wistful, analog synthesizers, mid tempo 100 bpm, 1980s sound",
    "acoustic folk, warm, fingerpicked guitar, slow tempo 70 bpm",
    "lo-fi hip hop, dreamy, soft piano, mid tempo 85 bpm",
    "cinematic orchestral, triumphant, strings and brass, 120 bpm",
    "late-night jazz, smoky, brushed drums and upright bass, slow tempo",
)

_re_think = _re.compile(r"<think>.*?</think>", _re.DOTALL)

_LYRICS_SKELETON = "[Verse]\n\n[Chorus]\n"


def music_llm_available() -> bool:
    """True when a chat model is reachable for music prompts and lyrics."""
    try:
        import artgen  # noqa: PLC0415 (lazy: keeps this module cheap to import)
        url, _model = artgen.detect_artgen_endpoint()
        return bool(url)
    except Exception:
        return False


def _music_llm_text(source: str, seed_text: str) -> "str | None":
    """Ask the best reachable chat model for music/lyrics text. None on any failure."""
    import artgen  # noqa: PLC0415
    url, model = artgen.detect_artgen_endpoint()
    if not url:
        return None
    system = _LYRICS_SYSTEM if source == "lyrics" else _MUSIC_SYSTEM
    user = seed_text if seed_text else (
        "Write original lyrics." if source == "lyrics" else "Invent a fresh music style.")
    payload = _json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 700 if source == "lyrics" else 120,
        "temperature": 0.8,
    }).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = _json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception:
        return None
    text = _re_think.sub("", text or "").strip()
    return text or None


def _generate_music_text(source: str, seed_text: str, max_tokens: int) -> str:
    """generate_prompt() for source 'music' or 'lyrics'. Always non-empty.

    With a seed, the LLM expands it; without one it invents.  With no LLM,
    a typed seed is returned as it is (lyrics with no seed get a bare tagged
    skeleton, music with no seed a canned style line), so the caller always has something to put in the box.
    """
    text = _music_llm_text(source, seed_text.strip())
    if text:
        return text
    if seed_text.strip():
        return seed_text.strip()        # keep what the user typed rather than replace it with a canned line
    if source == "lyrics":
        return _LYRICS_SKELETON
    import random  # noqa: PLC0415
    return random.choice(_MUSIC_FALLBACK)
