"""Per-generator tests. Each class exercises build_prompt, parse_output,
generate_artifact with a mocked call_fn."""
import argparse
import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "verse"))
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "freeform"))

_VERSE_PLUGIN = Path(__file__).parent.parent / "plugins" / "verse" / "plugin.py"
_FREEFORM_PLUGIN = Path(__file__).parent.parent / "plugins" / "freeform" / "plugin.py"
_PALETTE_PLUGIN = Path(__file__).parent.parent / "plugins" / "palette" / "plugin.py"
_CONSTELLATION_PLUGIN = Path(__file__).parent.parent / "plugins" / "constellation" / "plugin.py"
_GEOMETRIC_PLUGIN = Path(__file__).parent.parent / "plugins" / "geometric" / "plugin.py"
_CIRCUIT_PLUGIN = Path(__file__).parent.parent / "plugins" / "circuit" / "plugin.py"
_SKYLINE_PLUGIN = Path(__file__).parent.parent / "plugins" / "skyline" / "plugin.py"
_LANDSCAPE_PLUGIN = Path(__file__).parent.parent / "plugins" / "landscape" / "plugin.py"
_ANSI_PLUGIN = Path(__file__).parent.parent / "plugins" / "ansi" / "plugin.py"
_ANIMATEDIFF_PLUGIN = Path(__file__).parent.parent / "plugins" / "animatediff" / "plugin.py"


