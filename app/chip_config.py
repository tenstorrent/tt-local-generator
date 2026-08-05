# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""chip_config.py — Load prompt chip definitions from config/prompt_chips.yaml.

Public API:
    ChipEntry     — dataclass: label, text, tip, surprise
    ChipCategory  — dataclass: name, chips
    load_chips(tab, config_path=None) -> list[ChipCategory]
    load_chips_for_artgen(artgen_type, config_path=None) -> list[ChipCategory]
    surprise_pool(category) -> list[str]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parent
_DEFAULT_CONFIG = _REPO_ROOT / "config" / "prompt_chips.yaml"
_ALL_TABS = frozenset({"video", "image", "animate"})


@dataclass
class ChipEntry:
    label: str          # button label (may include emoji)
    text: str           # text appended to prompt on click
    tip: str = ""       # tooltip (empty string if omitted)
    surprise: bool = False  # if True, text is auto-generated; omit text in YAML


@dataclass
class ChipCategory:
    name: str                    # display name shown as group header
    chips: list[ChipEntry] = field(default_factory=list)


def load_chips(tab: str, config_path: Path | None = None) -> list[ChipCategory]:
    """Load chip categories for *tab* ('video', 'image', or 'animate').

    config_path defaults to <repo_root>/config/prompt_chips.yaml.
    Categories with no chips matching *tab* after filtering are omitted.

    Raises:
        FileNotFoundError: config file does not exist
        ValueError: schema error (missing required field)
    """
    path = Path(config_path) if config_path is not None else _DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"Chip config not found: {path}")

    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if data is None:
        return []

    result: list[ChipCategory] = []
    for cat_idx, cat_raw in enumerate(data):
        cat_name = cat_raw.get("name")
        if not cat_name:
            raise ValueError(f"Category at index {cat_idx} is missing required field 'name'")

        # Category-level for: defaults to all tabs
        cat_for = set(cat_raw.get("for", list(_ALL_TABS)))

        chips_raw = cat_raw.get("chips", [])
        matched: list[ChipEntry] = []
        for chip_idx, chip_raw in enumerate(chips_raw):
            label = chip_raw.get("label")
            if not label:
                raise ValueError(
                    f"Chip at category '{cat_name}' index {chip_idx} is missing required field 'label'"
                )
            text = chip_raw.get("text")
            is_surprise = bool(chip_raw.get("surprise", False))
            if text is None and not is_surprise:
                raise ValueError(
                    f"Chip at category '{cat_name}' index {chip_idx} is missing required field 'text'"
                )

            # Chip-level for: replaces (does not merge with) category for:
            if "for" in chip_raw:
                effective_for = set(chip_raw["for"])
            else:
                effective_for = cat_for

            if tab in effective_for:
                matched.append(ChipEntry(
                    label=label,
                    text=text if text is not None else "",
                    tip=chip_raw.get("tip", ""),
                    surprise=is_surprise,
                ))

        if matched:
            result.append(ChipCategory(name=cat_name, chips=matched))

    return result


def load_chips_for_artgen(artgen_type: str, config_path: Path | None = None) -> list[ChipCategory]:
    """Chip banks for an artgen TYPE: that type's own categories plus the
    shared cross-type 'artgen' mood bank. Deduped by category name (type
    first). A type with no curated banks yields just the shared bank."""
    type_cats = load_chips(artgen_type, config_path)
    seen = {c.name for c in type_cats}
    shared = [c for c in load_chips("artgen", config_path) if c.name not in seen]
    return type_cats + shared


def surprise_pool(category: ChipCategory) -> list[str]:
    """The `.text` of every non-surprise chip in *category* — the pool a
    Surprise chip picks from."""
    return [c.text for c in category.chips if not c.surprise and c.text]
