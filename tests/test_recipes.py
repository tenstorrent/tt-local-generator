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
from intent_vocab import intent_for
from spec_remix import editable_params


def test_curated_goals_nonempty_and_kind_safe():
    goals = curated_goals()
    assert any(g.id == "looping-animation" for g in goals)
    # every curated recipe materializes without raising (kind-safe wiring)
    for g in goals:
        if g.applies_to == "scoped":
            first_kind = intent_for(g.recipe_steps[0][0]).input_kind
            seed = (("frag one\n\nfrag two", "text") if first_kind == "text"
                    else ("/tmp/x.png", "image"))
        else:
            seed = None
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


def test_text_scoped_goals_present():
    ids = {g.id for g in recipes.goals_for(seed_output_kind="text")}
    assert {"illustrated-series", "illustrate-it", "lore-poster"} <= ids
    # image-seeded scoped goals are NOT offered for a text seed
    assert "animate-this" not in ids


def test_illustrated_series_builds_a_valid_seeded_spec():
    g = next(g for g in recipes.curated_goals() if g.id == "illustrated-series")
    spec = recipes.build_seed_spec(g, seed_artifact=("frag one\n\nfrag two", "text"))
    assert spec["1"]["class_type"] == "TTLGSplitText"
    assert spec["1"]["inputs"]["text"] == "frag one\n\nfrag two"   # text content seeded, not a path
    assert spec["2"]["inputs"]["prompt"] == ["1", "fragments"]      # auto-wired fan-out
    assert spec["3"]["inputs"]["images"] == ["2", "image_path"]     # montage consumes the batch
    assert spec["4"]["inputs"]["artifacts"] == ["2", "image_path"]  # playlist consumes the batch


def test_every_blank_goal_first_step_seeds_an_editable_prompt():
    """Every curated BLANK goal's first step must carry a default text
    literal on its intent's input_key, so the composer surfaces something
    editable on node "1" — the user rewrites it rather than starting from a
    blank field. Scoped goals must be unchanged: their first step consumes
    the seed artifact, not a typed-in default."""
    for g in curated_goals():
        first_ct, _first_params = g.recipe_steps[0]
        input_key = intent_for(first_ct).input_key
        assert input_key, f"{g.id}: first step {first_ct} has no input_key"

        if g.applies_to == "blank":
            spec = build_seed_spec(g, seed_artifact=None)
            fields = editable_params(spec)["1"]
            matching = [f for f in fields if f.key == input_key]
            assert matching, (
                f"{g.id}: expected an editable {input_key!r} field on node "
                f"'1', got {[f.key for f in fields]}"
            )
            assert isinstance(matching[0].value, str) and matching[0].value.strip(), (
                f"{g.id}: default literal for {input_key!r} must be a "
                "non-empty string"
            )
        elif g.applies_to == "scoped":
            # Scoped goals' first step consumes the seed artifact (wired at
            # build_seed_spec time via seed_artifact=), not a typed-in
            # default — the recipe's own declared params must NOT carry a
            # hardcoded literal on the canonical input_key.
            first_params = g.recipe_steps[0][1]
            assert input_key not in first_params, (
                f"{g.id}: scoped goal's first step should not declare a "
                f"literal default on {input_key!r} (its param dict is "
                f"{first_params!r}) — that key is filled by the seed "
                "artifact, not a hardcoded default"
            )


def test_illustrated_series_wires_per_image_prompts_as_captions():
    import recipes
    g = next(x for x in recipes.curated_goals() if x.id == "illustrated-series")
    spec = recipes.build_seed_spec(g, seed_artifact=("frag one\n\nfrag two", "text"))
    # montage + playlist both caption from node 2's per-image `prompts` (aligned),
    # so each still keeps its OWN vibrant caption (not one shared one)
    assert spec["3"]["inputs"]["captions"] == ["2", "prompts"]
    assert spec["4"]["inputs"]["captions"] == ["2", "prompts"]
