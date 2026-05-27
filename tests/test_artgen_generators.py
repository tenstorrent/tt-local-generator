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
