import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import recipes


def _animatediff_goal():
    # A scoped goal whose first step needs text (like AnimateDiff).
    return recipes.Goal("anim", "An animation", "🕺", "gif", "scoped",
                        (("TTLGAnimateDiff", {}),))


def test_goals_for_offers_palette_reachable_goal(monkeypatch):
    monkeypatch.setattr(recipes, "_CURATED", (_animatediff_goal(),))
    ids = {g.id for g in recipes.goals_for(seed_output_kind="palette")}
    assert "anim" in ids   # offered via the palette->text adapter


def test_goals_for_still_excludes_unreachable(monkeypatch):
    # A goal whose first step needs 'image' is not reachable from a palette.
    g = recipes.Goal("vid", "A video", "🎬", "video", "scoped",
                    (("TTLGImageToVideo", {}),))
    monkeypatch.setattr(recipes, "_CURATED", (g,))
    ids = {x.id for x in recipes.goals_for(seed_output_kind="palette")}
    assert "vid" not in ids


def test_real_catalog_offers_animatediff_from_palette_seed_and_blank():
    # Regression guard for the marquee "palette -> Remix -> AnimateDiff"
    # journey: the shipped catalog's ONLY TTLGAnimateDiff goal
    # ("looping-animation") must stay reachable from a palette seed (via the
    # TTLGPaletteToPrompt adapter) AND must not have been dropped from blank
    # mode by the applies_to fix. Uses the REAL curated catalog, not a
    # monkeypatched _CURATED, so catalog drift trips this test.
    scoped_goals = recipes.goals_for(seed_output_kind="palette")
    assert any(
        g.recipe_steps[0][0] == "TTLGAnimateDiff" for g in scoped_goals
    ), "no palette-reachable goal starts with TTLGAnimateDiff"

    blank_ids = {g.id for g in recipes.goals_for(seed_output_kind=None)}
    assert "looping-animation" in blank_ids


def test_build_seed_spec_prepends_adapter_and_wires():
    goal = _animatediff_goal()
    spec = recipes.build_seed_spec(
        goal, seed_artifact=None,
        prepend_steps=(("TTLGPaletteToPrompt", {"prompt": "a moody dusk"}),),
    )
    # Node 1 is the adapter holding the editable prompt; node 2 (AnimateDiff)
    # takes its prompt wired from node 1's "prompt" output.
    assert spec["1"]["class_type"] == "TTLGPaletteToPrompt"
    assert spec["1"]["inputs"]["prompt"] == "a moody dusk"
    assert spec["2"]["class_type"] == "TTLGAnimateDiff"
    assert spec["2"]["inputs"]["prompt"] == ["1", "prompt"]
