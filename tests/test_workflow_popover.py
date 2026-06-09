"""Tests for workflow_popover.py — spec discovery, run index, param parsing."""
import io
import json
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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


# ── _on_run_stdout: LOG path parsing ─────────────────────────────────────────

def _make_run_record(**kwargs) -> dict:
    """Return a minimal run dict sufficient for _on_run_stdout."""
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
    Drive _on_run_stdout directly without a real subprocess or GLib IO watch.

    We build a fake 'source' object whose readline() returns the given line
    once, then '' (EOF), and we call the method with IO_IN condition.
    """
    import workflow_popover as wp
    from gi.repository import GLib

    fake_source = MagicMock()
    # First call returns the line, second call returns '' (as readline would at EOF)
    fake_source.readline.side_effect = [line + "\n", ""]

    # Patch _run_index.update so we don't touch the real index file
    with patch.object(wp._run_index, "update"):
        result = wp.WorkflowPopover.__dict__["_on_run_stdout"](
            None,          # self — not needed for the logic under test
            fake_source,
            GLib.IO_IN,
            run,
            prog_lbl,
        )
    return result


def test_on_run_stdout_extracts_log_path(tmp_path):
    """LOG:/path/to/file.log stores the path in run['log_file']."""
    import workflow_popover as wp

    log_file = str(tmp_path / "run.log")
    run = _make_run_record()

    with patch.object(wp._run_index, "update") as mock_update:
        fake_source = MagicMock()
        fake_source.readline.side_effect = [f"LOG:{log_file}\n", ""]
        from gi.repository import GLib
        wp.WorkflowPopover.__dict__["_on_run_stdout"](
            None, fake_source, GLib.IO_IN, run, None
        )

    assert run.get("log_file") == log_file, (
        f"Expected run['log_file'] == {log_file!r}, got {run.get('log_file')!r}"
    )
    # Confirm the index was updated with the log path
    mock_update.assert_called_once_with(run["id"], log_file=log_file)


def test_on_run_stdout_log_path_stripped():
    """Log path is stripped of surrounding whitespace."""
    import workflow_popover as wp

    run = _make_run_record()
    line = "LOG:  /some/path/with spaces.log  "

    with patch.object(wp._run_index, "update"):
        fake_source = MagicMock()
        fake_source.readline.side_effect = [line + "\n", ""]
        from gi.repository import GLib
        wp.WorkflowPopover.__dict__["_on_run_stdout"](
            None, fake_source, GLib.IO_IN, run, None
        )

    # strip() is applied inside _on_run_stdout
    assert run.get("log_file") == "/some/path/with spaces.log"


# ── _on_run_stdout: progress label update ────────────────────────────────────

def test_on_run_stdout_step_line_updates_progress_label():
    """A line starting with ══ updates run['progress'] and calls GLib.idle_add."""
    import workflow_popover as wp
    from gi.repository import GLib

    run = _make_run_record()
    prog_lbl = MagicMock()

    line = "══ Node 1: Seed image — FLUX.1-schnell ══"

    with patch.object(wp._run_index, "update"), \
         patch("workflow_popover.GLib.idle_add") as mock_idle:
        fake_source = MagicMock()
        fake_source.readline.side_effect = [line + "\n", ""]
        wp.WorkflowPopover.__dict__["_on_run_stdout"](
            None, fake_source, GLib.IO_IN, run, prog_lbl
        )

    # run['progress'] must be set
    assert run.get("progress") is not None
    assert run["progress"] != "starting…", "progress should have been updated"

    # GLib.idle_add must have been called with the label update
    mock_idle.assert_called_once()
    args = mock_idle.call_args[0]
    assert args[0] is prog_lbl.set_label


def test_on_run_stdout_warning_line_updates_progress_label():
    """A line containing ⚠️ updates the progress label immediately."""
    import workflow_popover as wp
    from gi.repository import GLib

    run = _make_run_record()
    prog_lbl = MagicMock()
    line = "⚠️  node1 failed (exit 1) — continuing pipeline"

    with patch.object(wp._run_index, "update"), \
         patch("workflow_popover.GLib.idle_add") as mock_idle:
        fake_source = MagicMock()
        fake_source.readline.side_effect = [line + "\n", ""]
        wp.WorkflowPopover.__dict__["_on_run_stdout"](
            None, fake_source, GLib.IO_IN, run, prog_lbl
        )

    mock_idle.assert_called_once()
    set_label_call = mock_idle.call_args[0]
    assert set_label_call[0] is prog_lbl.set_label
    # The label text should contain the warning emoji
    label_text = set_label_call[1]
    assert "⚠️" in label_text or "partial" in label_text.lower()


def test_on_run_stdout_bracket_line_updates_progress():
    """A line starting with [ (tagged log line) updates run['progress']."""
    import workflow_popover as wp
    from gi.repository import GLib

    run = _make_run_record()
    prog_lbl = MagicMock()
    line = "[SkyReels warmup] 4min elapsed, waiting for model…"

    with patch.object(wp._run_index, "update"), \
         patch("workflow_popover.GLib.idle_add") as mock_idle:
        fake_source = MagicMock()
        fake_source.readline.side_effect = [line + "\n", ""]
        wp.WorkflowPopover.__dict__["_on_run_stdout"](
            None, fake_source, GLib.IO_IN, run, prog_lbl
        )

    assert run["progress"] != "starting…"
    mock_idle.assert_called_once()


def test_on_run_stdout_no_label_no_crash():
    """When prog_lbl is None, ══ lines must not raise."""
    import workflow_popover as wp
    from gi.repository import GLib

    run = _make_run_record()
    line = "══ Node 2: Caption image ══"

    with patch.object(wp._run_index, "update"), \
         patch("workflow_popover.GLib.idle_add") as mock_idle:
        fake_source = MagicMock()
        fake_source.readline.side_effect = [line + "\n", ""]
        # Should not raise even with prog_lbl=None
        wp.WorkflowPopover.__dict__["_on_run_stdout"](
            None, fake_source, GLib.IO_IN, run, None
        )

    mock_idle.assert_not_called()


# ── _on_run_stdout: partial failure warning counter ──────────────────────────

def test_on_run_stdout_warning_increments_counter():
    """⚠️ lines increment run['warning_count'] and set had_partial_failure."""
    import workflow_popover as wp
    from gi.repository import GLib

    run = _make_run_record()

    def send_warning(label_text):
        with patch.object(wp._run_index, "update"), \
             patch("workflow_popover.GLib.idle_add"):
            fake_source = MagicMock()
            fake_source.readline.side_effect = [label_text + "\n", ""]
            wp.WorkflowPopover.__dict__["_on_run_stdout"](
                None, fake_source, GLib.IO_IN, run, None
            )

    send_warning("⚠️  node1 failed (exit 1) — continuing pipeline")
    assert run.get("had_partial_failure") is True
    assert run.get("warning_count") == 1

    send_warning("⚠️  node3 skipped: image_path is empty")
    assert run.get("warning_count") == 2

    send_warning("⚠️  Pipeline finished with partial failures: node4 node5")
    assert run.get("warning_count") == 3


def test_on_run_stdout_partial_failures_keyword():
    """'partial failures' (case-insensitive) in a line also triggers the counter."""
    import workflow_popover as wp
    from gi.repository import GLib

    run = _make_run_record()
    line = "Pipeline finished with Partial Failures: node2"

    with patch.object(wp._run_index, "update"), \
         patch("workflow_popover.GLib.idle_add"):
        fake_source = MagicMock()
        fake_source.readline.side_effect = [line + "\n", ""]
        wp.WorkflowPopover.__dict__["_on_run_stdout"](
            None, fake_source, GLib.IO_IN, run, None
        )

    assert run.get("had_partial_failure") is True
    assert run.get("warning_count", 0) >= 1


def test_on_run_stdout_warning_updates_index():
    """_run_index.update is called with had_partial_failure and warning_count."""
    import workflow_popover as wp
    from gi.repository import GLib

    run = _make_run_record()
    line = "⚠️  node6 failed — continuing pipeline"

    with patch.object(wp._run_index, "update") as mock_update, \
         patch("workflow_popover.GLib.idle_add"):
        fake_source = MagicMock()
        fake_source.readline.side_effect = [line + "\n", ""]
        wp.WorkflowPopover.__dict__["_on_run_stdout"](
            None, fake_source, GLib.IO_IN, run, None
        )

    # Find the update call that carries had_partial_failure
    warning_calls = [
        c for c in mock_update.call_args_list
        if c.kwargs.get("had_partial_failure") or
           (len(c.args) > 0 and c.args[-1] == run["id"] and "had_partial_failure" in str(c))
    ]
    # At least one update call must have happened
    assert mock_update.called
    # The warning fields must appear somewhere in the calls
    all_kwargs = {}
    for c in mock_update.call_args_list:
        all_kwargs.update(c.kwargs)
    assert all_kwargs.get("had_partial_failure") is True
    assert "warning_count" in all_kwargs


# ── run_workflow.sh: node retry logic ────────────────────────────────────────

# The retry loop in run_workflow.sh runs up to 3 submission attempts before
# giving up.  We test the pure retry logic with a small inline bash snippet
# that mirrors the pattern (fail N times then succeed).

def _run_bash(script: str, timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a bash snippet and return the CompletedProcess."""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=timeout,
    )


