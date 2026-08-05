"""Classify an artgen artifact's pipeline seed KIND by file extension.

Pure module — zero GTK imports. Used by the "Remix as pipeline" bridge to
decide whether an artgen-generated file (lore .txt, ANSI .ans, SVG banner,
codeart .py, etc.) can seed a pipeline stage and, if so, what kind of seed
it is ("text", "image", "gif"). Extension is authoritative; `generator_type`
is accepted for future refinement (e.g. disambiguating generators that emit
multiple extensions) but never overrides the extension today.
"""
from pathlib import PurePath

# Extension -> pipeline seed kind. Keys are lowercase, include the leading dot.
_TEXT_EXTS = {".txt", ".md", ".py"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".ans", ".webp"}
_GIF_EXTS = {".gif"}


def artgen_seed_kind(file_path, generator_type=None):
    """Map an artgen artifact to a pipeline seed kind by file extension.

    .txt/.md/.py -> "text"; .png/.jpg/.jpeg/.svg/.ans/.webp -> "image";
    .gif -> "gif"; .json or unknown/missing/no-extension -> None (not
    seedable as a pipeline). Extension matching is case-insensitive.
    `generator_type` is accepted for future use but does not affect the
    result today. Never raises — any unexpected input yields None.
    """
    try:
        if not file_path:
            return None
        # A palette artgen record (JSON of colors+lore) is its own seed kind so
        # the Muse can offer palette-aware goals / adapters. Keyed on the
        # generator, not the bare .json ext, so unrelated JSON isn't miscast.
        if generator_type == "palette":
            return "palette"
        ext = PurePath(str(file_path)).suffix.lower()
        if not ext:
            return None
        if ext in _TEXT_EXTS:
            return "text"
        if ext in _IMAGE_EXTS:
            return "image"
        if ext in _GIF_EXTS:
            return "gif"
        return None
    except Exception:
        return None