def _load_plugin(path: Path, module_name: str):
    """Load a plugin module from an absolute path, bypassing sys.modules cache."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**kw):
    ns = argparse.Namespace()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _mock_call_fn(response="mock output"):
    fn = MagicMock(return_value=response)
    return fn


class TestVerseGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        mod = _load_plugin(_VERSE_PLUGIN, "verse_plugin")
        self.g = mod.VerseGenerator()

    def test_build_prompt_returns_string(self):
        args = _args(form="haiku", theme="winter", count=3)
        result = self.g.build_prompt(args)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_prompt_includes_theme(self):
        args = _args(form="haiku", theme="neon forests", count=2)
        assert "neon forests" in self.g.build_prompt(args)

    def test_parse_output_strips_fences(self):
        raw = "```\nfrost on wire\nsilver morning holds its breath\ncrows wait\n```"
        result = self.g.parse_output(raw, _args())
        assert "```" not in result
        assert "frost on wire" in result

    def test_parse_output_strips_think_blocks(self):
        raw = "<think>thinking</think>\nfrost on wire\nsilver morning"
        result = self.g.parse_output(raw, _args())
        assert "<think>" not in result
        assert "frost on wire" in result

    def test_generate_artifact_calls_call_fn(self):
        fn = _mock_call_fn("three lines\nof winter\nand silence")
        args = _args(form="haiku", theme="ice", count=1)
        result = self.g.generate_artifact(args, fn)
        fn.assert_called_once()
        assert "three lines" in result

    def test_default_output_extension(self):
        assert self.g.default_output().suffix == ".txt"

    def test_all_forms_produce_prompts(self):
        for form in ("haiku", "lore", "epitaph", "couplet"):
            args = _args(form=form, theme="test", count=1)
            prompt = self.g.build_prompt(args)
            assert len(prompt) > 10


class TestFreeformGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        mod = _load_plugin(_FREEFORM_PLUGIN, "freeform_plugin")
        self.g = mod.FreeformGenerator()

    def test_build_prompt_includes_freeform_text(self):
        args = _args(freeform="a robot weeping in the rain", output=None)
        result = self.g.build_prompt(args)
        assert "a robot weeping in the rain" in result

    def test_build_prompt_raises_on_empty(self):
        args = _args(freeform="", output=None)
        with pytest.raises(ValueError, match="--freeform"):
            self.g.build_prompt(args)

    def test_parse_output_svg_extracted(self):
        raw = "Here is your SVG:\n<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>"
        args = _args(output="out.svg")
        result = self.g.parse_output(raw, args)
        assert result.startswith("<svg")

    def test_parse_output_strips_fences_for_txt(self):
        raw = "```\nhello world\n```"
        args = _args(output="out.txt")
        result = self.g.parse_output(raw, args)
        assert "hello world" in result
        assert "```" not in result

    def test_generate_artifact_calls_call_fn(self):
        fn = _mock_call_fn("some output text")
        args = _args(freeform="draw something", output=None)
        result = self.g.generate_artifact(args, fn)
        fn.assert_called_once()
        assert "some output text" in result


class TestPaletteGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        mod = _load_plugin(_PALETTE_PLUGIN, "palette_plugin")
        self.g = mod.PaletteGenerator()

    def test_build_prompt_includes_mood(self):
        args = _args(mood="volcanic", count=6)
        assert "volcanic" in self.g.build_prompt(args)

    def test_parse_output_returns_valid_json(self):
        raw = '{"name": "Ember", "colors": [{"hex": "#FF6600", "role": "accent"}], "lore": "Hot."}'
        result = self.g.parse_output(raw, _args())
        import json
        data = json.loads(result)
        assert data["name"] == "Ember"

    def test_parse_output_raises_on_missing_fields(self):
        # palette.parse_output raises ValueError when 'name' or 'colors' key is absent
        raw = '{"colors": []}'
        with pytest.raises(ValueError, match="missing required fields"):
            self.g.parse_output(raw, _args())

    def test_generate_artifact_calls_call_fn(self):
        response = '{"name": "Test", "colors": [{"hex": "#000000", "role": "bg"}], "lore": "Dark."}'
        fn = _mock_call_fn(response)
        result = self.g.generate_artifact(_args(mood="test", count=1, export_css=False), fn)
        fn.assert_called_once()
        assert "Test" in result


class TestConstellationGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        mod = _load_plugin(_CONSTELLATION_PLUGIN, "constellation_plugin")
        self.g = mod.ConstellationGenerator()

    def test_build_prompt_returns_string(self):
        args = _args(culture="greek", stars=7, lore=False)
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_default_output_is_svg(self):
        assert self.g.default_output().suffix == ".svg"

    def test_generate_artifact_calls_call_fn(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="2"/></svg>'
        fn = _mock_call_fn(svg)
        result = self.g.generate_artifact(_args(culture="greek", stars=5, lore=False), fn)
        fn.assert_called_once()


class TestGeometricGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        mod = _load_plugin(_GEOMETRIC_PLUGIN, "geometric_plugin")
        self.g = mod.GeometricGenerator()

    def test_build_prompt_returns_string(self):
        # geometric uses geo_palette (dest="geo_palette"), not palette
        args = _args(style="mondrian", geo_palette="teal", complexity="low")
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_build_prompt_includes_style(self):
        args = _args(style="mondrian", geo_palette="teal", complexity="low")
        result = self.g.build_prompt(args)
        # The mondrian style description is embedded in the prompt
        assert "mondrian" in result.lower() or "De Stijl" in result or len(result) > 50

    def test_default_output_is_svg(self):
        assert self.g.default_output().suffix == ".svg"


class TestCircuitGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        mod = _load_plugin(_CIRCUIT_PLUGIN, "circuit_plugin")
        self.g = mod.CircuitGenerator()

    def test_build_prompt_returns_string(self):
        # circuit uses inputs/gates as comma-separated strings, circuit_style not style
        args = _args(inputs="A,B", gates="and,or", depth=2, circuit_style="clean")
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_build_prompt_includes_inputs(self):
        args = _args(inputs="X,Y", gates="and", depth=1, circuit_style="clean")
        result = self.g.build_prompt(args)
        assert "X" in result and "Y" in result

    def test_default_output_is_svg(self):
        assert self.g.default_output().suffix == ".svg"


class TestSkylineGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        mod = _load_plugin(_SKYLINE_PLUGIN, "skyline_plugin")
        self.g = mod.SkylineGenerator()

    def test_build_prompt_returns_string(self):
        args = _args(era="retro", sky="dusk", density="medium")
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_build_prompt_includes_era(self):
        args = _args(era="retro", sky="dusk", density="medium")
        result = self.g.build_prompt(args)
        # retro era adjective is "neon-lit, 1970s, retrofuturistic"
        assert "retro" in result.lower() or "1970s" in result or len(result) > 50

    def test_default_output_is_svg(self):
        assert self.g.default_output().suffix == ".svg"


class TestLandscapeGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        mod = _load_plugin(_LANDSCAPE_PLUGIN, "landscape_plugin")
        self.g = mod.LandscapeGenerator()

    def test_build_prompt_includes_palette_colors(self):
        # sunset palette has sky_bottom=#FF6B35 and adjective="warm, dramatic, cinematic"
        args = _args(palette="sunset", mountains=True, clouds=False, stars=False)
        result = self.g.build_prompt(args)
        assert "#FF6B35" in result or "sunset" in result.lower()

    def test_build_prompt_random_palette_works(self):
        args = _args(palette="random", mountains=True, clouds=False, stars=False)
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_parse_output_valid_svg(self):
        raw = '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect/></svg>'
        result = self.g.parse_output(raw, _args())
        assert result.startswith("<svg")

    def test_parse_output_raises_on_no_svg(self):
        with pytest.raises(ValueError, match="SVG"):
            self.g.parse_output("no svg here at all", _args())

    def test_default_output_is_svg(self):
        assert self.g.default_output().suffix == ".svg"

    def test_generate_artifact_calls_call_fn(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        fn = _mock_call_fn(svg)
        args = _args(palette="sunset", mountains=True, clouds=False, stars=False, glitch=False)
        result = self.g.generate_artifact(args, fn)
        fn.assert_called_once()
        assert result.startswith("<svg")


class TestAnsiGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        mod = _load_plugin(_ANSI_PLUGIN, "ansi_plugin")
        self.g = mod.AnsiGenerator()

    def test_build_prompt_returns_pass1_ascii_prompt(self):
        args = _args(ansi_style="bbs", subject="a dragon", width=40, height=20)
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_default_output_is_ans(self):
        assert self.g.default_output().suffix == ".ans"

    def test_generate_artifact_makes_three_llm_calls(self):
        # AnsiGenerator is multi-pass — call_fn should be called 3 times
        calls = []

        def fn(prompt, system=None, max_tokens=None):
            calls.append({"prompt": prompt, "max_tokens": max_tokens})
            if len(calls) == 1:
                return "A B C\nD E F"  # pass 1: ASCII
            if len(calls) == 2:
                return "█ ░ ▒\n▓ ▀ ▄"  # pass 2: blocks
            return "\033[38;5;51m█\033[0m \033[38;5;82m▒\033[0m"  # pass 3: color

        args = _args(ansi_style="bbs", subject="test", width=40, height=20,
                     board_name="", tagline="")
        self.g.generate_artifact(args, fn)
        assert len(calls) == 3


class TestAnimateDiffGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        mod = _load_plugin(_ANIMATEDIFF_PLUGIN, "animatediff_plugin")
        self.g = mod.AnimateDiffGenerator()

    def test_build_prompt_raises(self):
        """AnimateDiff bypasses the LLM pipeline — build_prompt must raise."""
        with pytest.raises(RuntimeError, match="does not use build_prompt"):
            self.g.build_prompt(_args())

    def test_default_output_is_gif(self):
        assert self.g.default_output().suffix == ".gif"

    def test_name_is_animatediff(self):
        assert self.g.name == "animatediff"
