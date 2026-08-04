# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Medium-chip discovery — the pure core behind the unified Create surface's
"what art types can I make?" chip row (Create-surface plan, Task 2).

The Create surface (docs/superpowers/specs/2026-07-13-create-surface-design.md)
replaces the four hardcoded medium tabs (Video / Animate / Image / Generative
Art) with ONE surface where the medium is a chip, not a top-level tab. That
chip row must include every native medium the app's own GenerationWorker
drives (image/video) AND one chip per artgen generator — so a new
plugin dropped into plugins/ shows up automatically with zero code changes
here.

This module has NO GTK imports and does no real I/O of its own: every
external dependency (the list of artgen generator names) is injected into
`discover_mediums`, so it is fully unit-testable with fakes. `default_mediums`
at the bottom is the thin real-deps wrapper the UI actually calls; it is
intentionally the only function here that imports `artgen` (and imports it
lazily, inside the function body) so this module stays importable even in a
context where the artgen package — with its own heavier plugin-loading
machinery — is unavailable or broken.

Mirrors the pure-core-plus-thin-wrapper house style already established by
capability_discovery.py (discover_capabilities/default_capabilities) and
intent_vocab.py (INTENTS/intent_for) — same shape, smaller scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Medium:
    """One selectable "make a ___" chip on the Create surface.

    id        — stable identifier: "image"/"video" for the native mediums,
                else the artgen generator name (e.g. "verse").
    label     — human, creative-language chip text ("Image", "Verse", "ANSI").
                Never a raw class_type or internal tool name.
    icon      — one emoji for the compact chip.
    kind      — the output KIND this medium produces: "image" | "video" |
                "gif" | "text". Artgen mediums report the kind matching how
                their results are stored/played in the gallery (e.g. "gif"
                for animatediff's animated clips).
    source    — "native" (a GenerationWorker medium the app drives directly)
                or "artgen" (routed through an artgen generator).
    generator — the artgen generator name for source="artgen" mediums, else
                None for native mediums.
    uses_llm  — whether this medium's generation drives the chat-LLM backend
                (`artgen.ArtGenerator.uses_llm` for artgen mediums; unused —
                always the True default — for native mediums, which never
                touch artgen at all). Defaults True so every existing call
                site/test (which never passes this) is unaffected. False
                marks a self-contained artgen generator that bypasses the
                chat LLM entirely (e.g. AnimateDiff, a Blackhole diffusion
                GIF generator) — CreateView's scoped model dropdown reads
                this to decide whether to offer the chat-LLM servers (True,
                the historical behavior for verse/ansi/landscape/…) or a
                single self-entry naming the generator itself (False) — see
                `create_view.CreateView._scoped_model_keys`.
    """

    id: str
    label: str
    icon: str
    kind: str
    source: str
    generator: Optional[str] = None
    uses_llm: bool = True


# ── Native mediums ─────────────────────────────────────────────────────────
#
# Fixed, deterministic, always first. Icons/labels match the existing
# per-medium toggle buttons in main_window.py's Create toolbar (🎥 Video,
# 🖼️ Image) so a chip never introduces a new visual vocabulary
# for a medium the user already recognizes elsewhere in the app.

_NATIVE_MEDIUMS: tuple[Medium, ...] = (
    Medium(id="image", label="Image", icon="🖼️", kind="image",
           source="native", generator=None),
    Medium(id="video", label="Video", icon="🎥", kind="video",
           source="native", generator=None),
)


# ── Artgen generator → (label, icon) ──────────────────────────────────────
#
# Icons for the 9 generators already shown in the art gallery reuse
# artgen_gallery.py's `_TYPE_EMOJI` table verbatim (values only — that module
# imports GTK at module scope, so we can't import it from here and stay
# pure; the values are copied so a gallery card and a Create chip for the
# same generator never disagree visually). `codeart` and `animatediff` have
# no gallery-table entry; `animatediff`'s icon instead matches
# intent_vocab.INTENTS["TTLGAnimateDiff"].icon (🕺) — the one case where an
# artgen generator name lines up exactly with a native pipeline intent.
_ARTGEN_LABELS_ICONS: dict[str, tuple[str, str]] = {
    "verse":         ("Verse", "✍"),
    "freeform":      ("Freeform", "?"),
    "codeart":       ("Code Art", "💻"),
    "landscape":     ("Landscape", "🏔"),
    "skyline":       ("Skyline", "🌃"),
    "constellation": ("Constellation", "✦"),
    "geometric":     ("Geometric", "⬡"),
    "circuit":       ("Circuit", "⬟"),
    "palette":       ("Palette", "◼"),
    "ansi":          ("ANSI", "▓"),
    "ansi-image":    ("ANSI Art", "▓"),
    "animatediff":   ("AnimateDiff", "🕺"),
}

