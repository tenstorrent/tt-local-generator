import json, sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
try:
    import gi; gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)

import pipeline_studio, recipes


def _palette_file(tmp_path):
    p = tmp_path / "pal.json"
    p.write_text(json.dumps({"colors": [{"hex": "#1a2b3c"}], "lore": "moody dusk"}))
    return str(p)


def test_choose_goal_palette_prepends_adapter(tmp_path, monkeypatch):
    goal = recipes.Goal("anim", "An animation", "🕺", "gif", "scoped",
                        (("TTLGAnimateDiff", {}),))
    captured = {}
    muse = pipeline_studio.MuseView(goals_fn=lambda k: [goal])   # compose_fn=None -> sync literal
    muse.connect("goal-chosen", lambda _w, spec: captured.update(spec=spec))
    muse.set_context(seed_artifact=(_palette_file(tmp_path), "palette", None))

    muse._choose_goal(goal)

    spec = captured["spec"]
    assert spec["1"]["class_type"] == "TTLGPaletteToPrompt"
    # literal fallback carries the palette colors + lore into the prompt
    assert "#1a2b3c" in spec["1"]["inputs"]["prompt"]
    assert "moody dusk" in spec["1"]["inputs"]["prompt"]
    assert spec["2"]["class_type"] == "TTLGAnimateDiff"
    assert spec["2"]["inputs"]["prompt"] == ["1", "prompt"]


def test_choose_goal_compose_fn_supplies_llm_prompt(tmp_path):
    goal = recipes.Goal("anim", "An animation", "🕺", "gif", "scoped",
                        (("TTLGAnimateDiff", {}),))
    # compose_fn that "polishes" synchronously for the test.
    def compose(medium, literal, on_done):
        on_done(f"LLM[{medium}]: shimmering {literal}")
    captured = {}
    muse = pipeline_studio.MuseView(goals_fn=lambda k: [goal], compose_fn=compose)
    muse.connect("goal-chosen", lambda _w, spec: captured.update(spec=spec))
    muse.set_context(seed_artifact=(_palette_file(tmp_path), "palette", None))

    muse._choose_goal(goal)

    assert captured["spec"]["1"]["inputs"]["prompt"].startswith("LLM[video]: shimmering")


def test_choose_goal_non_palette_unchanged(tmp_path):
    # A direct-match text seed still goes through the normal (no-adapter) path.
    goal = recipes.Goal("illus", "An illustration", "🖼", "image", "scoped",
                        (("TTLGTextToImage", {}),))
    captured = {}
    muse = pipeline_studio.MuseView(goals_fn=lambda k: [goal])
    muse.connect("goal-chosen", lambda _w, spec: captured.update(spec=spec))
    muse.set_context(seed_artifact=("some lore text", "text", None))
    muse._choose_goal(goal)
    assert "TTLGPaletteToPrompt" not in {n.get("class_type") for n in captured["spec"].values() if isinstance(n, dict)}
