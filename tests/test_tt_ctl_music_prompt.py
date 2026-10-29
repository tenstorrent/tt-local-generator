"""tt-ctl music-prompt and the Inspire (generate_prompt) library-first path."""
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

_loader = importlib.machinery.SourceFileLoader("tt_ctl_music", str(Path(__file__).parent.parent / "tt-ctl"))
_spec = importlib.util.spec_from_loader("tt_ctl_music", _loader)
tt_ctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tt_ctl)

import prompt_client  # noqa: E402

EXAMPLE = "tortoise-like song but someone is singing a bluegrass song on top"


def run_cli(capsys, *argv):
    args = tt_ctl._build_parser().parse_args(["music-prompt", *argv])
    tt_ctl.cmd_music_prompt(args)
    return capsys.readouterr().out


def run_json(capsys, *argv):
    return json.loads(run_cli(capsys, "--json", *argv))


def test_cli_translates_the_example_sentence(capsys):
    out = run_cli(capsys, EXAMPLE, "--model", "minimax")
    assert out.strip()
    assert "tortoise" not in out.splitlines()[0].lower()   # the prompt line; notes may name the source


def test_cli_model_minimax_returns_lyrics_and_stable_audio_does_not(capsys):
    mm = run_json(capsys, EXAMPLE, "--model", "minimax")
    sa = run_json(capsys, EXAMPLE, "--model", "stable-audio")
    assert mm["lyrics"]
    assert not sa["lyrics"]
    out = run_cli(capsys, EXAMPLE, "--model", "minimax")
    assert "lyrics:" in out


def test_cli_wand_with_seed_is_repeatable(capsys):
    a = run_json(capsys, "--wand", "--seed", "3")
    b = run_json(capsys, "--wand", "--seed", "3")
    assert a["prompt"] and a == b


def test_cli_json_has_prompt_lyrics_notes_inspired_by(capsys):
    d = run_json(capsys, EXAMPLE, "--model", "minimax")
    for k in ("prompt", "lyrics", "notes", "inspired_by"):
        assert k in d


def test_cli_no_names_in_prompt_unless_flagged(capsys):
    plain = run_json(capsys, "tortoise-like song")
    named = run_json(capsys, "tortoise-like song", "--include-names")
    assert "tortoise" not in plain["prompt"].lower()
    assert "tortoise" in named["prompt"].lower()


def test_cli_without_text_or_wand_exits_two(capsys):
    args = tt_ctl._build_parser().parse_args(["music-prompt"])
    with pytest.raises(SystemExit) as e:
        tt_ctl.cmd_music_prompt(args)
    assert e.value.code == 2


def test_inspire_music_uses_the_library_before_the_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("LLM called")
    monkeypatch.setattr(prompt_client, "_music_llm_text", boom)
    out = prompt_client.generate_prompt("music", "tortoise")
    assert out.strip() and "tortoise" not in out.lower()


def test_inspire_music_falls_back_to_the_llm_for_unknown_text(monkeypatch):
    monkeypatch.setattr(prompt_client, "_music_llm_text", lambda s, t: "FROM LLM")
    assert prompt_client.generate_prompt("music", "zxqv blorptastic") == "FROM LLM"


def test_inspire_library_failure_falls_through_to_the_llm(monkeypatch):
    import music_prompts
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(music_prompts, "translate", boom)
    monkeypatch.setattr(prompt_client, "_music_llm_text", lambda s, t: "FROM LLM")
    assert prompt_client.generate_prompt("music", "tortoise") == "FROM LLM"


def test_inspire_empty_text_uses_wand(monkeypatch):
    monkeypatch.setattr(prompt_client, "_music_llm_text", lambda s, t: (_ for _ in ()).throw(AssertionError("LLM")))
    assert prompt_client.generate_prompt("music", "").strip()


def test_inspire_lyrics_unchanged(monkeypatch):
    monkeypatch.setattr(prompt_client, "_music_llm_text", lambda s, t: "LYR")
    assert prompt_client.generate_prompt("lyrics", "tortoise") == "LYR"
