"""Tests for PipelinePanel state logic."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def test_parse_template_variables():
    """Template parser extracts {variable} tokens."""
    from pipeline_panel import parse_template_variables
    assert parse_template_variables("{era} World's Fair, {subject}, {style}") == ["era", "subject", "style"]
    assert parse_template_variables("no variables here") == []
    assert parse_template_variables("{a} {a} {b}") == ["a", "b"]  # deduped, order preserved


def test_resolve_prompt_from_row():
    """Variable row + template → resolved prompt string."""
    from pipeline_panel import resolve_prompt
    template = "{era} World's Fair, {subject}"
    row = {"name": "1964", "era": "1964", "subject": "IBM Wall"}
    assert resolve_prompt(template, row) == "1964 World's Fair, IBM Wall"


def test_resolve_prompt_missing_variable():
    """Missing variable left as-is in resolved prompt."""
    from pipeline_panel import resolve_prompt
    assert resolve_prompt("{era} test {missing}", {"era": "1964"}) == "1964 test {missing}"


def test_resolve_prompt_custom_override():
    """Row with __custom__ flag uses prompt field directly."""
    from pipeline_panel import resolve_prompt
    row = {"name": "test", "__custom__": True, "prompt": "a custom prompt here"}
    assert resolve_prompt("{era} template", row) == "a custom prompt here"


def test_jobs_to_runner_format():
    """Job rows → list[dict] suitable for PipelineRunner.start()."""
    from pipeline_panel import jobs_to_runner_format
    template = "{era} World's Fair"
    rows = [
        {"name": "1964 NY", "era": "1964"},
        {"name": "custom", "__custom__": True, "prompt": "a direct prompt"},
    ]
    result = jobs_to_runner_format(template, rows)
    assert result[0] == {"name": "1964 NY", "prompt": "1964 World's Fair"}
    assert result[1] == {"name": "custom", "prompt": "a direct prompt"}


def test_jobs_to_runner_format_skips_disabled():
    """Disabled rows (enabled=False) are excluded."""
    from pipeline_panel import jobs_to_runner_format
    rows = [
        {"name": "a", "era": "1964", "enabled": True},
        {"name": "b", "era": "1939", "enabled": False},
    ]
    result = jobs_to_runner_format("{era}", rows)
    assert len(result) == 1
    assert result[0]["name"] == "a"


def test_phases_from_spec():
    """Workflow JSON spec → phase list for PhaseGridWidget."""
    from pipeline_panel import phases_from_spec
    import json, tempfile
    spec = {
        "_description": "test",
        "1": {"class_type": "TTLGTextToImage", "_comment": "seed", "inputs": {}, "outputs": ["image_path"]},
        "4": {"class_type": "TTLGImageToVideo", "_comment": "video", "inputs": {}, "outputs": ["video_path"]},
        "9": {"class_type": "TTLGAddToPlaylist", "_comment": "save", "inputs": {}, "outputs": []},
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(spec, f)
        path = f.name
    phases = phases_from_spec(path)
    assert len(phases) == 3
    assert phases[0]["id"] == "1"
    assert phases[0]["label"] == "Seed"  # from _comment, capitalised
    assert phases[1]["id"] == "4"
