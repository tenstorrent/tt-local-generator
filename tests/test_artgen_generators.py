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
    """Load a plugin module from an absolute path, bypassing sys.modules cache.

    Registers the freshly-loaded module in sys.modules under module_name
    BEFORE exec_module runs — required for `from <module_name> import ...`
    to resolve inside individual test methods (a bare module_from_spec()
    result is not importable by name on its own; Python's import machinery
    only ever looks in sys.modules)."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
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

    def test_uses_llm_is_true(self):
        """Spot check: an ordinary LLM-backed generator must NOT be affected
        by AnimateDiff's uses_llm=False override -- verse still drives the
        chat LLM, so its Create-surface medium must still list chat models."""
        assert self.g.uses_llm is True


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

    def test_generate_artifact_defaults_to_large_tier_without_model_tier_attr(self):
        # A call_fn with no .model_tier attribute at all (e.g. a hand-rolled
        # test double, or any caller predating this change) must reproduce
        # today's exact 3-call whole-canvas behavior.
        calls = []

        def fn(prompt, system=None, max_tokens=None):
            calls.append(prompt)
            if len(calls) == 1:
                return "A B C\nD E F"
            if len(calls) == 2:
                return "█ ░ ▒\n▓ ▀ ▄"
            return "\033[38;5;51m█\033[0m \033[38;5;82m▒\033[0m"

        assert not hasattr(fn, "model_tier")
        args = _args(ansi_style="bbs", subject="test", width=40, height=20,
                     board_name="", tagline="")
        self.g.generate_artifact(args, fn)
        assert len(calls) == 3

    def test_generate_artifact_large_tier_explicit(self):
        calls = []

        def fn(prompt, system=None, max_tokens=None):
            calls.append(prompt)
            if len(calls) == 1:
                return "A B C\nD E F"
            if len(calls) == 2:
                return "█ ░ ▒\n▓ ▀ ▄"
            return "\033[38;5;51m█\033[0m \033[38;5;82m▒\033[0m"

        fn.model_tier = "large"
        args = _args(ansi_style="bbs", subject="test", width=40, height=20,
                     board_name="", tagline="")
        self.g.generate_artifact(args, fn)
        assert len(calls) == 3

    def test_generate_artifact_small_tier_band_calls_no_colorize_call(self):
        # small tier: 3 band calls (pass 1) + 1 refine call (pass 2) +
        # 1 legend call = 5 calls total. Colorization (pass 3) is now a
        # pure local function (_colorize_grid) — no LLM call at all, since
        # a per-row 'apply this legend' call was validated to return output
        # identical to the local fallback at real cost (up to 20+ extra
        # sequential round-trips on this tier before this fix).
        calls = []

        def fn(prompt, system=None, max_tokens=None):
            calls.append(prompt)
            n = len(calls)
            if n <= 3:
                # band calls — JSON array of row-strings. Width/height for
                # this test come from the bbs style defaults (40x20); bands
                # split into [2, 16, 2] rows for bbs. Reply with a plausible
                # JSON array sized to whatever the prompt actually asked for.
                if "exactly 2 strings" in prompt:
                    return '["                                        ", "                                        "]'
                return '[' + ", ".join(
                    ['"' + ("#" * 40) + '"'] * 16
                ) + ']'
            if n == 4:
                return "\n".join(["#" * 40] * 20)  # pass 2: block refine (identity)
            # n == 5: legend
            return '{"#": 236, " ": 232}'

        args = _args(ansi_style="bbs", subject="test", width=40, height=20,
                     board_name="", tagline="")
        fn.model_tier = "small"
        result = self.g.generate_artifact(args, fn)

        # 3 band calls + 1 refine + 1 legend — no per-row colorize calls.
        assert len(calls) == 5
        lines = result.split("\n")
        assert len(lines) == 20
        for line in lines:
            assert line.count("\033[38;5;236m#") == 40
            assert line.endswith("\033[0m")

    def test_split_bands_even_division(self):
        from ansi_plugin import _split_bands
        assert _split_bands(12, 3) == [4, 4, 4]

    def test_split_bands_uneven_division_favors_earlier_bands(self):
        from ansi_plugin import _split_bands
        assert _split_bands(20, 3) == [7, 7, 6]

    def test_parse_row_json_pads_short_rows(self):
        from ansi_plugin import _parse_row_json
        raw = '["ab", "cd"]'
        rows = _parse_row_json(raw, n_rows=3, width=4)
        assert rows == ["ab  ", "cd  ", "    "]

    def test_parse_row_json_truncates_long_rows(self):
        from ansi_plugin import _parse_row_json
        raw = '["abcdef"]'
        rows = _parse_row_json(raw, n_rows=1, width=4)
        assert rows == ["abcd"]

    def test_parse_row_json_fails_soft_on_garbage(self):
        from ansi_plugin import _parse_row_json
        rows = _parse_row_json("not json at all", n_rows=2, width=3)
        assert rows == ["   ", "   "]

    def test_colorize_row_applies_legend_to_every_character(self):
        from ansi_plugin import _colorize_row
        row = _colorize_row("##", legend={"#": 236})
        assert row == "\033[38;5;236m#\033[38;5;236m#\033[0m"

    def test_colorize_row_falls_back_to_gray_for_unknown_character(self):
        from ansi_plugin import _colorize_row
        row = _colorize_row("#?", legend={"#": 236})
        assert row == "\033[38;5;236m#\033[38;5;244m?\033[0m"

    def test_colorize_row_never_changes_the_characters(self):
        # There is no model-provided character to trust or distrust here
        # (this function takes no LLM response at all) — every character
        # in the output is exactly the one passed in, in order, by
        # construction. Regexing the wrapped chars back out confirms it.
        import re
        from ansi_plugin import _colorize_row
        row = _colorize_row("a#Z", legend={})
        wrapped_chars = re.findall(r"\033\[38;5;\d+m(.)", row)
        assert "".join(wrapped_chars) == "a#Z"

    def test_colorize_row_clamps_out_of_range_color(self):
        from ansi_plugin import _colorize_row
        row = _colorize_row("#", legend={"#": 9999})
        assert row == "\033[38;5;255m#\033[0m"

    def test_colorize_grid_joins_rows(self):
        from ansi_plugin import _colorize_grid
        grid = _colorize_grid("##\n##", {"#": 236})
        lines = grid.split("\n")
        assert len(lines) == 2
        for line in lines:
            assert line == "\033[38;5;236m#\033[38;5;236m#\033[0m"

    def test_generate_artifact_medium_tier_no_colorize_call(self):
        # medium tier: pass1 (whole canvas) + pass2 (whole canvas) +
        # legend = 3 calls total. Colorization (pass 3) is now a pure
        # local function (_colorize_grid) — no LLM call.
        calls = []

        def fn(prompt, system=None, max_tokens=None):
            calls.append(prompt)
            n = len(calls)
            if n == 1:
                return "\n".join(["#" * 40] * 20)  # pass 1
            if n == 2:
                return "\n".join(["#" * 40] * 20)  # pass 2
            # n == 3: legend
            return '{"#": 236, " ": 232}'

        args = _args(ansi_style="bbs", subject="test", width=40, height=20,
                     board_name="", tagline="")
        fn.model_tier = "medium"
        result = self.g.generate_artifact(args, fn)

        assert len(calls) == 3
        lines = result.split("\n")
        assert len(lines) == 20
        for line in lines:
            assert line.count("\033[38;5;236m#") == 40

    def test_parse_legend_json_drops_only_the_bad_entry(self):
        # Regression: one bad value (a color name instead of an int) used
        # to discard the whole legend via one shared try/except. The good
        # entry must survive.
        from ansi_plugin import _parse_legend_json
        legend = _parse_legend_json('{"#": 236, " ": "black"}')
        assert legend == {"#": 236}


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

    def test_uses_llm_is_false(self):
        """This is the class actually instantiated by artgen's plugin
        registry (plugin_loader._load_local_generator loads plugin.py, then
        artgen._load_generators() back-fills _GENERATORS from it) -- so this
        is the `uses_llm` value create_mediums.default_mediums()'s
        `uses_llm_for` callback (artgen.get("animatediff").uses_llm) will
        actually observe at runtime. AnimateDiff generates via a subprocess,
        never a chat LLM, so this must be False."""
        assert self.g.uses_llm is False
