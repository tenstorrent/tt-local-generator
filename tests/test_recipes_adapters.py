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
