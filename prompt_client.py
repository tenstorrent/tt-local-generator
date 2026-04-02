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
import requests

_DEFAULT_URL = "http://127.0.0.1:8001"


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
        resp = requests.get(f"{base_url}/health", timeout=3)
        if resp.status_code == 200:
            return bool(resp.json().get("model_ready"))
        return False
    except (requests.RequestException, ValueError):
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

    # No seed, or seed-mode fallback — three-tier generation: algo → markov → LLM
    result = _gp.generate(prompt_type=source, mode="markov", enhance=True)
    return result["prompt"]
