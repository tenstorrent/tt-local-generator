#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
#
# generate_prompt.py — Three-tier prompt generator for AI video/image/animate models.
#
# Tier 1 — Algorithmic (always available):
#   Uniformly random assembly from word_banks.py.  Guaranteed variety because
#   the selection is done in code, not by the LLM.  Zero dependencies beyond
#   word_banks.py.
#
# Tier 2 — Markov chain (requires markovify):
#   Trained on prompts/markov_seed.txt (and prompts/markov_output.txt if it
#   exists).  Produces novel sentence-level recombinations.  Falls back to
#   algorithmic if markovify is not installed or the corpus is too small.
#
# Tier 3 — LLM polish (requires prompt server on port 8001):
#   Sends the tier-1/2 slug to Qwen3-0.6B with a short polishing instruction.
#   The LLM's job is only to make the output flow naturally — the selection
#   randomness is already locked in by tiers 1/2.  Falls back gracefully if
#   the server is unavailable.
#
# Output: JSON {"prompt": str, "type": str, "source": str, "slug": str}
#   source: "llm" | "markov" | "algo"
#
# Usage:
#   python3 generate_prompt.py
#   python3 generate_prompt.py --type image --mode markov
#   python3 generate_prompt.py --count 5 --type video
#   python3 generate_prompt.py --mode algo --no-enhance
#   python3 generate_prompt.py --raw          # plain text, no JSON wrapper

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

import word_banks as wb

# ── Markov (optional) ─────────────────────────────────────────────────────────

try:
    import markovify
    _MARKOV_AVAILABLE = True
except ImportError:
    _MARKOV_AVAILABLE = False

# ── Paths ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
SEED_FILE = SCRIPT_DIR / "prompts" / "markov_seed.txt"
OUTPUT_LOG = SCRIPT_DIR / "prompts" / "markov_output.txt"  # accumulate good outputs here

LLM_URL = "http://127.0.0.1:8001/v1/chat/completions"
LLM_MODEL = "Qwen/Qwen3-0.6B"
LLM_HEALTH_URL = "http://127.0.0.1:8001/health"

# ── Markov model cache ────────────────────────────────────────────────────────

_markov_cache: dict[str, "markovify.Text | None"] = {}


def _build_markov(prompt_type: str) -> "markovify.Text | None":
    """Load corpus for the given type and build a markovify model."""
    if not _MARKOV_AVAILABLE:
        return None

    lines: list[str] = []

    for src in (SEED_FILE, OUTPUT_LOG):
        if not src.exists():
            continue
        for raw in src.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            # Tagged lines: "video|...", "image|...", "animate|..."
            if "|" in raw:
                tag, _, text = raw.partition("|")
                if tag.strip() == prompt_type:
                    lines.append(text.strip())
            else:
                # Untagged lines go into every pool
                lines.append(raw)

    if len(lines) < 8:
        # Too sparse for state_size=2 to produce novel output reliably
        return None

    corpus = "\n".join(lines)
    try:
        return markovify.Text(
            corpus,
            state_size=2,
            well_formed=False,  # prompts aren't always grammatical sentences
        )
    except Exception:
        return None


def _get_markov(prompt_type: str) -> "markovify.Text | None":
    if prompt_type not in _markov_cache:
        _markov_cache[prompt_type] = _build_markov(prompt_type)
    return _markov_cache[prompt_type]


def _markov_sentence(prompt_type: str) -> str | None:
    """Try up to 10 times to produce a non-overlapping markov sentence."""
    model = _get_markov(prompt_type)
    if model is None:
        return None
    for _ in range(10):
        sentence = model.make_sentence(
            max_overlap_ratio=0.55,   # reject if >55% is a verbatim training run
            max_overlap_total=8,      # reject if any 8-word run matches training
            tries=40,
        )
        if sentence:
            return sentence
    return None

# ── Algorithmic generators ────────────────────────────────────────────────────

