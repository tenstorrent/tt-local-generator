# SPDX-License-Identifier: Apache-2.0
"""Unit tests for app/split_text.py — pure, no GTK, no engine dependency.

Mirrors the exact test cases from .superpowers/sdd/task-1-brief.md.
"""
from split_text import split_text


def test_paragraphs():
    assert split_text("a\n\nb\n\nc", "paragraphs") == ["a", "b", "c"]


def test_numbered_strips_markers():
    assert split_text("1. red\n2. blue\n3) green", "numbered") == ["red", "blue", "green"]


def test_caps_at_max_items():
    assert len(split_text("\n\n".join(str(i) for i in range(20)), "paragraphs", max_items=8)) == 8


def test_empty_returns_empty():
    assert split_text("   \n  ", "paragraphs") == []


def test_lines_drops_blanks():
    assert split_text("x\n\n y \n", "lines") == ["x", "y"]
