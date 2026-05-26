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
import re as _re
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

# ── Anti-repetition sliding window ────────────────────────────────────────────
#
# Tracks content words from the last N accepted slugs in the current process.
# generate() retries assembly up to _ANTI_REP_RETRIES times if the new slug is
# too lexically similar to a recent one (Jaccard threshold _ANTI_REP_THRESHOLD).
#
# Because generate_prompt.py is imported (not exec'd as a subprocess) by both
# the GUI and the prompt server, this module-level list persists for the
# lifetime of the host process — across every Inspire button press in the GUI.

_RECENT_SLUGS: list[set] = []
_ANTI_REP_MAX = 20          # how many past slugs to remember
_ANTI_REP_THRESHOLD = 0.35  # Jaccard ≥ this → "too similar, try again"
_ANTI_REP_RETRIES = 5       # max extra draws before accepting anyway

_STOPWORDS: frozenset = frozenset({
    "a", "an", "the", "in", "on", "at", "of", "and", "with", "through",
    "by", "to", "as", "is", "are", "was", "its", "into", "over", "from",
    "for", "that", "this", "but", "or", "not", "up", "out", "down", "one",
})


def _content_words(text: str) -> set:
    """Extract lowercase words longer than 3 chars that aren't stopwords."""
    return {
        w.lower().strip(".,!?:;—\"'")
        for w in text.split()
        if len(w) > 3 and w.lower() not in _STOPWORDS
    }


def _is_too_similar(slug: str) -> bool:
    """True if slug shares ≥35% content words with any recently accepted slug."""
    words = _content_words(slug)
    if not words:
        return False
    for recent in _RECENT_SLUGS:
        if not recent:
            continue
        jaccard = len(words & recent) / len(words | recent)
        if jaccard >= _ANTI_REP_THRESHOLD:
            return True
    return False