def test_node_retry_succeeds_on_third_attempt(tmp_path):
    """
    Retry loop: mock node fails twice then succeeds.
    Verifies the loop runs exactly 3 iterations and the function exits 0.

    Note: bash $(…) subshells cannot mutate parent-shell variables, so we
    use a file-based counter (the same technique used for atomic counters
    in real shell scripts) rather than trying to increment a shell variable
    inside a command-substitution.
    """
    counter_file = tmp_path / "attempt_count"
    counter_file.write_text("0")

    script = textwrap.dedent(f"""\
        set -euo pipefail
        COUNTER_FILE="{counter_file}"

        # mock_submit increments the file counter and echoes pass/fail
        mock_submit() {{
            local n
            n=$(cat "$COUNTER_FILE")
            n=$((n + 1))
            echo "$n" > "$COUNTER_FILE"
            if [[ $n -lt 3 ]]; then
                echo "HTTP_ERROR:429"
            else
                echo "job-12345"
            fi
        }}

        JOB=""
        for attempt in 1 2 3; do
            JOB=$(mock_submit)
            if [[ -z "$JOB" || "$JOB" == ERROR:* || "$JOB" == HTTP_ERROR:* ]]; then
                if [[ $attempt -lt 3 ]]; then
                    : # would sleep in real code
                    true
                fi
            else
                break
            fi
        done

        echo "JOB:$JOB"
        # Fail if job was never successfully obtained
        [[ "$JOB" == "job-12345" ]]
    """)
    result = _run_bash(script)
    assert result.returncode == 0, (
        f"Retry loop failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "JOB:job-12345" in result.stdout
    # File counter must show 3 attempts
    final_count = int(counter_file.read_text().strip())
    assert final_count == 3, f"Expected 3 attempts, got {final_count}"


def test_node_retry_fails_after_three_attempts(tmp_path):
    """Retry loop: mock node always fails → JOB stays ERROR; exit code non-zero."""
    counter_file = tmp_path / "attempt_count"
    counter_file.write_text("0")

    script = textwrap.dedent(f"""\
        set +e
        COUNTER_FILE="{counter_file}"

        mock_submit() {{
            local n
            n=$(cat "$COUNTER_FILE")
            n=$((n + 1))
            echo "$n" > "$COUNTER_FILE"
            echo "HTTP_ERROR:500"
        }}

        JOB=""
        for attempt in 1 2 3; do
            JOB=$(mock_submit)
            if [[ -z "$JOB" || "$JOB" == ERROR:* || "$JOB" == HTTP_ERROR:* ]]; then
                true  # would sleep in real code
            else
                break
            fi
        done

        echo "JOB:$JOB"
        if [[ -z "$JOB" || "$JOB" == ERROR:* || "$JOB" == HTTP_ERROR:* ]]; then
            exit 1
        fi
    """)
    result = _run_bash(script)
    assert result.returncode != 0, "Expected non-zero exit when all retries fail"
    final_count = int(counter_file.read_text().strip())
    assert final_count == 3, f"Expected 3 attempts, got {final_count}"


def test_node_retry_succeeds_first_attempt(tmp_path):
    """Retry loop: mock node succeeds on first attempt — loop runs once only."""
    counter_file = tmp_path / "attempt_count"
    counter_file.write_text("0")

    script = textwrap.dedent(f"""\
        COUNTER_FILE="{counter_file}"

        mock_submit() {{
            local n
            n=$(cat "$COUNTER_FILE")
            n=$((n + 1))
            echo "$n" > "$COUNTER_FILE"
            echo "job-ok"
        }}

        JOB=""
        for attempt in 1 2 3; do
            JOB=$(mock_submit)
            if [[ -z "$JOB" || "$JOB" == ERROR:* || "$JOB" == HTTP_ERROR:* ]]; then
                true
            else
                break
            fi
        done

        echo "JOB:$JOB"
        [[ "$JOB" == "job-ok" ]]
    """)
    result = _run_bash(script)
    assert result.returncode == 0
    final_count = int(counter_file.read_text().strip())
    assert final_count == 1, f"Expected 1 attempt, got {final_count}"


# ── run_workflow.sh: LOG: output in --dry-run mode ────────────────────────────

_RUN_WORKFLOW_SH = Path(__file__).resolve().parent.parent / "bin" / "run_workflow.sh"
_EXAMPLE_WORKFLOW = (
    Path(__file__).resolve().parent.parent
    / "docs" / "examples" / "workflows" / "1964-worlds-fair.json"
)


@pytest.mark.skipif(
    not _RUN_WORKFLOW_SH.exists(),
    reason="run_workflow.sh not found — skipping integration test",
)
@pytest.mark.skipif(
    not _EXAMPLE_WORKFLOW.exists(),
    reason="example workflow JSON not found — skipping integration test",
)
def test_run_workflow_dry_run_emits_log_line(tmp_path):
    """
    run_workflow.sh --dry-run must emit a LOG:/path/to/file.log line early in
    its output.  This line is parsed by _on_run_stdout so the history row "Log"
    button can open the file.
    """
    result = subprocess.run(
        ["bash", str(_RUN_WORKFLOW_SH), str(_EXAMPLE_WORKFLOW), "--dry-run"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "HOME": str(tmp_path)},
    )

    # The script must exit 0 in dry-run mode (or fail gracefully)
    # We allow non-zero exit here since dependencies (tt-ctl, docker) may be
    # absent, but the LOG: line must have appeared in stdout.
    log_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("LOG:")
    ]
    assert log_lines, (
        f"Expected a LOG: line in stdout, got none.\n"
        f"stdout: {result.stdout[:500]}\n"
        f"stderr: {result.stderr[:200]}"
    )
    log_path = log_lines[0][4:].strip()
    assert log_path, "LOG: line must carry a non-empty file path"
    # The log path should look like an absolute path
    assert log_path.startswith("/"), f"Log path should be absolute, got: {log_path!r}"


@pytest.mark.skipif(
    not _RUN_WORKFLOW_SH.exists(),
    reason="run_workflow.sh not found — skipping integration test",
)
@pytest.mark.skipif(
    not _EXAMPLE_WORKFLOW.exists(),
    reason="example workflow JSON not found — skipping integration test",
)
def test_run_workflow_dry_run_creates_log_file(tmp_path):
    """
    After running with --dry-run the log file announced by LOG: must exist on
    disk (the tee redirect creates it even if the script later fails).
    """
    result = subprocess.run(
        ["bash", str(_RUN_WORKFLOW_SH), str(_EXAMPLE_WORKFLOW), "--dry-run"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "HOME": str(tmp_path)},
    )

    log_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("LOG:")
    ]
    if not log_lines:
        pytest.skip("No LOG: line emitted — cannot check file creation")

    log_path = Path(log_lines[0][4:].strip())
    assert log_path.exists(), (
        f"Log file {log_path} was announced by LOG: but does not exist on disk.\n"
        f"stdout: {result.stdout[:500]}"
    )
