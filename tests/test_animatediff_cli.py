"""Unit tests for CLI parity: argv → run_subprocess kwarg forwarding for the
'tt-ctl artgen animatediff' route.

Prior to this fix, `app/artgen/cli.py::_cmd_animatediff` forwarded only
prompt/frames/steps/seed/negative_prompt/temporal_alpha to run_subprocess,
silently dropping every advanced/multi-chip flag that `add_args` already
declared (or, for --multichip-mode/--prompt-schedule/--loop, that didn't
exist as a flag at all). These tests build a real argparse parser via
AnimateDiffGenerator.add_args, parse a full argv, and assert every kwarg
reaches the (mocked) run_subprocess call with the right value.
"""
import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import artgen.generators.animatediff as ad  # noqa: E402  (after sys.path insert)
from artgen import cli as artgen_cli  # noqa: E402


def test_uses_llm_is_false():
    """AnimateDiff bypasses the LLM pipeline entirely (build_prompt raises,
    generation runs via subprocess) -- ArtGenerator.uses_llm must be
    overridden to False here so callers (pipeline_engine._backend_for,
    create_mediums.default_mediums) know not to expect/require a chat-LLM
    backend for it. Base ArtGenerator defaults uses_llm=True."""
    assert ad.AnimateDiffGenerator().uses_llm is False