# Generator name → output kind. Pinned per the Create-surface Task 2 brief:
# verse/freeform/codeart are plain text; landscape/constellation/geometric/
# skyline/circuit/palette/ansi all render as a visual artifact (SVG banner,
# ANSI color grid, or a swatch grid for palette — none of these are ever
# shown as raw text/JSON, see artgen_gallery.py's card-widget dispatch);
# animatediff produces a .gif.
_ARTGEN_KIND: dict[str, str] = {
    "verse": "text",
    "freeform": "text",
    "codeart": "text",
    "landscape": "image",
    "constellation": "image",
    "geometric": "image",
    "skyline": "image",
    "circuit": "image",
    "palette": "image",
    "ansi": "image",
    "ansi-image": "image",
    "animatediff": "gif",
}

# Fallback for a generator name not (yet) in the tables above — e.g. a
# brand-new plugin dropped in before this module is updated. Keeps the chip
# row render-able instead of crashing; "image" is the most common artgen
# output kind today, so it's the least-surprising default.
_DEFAULT_KIND = "image"
_DEFAULT_ICON = "✨"


def _fallback_label(name: str) -> str:
    """Title-case a raw generator name into a passable chip label.

    e.g. "some_future_plugin" -> "Some Future Plugin". Only used for
    generator names absent from `_ARTGEN_LABELS_ICONS` (see module docstring).
    """
    return str(name).replace("_", " ").replace("-", " ").title()


def discover_mediums(
    *,
    artgen_names,
    native: "list[Medium] | tuple[Medium, ...] | None" = None,
    uses_llm_for: "Optional[Callable[[str], bool]]" = None,
) -> list[Medium]:
    """Return every Medium chip the Create surface should offer.

    Pure core: `artgen_names` (an iterable of artgen generator name strings)
    is the only REQUIRED external input, and it's injected — this function
    never imports artgen or touches disk/network itself. `native` lets a
    caller override the default native-medium list (e.g. for tests, or a
    future slice that adds a new native medium without editing this module).
    `uses_llm_for` is an OPTIONAL injected callable (`name -> bool`) reporting
    whether a given artgen generator drives the chat-LLM backend — mirrors
    `artgen.ArtGenerator.uses_llm` (see `Medium.uses_llm`'s docstring for why
    this matters to CreateView's scoped model dropdown). Omitted entirely
    (every pre-existing caller) -> every artgen medium gets `uses_llm=True`,
    byte-identical to pre-this-feature behavior. A per-name call that raises
    also defaults to True — never let one bad lookup crash discovery.

    Order is deterministic: native mediums first (in `native`'s order, or
    the default image/video order), then one Medium per name in
    `artgen_names`, in the exact order given — this function never sorts or
    reorders the caller's list.

    Robustness: never raises.
      - `artgen_names=None` or `[]` -> just the native mediums.
      - An `artgen_names` that raises when iterated (e.g. a broken plugin
        registry) -> caught, falls back to just the native mediums.
      - A single malformed/unexpected entry inside an otherwise-good
        `artgen_names` (e.g. a non-string) -> that one entry is skipped;
        the rest of the list is still discovered.
      - A generator name absent from the label/icon/kind tables -> gets a
        title-cased fallback label, a generic icon, and kind "image" rather
        than crashing or being silently dropped.
      - A raising `uses_llm_for(name)` -> that one medium defaults to
        `uses_llm=True` (the safe assumption) instead of crashing discovery.
    """
    mediums: list[Medium] = list(native) if native is not None else list(_NATIVE_MEDIUMS)

    try:
        names = list(artgen_names) if artgen_names else []
    except Exception:
        names = []

    for name in names:
        try:
            key = str(name)
            if key == "animatediff":
                # Folded into the Video medium as a model (see the
                # 'Video is Video' spec) — no longer its own chip.
                continue
            label, icon = _ARTGEN_LABELS_ICONS.get(key, (_fallback_label(key), _DEFAULT_ICON))
            kind = _ARTGEN_KIND.get(key, _DEFAULT_KIND)
            uses_llm = True
            if uses_llm_for is not None:
                try:
                    uses_llm = bool(uses_llm_for(key))
                except Exception:
                    uses_llm = True
            mediums.append(Medium(
                id=key, label=label, icon=icon, kind=kind,
                source="artgen", generator=key, uses_llm=uses_llm,
            ))
        except Exception:
            # One bad entry must not take down discovery of the rest.
            continue

    return mediums


