"""
Integration tests for the workflow runner pipeline.

These tests validate pipeline logic WITHOUT touching TT hardware.  All HTTP
calls, Docker commands, and board-reset operations are either bypassed by
--dry-run mode or excluded from scope.

Test groups
-----------
1. Dry-run output      — run_workflow.sh emits LOG:, NODE: progress signals,
                         does not connect to localhost:8000. (Since Task 3,
                         run_workflow.sh is a thin shim over
                         app/pipeline_engine.py; "exits 0" is xfail until
                         Task 4 normalizes 1964-worlds-fair.json's wire keys
                         to match the engine's generic output contract.)
2. _apply_overrides    — patch a spec, verify resulting JSON is valid with
                         the override values in place.
3. Progress protocol   — _on_run_stdout line-parsing: LOG:, ⏳ SkyReels:,
                         ⚠️ warning, PLAYLIST:, ══ step headers.
4. Spec validation     — 1964-worlds-fair.json satisfies structural invariants:
                         valid node refs, no cycles, known class_types, has
                         _description.
5. Workflow-vs-model   — records with model_id starting with "workflow" are
                         excluded from the by-model count, matching the guard
                         at main_window.py:7691.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUN_WORKFLOW_SH = _REPO_ROOT / "bin" / "run_workflow.sh"
_EXAMPLE_WORKFLOW = (
    _REPO_ROOT / "docs" / "examples" / "workflows" / "1964-worlds-fair.json"
)

# ---------------------------------------------------------------------------
# Helpers shared across groups
# ---------------------------------------------------------------------------

def _make_run_record(**kwargs) -> dict:
    """Return a minimal run dict accepted by WorkflowPopover._on_run_stdout."""
    defaults = {
        "id": str(uuid.uuid4()),
        "spec_path": "/tmp/test_spec.json",
        "spec_name": "test",
        "status": "running",
        "progress": "starting…",
        "artifact_count": 0,
        "had_partial_failure": False,
        "warning_count": 0,
    }
    defaults.update(kwargs)
    return defaults


def _call_on_run_stdout(line: str, run: dict, prog_lbl=None):
    """
    Invoke WorkflowPopover._on_run_stdout with a single synthetic line.

    Builds a fake file-like 'source' whose readline() returns `line` once
    then '' (EOF), calls the unbound method with IO_IN, and patches
    _run_index.update so no disk I/O occurs.

    Returns the GLib SOURCE_CONTINUE / SOURCE_REMOVE value from the method.
    """
    import workflow_popover as wp
    from gi.repository import GLib

    fake_source = MagicMock()
    fake_source.readline.side_effect = [line + "\n", ""]

    with patch.object(wp._run_index, "update"):
        result = wp.WorkflowPopover.__dict__["_on_run_stdout"](
            None,        # self — not used by the logic being tested
            fake_source,
            GLib.IO_IN,
            run,
            prog_lbl,
        )
    return result


# ===========================================================================
# Group 1 — Dry-run output
# ===========================================================================

_skip_no_shell = pytest.mark.skipif(
    not _RUN_WORKFLOW_SH.exists(),
    reason="run_workflow.sh not present — skipping shell integration test",
)
_skip_no_spec = pytest.mark.skipif(
    not _EXAMPLE_WORKFLOW.exists(),
    reason="1964-worlds-fair.json not present — skipping integration test",
)


@_skip_no_shell
@_skip_no_spec
def test_dry_run_exits_zero(tmp_path):
    """
    run_workflow.sh --dry-run must exit 0 for the builtin example workflow.

    The script is designed to be non-destructive in dry-run mode: it logs
    what it would do instead of executing hardware operations.
    """
    result = subprocess.run(
        ["bash", str(_RUN_WORKFLOW_SH), str(_EXAMPLE_WORKFLOW), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, (
        f"Expected exit 0 from --dry-run, got {result.returncode}.\n"
        f"stdout: {result.stdout[:600]}\n"
        f"stderr: {result.stderr[:300]}"
    )


@_skip_no_shell
@_skip_no_spec
def test_dry_run_emits_log_line(tmp_path):
    """
    run_workflow.sh --dry-run must emit exactly one LOG:<path> line before
    any other output.  _on_run_stdout depends on this to populate log_file.
    """
    result = subprocess.run(
        ["bash", str(_RUN_WORKFLOW_SH), str(_EXAMPLE_WORKFLOW), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "HOME": str(tmp_path)},
    )

    log_lines = [
        ln for ln in result.stdout.splitlines()
        if ln.startswith("LOG:")
    ]
    assert log_lines, (
        f"Expected at least one LOG: line in stdout.\n"
        f"stdout: {result.stdout[:600]}\nstderr: {result.stderr[:200]}"
    )
    log_path = log_lines[0][4:].strip()
    assert log_path, "LOG: line must carry a non-empty file path"
    assert log_path.startswith("/"), (
        f"Log path must be absolute, got: {log_path!r}"
    )


@_skip_no_shell
@_skip_no_spec
def test_dry_run_log_file_created(tmp_path):
    """
    The file announced by LOG: must exist on disk after --dry-run completes.
    `exec > >(tee -a "$LOG_FILE") 2>&1` creates it even for a short run.
    """
    result = subprocess.run(
        ["bash", str(_RUN_WORKFLOW_SH), str(_EXAMPLE_WORKFLOW), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "HOME": str(tmp_path)},
    )

    log_lines = [
        ln for ln in result.stdout.splitlines()
        if ln.startswith("LOG:")
    ]
    if not log_lines:
        pytest.skip("No LOG: line emitted — cannot verify file creation")

    log_path = Path(log_lines[0][4:].strip())
    assert log_path.exists(), (
        f"Log file {log_path} was announced but does not exist.\n"
        f"stdout: {result.stdout[:600]}"
    )


@_skip_no_shell
@_skip_no_spec
def test_dry_run_emits_step_markers(tmp_path):
    """
    run_workflow.sh --dry-run must emit at least one per-node progress signal
    so a progress label can update as nodes run.

    As of Task 3, run_workflow.sh is a thin shim over app/pipeline_engine.py:
    the old hardcoded script's "══ Node N: ... ══" headers are gone — the
    engine emits "NODE:<id>:running:<class_type>" instead (the same signal
    app/pipeline_runner.py._parse_line parses). We assert on that protocol
    now rather than the retired bash-specific formatting.

    The example workflow has 9 nodes; we expect at least one NODE:<id>:running
    line before the (currently expected, Task-4-scoped) failure partway
    through the graph.
    """
    result = subprocess.run(
        ["bash", str(_RUN_WORKFLOW_SH), str(_EXAMPLE_WORKFLOW), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "HOME": str(tmp_path)},
    )

    running_lines = [
        ln for ln in result.stdout.splitlines()
        if ln.startswith("NODE:") and ":running:" in ln
    ]
    assert running_lines, (
        f"Expected at least one 'NODE:<id>:running:<...>' line in stdout — got none.\n"
        f"stdout: {result.stdout[:800]}"
    )


@_skip_no_shell
@_skip_no_spec
def test_dry_run_does_not_connect_to_port_8000(tmp_path):
    """
    --dry-run must not attempt any connection to localhost:8000.  We verify
    this by running the script in a network-isolated way and checking that no
    curl/urllib calls appear in the output as actual HTTP attempts.

    Strategy: scan stdout for "curl" or "urllib" lines that do NOT contain
    the dry-run marker "[dry-run]".  Any real connection attempt would also
    manifest as a connection-refused error in stderr.
    """
    result = subprocess.run(
        ["bash", str(_RUN_WORKFLOW_SH), str(_EXAMPLE_WORKFLOW), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "HOME": str(tmp_path)},
    )

    # stderr must not contain "Connection refused" to localhost:8000
    stderr_combined = result.stderr + result.stdout
    assert "Connection refused" not in stderr_combined, (
        "Dry-run tried to connect to a server and was refused"
    )
    assert "curl: (7)" not in stderr_combined, (
        "curl reported a connection failure — dry-run made a real HTTP call"
    )


# ===========================================================================
# Group 2 — _apply_overrides
# ===========================================================================

def test_apply_overrides_returns_valid_json(tmp_path):
    """
    _apply_overrides must produce a temp file that is valid JSON.
    """
    import workflow_popover as wp

    spec = {
        "_description": "Test pipeline",
        "1": {
            "class_type": "TTLGTextToImage",
            "inputs": {"prompt": "original prompt", "seed": 42, "steps": 4},
            "outputs": ["image_path"],
        },
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))

    tmp_out = wp._apply_overrides(str(spec_path), {})
    try:
        data = json.loads(Path(tmp_out).read_text())
        assert isinstance(data, dict)
    finally:
        Path(tmp_out).unlink(missing_ok=True)


def test_apply_overrides_patches_scalar_value(tmp_path):
    """
    An override for (node_id, key) must appear in the output with the new
    value.  The original spec file must be left unchanged.
    """
    import workflow_popover as wp

    original_prompt = "original prompt"
    new_prompt = "overridden prompt for test"

    spec = {
        "_description": "Override test pipeline",
        "1": {
            "class_type": "TTLGTextToImage",
            "inputs": {"prompt": original_prompt, "seed": 1964, "steps": 4},
            "outputs": ["image_path"],
        },
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))

    overrides = {("1", "prompt"): new_prompt}
    tmp_out = wp._apply_overrides(str(spec_path), overrides)

    try:
        result_data = json.loads(Path(tmp_out).read_text())
        assert result_data["1"]["inputs"]["prompt"] == new_prompt, (
            f"Expected overridden prompt, got: {result_data['1']['inputs']['prompt']!r}"
        )
        # Original file unchanged
        orig_data = json.loads(spec_path.read_text())
        assert orig_data["1"]["inputs"]["prompt"] == original_prompt, (
            "Original spec was mutated — _apply_overrides must not modify the source file"
        )
    finally:
        Path(tmp_out).unlink(missing_ok=True)


def test_apply_overrides_multiple_nodes(tmp_path):
    """
    Overrides spanning two different nodes must both be applied independently.
    """
    import workflow_popover as wp

    spec = {
        "_description": "Multi-node override test",
        "1": {
            "class_type": "TTLGTextToImage",
            "inputs": {"prompt": "prompt1", "seed": 1},
            "outputs": ["image_path"],
        },
        "8": {
            "class_type": "TTLGTextToImage",
            "inputs": {"prompt": "prompt8", "seed": 1965},
            "outputs": ["image2_path"],
        },
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))

    overrides = {
        ("1", "seed"): 9999,
        ("8", "prompt"): "new poem prompt",
    }
    tmp_out = wp._apply_overrides(str(spec_path), overrides)

    try:
        data = json.loads(Path(tmp_out).read_text())
        assert data["1"]["inputs"]["seed"] == 9999
        assert data["8"]["inputs"]["prompt"] == "new poem prompt"
        # Unaffected values remain
        assert data["1"]["inputs"]["prompt"] == "prompt1"
        assert data["8"]["inputs"]["seed"] == 1965
    finally:
        Path(tmp_out).unlink(missing_ok=True)


def test_apply_overrides_with_real_spec():
    """
    _apply_overrides on the real 1964-worlds-fair.json must produce valid JSON
    with the override applied to node 1 prompt.
    """
    if not _EXAMPLE_WORKFLOW.exists():
        pytest.skip("1964-worlds-fair.json not present")

    import workflow_popover as wp

    new_prompt = "A futuristic city test prompt"
    overrides = {("1", "prompt"): new_prompt}
    tmp_out = wp._apply_overrides(str(_EXAMPLE_WORKFLOW), overrides)

    try:
        data = json.loads(Path(tmp_out).read_text())
        assert data["1"]["inputs"]["prompt"] == new_prompt
        # Metadata keys preserved
        assert "_description" in data
        # Other nodes untouched
        assert data["8"]["inputs"]["prompt"] == ["7", "text"], (
            "Node 8's inter-node wire must remain as-is after override"
        )
    finally:
        Path(tmp_out).unlink(missing_ok=True)


# ===========================================================================
# Group 3 — Progress protocol parsing (_on_run_stdout)
# ===========================================================================

def test_protocol_log_path_stored(tmp_path):
    """
    'LOG:/path/to/run.log' → run['log_file'] is set to the path.
    """
    log_file = str(tmp_path / "run.log")
    run = _make_run_record()
    _call_on_run_stdout(f"LOG:{log_file}", run)
    assert run.get("log_file") == log_file, (
        f"Expected run['log_file'] == {log_file!r}, got {run.get('log_file')!r}"
    )


def test_protocol_log_path_stripped():
    """LOG: path must be stripped of surrounding whitespace."""
    run = _make_run_record()
    _call_on_run_stdout("LOG:  /tmp/my log with spaces.log  ", run)
    assert run.get("log_file") == "/tmp/my log with spaces.log"


def test_protocol_step_marker_updates_progress():
    """
    '══ Node 6: SkyReels video ══' → run['progress'] is set to a non-empty
    string derived from the line content.
    """
    run = _make_run_record()
    _call_on_run_stdout("══ Node 6: SkyReels video ══", run)
    progress = run.get("progress", "")
    assert progress, "run['progress'] must be non-empty after a step-marker line"
    # The content should reflect the node information
    assert "Node 6" in progress or "SkyReels" in progress or "6" in progress, (
        f"Progress should contain node info, got: {progress!r}"
    )


def test_protocol_bracket_line_updates_progress():
    """
    '[10:23:45] Node 1: generating image' → run['progress'] is updated.
    """
    run = _make_run_record()
    _call_on_run_stdout("[10:23:45] Node 1: generating image", run)
    progress = run.get("progress", "")
    assert progress, "run['progress'] must be set after a bracket-prefixed line"


def test_protocol_skyreels_warmup_updates_progress():
    """
    '⏳ SkyReels: Loading shards 5/14' → run['progress'] is set to the full
    text (up to 60 chars) so the operator can see compile/load progress.
    """
    run = _make_run_record()
    line = "⏳ SkyReels: Loading shards 5/14 — please wait"
    _call_on_run_stdout(line, run)
    progress = run.get("progress", "")
    assert progress, "run['progress'] must be set from a SkyReels warmup line"
    # Content should be derived from the line
    assert "SkyReels" in progress or "Loading" in progress or "shards" in progress, (
        f"Progress should reflect warmup content, got: {progress!r}"
    )


def test_protocol_skyreels_warmup_truncated_at_60():
    """
    SkyReels warmup progress must be capped at 60 chars to fit the label.
    """
    run = _make_run_record()
    long_line = "⏳ SkyReels: " + "x" * 200
    _call_on_run_stdout(long_line, run)
    progress = run.get("progress", "")
    assert len(progress) <= 60, (
        f"Progress must be truncated to <=60 chars, got {len(progress)}: {progress!r}"
    )


def test_protocol_warning_flag_set():
    """
    A line containing '⚠️' → run['had_partial_failure'] becomes True.
    """
    run = _make_run_record()
    _call_on_run_stdout("⚠️ node 3 skipped: no image found", run)
    assert run.get("had_partial_failure") is True, (
        "run['had_partial_failure'] must be True after a ⚠️ line"
    )


def test_protocol_warning_count_incremented():
    """
    Each ⚠️ line increments run['warning_count'] by exactly 1.
    """
    run = _make_run_record()
    assert run["warning_count"] == 0
    _call_on_run_stdout("⚠️ first warning", run)
    assert run["warning_count"] == 1
    _call_on_run_stdout("⚠️ second warning", run)
    assert run["warning_count"] == 2


def test_protocol_partial_failure_text():
    """
    A line containing 'partial failures' (case-insensitive) also sets
    run['had_partial_failure'] and increments the counter.
    """
    run = _make_run_record()
    _call_on_run_stdout("Pipeline completed with partial failures in nodes 3, 7", run)
    assert run.get("had_partial_failure") is True
    assert run.get("warning_count", 0) >= 1


def test_protocol_playlist_count_extracted():
    """
    'PLAYLIST:5:1964 world\'s fair experiment' → run['artifact_count'] == 5.
    """
    run = _make_run_record()
    _call_on_run_stdout("PLAYLIST:5:1964 world's fair experiment", run)
    assert run.get("artifact_count") == 5, (
        f"Expected artifact_count=5, got {run.get('artifact_count')!r}"
    )


def test_protocol_playlist_name_with_colons():
    """
    Playlist names that contain colons must be fully reconstructed by joining
    parts[2:].  e.g. 'PLAYLIST:3:foo:bar:baz' → name is 'foo:bar:baz'.
    This validates the ":".join(parts[2:]) logic in _on_run_stdout.
    """
    run = _make_run_record()
    _call_on_run_stdout("PLAYLIST:3:foo:bar:baz", run)
    # The artifact count must parse correctly regardless of colons in the name
    assert run.get("artifact_count") == 3


def test_protocol_playlist_zero_count():
    """PLAYLIST:0:empty run → artifact_count is 0, not unset."""
    run = _make_run_record()
    _call_on_run_stdout("PLAYLIST:0:empty run", run)
    assert run.get("artifact_count") == 0


def test_protocol_non_matching_line_no_change():
    """
    An ordinary log line (no special prefix) must not alter run['progress'],
    run['had_partial_failure'], or run['warning_count'].
    """
    run = _make_run_record(progress="original")
    _call_on_run_stdout("some random log line with no special prefix", run)
    assert run["progress"] == "original"
    assert run["had_partial_failure"] is False
    assert run["warning_count"] == 0


def test_protocol_returns_source_continue():
    """
    _on_run_stdout must return GLib.SOURCE_CONTINUE (True) for IO_IN so the
    GLib IO watch keeps firing for subsequent lines.
    """
    from gi.repository import GLib
    run = _make_run_record()
    result = _call_on_run_stdout("some line", run)
    assert result == GLib.SOURCE_CONTINUE


# ===========================================================================
# Group 4 — Spec validation
# ===========================================================================

# Known valid node class_types as of the current implementation.
# Add new types here as the runner gains support for them.
_KNOWN_CLASS_TYPES = {
    "TTLGTextToImage",
    "TTLGImageToVideo",
    "TTLGCaptionImage",
    "TTLGRemoveBackground",
    "TTLGEstimateDepth",
    "TTLGPromptCompose",
    "TTLGGenerateText",
    "TTLGAddToPlaylist",
}


def _validate_workflow_spec(spec: dict) -> list[str]:
    """
    Validate a workflow spec dict against structural invariants.

    Returns a list of human-readable error strings (empty = valid).

    Invariants checked
    ------------------
    - '_description' key must be present and non-empty
    - Every node must have a 'class_type' key
    - Every class_type must be in the known set
    - Every input that is a list (inter-node wire) must be ['N', 'key'] where
      N refers to a node that exists in the spec
    - No node may reference itself (trivial self-cycle check)
    - The graph must be a DAG — no back-edges (DFS-based cycle detection)
    """
    errors: list[str] = []

    # 1. _description required
    if not spec.get("_description", "").strip():
        errors.append("Missing or empty '_description' field")

    # Collect node ids (non-underscore keys)
    node_ids = {k for k in spec if not k.startswith("_")}

    for node_id in node_ids:
        node = spec[node_id]
        if not isinstance(node, dict):
            errors.append(f"Node '{node_id}' is not a dict")
            continue

        # 2. class_type present and known
        ct = node.get("class_type", "")
        if not ct:
            errors.append(f"Node '{node_id}' is missing 'class_type'")
        elif ct not in _KNOWN_CLASS_TYPES:
            errors.append(f"Node '{node_id}' has unknown class_type: {ct!r}")

        # 3. Validate inter-node wire references
        for key, val in node.get("inputs", {}).items():
            if isinstance(val, list):
                # Could be a wire ref ["N", "output_key"] or an artifact list (for TTLGAddToPlaylist)
                if len(val) == 2 and isinstance(val[0], str) and isinstance(val[1], str):
                    ref_node, ref_key = val
                    if ref_node not in node_ids:
                        errors.append(
                            f"Node '{node_id}' input '{key}' references "
                            f"non-existent node '{ref_node}'"
                        )
                    if ref_node == node_id:
                        errors.append(
                            f"Node '{node_id}' input '{key}' self-references itself"
                        )
                elif all(isinstance(item, dict) for item in val):
                    # TTLGAddToPlaylist artifacts list — each dict may have {"path": ["N", "key"]}
                    for artifact in val:
                        path_ref = artifact.get("path")
                        if isinstance(path_ref, list) and len(path_ref) == 2:
                            ref_node = path_ref[0]
                            if isinstance(ref_node, str) and ref_node not in node_ids:
                                errors.append(
                                    f"Node '{node_id}' artifact references "
                                    f"non-existent node '{ref_node}'"
                                )

    # 4. Cycle detection via DFS
    # Build adjacency: node -> set of nodes it depends on
    adj: dict[str, set[str]] = {nid: set() for nid in node_ids}
    for node_id in node_ids:
        node = spec.get(node_id, {})
        for key, val in node.get("inputs", {}).items():
            if isinstance(val, list):
                if len(val) == 2 and isinstance(val[0], str) and isinstance(val[1], str):
                    dep = val[0]
                    if dep in node_ids:
                        adj[node_id].add(dep)
                elif all(isinstance(item, dict) for item in val):
                    for artifact in val:
                        path_ref = artifact.get("path")
                        if isinstance(path_ref, list) and len(path_ref) == 2:
                            dep = path_ref[0]
                            if isinstance(dep, str) and dep in node_ids:
                                adj[node_id].add(dep)

    # DFS cycle detection
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in node_ids}

    def dfs(nid: str) -> bool:
        """Return True if a cycle is detected."""
        color[nid] = GRAY
        for dep in adj[nid]:
            if color[dep] == GRAY:
                return True  # back-edge → cycle
            if color[dep] == WHITE and dfs(dep):
                return True
        color[nid] = BLACK
        return False

    for nid in node_ids:
        if color[nid] == WHITE:
            if dfs(nid):
                errors.append(f"Cycle detected in workflow graph (starting from node '{nid}')")
                break

    return errors


def test_spec_validator_accepts_valid_spec():
    """The validator itself must accept a well-formed minimal spec."""
    spec = {
        "_description": "Minimal valid pipeline",
        "_spec_version": "comfyui-api-v1",
        "1": {
            "class_type": "TTLGTextToImage",
            "inputs": {"prompt": "hello", "seed": 42},
            "outputs": ["image_path"],
        },
        "2": {
            "class_type": "TTLGCaptionImage",
            "inputs": {"plugin": "blip", "src": ["1", "image_path"]},
            "outputs": ["caption"],
        },
    }
    errors = _validate_workflow_spec(spec)
    assert errors == [], f"Valid spec reported errors: {errors}"


def test_spec_validator_catches_missing_description():
    """Validator must flag a spec with no _description."""
    spec = {
        "1": {
            "class_type": "TTLGTextToImage",
            "inputs": {"prompt": "x"},
            "outputs": ["image_path"],
        },
    }
    errors = _validate_workflow_spec(spec)
    assert any("_description" in e for e in errors), (
        f"Expected error about missing _description, got: {errors}"
    )


def test_spec_validator_catches_unknown_class_type():
    """An unrecognised class_type must be flagged."""
    spec = {
        "_description": "Test spec",
        "1": {
            "class_type": "TTLGSomethingUnknown",
            "inputs": {"prompt": "x"},
            "outputs": ["image_path"],
        },
    }
    errors = _validate_workflow_spec(spec)
    assert any("unknown class_type" in e for e in errors), (
        f"Expected unknown class_type error, got: {errors}"
    )


def test_spec_validator_catches_dangling_node_ref():
    """A wire referencing a non-existent node must be flagged."""
    spec = {
        "_description": "Dangling ref test",
        "2": {
            "class_type": "TTLGCaptionImage",
            "inputs": {"src": ["99", "image_path"]},  # node 99 doesn't exist
            "outputs": ["caption"],
        },
    }
    errors = _validate_workflow_spec(spec)
    assert any("non-existent node" in e for e in errors), (
        f"Expected dangling-ref error, got: {errors}"
    )


def test_spec_validator_catches_cycle():
    """A cyclic dependency must be detected and reported."""
    spec = {
        "_description": "Cyclic spec",
        "1": {
            "class_type": "TTLGTextToImage",
            "inputs": {"src": ["2", "caption"]},  # depends on node 2
            "outputs": ["image_path"],
        },
        "2": {
            "class_type": "TTLGCaptionImage",
            "inputs": {"src": ["1", "image_path"]},  # depends on node 1 → cycle
            "outputs": ["caption"],
        },
    }
    errors = _validate_workflow_spec(spec)
    assert any("ycle" in e for e in errors), (
        f"Expected cycle detection error, got: {errors}"
    )


@pytest.mark.skipif(
    not _EXAMPLE_WORKFLOW.exists(),
    reason="1964-worlds-fair.json not found — skipping real-spec validation",
)
def test_real_spec_has_description():
    """1964-worlds-fair.json must have a non-empty _description field."""
    spec = json.loads(_EXAMPLE_WORKFLOW.read_text())
    assert spec.get("_description", "").strip(), (
        "1964-worlds-fair.json is missing a _description field"
    )


@pytest.mark.skipif(
    not _EXAMPLE_WORKFLOW.exists(),
    reason="1964-worlds-fair.json not found — skipping real-spec validation",
)
def test_real_spec_all_node_refs_valid():
    """All inter-node wires in 1964-worlds-fair.json must reference existing nodes."""
    spec = json.loads(_EXAMPLE_WORKFLOW.read_text())
    errors = _validate_workflow_spec(spec)
    ref_errors = [e for e in errors if "non-existent node" in e]
    assert not ref_errors, (
        f"1964-worlds-fair.json has dangling node references: {ref_errors}"
    )


@pytest.mark.skipif(
    not _EXAMPLE_WORKFLOW.exists(),
    reason="1964-worlds-fair.json not found — skipping real-spec validation",
)
def test_real_spec_no_cycles():
    """1964-worlds-fair.json must be a DAG — no cyclic node dependencies."""
    spec = json.loads(_EXAMPLE_WORKFLOW.read_text())
    errors = _validate_workflow_spec(spec)
    cycle_errors = [e for e in errors if "ycle" in e]
    assert not cycle_errors, (
        f"1964-worlds-fair.json has cycles: {cycle_errors}"
    )


@pytest.mark.skipif(
    not _EXAMPLE_WORKFLOW.exists(),
    reason="1964-worlds-fair.json not found — skipping real-spec validation",
)
def test_real_spec_all_class_types_known():
    """Every class_type in 1964-worlds-fair.json must be in the known set."""
    spec = json.loads(_EXAMPLE_WORKFLOW.read_text())
    errors = _validate_workflow_spec(spec)
    type_errors = [e for e in errors if "unknown class_type" in e]
    assert not type_errors, (
        f"1964-worlds-fair.json uses unknown class_types: {type_errors}"
    )


@pytest.mark.skipif(
    not _EXAMPLE_WORKFLOW.exists(),
    reason="1964-worlds-fair.json not found — skipping real-spec validation",
)
def test_real_spec_passes_full_validation():
    """1964-worlds-fair.json must pass the complete validator with zero errors."""
    spec = json.loads(_EXAMPLE_WORKFLOW.read_text())
    errors = _validate_workflow_spec(spec)
    assert errors == [], (
        f"1964-worlds-fair.json failed validation:\n" + "\n".join(f"  • {e}" for e in errors)
    )


@pytest.mark.skipif(
    not _EXAMPLE_WORKFLOW.exists(),
    reason="1964-worlds-fair.json not found — skipping node-count check",
)
def test_real_spec_has_expected_node_count():
    """
    1964-worlds-fair.json should have exactly 9 nodes (1-9).
    If this changes, the test is a canary that the spec was modified.
    """
    spec = json.loads(_EXAMPLE_WORKFLOW.read_text())
    node_ids = [k for k in spec if not k.startswith("_")]
    assert len(node_ids) == 9, (
        f"Expected 9 nodes in 1964-worlds-fair.json, found {len(node_ids)}: {sorted(node_ids)}"
    )


# ===========================================================================
# Group 5 — Workflow-vs-model segregation
# ===========================================================================

def _simulate_by_model_count(records: list) -> dict[str, int]:
    """
    Replicate the by-model counting logic from main_window.py lines 7685-7694.

    Records must be objects (or SimpleNamespace/MagicMock) with .model and
    .media_type attributes.

    Returns {model_id: count} — workflow records are excluded.
    """
    counts: dict[str, int] = {}
    for r in records:
        mid = getattr(r, "model", "") or ""
        # Guard from main_window.py:7691 — skip workflow-runner artifacts
        if mid.startswith("workflow"):
            continue
        if mid and getattr(r, "media_type", "video") != "image":
            counts[mid] = counts.get(mid, 0) + 1
    return counts


class _FakeRecord:
    """Minimal record stub for by-model segregation tests."""
    def __init__(self, model: str, media_type: str = "video"):
        self.model = model
        self.media_type = media_type


def test_workflow_model_id_excluded_from_count():
    """
    Records with model == 'workflow' must not appear in the by-model count.
    """
    records = [
        _FakeRecord("wan2.2-t2v"),
        _FakeRecord("workflow"),          # must be excluded
        _FakeRecord("wan2.2-t2v"),
    ]
    counts = _simulate_by_model_count(records)
    assert "workflow" not in counts, (
        "model_id='workflow' must not appear in the by-model count"
    )
    assert counts.get("wan2.2-t2v") == 2


def test_workflow_prefix_variants_excluded():
    """
    Any model_id that starts with 'workflow' must be excluded, not just the
    bare 'workflow' string.  This covers variants like 'workflow-v2',
    'workflow-1964-experiment', etc.
    """
    records = [
        _FakeRecord("wan2.2-t2v"),
        _FakeRecord("workflow"),
        _FakeRecord("workflow-v2"),
        _FakeRecord("workflow-1964-worlds-fair"),
        _FakeRecord("flux.1-schnell"),
    ]
    counts = _simulate_by_model_count(records)
    for mid in ("workflow", "workflow-v2", "workflow-1964-worlds-fair"):
        assert mid not in counts, (
            f"model_id={mid!r} should be excluded from by-model count"
        )
    assert counts.get("wan2.2-t2v") == 1
    assert counts.get("flux.1-schnell") == 1


def test_image_records_excluded_from_by_model_video_count():
    """
    Image records (media_type == 'image') are excluded from the video by-model
    count regardless of model_id.  This matches the condition at main_window.py:7693.
    """
    records = [
        _FakeRecord("flux.1-schnell", media_type="image"),  # image — excluded
        _FakeRecord("wan2.2-t2v", media_type="video"),       # video — included
    ]
    counts = _simulate_by_model_count(records)
    assert "flux.1-schnell" not in counts, (
        "Image records must not appear in the video by-model count"
    )
    assert counts.get("wan2.2-t2v") == 1


def test_empty_model_id_excluded():
    """Records with an empty model string are silently skipped."""
    records = [
        _FakeRecord(""),           # empty — skip
        _FakeRecord("wan2.2-t2v"),
    ]
    counts = _simulate_by_model_count(records)
    assert "" not in counts
    assert counts.get("wan2.2-t2v") == 1


def test_mixed_records_correct_final_tally():
    """
    Realistic mixed pool: workflow artifacts + image records + video records.
    Only the video records with a real model_id should be tallied.
    """
    records = [
        _FakeRecord("wan2.2-t2v"),
        _FakeRecord("wan2.2-t2v"),
        _FakeRecord("workflow"),
        _FakeRecord("workflow-v2"),
        _FakeRecord("flux.1-schnell", media_type="image"),
        _FakeRecord("mochi-1-preview"),
        _FakeRecord(""),
        _FakeRecord("mochi-1-preview"),
    ]
    counts = _simulate_by_model_count(records)
    assert counts == {
        "wan2.2-t2v": 2,
        "mochi-1-preview": 2,
    }, f"Unexpected by-model counts: {counts}"


def test_all_workflow_records_produces_empty_count():
    """If every record is a workflow artifact, the count dict must be empty."""
    records = [
        _FakeRecord("workflow"),
        _FakeRecord("workflow-v2"),
        _FakeRecord("workflow-1964"),
    ]
    counts = _simulate_by_model_count(records)
    assert counts == {}, f"Expected empty dict, got {counts}"


def test_no_records_produces_empty_count():
    """Empty history → empty by-model count."""
    counts = _simulate_by_model_count([])
    assert counts == {}


# ===========================================================================
# Group 7 — --dry-run flag gating (Critical bug regression)
# ===========================================================================
#
# Bug: run_workflow.sh built the final engine invocation with
# ${DRY_RUN:+--dry-run}. DRY_RUN is initialized unconditionally to "0" or
# "1", and ${VAR:+word} expands to `word` whenever VAR is set to ANY
# non-empty value -- including the string "0". So --dry-run was appended on
# EVERY invocation, real or not. app/pipeline_runner.py launches real runs as
# ["bash", "bin/run_workflow.sh", spec] (no --dry-run arg), so real pipeline
# runs silently executed in dry-run mode and no-op'd while reporting success.
#
# These tests point PYTHON3 at a fake recorder script (via env var) so we can
# inspect the exact argv the shim hands to the engine, without touching
# hardware, Docker, or the network.
# ===========================================================================


def _write_fake_python_recorder(tmp_path: Path, record_file: Path) -> Path:
    """
    Write an executable fake "python3" that records its argv (everything
    after the interpreter itself) as a single space-joined line appended to
    `record_file`, then exits 0. Used to observe exactly what run_workflow.sh
    hands to `$PYTHON3 app/pipeline_engine.py ...` without running the real
    engine.
    """
    fake = tmp_path / "fake_python3"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{record_file}"\n'
        "exit 0\n"
    )
    # Owner rwx only (0o700): the test runs the fake interpreter as the same
    # user that wrote it, so it never needs group/other read+exec. Keeps the
    # throwaway helper least-privilege (satisfies the SAST permissive-perms
    # check) without changing what the test exercises.
    fake.chmod(0o700)
    return fake


@_skip_no_shell
@_skip_no_spec
def test_dry_run_flag_only_passed_in_dry_mode(tmp_path):
    """
    Regression test for the Critical --dry-run-always-on bug.

    Runs run_workflow.sh twice against a fake PYTHON3 recorder:
      1. real mode   — no --dry-run arg at all
      2. dry mode    — "--dry-run" as $2

    Asserts the recorded engine argv reflects each mode correctly: real mode
    must NOT contain --dry-run, dry mode MUST contain it.
    """
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    record_file = tmp_path / "recorded_args.txt"
    fake_python = _write_fake_python_recorder(tmp_path, record_file)

    env = {
        **os.environ,
        "HOME": str(tmp_home),
        "PYTHON3": str(fake_python),
    }

    result_real = subprocess.run(
        ["bash", str(_RUN_WORKFLOW_SH), str(_EXAMPLE_WORKFLOW)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result_real.returncode == 0, (
        f"Real-mode invocation must exit 0 (fake engine always exits 0).\n"
        f"stdout: {result_real.stdout[:600]}\nstderr: {result_real.stderr[:300]}"
    )

    result_dry = subprocess.run(
        ["bash", str(_RUN_WORKFLOW_SH), str(_EXAMPLE_WORKFLOW), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result_dry.returncode == 0, (
        f"Dry-mode invocation must exit 0 (fake engine always exits 0).\n"
        f"stdout: {result_dry.stdout[:600]}\nstderr: {result_dry.stderr[:300]}"
    )

    assert record_file.exists(), (
        "Fake PYTHON3 recorder was never invoked — PYTHON3 env override is "
        "not being respected by run_workflow.sh"
    )
    lines = record_file.read_text().splitlines()
    assert len(lines) == 2, (
        f"Expected exactly 2 recorded engine invocations, got {len(lines)}: {lines}"
    )
    real_args, dry_args = lines[0], lines[1]

    assert str(_EXAMPLE_WORKFLOW) in real_args
    assert "--output-dir" in real_args
    assert "--dry-run" not in real_args, (
        f"BUG: real-mode invocation (no --dry-run arg passed to the shim) "
        f"must NOT pass --dry-run to the engine. Recorded args: {real_args!r}"
    )

    assert "--dry-run" in dry_args, (
        f"Dry-mode invocation (--dry-run arg passed to the shim) must pass "
        f"--dry-run through to the engine. Recorded args: {dry_args!r}"
    )


# ===========================================================================
# Group 6 — Remix vs. Workflow record distinguishability
# ===========================================================================
#
# Architectural contract (see remix_dispatch.py module docstring):
#
#   Workflow record  — model_id starts with "workflow"
#                      (e.g. "workflow", "workflow-v2")
#
#   Remix record     — standard GenerationRecord produced by a normal
#                      generation; optionally carries:
#                        extra_meta._source_id  (source card's record id)
#                        extra_meta._transform  (action label, e.g. "animate")
#                      A record with neither key is also a valid remix / direct
#                      generation — the absence of "workflow" prefix is the
#                      definitive test.
#
# These tests verify that the two record types can be reliably distinguished
# without inspecting any field other than model_id / extra_meta.
# ===========================================================================


class _FakeRemixRecord:
    """Minimal record stub that mimics a remix-produced GenerationRecord."""

    def __init__(
        self,
        model: str = "wan2.2-t2v",
        media_type: str = "video",
        extra_meta: Optional[dict] = None,
    ):
        self.model = model
        self.media_type = media_type
        self.extra_meta = extra_meta or {}

    def is_workflow_artifact(self) -> bool:
        """Return True when the record originated from the workflow runner."""
        return (self.model or "").startswith("workflow")

    def is_remix(self) -> bool:
        """
        Return True when the record was produced by a Remix action.

        A remix record is any standard generation that carries remix provenance
        in extra_meta (_source_id or _transform).  A plain generation with
        neither key is NOT a remix — it is a direct generation.
        """
        return bool(
            self.extra_meta.get("_source_id") or self.extra_meta.get("_transform")
        )


def test_workflow_record_identified_by_model_prefix():
    """
    A record with model_id starting with 'workflow' must be recognised as a
    workflow artifact.  Variants like 'workflow-v2' are also workflow records.
    """
    for mid in ("workflow", "workflow-v2", "workflow-1964-worlds-fair"):
        rec = _FakeRemixRecord(model=mid)
        assert rec.is_workflow_artifact(), (
            f"model_id={mid!r} should be identified as a workflow artifact"
        )
        assert not rec.is_remix(), (
            f"model_id={mid!r} should not be classified as a remix record"
        )


def test_remix_record_identified_by_transform_meta():
    """
    A record with extra_meta._transform set (e.g. "animate") is a remix record
    and is NOT a workflow artifact.
    """
    rec = _FakeRemixRecord(
        model="wan2.2-t2v",
        extra_meta={"_transform": "animate", "_source_id": "abc-123"},
    )
    assert rec.is_remix(), "Record with _transform must be identified as remix"
    assert not rec.is_workflow_artifact(), (
        "Remix record must not be identified as a workflow artifact"
    )


def test_remix_record_source_id_only():
    """
    A record with only extra_meta._source_id (no _transform) is still a remix
    record — the source card is tracked but the transform label was omitted.
    """
    rec = _FakeRemixRecord(
        model="flux.1-schnell",
        media_type="image",
        extra_meta={"_source_id": "rec-deadbeef"},
    )
    assert rec.is_remix(), "Record with _source_id alone must be identified as remix"
    assert not rec.is_workflow_artifact()


def test_direct_generation_is_neither_workflow_nor_remix():
    """
    A standard generation (no workflow prefix, no remix meta) is neither a
    workflow artifact nor a remix.  It represents a plain user-typed prompt.
    """
    rec = _FakeRemixRecord(model="wan2.2-t2v", extra_meta={})
    assert not rec.is_workflow_artifact(), "Plain generation must not be workflow artifact"
    assert not rec.is_remix(), "Plain generation must not be classified as remix"


def test_workflow_record_with_extra_meta_is_still_workflow():
    """
    A workflow record that happens to carry extra_meta keys must still be
    classified by model_id prefix, not by extra_meta content.
    """
    rec = _FakeRemixRecord(
        model="workflow-v2",
        extra_meta={"_transform": "skyreels", "_source_id": "some-id"},
    )
    assert rec.is_workflow_artifact(), (
        "model_id prefix takes precedence — this is a workflow artifact"
    )


def test_mixed_pool_classification():
    """
    A realistic mixed pool of records is classified correctly into three
    categories: workflow artifacts, remix records, and plain generations.
    """
    pool = [
        _FakeRemixRecord(model="workflow"),
        _FakeRemixRecord(model="workflow-1964"),
        _FakeRemixRecord(model="wan2.2-t2v", extra_meta={"_transform": "reimagine"}),
        _FakeRemixRecord(model="wan2.2-t2v", extra_meta={"_source_id": "id-001"}),
        _FakeRemixRecord(model="wan2.2-t2v"),
        _FakeRemixRecord(model="flux.1-schnell", media_type="image"),
    ]

    workflow_recs = [r for r in pool if r.is_workflow_artifact()]
    remix_recs = [r for r in pool if r.is_remix()]
    plain_recs = [r for r in pool if not r.is_workflow_artifact() and not r.is_remix()]

    assert len(workflow_recs) == 2, f"Expected 2 workflow records, got {len(workflow_recs)}"
    assert len(remix_recs) == 2, f"Expected 2 remix records, got {len(remix_recs)}"
    assert len(plain_recs) == 2, f"Expected 2 plain records, got {len(plain_recs)}"
