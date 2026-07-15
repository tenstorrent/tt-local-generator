"""Unit tests for the emoji-storyteller artgen plugin.

Doubles as executable documentation for docs/tutorials/adding-an-artgen-plugin.md:
it shows that a generator which reuses an existing chat model is testable with a
fake `call_fn` and no network / no TT hardware.
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
_PLUGIN = Path(__file__).parent.parent / "plugins" / "emoji-storyteller" / "plugin.py"


def _load():
    """Load plugins/emoji-storyteller/plugin.py fresh, bypassing sys.modules."""
    spec = importlib.util.spec_from_file_location("emoji_storyteller_plugin", _PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gen():
    return _load().EmojiStorytellerGenerator()


def _args(**kw):
    ns = argparse.Namespace()
    ns.theme = "a hero's journey"
    ns.scenes = 6
    ns.words = 0
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_generator_identity():
    g = _gen()
    assert g.name == "emoji-storyteller"
    assert g.output_ext == ".txt"
    assert g.uses_llm is True  # reuses the running chat model; no new backend


def test_add_args_defaults_parse():
    g = _gen()
    p = argparse.ArgumentParser()
    g.add_args(p)
    ns = p.parse_args([])
    assert ns.theme == "a hero's journey"
    assert ns.scenes == 6
    assert ns.words == 0


def test_build_prompt_mentions_theme_and_scene_count():
    g = _gen()
    prompt = g.build_prompt(_args(theme="a cat who wants to fly", scenes=5))
    assert "a cat who wants to fly" in prompt
    assert "5 scene" in prompt


def test_build_prompt_zero_words_forbids_real_words():
    g = _gen()
    assert "NO real words" in g.build_prompt(_args(words=0))


def test_build_prompt_word_budget_states_limit():
    g = _gen()
    assert "at most 2 real word" in g.build_prompt(_args(words=2))


def test_generate_artifact_passes_house_system_prompt_and_parses():
    """A fake call_fn stands in for the LLM — no network, no hardware."""
    g = _gen()
    captured = {}

    def fake_call(prompt, system=None, max_tokens=None):
        captured["prompt"] = prompt
        captured["system"] = system
        return "<think>plan</think>```\n🐱 -> ✈️ -> 🌈\n```"

    out = g.generate_artifact(_args(), fake_call)
    assert out == "🐱 -> ✈️ -> 🌈"          # think-block + fences stripped
    assert captured["system"] is not None     # house style forwarded
    assert "Emoji Storyteller" in captured["system"]
    assert captured["prompt"] == g.build_prompt(_args())


def test_discovered_by_plugin_loader():
    """The dir + mcp.json + plugin.py combination registers automatically."""
    import plugin_loader
    plugin_loader.load_plugins()
    assert "emoji-storyteller" in plugin_loader.all_names()
    pdef = plugin_loader.get("emoji-storyteller")
    assert pdef.runnable is True
    assert pdef.generator.name == "emoji-storyteller"