def default_mediums() -> list[Medium]:
    """The real-deps wrapper the Create surface UI actually calls.

    Thin by design — all logic lives in `discover_mediums`; this function
    only wires up the two real dependencies (`artgen.all_names()` and, per
    generator, `artgen.get(name).uses_llm`). Imports artgen lazily, inside
    the function body (and inside the `_uses_llm_for` closure again, for the
    same reason), so `create_mediums` itself stays importable (and
    unit-testable) without pulling in artgen's plugin-loading machinery —
    and so that a broken/missing artgen package degrades to "just the native
    mediums" instead of making the whole Create surface unable to even list
    its chips. `_uses_llm_for` mirrors `pipeline_engine._artgen_uses_llm`'s
    own fail-soft-to-True convention (a broken/missing generator lookup must
    never crash discovery of the rest of the chip row).
    """
    try:
        import artgen
        names = artgen.all_names()
    except Exception:
        names = []

    def _uses_llm_for(name: str) -> bool:
        try:
            import artgen as _artgen
            return bool(getattr(_artgen.get(name), "uses_llm", True))
        except Exception:
            return True

    return discover_mediums(artgen_names=names, uses_llm_for=_uses_llm_for)


# ── Display order: most-visual → most-textual ──────────────────────────────
#
# The Create chip row and the Possibilities wall present art types from the
# most VISUAL (photographic / motion / scenic / pure-color) through the
# semi-visual (ANSI block art) to the most TEXTUAL (code, prose, verse). This
# is a DISPLAY-ONLY concern: `default_mediums()` / `discover_mediums()` keep
# their own native-first / registry order (and the tests that pin it) — callers
# that care about the visual gradient sort with `sort_mediums_visual_first`.
_VISUAL_TEXTUAL_ORDER: tuple = (
    "video", "image",                                             # motion / photo
    "landscape", "skyline", "constellation", "geometric", "circuit",  # scenic / abstract SVG
    "palette",                                                    # pure color
    "ansi",                                                       # visual-but-made-of-text bridge
    "codeart", "freeform", "verse",                              # text
)


def _visual_sort_rank(medium) -> int:
    """Sort key for `sort_mediums_visual_first`. Known ids get their position
    in `_VISUAL_TEXTUAL_ORDER`; an unknown medium (e.g. a newly dropped plugin)
    slots AFTER the known list — and text-kind unknowns after non-text ones, so
    the "textual comes last" rule still holds for names this table hasn't been
    taught yet."""
    try:
        return _VISUAL_TEXTUAL_ORDER.index(medium.id)
    except (ValueError, AttributeError):
        base = len(_VISUAL_TEXTUAL_ORDER)
        return base + (1 if getattr(medium, "kind", "") == "text" else 0)


def sort_mediums_visual_first(mediums) -> list:
    """Return *mediums* stably ordered most-visual → most-textual (see
    `_VISUAL_TEXTUAL_ORDER`). Stable: equal-rank mediums keep their input order.
    Never raises — returns the input order on any error (display sort must never
    break the Create surface)."""
    try:
        return sorted(mediums, key=_visual_sort_rank)
    except Exception:
        return list(mediums)
