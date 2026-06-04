"""Tests for PipelinePortfolioView data extraction — no GTK required."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def make_phases():
    return [
        {"id": "1", "label": "Generate",       "model": "FLUX"},
        {"id": "2", "label": "Blip",            "model": "BLIP"},
        {"id": "3", "label": "Rmbg",            "model": "RMBG"},
        {"id": "4", "label": "Depth",           "model": "GLPN"},
        {"id": "5", "label": "Compose",         "model": "compose"},
        {"id": "6", "label": "Skyreels",        "model": "SkyReels"},
        {"id": "7", "label": "Artgen",          "model": "Llama"},
        {"id": "8", "label": "Flux.1-schnell",  "model": "FLUX"},
        {"id": "9", "label": "Collect",         "model": "save"},
    ]


def make_job_states(tmp_path):
    # Create real temp files so path existence checks pass
    seed = tmp_path / "node1_seed.png"; seed.write_bytes(b"PNG")
    fg   = tmp_path / "node3_fg.png";   fg.write_bytes(b"PNG")
    dep  = tmp_path / "node4_depth.png"; dep.write_bytes(b"PNG")
    vid  = tmp_path / "node6_video.mp4"; vid.write_bytes(b"MP4")
    poem_img = tmp_path / "node8_poem.png"; poem_img.write_bytes(b"PNG")
    return {
        "1": {"status": "done", "detail": str(seed),      "elapsed_s": 3.1},
        "2": {"status": "done", "detail": "The IBM pavilion...", "elapsed_s": 5.0},
        "3": {"status": "done", "detail": str(fg),        "elapsed_s": 8.0},
        "4": {"status": "done", "detail": str(dep),       "elapsed_s": 33.0},
        "5": {"status": "done", "detail": "IBM Pavilion prompt", "elapsed_s": 0.1},
        "6": {"status": "done", "detail": str(vid),       "elapsed_s": 600.0},
        "7": {"status": "done", "detail": "In the dome,\nVisitors rise", "elapsed_s": 5.0},
        "8": {"status": "done", "detail": str(poem_img),  "elapsed_s": 3.0},
        "9": {"status": "done", "detail": "",              "elapsed_s": 0.5},
    }


def test_extract_seed_image(tmp_path):
    from pipeline_portfolio_view import extract_job_artifacts
    artifacts = extract_job_artifacts(make_job_states(tmp_path), make_phases())
    assert artifacts["seed_image"] is not None
    assert artifacts["seed_image"].endswith("node1_seed.png")


def test_extract_video(tmp_path):
    from pipeline_portfolio_view import extract_job_artifacts
    artifacts = extract_job_artifacts(make_job_states(tmp_path), make_phases())
    assert artifacts["video"] is not None
    assert artifacts["video"].endswith("node6_video.mp4")


def test_extract_poem_text(tmp_path):
    from pipeline_portfolio_view import extract_job_artifacts
    artifacts = extract_job_artifacts(make_job_states(tmp_path), make_phases())
    assert artifacts["poem"] == "In the dome,\nVisitors rise"


def test_extract_poem_image(tmp_path):
    from pipeline_portfolio_view import extract_job_artifacts
    artifacts = extract_job_artifacts(make_job_states(tmp_path), make_phases())
    assert artifacts["poem_image"] is not None
    assert artifacts["poem_image"].endswith("node8_poem.png")


def test_extract_missing_video_returns_none(tmp_path):
    from pipeline_portfolio_view import extract_job_artifacts
    states = make_job_states(tmp_path)
    del states["6"]
    artifacts = extract_job_artifacts(states, make_phases())
    assert artifacts["video"] is None


def test_extract_skipped_node_returns_none(tmp_path):
    from pipeline_portfolio_view import extract_job_artifacts
    states = make_job_states(tmp_path)
    states["6"] = {"status": "skipped", "detail": "fog/exterior"}
    artifacts = extract_job_artifacts(states, make_phases())
    assert artifacts["video"] is None


def test_extract_partial_run_no_crash(tmp_path):
    from pipeline_portfolio_view import extract_job_artifacts
    seed = tmp_path / "seed.png"; seed.write_bytes(b"PNG")
    states = {"1": {"status": "done", "detail": str(seed)}}
    artifacts = extract_job_artifacts(states, make_phases())
    assert artifacts["seed_image"] is not None
    assert artifacts["video"] is None
    assert artifacts["poem"] is None
    assert artifacts["poem_image"] is None


def test_run_has_portfolio_artifacts(tmp_path):
    from pipeline_portfolio_view import run_has_portfolio_artifacts
    states = make_job_states(tmp_path)
    assert run_has_portfolio_artifacts({"job1": states}, make_phases()) is True


def test_run_without_visual_artifacts_not_portfolio(tmp_path):
    from pipeline_portfolio_view import run_has_portfolio_artifacts
    states = {
        "5": {"status": "done", "detail": "a prompt"},
        "7": {"status": "done", "detail": "a poem"},
    }
    assert run_has_portfolio_artifacts({"job1": states}, make_phases()) is False