def _record_slug(slug: str) -> None:
    """Add slug's content words to the sliding window; evict oldest if full."""
    _RECENT_SLUGS.append(_content_words(slug))
    if len(_RECENT_SLUGS) > _ANTI_REP_MAX:
        _RECENT_SLUGS.pop(0)


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

    if len(lines) < 20:
        # state_size=1 needs at least 20 lines to produce meaningful transitions.
        # Below that the model degenerates to near-verbatim training lines.
        return None

    corpus = "\n".join(lines)
    try:
        return markovify.Text(
            corpus,
            state_size=1,       # was 2 — produces wilder recombinations at the
                                # cost of some grammaticality, which the LLM
                                # polish pass is designed to fix anyway.
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

# ── Slug structural templates ──────────────────────────────────────────────────
#
# Every slot ({subj}, {act}, {sett}, {cam}, {style}) is filled identically
# regardless of template — only the *order* and *punctuation* vary.  This
# changes the syntactic shape the LLM receives, which drives it to produce
# different sentence patterns even from semantically identical raw material.
#
# Why this matters: a small model trained on web text has strong positional
# priors.  If it always sees "subject action, setting, camera, style" it
# produces "X does Y in Z, filmed W, moody" 90% of the time.  Rotating the
# template forces it to lead with camera, atmosphere, or action instead.

_VIDEO_SLUG_TEMPLATES = [
    "{subj} {act}, {sett}, {cam}, {style}",           # who-first   (classic)
    "{cam}: {subj} {act} in {sett}. {style}.",         # camera-first (POV anchor)
    "{sett}. {subj} {act}. {cam}. {style}.",           # place-first  (environment anchor)
    "{style} — {subj} {act}, {sett}, {cam}.",          # tone-first   (mood anchor)
    "{subj}. {sett}. {cam} as {act}. {style}.",        # prose-pause  (staccato rhythm)
    "{act}: {subj}, {sett}. {cam}. {style}.",          # action-first (motion anchor)
]

_SKYREELS_SLUG_TEMPLATES = [
    "{subj}, {cam}, {style}",                          # subject-first (default)
    "{cam}: {subj}. {style}.",                         # camera-first
    "{style} — {subj}. {cam}.",                        # tone-first
]

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
        meta = {"subject": subj, "action": act, "setting": sett,
                "camera": cam, "director_style": style_slot}
    elif random.random() < director_prob:
        style_slot = wb.director_style()
        meta = {"subject": subj, "action": act, "setting": sett,
                "camera": cam, "director_style": style_slot}
    else:
        style_slot = mo
        meta = {"subject": subj, "action": act, "setting": sett,
                "camera": cam, "mood": mo}

    # Pick a random structural shape — same slots, different syntactic order.
    # Optionally append an unexpected juxtaposition (12% chance) as a 6th element
    # the LLM can't fully neutralise without ignoring it outright.
    tmpl = random.choice(_VIDEO_SLUG_TEMPLATES)
    slug = tmpl.format(subj=subj, act=act, sett=sett, cam=cam, style=style_slot)
    if random.random() < 0.12:
        jux = wb.unexpected_juxtaposition()
        slug = f"{slug.rstrip('.')} — {jux}"
        meta["juxtaposition"] = jux

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


def _algo_artgen() -> tuple[str, dict]:
    """
    Return a short thematic seed phrase for artgen's Inspire button.

    Unlike video/image slugs (which are cinematic sentences), artgen seeds are
    compact evocative phrases — 3-8 words — suitable as verse themes, palette
    moods, ANSI subjects, or visual generator inspiration hints.
    """
    theme = wb.artgen_theme()
    return theme, {"theme": theme}


def _algo_skyreels() -> tuple[str, dict]:
    """
    Build one algorithmic SkyReels prompt slug.

    SkyReels is optimised for cinematic, physically-plausible motion: nature,
    animals, wide landscapes, urban atmospherics, and cosmic vistas.
    Camera move and style are drawn from SkyReels-specific banks.
    """
    subj = wb.skyreels_subject()
    cam = wb.skyreels_camera()
    style = wb.skyreels_style()
    tmpl = random.choice(_SKYREELS_SLUG_TEMPLATES)
    slug = tmpl.format(subj=subj, cam=cam, style=style)
    meta = {"subject": subj, "camera": cam, "style": style}
    return slug, meta


def _algo_commercial() -> tuple[str, dict]:
    """
    Build one algorithmic commercial/product-spot prompt slug.

    Commercial prompts foreground the product — a specific object, package, or
    mail-order novelty — and frame it with a product-shot camera directive.
    The LLM polishing pass is told to keep the product central and not drift
    into narrative.
    """
    product = wb.commercial_product()
    sett = wb.commercial_setting()
    hook = wb.commercial_copy_hook()
    slug = f"{product}, {sett}, {hook}"
    meta = {"product": product, "setting": sett, "copy_hook": hook}
    return slug, meta


def _algo_animatediff() -> tuple[str, dict]:
    """
    Build one algorithmic AnimateDiff GIF-loop prompt slug.

    AnimateDiff generates 8-frame looping GIFs. Best results come from subjects
    with natural cyclical or oscillating motion — fire, water, wind, breath.
    No camera moves; keep composition simple: one subject + one modifier.
    """
    subj = wb.animatediff_subject()
    mod = wb.animatediff_modifier()
    slug = f"{subj}, {mod}"
    meta = {"subject": subj, "modifier": mod}
    return slug, meta


_ALGO_FN = {
    "video": _algo_video,
    "image": _algo_image,
    "animate": _algo_animate,
    "commercial": _algo_commercial,
    "skyreels": _algo_skyreels,
    "artgen": _algo_artgen,
    "animatediff": _algo_animatediff,
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
    "Output ONLY the final prompt — never echo format labels, frame counts, or any instruction text. "
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
    "commercial": (
        "Product commercial (4-6 s clip). Keep the product the subject. "
        "One camera move, one product action. Focus on the object, not people. Under 25 words."
    ),
    "skyreels": (
        "Cinematic short video (1-4 s clip). "
        "One subject, one camera move, one lighting or mood cue. "
        "Nature, animals, wide landscapes, urban atmosphere, or cosmic. "
        "Use specific cinematic language: dolly, track, shallow depth of field, golden hour. "
        "Under 30 words. No preamble, no quotes."
    ),
    "artgen": (
        "Artgen theme seed. Produce one evocative phrase (3-8 words) suitable as a theme "
        "for generative art: a mood, a visual atmosphere, an emotional texture, or a short "
        "poetic image. Examples: 'volcanic winter twilight', 'the weight of forgotten names', "
        "'copper and verdigris', 'neon monastery at 4am'. "
        "No sentences, no camera directions, no explanation. Just the phrase."
    ),
    # Deliberately avoids starting with "AnimateDiff" or "GIF loop" — small models
    # echo back instruction text that begins the user message when it reads like output.
    "animatediff": (
        "Looping GIF. One subject with natural cyclical motion: fire, water, wind, breath, "
        "fabric, foliage. No camera moves. Describe only the looping subject. Under 18 words."
    ),
}

# Patterns that indicate the LLM echoed instruction metadata rather than generating
# prompt content. Stripped from output before it reaches the user or the model.
_INSTRUCTION_LEAK_PATTERNS = [
    _re.compile(r'^\s*(?:animatediff\s+)?(?:gif\s+)?loop\s*[\(\[]?\s*\d+\s*frames?\s*[\)\]]?\s*[.\-—]\s*', _re.I),
    _re.compile(r'^\s*looping\s+gif\s*[.\-—]\s*', _re.I),
    _re.compile(r'^\s*one\s+subject\s+with\s+natural\s+cyclical\s+motion\s*[:\-—]', _re.I),
    _re.compile(r'\bno\s+camera\s+moves?\b', _re.I),
    _re.compile(r'\bunder\s+\d+\s+words?\b', _re.I),
    _re.compile(r'\bdescribe\s+(?:what|only\s+the)\s+loop', _re.I),
]


def _strip_instruction_leak(text: str) -> str:
    """Remove instruction metadata that leaked into the LLM output."""
    for pat in _INSTRUCTION_LEAK_PATTERNS:
        text = pat.sub("", text)
    return text.strip(". \t\n")


def _llm_available() -> bool:
    """Quick health check — 3s timeout to tolerate remote servers over LAN."""
    try:
        with urllib.request.urlopen(LLM_HEALTH_URL, timeout=3) as r:
            data = json.loads(r.read())
            return bool(data.get("model_ready"))
    except Exception:
        return False


def _model_sampling_params(model_id: str = LLM_MODEL) -> dict:
    """
    Return temperature, top_p, and max_tokens tuned to the model's parameter count.

    Small models (≤2B) collapse toward boring modes at moderate temperature —
    they need to be pushed harder off their greedy peak.  Large models have
    richer vocabularies and benefit from tighter sampling to stay on-topic.

    max_tokens scales up with model capacity: a 0.6B model fits its prompt in
    80 tokens; a 14B model can elaborate a richer two-clause sentence in 160.
    """
    name = model_id.lower()
    if any(x in name for x in ("0.6b", "0.5b", "1b", "1.5b", "2b", "2.5b")):
        # Sub-3B: mode is flat and generic — raise temperature significantly.
        # top_p=0.95 allows the long tail of less-common vocabulary.
        return {"temperature": 1.05, "top_p": 0.95, "max_tokens": 80}
    elif any(x in name for x in ("3b", "4b", "6b", "7b", "8b", "9b")):
        return {"temperature": 0.85, "top_p": 0.92, "max_tokens": 120}
    elif any(x in name for x in ("12b", "13b", "14b", "15b")):
        return {"temperature": 0.75, "top_p": 0.90, "max_tokens": 160}
    else:
        # 20B+: large vocabulary, richer mode — tighten sampling for precision.
        return {"temperature": 0.65, "top_p": 0.88, "max_tokens": 180}


def _is_small_model(model_id: str = LLM_MODEL) -> bool:
    """True for models ≤3B where multi-candidate selection gives the most benefit."""
    name = model_id.lower()
    return any(x in name for x in ("0.6b", "0.5b", "1b", "1.5b", "2b", "2.5b", "3b"))


def _pick_best_candidate(candidates: list[str]) -> str:
    """
    Choose the most content-rich candidate from a list of LLM outputs.

    Scores by counting lowercase words longer than 5 characters — a cheap
    proxy for concrete, specific language vs. generic filler.  When multiple
    candidates tie, the first is returned (preserving model-native order).
    """
    def _specificity(s: str) -> int:
        return sum(1 for w in s.split() if len(w) > 5 and w[0].islower())
    return max(candidates, key=_specificity)


def _llm_polish(slug: str, prompt_type: str, timeout: int = 45) -> str | None:
    """
    Send a slug to the prompt server for natural-language polishing.

    Uses model-adaptive sampling (temperature/top_p/max_tokens from
    _model_sampling_params).  For small models (≤3B), requests 3 candidates
    and picks the most content-rich one — compensates for those models'
    tendency to collapse to the same flat phrasing.
    """
    sampling = _model_sampling_params()
    use_multi = _is_small_model()
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": _POLISH_SYSTEM},
            {"role": "user", "content": f"{_TYPE_HINT[prompt_type]}\n\nSlug: {slug}"},
        ],
        "n": 3 if use_multi else 1,
        **sampling,
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
        candidates = [_strip_instruction_leak(c["message"]["content"]) for c in resp["choices"]]
        return _pick_best_candidate(candidates) if len(candidates) > 1 else candidates[0]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError):
        return None


