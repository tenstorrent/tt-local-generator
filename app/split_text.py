# SPDX-License-Identifier: Apache-2.0
"""Split a text "lore" artifact into a list of fragments.

Pure text-processing helper — NO GTK imports, no network/plugin calls. This
is the first step of a fan-out pipeline (lore -> one image per fragment ->
montage): `pipeline_engine._h_split_text` wraps this function so the engine
can turn one text artifact into N downstream nodes.

Kept intentionally dumb: no LLM call, no heuristics beyond simple text
splitting, so it's free to run even during a dry-run (see the handler's
docstring in pipeline_engine.py for why that matters for fan-out width).
"""
from __future__ import annotations
import re


def split_text(text: str, mode: str = "paragraphs", max_items: int = 8) -> "list[str]":
    """Split *text* into a list of trimmed, non-empty fragments.

    mode:
      "paragraphs" — split on blank-line boundaries (one or more blank lines
                     between fragments).
      "lines"      — one fragment per non-blank line.
      "numbered"   — one fragment per non-blank line, with a leading list
                     marker ("1.", "2)", etc.) stripped off; lines with no
                     marker fall back to their raw (trimmed) content — i.e.
                     the same behavior as "lines" on a per-line basis.
      anything else — falls back to "lines" behavior.

    Every fragment is whitespace-trimmed and empty fragments are dropped.
    The result is capped at ``max_items`` (a floor of 1 is enforced so a
    caller can never request zero fragments back). Never raises: empty or
    whitespace-only input returns ``[]`` and is the caller's problem to
    handle (e.g. skip the fan-out, show a placeholder).
    """
    # Never fail hard — coerce/guard rather than raise on odd input.
    if max_items < 1:
        max_items = 1
    if text is None:
        return []
    text = str(text)
    if not text.strip():
        return []

    if mode == "paragraphs":
        # One or more blank lines (allowing trailing whitespace on the blank
        # line itself) separates fragments.
        raw = re.split(r"\n\s*\n+", text)
    elif mode == "numbered":
        raw = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^\d+[.)]\s*(.*)$", line)
            raw.append(m.group(1).strip() if m else line)
    else:  # "lines" and any unrecognized mode fall back to plain lines.
        raw = text.split("\n")

    fragments = [f.strip() for f in raw]
    fragments = [f for f in fragments if f]
    return fragments[:max_items]
