"""Tests for PhaseGridWidget state logic — no GTK display required."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def make_grid_state():
    """Return a fresh grid state dict — the pure-data model for PhaseGridWidget."""
    from phase_grid_widget import GridState
    return GridState(
        jobs=["1964-ny", "1939-ny"],
        phases=[
            {"id": "1", "label": "Seed",  "model": "FLUX"},
            {"id": "4", "label": "Video", "model": "SkyReels"},
            {"id": "5", "label": "Poem",  "model": "Llama"},
        ]
    )


def test_initial_cells_are_pending():
    gs = make_grid_state()
    assert gs.cell("1964-ny", "1")["status"] == "pending"
    assert gs.cell("1939-ny", "4")["status"] == "pending"


def test_update_cell_to_running():
    gs = make_grid_state()
    gs.update("1964-ny", "1", "running", "FLUX.1-schnell")
    assert gs.cell("1964-ny", "1")["status"] == "running"
    assert gs.cell("1964-ny", "1")["detail"] == "FLUX.1-schnell"


def test_update_cell_to_done():
    gs = make_grid_state()
    gs.update("1964-ny", "1", "done", "/tmp/node1.png")
    c = gs.cell("1964-ny", "1")
    assert c["status"] == "done"
    assert c["detail"] == "/tmp/node1.png"


def test_update_cell_to_failed():
    gs = make_grid_state()
    gs.update("1964-ny", "4", "failed", "SkyReels OOM")
    assert gs.cell("1964-ny", "4")["status"] == "failed"


def test_update_cell_to_skipped():
    gs = make_grid_state()
    gs.update("1970-osaka", "2", "skipped", "fog/exterior")
    # Job not in initial list — update creates it
    assert gs.cell("1970-osaka", "2")["status"] == "skipped"


def test_cells_for_job():
    gs = make_grid_state()
    gs.update("1964-ny", "1", "done", "/tmp/a.png")
    gs.update("1964-ny", "4", "running", "SkyReels")
    cells = gs.cells_for_job("1964-ny")
    assert cells["1"]["status"] == "done"
    assert cells["4"]["status"] == "running"
    assert cells["5"]["status"] == "pending"


def test_health_cell_is_special():
    gs = make_grid_state()
    gs.update("__health__", "__chips__", "degraded", "AC power cycle recommended")
    # Health signals are stored separately, not as a regular cell
    assert gs.health_status() == "degraded"
    assert gs.health_detail() == "AC power cycle recommended"


def test_load_from_store_record():
    """GridState can be populated from a PipelineStore run record."""
    from phase_grid_widget import GridState
    run_record = {
        "jobs": [{"name": "1964-ny"}, {"name": "1939-ny"}],
        "job_states": {
            "1964-ny": {
                "1": {"status": "done",    "detail": "/tmp/a.png", "elapsed_s": 3.1},
                "4": {"status": "running", "detail": "SkyReels",   "elapsed_s": 0.0},
            },
            "1939-ny": {
                "1": {"status": "done",    "detail": "/tmp/b.png", "elapsed_s": 3.0},
            }
        }
    }
    phases = [{"id": "1", "label": "Seed", "model": "FLUX"},
              {"id": "4", "label": "Video", "model": "SkyReels"}]
    gs = GridState.from_run_record(run_record, phases)
    assert gs.cell("1964-ny", "1")["status"] == "done"
    assert gs.cell("1964-ny", "4")["status"] == "running"
    assert gs.cell("1939-ny", "4")["status"] == "pending"  # not in job_states