def _parse(argv):
    """Build a parser the same way _build_artgen_parser does for animatediff:
    generator-specific flags plus the --output common flag _cmd_animatediff
    reads via getattr."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=None)
    ad.AnimateDiffGenerator().add_args(parser)
    return parser.parse_args(argv)


@pytest.fixture
def mock_run_subprocess(monkeypatch):
    mock = MagicMock(return_value=(True, ""))
    monkeypatch.setattr(ad, "run_subprocess", mock)
    # check_hardware() is a 3-tuple: (ok, message, num_chips). A single
    # healthy chip is the default here so tests that don't care about
    # multichip forwarding aren't affected.
    monkeypatch.setattr(ad, "check_hardware", lambda: (True, "blackhole ok", 1))
    monkeypatch.setattr(ad, "make_gif_thumbnail", lambda *a, **k: None)
    return mock


FULL_ARGV = [
    "--prompt", "koi pond",
    "--negative-prompt", "ugly",
    "--mode", "cpu",
    "--frames", "16",
    "--steps", "10",
    "--seed", "7",
    "--temporal-alpha", "0.5",
    "--lightning",
    "--lightning-steps", "8",
    "--device-id", "2",
    "--chain-from", "prev.pt",
    "--chain-save", "next.pt",
    "--chain-alpha", "0.4",
    "--motion-adapter", "some/path",
    "--motion-adapter-alpha", "0.8",
    "--motion-adapter-skip", "up1", "up2",
    "--count", "1",
    "--multichip-mode", "remix",
    "--per-chip-prompt", "dawn",
    "--per-chip-prompt", "storm",
    "--seed-spread", "3",
    "--ramp", "temporal",
    "--ramp-lo", "0.1",
    "--ramp-hi", "0.9",
    "--stitch-order", "concatenate",
    "--prompt-schedule", "0:spring meadow",
    "--prompt-schedule", "16:snowfall",
    "--loop", "seamless",
]


class TestFullFlagForwarding:
    def test_forwards_every_flag_to_run_subprocess(self, mock_run_subprocess, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # avoid writing animatediff_00.gif into the repo
        args = _parse(FULL_ARGV)
        artgen_cli._cmd_animatediff(args)

        assert mock_run_subprocess.call_count == 1
        kwargs = mock_run_subprocess.call_args.kwargs

        # Previously-forwarded params, unchanged
        assert kwargs["prompt"] == "koi pond"
        assert kwargs["negative_prompt"] == "ugly"
        assert kwargs["frames"] == 16
        assert kwargs["steps"] == 10
        assert kwargs["seed"] == 7
        assert kwargs["temporal_alpha"] == 0.5

        # Hardware mode — previously dropped entirely
        assert kwargs["mode"] == "cpu"

        # Previously-dropped advanced params
        assert kwargs["lightning"] is True
        assert kwargs["lightning_steps"] == 8
        assert kwargs["device_id"] == 2
        assert kwargs["chain_from"] == "prev.pt"
        assert kwargs["chain_save"] == "next.pt"
        assert kwargs["chain_alpha"] == 0.4
        assert kwargs["motion_adapter"] == "some/path"
        assert kwargs["motion_adapter_alpha"] == 0.8
        assert kwargs["motion_adapter_skip"] == ["up1", "up2"]

        # Multi-chip params — flags added by this task
        assert kwargs["multichip_mode"] == "remix"
        assert kwargs["per_chip_prompts"] == ["dawn", "storm"]
        assert kwargs["seed_spread"] == 3
        assert kwargs["ramp"] == "temporal"
        assert kwargs["ramp_lo"] == 0.1
        assert kwargs["ramp_hi"] == 0.9
        assert kwargs["stitch_order"] == "concatenate"

        # Brand-new flags
        assert kwargs["prompt_schedule"] == [(0, "spring meadow"), (16, "snowfall")]
        assert kwargs["loop"] == "seamless"

    def test_defaults_when_advanced_flags_omitted(self, mock_run_subprocess, tmp_path, monkeypatch):
        """A bare-minimum invocation must still forward the (default) values for
        every param, proving the getattr()-with-default fallbacks match add_args."""
        monkeypatch.chdir(tmp_path)
        args = _parse(["--prompt", "koi pond"])
        artgen_cli._cmd_animatediff(args)

        kwargs = mock_run_subprocess.call_args.kwargs
        assert kwargs["mode"] == "blackhole"
        assert kwargs["multichip_mode"] == "off"
        assert kwargs["per_chip_prompts"] is None
        assert kwargs["seed_spread"] == 1
        assert kwargs["ramp"] == "none"
        assert kwargs["ramp_lo"] == 0.0
        assert kwargs["ramp_hi"] == 1.0
        assert kwargs["stitch_order"] == "interleave"
        assert kwargs["lightning"] is False
        assert kwargs["lightning_steps"] == 4
        assert kwargs["device_id"] is None
        assert kwargs["chain_from"] is None
        assert kwargs["chain_save"] is None
        assert kwargs["chain_alpha"] == 0.6
        assert kwargs["motion_adapter"] is None
        assert kwargs["motion_adapter_alpha"] == 1.0
        assert kwargs["motion_adapter_skip"] is None
        assert kwargs["prompt_schedule"] is None
        assert kwargs["loop"] == "none"


class TestPromptScheduleParsing:
    def test_malformed_entry_missing_colon_errors_cleanly(self, mock_run_subprocess, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        args = _parse(["--prompt", "koi pond", "--prompt-schedule", "no-colon-here"])
        with pytest.raises(SystemExit) as exc_info:
            artgen_cli._cmd_animatediff(args)
        assert exc_info.value.code == 1
        assert mock_run_subprocess.call_count == 0
        err = capsys.readouterr().err
        assert "prompt-schedule" in err.lower()

    def test_malformed_entry_non_integer_frame_errors_cleanly(self, mock_run_subprocess, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        args = _parse(["--prompt", "koi pond", "--prompt-schedule", "abc:some prompt"])
        with pytest.raises(SystemExit) as exc_info:
            artgen_cli._cmd_animatediff(args)
        assert exc_info.value.code == 1
        assert mock_run_subprocess.call_count == 0
        err = capsys.readouterr().err
        assert "prompt-schedule" in err.lower()

    def test_valid_schedule_parses_to_tuples(self):
        parsed = artgen_cli._parse_prompt_schedule(["0:spring meadow", "16:snowfall"])
        assert parsed == [(0, "spring meadow"), (16, "snowfall")]

    def test_none_input_returns_none(self):
        assert artgen_cli._parse_prompt_schedule(None) is None

    def test_prompt_with_colon_survives_split_on_first_colon_only(self):
        parsed = artgen_cli._parse_prompt_schedule(["5:a scene: cold and blue"])
        assert parsed == [(5, "a scene: cold and blue")]


class TestMultichipNumChipsForwarding:
    """check_hardware() returns a 3-tuple (ok, message, num_chips). _cmd_animatediff
    must unpack all three AND forward num_chips to run_subprocess (mirroring
    artgen_panel.py's GUI path) so --multichip-mode remix|coherent can actually
    engage from the CLI. Prior to the fix, cli.py did `ok, hw_msg = check_hardware()`
    which raises ValueError against a real 3-tuple return, and num_chips was never
    forwarded at all.
    """

    def test_forwards_num_chips_from_check_hardware(self, mock_run_subprocess, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(ad, "check_hardware", lambda: (True, "ok", 4))
        args = _parse(["--prompt", "koi pond", "--multichip-mode", "remix"])
        artgen_cli._cmd_animatediff(args)

        kwargs = mock_run_subprocess.call_args.kwargs
        assert kwargs["num_chips"] == 4

    def test_explicit_device_id_forces_single_chip(self, mock_run_subprocess, tmp_path, monkeypatch):
        """An explicit --device-id pin means "run on this one chip only" —
        num_chips must be forwarded as 1 even though the hardware has 4 chips."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(ad, "check_hardware", lambda: (True, "ok", 4))
        args = _parse([
            "--prompt", "koi pond",
            "--multichip-mode", "remix",
            "--device-id", "0",
        ])
        artgen_cli._cmd_animatediff(args)

        kwargs = mock_run_subprocess.call_args.kwargs
        assert kwargs["num_chips"] == 1
