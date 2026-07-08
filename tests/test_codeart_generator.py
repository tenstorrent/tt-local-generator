"""Unit tests for the codeart (code-as-art) artgen plugin."""
import argparse
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
_CODEART_PLUGIN = Path(__file__).parent.parent / "plugins" / "codeart" / "plugin.py"


def _load():
    """Load plugins/codeart/plugin.py fresh, bypassing the sys.modules cache."""
    spec = importlib.util.spec_from_file_location("codeart_plugin", _CODEART_PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**kw):
    ns = argparse.Namespace()
    # sensible defaults so getattr() in the plugin always resolves
    ns.language = "python"
    ns.inspiration = "the nature of recursion"
    ns.style = "auto"
    ns.should_compile = True
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestHelpers:
    @pytest.fixture(autouse=True)
    def mod(self):
        self.m = _load()

    def test_styles_has_eight_including_auto(self):
        assert set(self.m._STYLES) == {
            "auto", "quine", "ascii", "poem", "oneliner",
            "glitch", "unusually_verbose", "function_oriented",
        }
        assert self.m._STYLES["auto"] == ""

    def test_build_messages_user_has_language_and_inspiration(self):
        system, user = self.m._build_messages(_args(language="rust", inspiration="the tide"))
        assert "rust" in user
        assert "the tide" in user

    def test_should_compile_directive_present_when_true(self):
        system, _ = self.m._build_messages(_args(should_compile=True))
        assert "compiles" in system.lower()

    def test_should_compile_directive_absent_when_false(self):
        system, _ = self.m._build_messages(_args(should_compile=False))
        assert "compiles and runs as-is" not in system.lower()

    def test_style_auto_adds_no_style_hint(self):
        system_auto, _ = self.m._build_messages(_args(style="auto"))
        system_quine, _ = self.m._build_messages(_args(style="quine"))
        assert len(system_quine) > len(system_auto)
        assert "quine" in system_quine.lower()

    def test_validate_python_accepts_valid(self):
        ok, err = self.m.validate_python("def f(x):\n    return x * 2\n")
        assert ok is True
        assert err is None

    def test_validate_python_rejects_invalid(self):
        ok, err = self.m.validate_python("def f(:\n  pass")
        assert ok is False
        assert isinstance(err, str) and err


class TestCodeArtGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        self.g = _load().CodeArtGenerator()

    def test_identity(self):
        assert self.g.name == "codeart"
        assert self.g.output_ext == ".py"

    def test_add_args_wires_defaults(self):
        p = argparse.ArgumentParser()
        self.g.add_args(p)
        ns = p.parse_args([])
        assert ns.language == "python"
        assert ns.inspiration == "the nature of recursion"
        assert ns.style == "auto"
        assert ns.should_compile is True

    def test_add_args_no_should_compile_flag(self):
        p = argparse.ArgumentParser()
        self.g.add_args(p)
        ns = p.parse_args(["--no-should-compile"])
        assert ns.should_compile is False

    def test_build_prompt_returns_user_message(self):
        out = self.g.build_prompt(_args(language="c", inspiration="entropy"))
        assert isinstance(out, str)
        assert "c" in out and "entropy" in out

    def test_parse_output_strips_fences_and_think(self):
        raw = "<think>plan</think>\n```python\nprint('hi')\n```"
        out = self.g.parse_output(raw, _args())
        assert "```" not in out
        assert "<think>" not in out
        assert "print('hi')" in out

    def test_post_process_flags_valid_python(self):
        args = _args(language="python")
        out = self.g.post_process("x = 1\n", args)
        assert out == "x = 1\n"                    # unchanged
        assert args._codeart_compiles is True
        assert args._codeart_error is None

    def test_post_process_flags_invalid_python(self):
        args = _args(language="python")
        self.g.post_process("def (:\n", args)
        assert args._codeart_compiles is False
        assert isinstance(args._codeart_error, str)

    def test_post_process_non_python_unvalidated(self):
        args = _args(language="rust")
        self.g.post_process("fn main() {}", args)
        assert args._codeart_compiles is None
        assert args._codeart_error is None

    def test_generate_artifact_end_to_end(self):
        args = _args(language="python", inspiration="mirrors", style="quine")
        call_fn = MagicMock(return_value="```python\nprint(open(__file__).read())\n```")
        artifact = self.g.generate_artifact(args, call_fn)
        # system prompt was passed through
        _, kwargs = call_fn.call_args
        assert "system" in kwargs and "quine" in kwargs["system"].lower()
        # cleaned + validated
        assert "```" not in artifact
        assert args._codeart_compiles is True
