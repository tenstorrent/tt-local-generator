"""Tests for pipeline_view_model — record -> intent-steps + artifacts."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import pipeline_view_model as pvm  # noqa: E402
import intent_vocab as iv  # noqa: E402

_FIX = Path(__file__).parent / "fixtures" / "sp_c_run"
_SPEC = str(_FIX / "spec.json")
_OUT = str(_FIX)


def _make_record(**overrides) -> dict:
    record = {
        "id": "11111111-1111-1111-1111-111111111111",
        "spec_path": _SPEC,
        "spec_name": "Lighthouse recipe",
        "output_dir": _OUT,
        "status": "running",
        "started_at": "2026-07-10T12:00:00+00:00",
        "finished_at": None,
        # node "2" (CaptionImage) is marked done via job_states even though it
        # has no on-disk artifact (caption is a text output, not a file) —
        # exercises the job_states resolution path. node "1" has NO job_states
        # entry at all, so its status must come from artifact existence
        # instead (node1_image.png is a real fixture file). nodes "3"/"4" have
        # neither a job_states entry nor an artifact -> pending.
        "job_states": {"job1": {"2": {"status": "done", "detail": "", "elapsed_s": 1.2}}},
    }
    record.update(overrides)
    return record


# ── build_run_view ────────────────────────────────────────────────────────────

def test_steps_are_in_topo_order():
    view = pvm.build_run_view(_make_record())
    assert [s.node_id for s in view.steps] == ["1", "2", "3", "4"]


def test_step_intents_match_class_types():
    view = pvm.build_run_view(_make_record())
    by_id = {s.node_id: s for s in view.steps}
    assert by_id["1"].intent.class_type == "TTLGTextToImage"
    assert by_id["2"].intent.class_type == "TTLGCaptionImage"
    assert by_id["3"].intent.class_type == "TTLGEstimateDepth"
    assert by_id["4"].intent.class_type == "TTLGGenerateText"


def test_status_done_via_artifact_existence():
    """Node 1 has no job_states entry — status must come from the artifact
    (node1_image.png exists on disk) being present."""
    view = pvm.build_run_view(_make_record())
    step1 = next(s for s in view.steps if s.node_id == "1")
    assert step1.status == "done"
    assert step1.artifact_path == str(_FIX / "node1_image.png")


def test_status_done_via_job_states():
    """Node 2 has no file artifact (caption is text-only) but job_states
    marks it done — that must win over the (absent) artifact."""
    view = pvm.build_run_view(_make_record())
    step2 = next(s for s in view.steps if s.node_id == "2")
    assert step2.status == "done"
    assert step2.artifact_path is None


def test_status_pending_when_neither_job_state_nor_artifact():
    view = pvm.build_run_view(_make_record())
    by_id = {s.node_id: s for s in view.steps}
    assert by_id["3"].status == "pending"
    assert by_id["3"].artifact_path is None
    assert by_id["4"].status == "pending"
    assert by_id["4"].artifact_path is None


def test_job_states_running_and_failed_map_through():
    """job_states statuses other than "done" must reach StepView.status
    unchanged (running -> running, failed -> failed), not just "done"."""
    record = _make_record(job_states={
        "job1": {
            "2": {"status": "done", "detail": "", "elapsed_s": 1.2},
            "3": {"status": "running", "detail": "", "elapsed_s": 0.5},
            "4": {"status": "failed", "detail": "boom", "elapsed_s": 0.1},
        }
    })
    view = pvm.build_run_view(record)
    by_id = {s.node_id: s for s in view.steps}
    assert by_id["3"].status == "running"
    assert by_id["4"].status == "failed"


def test_job_states_unknown_status_degrades_to_pending():
    """A status the engine has never actually emitted (e.g. a hypothetical
    future "queued") must degrade to "pending" rather than leaking an
    unrecognized status string into the view."""
    record = _make_record(job_states={"job1": {"3": {"status": "queued"}}})
    view = pvm.build_run_view(record)
    step3 = next(s for s in view.steps if s.node_id == "3")
    assert step3.status == "pending"


def test_hero_is_first_image_or_video_artifact():
    view = pvm.build_run_view(_make_record())
    assert view.hero_path == str(_FIX / "node1_image.png")


def test_recipe_is_ordered_intent_labels():
    view = pvm.build_run_view(_make_record())
    assert view.recipe == [
        iv.label("TTLGTextToImage"),
        iv.label("TTLGCaptionImage"),
        iv.label("TTLGEstimateDepth"),
        iv.label("TTLGGenerateText"),
    ]


def test_run_view_identity_fields():
    view = pvm.build_run_view(_make_record())
    assert view.run_id == "11111111-1111-1111-1111-111111111111"
    assert view.title == "Lighthouse recipe"
    assert view.created_at == "2026-07-10T12:00:00+00:00"


def test_title_falls_back_to_spec_filename_stem():
    record = _make_record(spec_name="")
    view = pvm.build_run_view(record)
    assert view.title == "spec"


def test_empty_output_dir_guards_against_cwd_scan(monkeypatch):
    """An empty output_dir (e.g. an old/partial record) must not glob
    Path("") — which resolves to "." and scans the current working
    directory. Assert glob is never even called (not just that it happens
    to find nothing) and that every step resolves to no artifact."""
    from pathlib import Path as _Path

    def _boom(self, pattern):
        raise AssertionError(f"must not glob when output_dir is empty (pattern={pattern!r})")

    monkeypatch.setattr(_Path, "glob", _boom)

    view = pvm.build_run_view(_make_record(output_dir=""))
    assert all(s.artifact_path is None for s in view.steps)
    assert view.hero_path is None


def test_most_recently_modified_artifact_wins_on_ambiguity(tmp_path):
    """When two files match node{id}_*, the most recently modified is picked."""
    import shutil
    import time

    out_dir = tmp_path / "run_out"
    out_dir.mkdir()
    spec = {"1": {"class_type": "TTLGTextToImage", "inputs": {}}}
    (out_dir / "spec.json").write_text(
        '{"1": {"class_type": "TTLGTextToImage", "inputs": {}}}'
    )
    older = out_dir / "node1_image.png"
    newer = out_dir / "node1_image_fixed.png"
    shutil.copy(_FIX / "node1_image.png", older)
    time.sleep(0.02)
    shutil.copy(_FIX / "node1_image.png", newer)

    record = _make_record(
        spec_path=str(out_dir / "spec.json"),
        output_dir=str(out_dir),
        job_states={},
    )
    view = pvm.build_run_view(record)
    step1 = next(s for s in view.steps if s.node_id == "1")
    assert step1.artifact_path == str(newer)


# ── list_run_views ────────────────────────────────────────────────────────────

class _FakeStore:
    def __init__(self, records):
        self._records = records

    def list_runs(self, limit=50):
        return self._records[:limit]


def test_list_run_views_maps_records():
    views = pvm.list_run_views(_FakeStore([_make_record()]))
    assert len(views) == 1
    assert views[0].run_id == "11111111-1111-1111-1111-111111111111"


def test_list_run_views_skips_unloadable_record():
    bad = _make_record(id="bad", spec_path="/no/such/spec.json")
    good = _make_record()
    views = pvm.list_run_views(_FakeStore([bad, good]))
    assert len(views) == 1
    assert views[0].run_id == good["id"]
