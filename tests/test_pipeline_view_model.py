"""Tests for pipeline_view_model — record -> intent-steps + artifacts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import pipeline_view_model as pvm  # noqa: E402
import intent_vocab as iv  # noqa: E402

_FIX = Path(__file__).parent / "fixtures" / "sp_c_run"
_SPEC = str(_FIX / "spec.json")
_OUT = str(_FIX)

# Real historical runs from bin/run_worlds_fair.sh fan out one pipeline spec
# over several per-job subdirectories (e.g. <output_dir>/1964-ny/node1_image.png)
# rather than writing artifacts flat into output_dir. This fixture mirrors that
# layout with a single job subdir ("jobA") so build_run_view's artifact
# resolution is exercised against both flat (sp_c_run) and nested (this)
# layouts.
_FIX_NESTED = Path(__file__).parent / "fixtures" / "sp_c_run_nested"
_SPEC_NESTED = str(_FIX_NESTED / "spec.json")
_OUT_NESTED = str(_FIX_NESTED)


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


def test_artifact_resolved_from_per_job_subdirectory():
    """A run_worlds_fair.sh-style record whose output_dir holds artifacts one
    level down (in a per-job subdir) must still resolve node artifacts —
    not just runs that write flat into output_dir. Regression for the
    Pipeline Studio Discover/Open bug where every real multi-job run
    rendered as all-placeholder because the resolver only globbed the top
    level of output_dir."""
    record = {
        "id": "22222222-2222-2222-2222-222222222222",
        "spec_path": _SPEC_NESTED,
        "spec_name": "World's Fair recipe",
        "output_dir": _OUT_NESTED,
        "status": "done",
        "started_at": "2026-06-02T09:25:16+00:00",
        "finished_at": "2026-06-02T09:30:00+00:00",
        "job_states": {},
    }
    view = pvm.build_run_view(record)
    by_id = {s.node_id: s for s in view.steps}

    assert by_id["1"].artifact_path == str(_FIX_NESTED / "jobA" / "node1_image.png")
    assert Path(by_id["1"].artifact_path).exists()
    assert by_id["4"].artifact_path == str(_FIX_NESTED / "jobA" / "node4_video.mp4")
    assert Path(by_id["4"].artifact_path).exists()
    # Whole-branch review Finding 3: the run's hero/Library deliverable is the
    # LAST heroable (topologically-final) artifact, not the first — node "4"
    # (TTLGImageToVideo, the finished video) wins over node "1" (the seed
    # image that merely fed it). Pre-fix this asserted node "1" (first-wins),
    # which meant an image->video pipeline's Library registration was always
    # the intermediate seed image instead of the actual video deliverable.
    assert view.hero_path == str(_FIX_NESTED / "jobA" / "node4_video.mp4")
    assert pvm.final_index_for(view) == [s.node_id for s in view.steps].index("4")


# ── StepView.text_content (Task 6, fix #6) ──────────────────────────────────
#
# TTLGCaptionImage (node "2") and TTLGGenerateText (node "4") in the shared
# fixture spec never have a file artifact (caption/text are text-only output
# keys — see _OUTPUT_KIND), so their text, if any, must come from
# output_dir/results.json instead.

def test_text_content_populated_from_results_json(tmp_path):
    out_dir = tmp_path / "run_out"
    out_dir.mkdir()
    (out_dir / "spec.json").write_text(Path(_SPEC).read_text())
    (out_dir / "results.json").write_text(
        '{"2": {"caption": "a weathered stone lighthouse"}, '
        ' "4": {"text": "The lighthouse stands alone against the dusk."}}'
    )

    record = _make_record(
        spec_path=str(out_dir / "spec.json"),
        output_dir=str(out_dir),
        job_states={},
    )
    view = pvm.build_run_view(record)
    by_id = {s.node_id: s for s in view.steps}

    assert by_id["2"].text_content == "a weathered stone lighthouse"
    assert by_id["4"].text_content == "The lighthouse stands alone against the dusk."


def test_text_content_none_when_results_json_absent():
    """The shared sp_c_run fixture has no results.json at all — every
    text-producing step must degrade to None, never crash."""
    view = pvm.build_run_view(_make_record())
    by_id = {s.node_id: s for s in view.steps}
    assert by_id["2"].text_content is None
    assert by_id["4"].text_content is None


def test_text_content_none_when_results_json_malformed(tmp_path):
    out_dir = tmp_path / "run_out"
    out_dir.mkdir()
    (out_dir / "spec.json").write_text(Path(_SPEC).read_text())
    (out_dir / "results.json").write_text("{not valid json")

    record = _make_record(
        spec_path=str(out_dir / "spec.json"),
        output_dir=str(out_dir),
        job_states={},
    )
    view = pvm.build_run_view(record)  # must not raise
    by_id = {s.node_id: s for s in view.steps}
    assert by_id["4"].text_content is None


def test_text_content_none_when_node_or_key_missing_from_results(tmp_path):
    out_dir = tmp_path / "run_out"
    out_dir.mkdir()
    (out_dir / "spec.json").write_text(Path(_SPEC).read_text())
    # results.json exists but has neither node "4" nor a "text" key for it.
    (out_dir / "results.json").write_text('{"2": {"caption": "ok"}}')

    record = _make_record(
        spec_path=str(out_dir / "spec.json"),
        output_dir=str(out_dir),
        job_states={},
    )
    view = pvm.build_run_view(record)
    by_id = {s.node_id: s for s in view.steps}
    assert by_id["4"].text_content is None


def test_text_content_never_returns_a_file_path(tmp_path):
    """An image step records its output as a *_path string; that path must
    render as an image, never leak into the review's text block (the original
    '/…/node3_fg.png shown as text' bug)."""
    out_dir = tmp_path / "run_out"
    out_dir.mkdir()
    (out_dir / "spec.json").write_text(Path(_SPEC).read_text())
    # node 1 = TTLGTextToImage; its output key is a file path, not text.
    (out_dir / "results.json").write_text(
        '{"1": {"image_path": "/home/ttuser/x/node1_image.png"}}'
    )
    record = _make_record(spec_path=str(out_dir / "spec.json"),
                          output_dir=str(out_dir), job_states={})
    view = pvm.build_run_view(record)
    step1 = next(s for s in view.steps if s.node_id == "1")
    assert step1.text_content is None


def test_text_content_surfaces_genuine_text_under_drifted_key(tmp_path):
    """Historical runs record text under non-canonical keys (poem vs text);
    it must still surface so review mode SHOWS the content, not an icon."""
    out_dir = tmp_path / "run_out"
    out_dir.mkdir()
    (out_dir / "spec.json").write_text(Path(_SPEC).read_text())
    # node 4 = TTLGGenerateText (canonical key 'text'); results has 'poem'.
    (out_dir / "results.json").write_text(
        '{"4": {"poem": "In twilight hush, where shadows play.", "_label": "poem"}}'
    )
    record = _make_record(spec_path=str(out_dir / "spec.json"),
                          output_dir=str(out_dir), job_states={})
    view = pvm.build_run_view(record)
    step4 = next(s for s in view.steps if s.node_id == "4")
    assert step4.text_content == "In twilight hush, where shadows play."


def test_text_content_skips_metadata_and_media_paths(tmp_path):
    """A '_'-prefixed metadata key and a drifted key whose value is a media
    path are both ignored — neither is genuine display text."""
    out_dir = tmp_path / "run_out"
    out_dir.mkdir()
    (out_dir / "spec.json").write_text(Path(_SPEC).read_text())
    (out_dir / "results.json").write_text(
        '{"4": {"_label": "x", "output_img": "/tmp/node4_out.png"}}'
    )
    record = _make_record(spec_path=str(out_dir / "spec.json"),
                          output_dir=str(out_dir), job_states={})
    view = pvm.build_run_view(record)
    step4 = next(s for s in view.steps if s.node_id == "4")
    assert step4.text_content is None


def test_text_content_stays_none_when_artifact_present():
    """Node 1 (TTLGTextToImage) has a real on-disk artifact — text_content
    must stay None for it even if build_run_view is asked to resolve it,
    since the artifact is always the more informative thing to show."""
    view = pvm.build_run_view(_make_record())
    step1 = next(s for s in view.steps if s.node_id == "1")
    assert step1.artifact_path is not None
    assert step1.text_content is None


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


# ── StepView.artifact_paths (fan-out: all N stills, not just one) ─────────────

def _touch_png(p):
    from pathlib import Path as _P
    _P(p).write_bytes(b"\x89PNG\r\n\x1a\n")  # enough to exist with a .png ext
    return str(p)

def test_artifact_paths_lists_all_fanout_images(tmp_path):
    out_dir = tmp_path / "run_out"; out_dir.mkdir()
    (out_dir / "spec.json").write_text(Path(_SPEC).read_text())
    a = _touch_png(out_dir / "node1_image_0.png")
    b = _touch_png(out_dir / "node1_image_1.png")
    (out_dir / "results.json").write_text(json.dumps({"1": {"image_path": [a, b]}}))
    rec = _make_record(spec_path=str(out_dir / "spec.json"), output_dir=str(out_dir), job_states={})
    step1 = next(s for s in pvm.build_run_view(rec).steps if s.node_id == "1")
    assert list(step1.artifact_paths) == [a, b]

def test_artifact_paths_filters_missing_files(tmp_path):
    out_dir = tmp_path / "run_out"; out_dir.mkdir()
    (out_dir / "spec.json").write_text(Path(_SPEC).read_text())
    a = _touch_png(out_dir / "node1_image_0.png")
    (out_dir / "results.json").write_text(json.dumps({"1": {"image_path": [a, str(out_dir / "gone.png")]}}))
    rec = _make_record(spec_path=str(out_dir / "spec.json"), output_dir=str(out_dir), job_states={})
    step1 = next(s for s in pvm.build_run_view(rec).steps if s.node_id == "1")
    assert list(step1.artifact_paths) == [a]

def test_artifact_paths_falls_back_to_single_artifact():
    # default fixture run: node 1 has one real on-disk artifact, no results.json list
    step1 = next(s for s in pvm.build_run_view(_make_record()).steps if s.node_id == "1")
    assert step1.artifact_path is not None
    assert list(step1.artifact_paths) == [step1.artifact_path]

def test_artifact_paths_empty_for_text_step():
    step2 = next(s for s in pvm.build_run_view(_make_record()).steps if s.node_id == "2")
    assert step2.artifact_path is None
    assert list(step2.artifact_paths) == []