def _algo_video(
    director_prob: float = 0.33,
    director_pin: str = "",
) -> tuple[str, dict]:
    """
    Build one algorithmic video prompt slug.

    Args:
        director_prob: Probability (0.0–1.0) of using a named director aesthetic
                       instead of a generic mood/style slot.  Default 0.33 (1-in-3).
        director_pin:  If non-empty, always use this string as the style slot
                       (overrides director_prob sampling entirely).
    """
    subj = wb.subject()
    act = wb.action()
    sett = wb.setting()
    cam = wb.camera()
    mo = wb.mood()
    # Determine style slot: pinned director > prob-sampled director > generic mood.
    # Time-of-day and lighting are intentionally omitted from the slug — they
    # balloon prompt length without improving short-clip generation quality.
    if director_pin:
        style_slot = director_pin
        slug = f"{subj} {act}, {sett}, {cam}, {style_slot}"
        meta = {"subject": subj, "action": act, "setting": sett,
                "camera": cam, "director_style": style_slot}
    elif random.random() < director_prob:
        style_slot = wb.director_style()
        slug = f"{subj} {act}, {sett}, {cam}, {style_slot}"
        meta = {"subject": subj, "action": act, "setting": sett,
                "camera": cam, "director_style": style_slot}
    else:
        slug = f"{subj} {act}, {sett}, {cam}, {mo}"
        meta = {"subject": subj, "action": act, "setting": sett,
                "camera": cam, "mood": mo}
    return slug, meta


def _algo_image() -> tuple[str, dict]:
    subj = wb.subject()
    sett = wb.setting()
    lit = wb.lighting()
    st = wb.style()
    qt = wb.quality_tags(2)
    slug = f"{subj}, {sett}, {lit}, {st}, {qt}"
    meta = {
        "subject": subj, "setting": sett, "lighting": lit,
        "style": st, "quality": qt,
    }
    return slug, meta


def _algo_animate() -> tuple[str, dict]:
    subj = wb.subject()
    act = wb.action()
    sett = wb.setting()
    lit = wb.lighting()
    mo = wb.mood()
    slug = f"{subj}, {act}, {sett}, {lit}, {mo}"
    meta = {
        "subject": subj, "action": act, "setting": sett,
        "lighting": lit, "mood": mo,
    }
    return slug, meta


_ALGO_FN = {
    "video": _algo_video,
    "image": _algo_image,
    "animate": _algo_animate,
}

# ── LLM polish ─────────────────────────────────────────────────────────────────

# Short, focused system prompt — the LLM only needs to polish, not select.
# Target: <=40 words. Video models generate 4-6 second clips, so prompts must
# describe a single contained action, not a journey. Longer prompts do not
# produce longer or better clips — they just dilute the core image.
_POLISH_SYSTEM = (
    "You are a cinematic prompt editor for AI video generation. "
    "Rewrite the slug as one tight, vivid sentence. "
    "Keep every element. Add nothing. Cut all filler ('bathed in', 'as if', 'seems to', adverb stacks). "
    "Hard limit: 25 words. No preamble, no quotes, no explanation. "
    "Never add gore, body horror, graphic violence, or disturbing imagery."
)

_TYPE_HINT = {
    "video": (
        "Video (4-6 s clip). One action, one location, one camera cue. Under 25 words."
    ),
    "image": "Image. End with 2-3 style tags (e.g. 35mm grain, sharp focus). Under 28 words.",
    "animate": (
        "Character animation. One character, one action, one emotional beat. Under 22 words."
    ),
}


def _llm_available() -> bool:
    """Quick health check — 2s timeout so we don't stall the caller."""
    try:
        with urllib.request.urlopen(LLM_HEALTH_URL, timeout=2) as r:
            data = json.loads(r.read())
            return bool(data.get("model_ready"))
    except Exception:
        return False


