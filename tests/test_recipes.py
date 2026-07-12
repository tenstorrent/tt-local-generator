"""Tests for recipes — the Muse's goal catalog (SP-C).

Curated goals are hand-authored starter pipelines (spec_remix.seed_spec
wiring); discovered goals come from plugin manifests' x-ttlg.goal block via
an injected mcp_reader, never real disk/network I/O in these tests.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import recipes
from recipes import Goal, curated_goals, all_goals, goals_for, build_seed_spec


def test_curated_goals_nonempty_and_kind_safe():
    goals = curated_goals()
    assert any(g.id == "looping-animation" for g in goals)
    # every curated recipe materializes without raising (kind-safe wiring)
    for g in goals:
        seed = ("/tmp/x.png", "image") if g.applies_to == "scoped" else None
        spec = build_seed_spec(g, seed_artifact=seed)
        assert len(spec) == len(g.recipe_steps)


def test_goals_for_blank_excludes_scoped():
    ids = {g.id for g in goals_for(seed_output_kind=None)}
    assert "poster" in ids
    assert "animate-this" not in ids   # scoped-only


def test_goals_for_scoped_image_only_image_consumers():
    goals = goals_for(seed_output_kind="image")
    assert goals, "expected image-consuming scoped goals"
    for g in goals:
        first_ct = g.recipe_steps[0][0]
        import intent_vocab as iv
        assert iv.intent_for(first_ct).input_kind == "image"


def test_discover_goals_from_fake_mcp_reader():
    fake = lambda: {"myplug": {"x-ttlg": {"goal": {
        "label": "A music video", "icon": "🎵", "output_kind": "video",
        "recipe": ["TTLGTextToImage", "TTLGImageToVideo"]}},
        "tools": [{"name": "myplug"}]}}
    goals = recipes.discover_goals(mcp_reader=fake)
    assert any(g.label == "A music video" and g.via == "discovered" for g in goals)


def test_discover_goals_bad_reader_returns_empty():
    def boom(): raise RuntimeError("no disk")
    assert recipes.discover_goals(mcp_reader=boom) == []


def test_all_goals_curated_wins_on_id_collision():
    fake = lambda: {"p": {"x-ttlg": {"goal": {
        "label": "X", "icon": "x", "output_kind": "image", "recipe": ["TTLGTextToImage"],
        "id": "poster"}}, "tools": [{"name": "p"}]}}
    poster = [g for g in all_goals(mcp_reader=fake) if g.id == "poster"]
    assert len(poster) == 1 and poster[0].via == "curated"