def _llm_guided(guide: str, prompt_type: str, timeout: int = 45) -> str | None:
    """
    Ask the prompt server to generate a fresh prompt inspired by a guiding theme.

    Unlike _llm_polish (which rewrites an existing slug), this function gives the
    LLM the user's theme string and asks it to produce a complete cinematic prompt
    from scratch.  Uses the same model-adaptive sampling as _llm_polish.
    Returns None on any network or parse error.
    """
    # Guard against unrecognised prompt_type — fall back to video hint rather
    # than raising an uncaught KeyError before the try block below.
    type_hint = _TYPE_HINT.get(prompt_type, _TYPE_HINT["video"])
    sampling = _model_sampling_params()
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a cinematic prompt writer for AI video generation. "
                    "Write one tight, vivid prompt inspired by the theme below. "
                    "Hard limit: 25 words. No preamble, no quotes, no explanation. "
                    "Never add gore, body horror, graphic violence, or disturbing imagery."
                ),
            },
            {
                "role": "user",
                "content": f"{type_hint}\n\nTheme: {guide}",
            },
        ],
        **sampling,
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
        return _strip_instruction_leak(resp["choices"][0]["message"]["content"])
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError):
        return None


def guided_generate(
    guide: str,
    prompt_type: str = "video",
    enhance: bool = True,
) -> dict:
    """
    Generate one prompt centred on a user-supplied guiding theme.

    When the Qwen prompt server is up and enhance=True, sends the guide to the
    LLM and asks it to write a complete cinematic prompt around that theme.
    Falls back to an algorithmic slug with the guide prepended if the server is
    down or returns an error.

    Returns the same schema as generate():
        {"prompt": str, "type": str, "source": "llm"|"algo", "slug": str}
    """
    if enhance and _llm_available():
        polished = _llm_guided(guide, prompt_type)
        if polished:
            return {
                "prompt": polished,
                "type": prompt_type,
                "source": "llm",
                "slug": guide,
            }

    # Fallback: algo slug with guide prepended so the user's intent is preserved.
    algo_fn = _ALGO_FN.get(prompt_type, _algo_video)
    slug_base, _ = algo_fn()
    slug = f"{guide}; {slug_base}"
    return {
        "prompt": slug,
        "type": prompt_type,
        "source": "algo",
        "slug": slug,
    }


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

    # Tier 1: Algorithmic (fallback or primary).
    #
    # Anti-repetition: retry up to _ANTI_REP_RETRIES times if the new slug is
    # too lexically similar to recently accepted slugs.  Each retry draws a
    # completely fresh set of random slots, so the retry cost is negligible
    # (just Python random.choice calls).  After all retries are exhausted we
    # accept the last candidate anyway — a slight near-duplicate is better than
    # an infinite loop.  Markov slugs are not retried (each is already novel).
    if slug is None:
        for _attempt in range(1 + _ANTI_REP_RETRIES):
            if prompt_type == "video":
                candidate, _ = _algo_video(director_prob=director_prob, director_pin=director_pin)
            else:
                candidate, _ = _ALGO_FN[prompt_type]()
            if not _is_too_similar(candidate) or _attempt == _ANTI_REP_RETRIES:
                slug = candidate
                break
        source = "algo"

    # Record the accepted slug before polishing so both the slug and the final
    # prompt contribute to the rolling similarity window if the LLM is down.
    assert slug is not None
    _record_slug(slug)

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
        description="Generate video/image/animate/commercial prompts (algo → markov → LLM).",
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
        "--type", choices=["video", "image", "animate", "commercial", "skyreels"],
        default="video",
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
