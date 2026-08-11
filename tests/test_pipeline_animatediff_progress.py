"""Task 2 (pipeline Stage 'making-of'): stream AnimateDiff per-chip lines.

`_run_tt_ctl` used `subprocess.run(capture_output=True)` unconditionally, so
the `tt-ctl artgen animatediff` child's stdout — including the `  chipN:
Step 5/25` progress lines `animatediff._make_drain`'s `on_progress` writes —
was captured and discarded. A pipeline run's live view never saw per-chip
progress. Fixed by giving `_run_tt_ctl` an optional `emit` callback that
streams the child's stdout line-by-line instead of capturing it; `_h_animatediff`
passes an `emit` that re-emits each non-blank line into the run stream as a
`LOG:` line via `ctx.emit` (mirroring every other handler's `ctx.emit("LOG: ...")`
calls), which `PipelineRunner._watch_stdout` forwards verbatim to `on_log`.

Every other `_run_tt_ctl` caller (e.g. `TTLGArtgenGenerate`) never passes
`emit`, so it keeps the original capture-and-return behavior byte-for-byte —
covered here by `test_run_tt_ctl_without_emit_is_unchanged`.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pipeline_engine as pe
from unittest.mock import patch, MagicMock


class _FakeProc:
    """Stands in for a Popen handle whose stdout is an iterable of lines."""

    def __init__(self, lines):
        self.stdout = iter(lines)
        self.returncode = 0

    def wait(self):
        return 0


def test_run_tt_ctl_streams_lines_to_emit():
    """With an emit callback, _run_tt_ctl forwards each child stdout line
    (so a pipeline AnimateDiff step's chipN: lines reach the run stream)."""
    seen = []
    lines = ["Starting AnimateDiff on 2 chips\n", "  chip0: Step 5/25\n", "  chip1: Step 6/25\n"]
    with patch.object(pe.subprocess, "Popen", return_value=_FakeProc(lines)):
        pe._run_tt_ctl(["artgen", "animatediff"], timeout=10, emit=lambda s: seen.append(s))
    assert any("chip0: Step 5/25" in s for s in seen)
    assert any("chip1: Step 6/25" in s for s in seen)


def test_run_tt_ctl_streaming_raises_on_nonzero_exit():
    """Nonzero exit on the streaming path still raises RuntimeError, same
    contract as the capture-and-return path."""
    class _FailProc(_FakeProc):
        def wait(self):
            return 1

    with patch.object(pe.subprocess, "Popen", return_value=_FailProc(["oops\n"])):
        try:
            pe._run_tt_ctl(["artgen", "animatediff"], emit=lambda s: None)
        except RuntimeError as exc:
            assert "exit 1" in str(exc)
        else:
            raise AssertionError("expected RuntimeError on nonzero exit")


def test_run_tt_ctl_without_emit_is_unchanged():
    """Default (emit=None) keeps the capture-and-return behavior other callers rely on."""
    with patch.object(pe.subprocess, "run",
                      return_value=MagicMock(returncode=0, stdout="ok", stderr="")):
        out = pe._run_tt_ctl(["servers"], timeout=5)
        assert out.returncode == 0


def test_run_tt_ctl_without_emit_still_raises_with_original_message():
    """Non-emit failure path keeps the exact existing error-message contract."""
    with patch.object(pe.subprocess, "run",
                      return_value=MagicMock(returncode=2, stdout="out-text", stderr="err-text")):
        try:
            pe._run_tt_ctl(["servers"], timeout=5)
        except RuntimeError as exc:
            msg = str(exc)
            assert "servers" in msg
            assert "exit 2" in msg
            assert "err-text" in msg
        else:
            raise AssertionError("expected RuntimeError on nonzero exit")


def test_h_animatediff_emit_forwards_chip_lines_as_log(monkeypatch, tmp_path):
    """_h_animatediff wires _run_tt_ctl's emit to ctx.emit, prefixed LOG:,
    skipping blank lines — this is what makes chipN: progress visible to
    PipelineRunner/LiveRunView during a real (non-dry-run) pipeline step."""
    captured = {}

    def fake_run_tt_ctl(argv, timeout=600, emit=None):
        captured["argv"] = argv
        captured["emit"] = emit
        # Simulate the child process emitting a blank line and two chip lines.
        if emit is not None:
            emit("")
            emit("  chip0: Step 5/25")
            emit("  chip1: Step 6/25")

    monkeypatch.setattr(pe, "_run_tt_ctl", fake_run_tt_ctl)

    emitted = []

    class _Ctx:
        dry_run = False
        output_dir = tmp_path

        def emit(self, s):
            emitted.append(s)

    out = pe.HANDLERS["TTLGAnimateDiff"]("30", {"prompt": "a walk"}, _Ctx())

    assert out["gif_path"] == str(tmp_path / "node30.gif")
    assert emitted == ["LOG:  chip0: Step 5/25", "LOG:  chip1: Step 6/25"]
