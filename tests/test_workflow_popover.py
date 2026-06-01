"""Tests for workflow_popover.py — spec discovery, run index, param parsing."""
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


# ── _discover_specs ───────────────────────────────────────────────────────────

def test_discover_specs_finds_builtin(tmp_path):
    import workflow_popover as wp
    orig_builtin = wp._BUILTIN_WORKFLOW_DIR
    orig_user = wp._USER_WORKFLOW_DIR
    wp._BUILTIN_WORKFLOW_DIR = tmp_path / "builtin"
    wp._USER_WORKFLOW_DIR = tmp_path / "user"
    wp._BUILTIN_WORKFLOW_DIR.mkdir()
    wp._USER_WORKFLOW_DIR.mkdir()

    spec = {
        "_description": "Test pipeline → output",
        "_spec_version": "1",
        "1": {"class_type": "TTLGTextToImage", "inputs": {"prompt": "hello", "seed": 42}},
    }
    (wp._BUILTIN_WORKFLOW_DIR / "test.json").write_text(json.dumps(spec))

    try:
        specs = wp._discover_specs()
        assert len(specs) == 1
        assert "Test pipeline" in specs[0]["name"]
        assert specs[0]["nodes"] == 1
    finally:
        wp._BUILTIN_WORKFLOW_DIR = orig_builtin
        wp._USER_WORKFLOW_DIR = orig_user


def test_discover_specs_skips_malformed(tmp_path):
    import workflow_popover as wp
    orig = wp._BUILTIN_WORKFLOW_DIR
    wp._BUILTIN_WORKFLOW_DIR = tmp_path
    (tmp_path / "bad.json").write_text("{not valid json")
    try:
        specs = wp._discover_specs()
        assert not any(s["path"].endswith("bad.json") for s in specs)
    finally:
        wp._BUILTIN_WORKFLOW_DIR = orig


# ── _parse_overridable_inputs ─────────────────────────────────────────────────

def test_parse_overridable_inputs_returns_leaf_scalars(tmp_path):
    import workflow_popover as wp
    spec = {
        "_description": "test",
        "1": {"class_type": "TTLGTextToImage", "inputs": {
            "prompt": "hello world",        # overridable
            "seed": 1964,                   # overridable
            "image": ["2", "image_path"],   # inter-node wire — skip
        }},
        "2": {"class_type": "TTLGCaptionImage", "inputs": {
            "src": ["1", "image_path"],     # wire
            "nonstandard_key": "value",     # not in allowlist — skip
        }},
    }
    spec_path = str(tmp_path / "spec.json")
    Path(spec_path).write_text(json.dumps(spec))

    inputs = wp._parse_overridable_inputs(spec_path)
    keys = [(i["node_id"], i["key"]) for i in inputs]
    assert ("1", "prompt") in keys
    assert ("1", "seed") in keys
    assert ("1", "image") not in keys   # wire
    assert ("2", "src") not in keys     # wire
    assert ("2", "nonstandard_key") not in keys  # not in allowlist


def test_parse_overridable_inputs_detects_types(tmp_path):
    import workflow_popover as wp
    spec = {
        "1": {"class_type": "T", "inputs": {
            "prompt": "text",
            "seed": 42,
            "guidance_scale": 3.5,
        }}
    }
    path = str(tmp_path / "s.json")
    Path(path).write_text(json.dumps(spec))
    inputs = {i["key"]: i for i in wp._parse_overridable_inputs(path)}
    assert inputs["prompt"]["type"] == "str"
    assert inputs["seed"]["type"] == "int"
    assert inputs["guidance_scale"]["type"] == "float"


# ── _apply_overrides ──────────────────────────────────────────────────────────

def test_apply_overrides_patches_without_mutating_original(tmp_path):
    import workflow_popover as wp
    spec = {"1": {"class_type": "T", "inputs": {"prompt": "original", "seed": 1}}}
    spec_path = str(tmp_path / "spec.json")
    Path(spec_path).write_text(json.dumps(spec))

    overrides = {("1", "prompt"): "new prompt", ("1", "seed"): 2024}
    temp_path = wp._apply_overrides(spec_path, overrides)

    # Original unchanged
    assert json.loads(Path(spec_path).read_text())["1"]["inputs"]["prompt"] == "original"
    # Temp has overrides
    patched = json.loads(Path(temp_path).read_text())
    assert patched["1"]["inputs"]["prompt"] == "new prompt"
    assert patched["1"]["inputs"]["seed"] == 2024

    Path(temp_path).unlink(missing_ok=True)


def test_apply_overrides_empty_returns_original_path(tmp_path):
    import workflow_popover as wp
    spec = {"1": {"class_type": "T", "inputs": {"prompt": "x"}}}
    spec_path = str(tmp_path / "spec.json")
    Path(spec_path).write_text(json.dumps(spec))
    # With no overrides, still writes a temp (current behaviour — consistent)
    temp_path = wp._apply_overrides(spec_path, {})
    assert Path(temp_path).exists()
    Path(temp_path).unlink(missing_ok=True)


# ── WorkflowRunIndex ──────────────────────────────────────────────────────────

def test_run_index_add_and_load(tmp_path):
    import workflow_popover as wp
    orig = wp._RUN_INDEX
    wp._RUN_INDEX = tmp_path / "index.json"
    idx = wp.WorkflowRunIndex()

    try:
        record = {"id": str(uuid.uuid4()), "spec_path": "/a/b.json",
                  "status": "done", "started_at": "2026-01-01T00:00:00+00:00"}
        idx.add(record)
        loaded = idx.load()
        assert len(loaded) == 1
        assert loaded[0]["id"] == record["id"]
    finally:
        wp._RUN_INDEX = orig


def test_run_index_update(tmp_path):
    import workflow_popover as wp
    orig = wp._RUN_INDEX
    wp._RUN_INDEX = tmp_path / "index.json"
    idx = wp.WorkflowRunIndex()

    try:
        rid = str(uuid.uuid4())
        idx.add({"id": rid, "spec_path": "/x.json", "status": "running"})
        idx.update(rid, status="done", playlist_id="abc-123")
        rec = next(r for r in idx.load() if r["id"] == rid)
        assert rec["status"] == "done"
        assert rec["playlist_id"] == "abc-123"
    finally:
        wp._RUN_INDEX = orig


def test_run_index_for_spec_filters_and_limits(tmp_path):
    import workflow_popover as wp
    orig = wp._RUN_INDEX
    wp._RUN_INDEX = tmp_path / "index.json"
    idx = wp.WorkflowRunIndex()

    try:
        for i in range(8):
            idx.add({"id": str(uuid.uuid4()), "spec_path": "/spec_a.json", "status": "done"})
        idx.add({"id": str(uuid.uuid4()), "spec_path": "/spec_b.json", "status": "done"})

        a_runs = idx.for_spec("/spec_a.json", limit=5)
        assert len(a_runs) == 5  # limited
        assert all(r["spec_path"] == "/spec_a.json" for r in a_runs)

        b_runs = idx.for_spec("/spec_b.json")
        assert len(b_runs) == 1
    finally:
        wp._RUN_INDEX = orig


# ── WorkflowPopover signature check ──────────────────────────────────────────

def test_workflow_popover_accepts_callback():
    import inspect
    import workflow_popover as wp
    sig = inspect.signature(wp.WorkflowPopover.__init__)
    assert "on_watch_playlist" in sig.parameters


def test_workflow_popover_is_gtk_popover():
    import workflow_popover as wp
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    assert issubclass(wp.WorkflowPopover, Gtk.Popover)
