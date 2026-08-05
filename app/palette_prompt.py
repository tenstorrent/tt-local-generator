# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""Pure palette -> text-prompt helpers (no GTK).

A palette artgen artifact is JSON: {"name", "colors":[{"hex","role"}], "lore"}.
`literal_prompt` turns it into a deterministic, palette-faithful prompt string
(the same colors+lore extraction previously trapped in
remix_popover._build_hint) — used as the seed for LLM polishing and as the
guaranteed fallback when no prompt LLM is running.
"""
from __future__ import annotations

import json


def load_palette(path: str) -> "dict | None":
    """Parse a palette JSON file. None on any failure (missing / unreadable /
    not an object)."""
    if not path:
        return None
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def literal_prompt(palette: dict) -> str:
    """Deterministic prompt from a palette dict: up to the first 6 hex colors
    plus the lore sentence(s). Best-effort — returns "" if there's nothing
    usable, never raises."""
    parts = []
    try:
        hexes = " ".join(
            c["hex"] for c in (palette.get("colors") or [])[:6]
            if isinstance(c, dict) and c.get("hex")
        )
        if hexes:
            parts.append(f"palette: {hexes}")
    except Exception:
        pass
    lore = (palette.get("lore") or "").strip() if isinstance(palette, dict) else ""
    if lore:
        parts.append(lore)
    return ", ".join(parts)