def _llm_polish(slug: str, prompt_type: str, timeout: int = 45) -> str | None:
    """Send slug to the prompt server for natural-language polishing."""
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": _POLISH_SYSTEM},
            {"role": "user", "content": f"{_TYPE_HINT[prompt_type]}\n\nSlug: {slug}"},
        ],
        "max_tokens": 80,
        "temperature": 0.70,
        "top_p": 0.90,
    }).encode()

    req = urllib.request.Request(
        LLM_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        return resp["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError):
        return None


# ── Top-level generator ───────────────────────────────────────────────────────

def generate(
    prompt_type: str = "video",
    mode: str = "algo",
    enhance: bool = True,
    director_prob: float = 0.33,
    director_pin: str = "",
) -> dict:
    """
    Generate one prompt.

    Args:
        prompt_type:   "video" | "image" | "animate"
        mode:          "algo" | "markov" — base generation before optional LLM polish
        enhance:       if True and LLM server is up, polish the slug with the LLM
        director_prob: probability of using a named director style in video prompts
        director_pin:  if non-empty, always use this director name (video only)

    Returns:
        {
            "prompt": str,     # final prompt (polished if LLM available)
            "type": str,       # prompt_type
            "source": str,     # "llm" | "markov" | "algo"
            "slug": str,       # raw pre-polish slug
        }
    """
    slug: str | None = None
    source = "algo"

    # Tier 2: Markov
    if mode == "markov":
        slug = _markov_sentence(prompt_type)
        if slug:
            source = "markov"

    # Tier 1: Algorithmic (fallback or primary)
    if slug is None:
        if prompt_type == "video":
            slug, _ = _algo_video(director_prob=director_prob, director_pin=director_pin)
        else:
            slug, _ = _ALGO_FN[prompt_type]()
        source = "algo"

    # Tier 3: LLM polish
    prompt = slug
    if enhance and _llm_available():
        polished = _llm_polish(slug, prompt_type)
        if polished:
            prompt = polished
            source = "llm"

    return {
        "prompt": prompt,
        "type": prompt_type,
        "source": source,
        "slug": slug,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate video/image/animate prompts (algo → markov → LLM).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 generate_prompt.py
  python3 generate_prompt.py --type image --mode markov
  python3 generate_prompt.py --count 5 --type video
  python3 generate_prompt.py --mode algo --no-enhance
  python3 generate_prompt.py --raw
        """,
    )
    parser.add_argument(
        "--type", choices=["video", "image", "animate"], default="video",
        help="Prompt type (default: video)",
    )
    parser.add_argument(
        "--mode", choices=["algo", "markov"], default="algo",
        help="Base generation mode before optional LLM polish (default: algo)",
    )
    parser.add_argument(
        "--enhance", action=argparse.BooleanOptionalAction, default=True,
        help="Polish with LLM if server is running (default: on)",
    )
    parser.add_argument(
        "--count", type=int, default=1, metavar="N",
        help="Number of prompts to generate (default: 1)",
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="Output plain text instead of JSON",
    )
    parser.add_argument(
        "--director-prob", type=float, default=0.33, metavar="PROB",
        help="Probability (0.0–1.0) of using a named director style in video prompts "
             "(default: 0.33).  Ignored for image/animate types.",
    )
    parser.add_argument(
        "--director", default="", metavar="NAME",
        help="Always use this director name as the style slot in video prompts "
             "(overrides --director-prob).  Must match an entry in CINEMATIC_DIRECTORS.",
    )
    args = parser.parse_args()

    if args.mode == "markov" and not _MARKOV_AVAILABLE:
        print(
            "Warning: markovify not installed — falling back to algo mode.\n"
            "  Install with: pip install markovify",
            file=sys.stderr,
        )

    results = [
        generate(
            args.type, args.mode, args.enhance,
            director_prob=args.director_prob,
            director_pin=args.director,
        )
        for _ in range(args.count)
    ]

    if args.raw:
        for r in results:
            print(r["prompt"])
    elif args.count == 1:
        print(json.dumps(results[0], ensure_ascii=False))
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
