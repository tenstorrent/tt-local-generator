import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import palette_prompt as pp


def test_literal_prompt_uses_hexes_and_lore():
    palette = {"name": "Dusk", "colors": [{"hex": "#1a2b3c"}, {"hex": "#ffcc00"}],
               "lore": "a moody teal-and-amber dusk"}
    out = pp.literal_prompt(palette)
    assert "#1a2b3c" in out and "#ffcc00" in out
    assert "palette:" in out
    assert "a moody teal-and-amber dusk" in out


def test_literal_prompt_caps_hexes_at_six():
    palette = {"colors": [{"hex": f"#00000{i}"} for i in range(9)]}
    assert pp.literal_prompt(palette).count("#") == 6


def test_literal_prompt_missing_colors_is_best_effort_not_raise():
    assert pp.literal_prompt({"lore": "just vibes"}) == "just vibes"
    assert pp.literal_prompt({}) == ""


def test_load_palette_reads_json(tmp_path):
    p = tmp_path / "pal.json"
    p.write_text(json.dumps({"colors": [{"hex": "#fff"}], "lore": "x"}))
    assert pp.load_palette(str(p))["lore"] == "x"


def test_load_palette_none_on_bad_input(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert pp.load_palette(str(bad)) is None
    assert pp.load_palette("") is None
    assert pp.load_palette(str(tmp_path / "missing.json")) is None
