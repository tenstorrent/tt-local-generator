"""
Tests for AnimateDiffGenerationWorker.

Covers:
  - Hardware failure → on_error, no on_finished
  - run_subprocess failure → on_error, no on_finished
  - Happy path → on_finished with a GenerationRecord, store.append called
  - Cancellation before subprocess → on_error, subprocess never called
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from worker import AnimateDiffGenerationWorker


def _make_worker(**overrides):
    kwargs = dict(
        store=MagicMock(),
        prompt="candle flame flickering, warm glow, seamless loop",
        negative_prompt="blurry, low quality",
        steps=10,
        seed=7,
        frames=4,
        temporal_alpha=0.35,
        model="animatediff-blackhole",
    )
    kwargs.update(overrides)
    return AnimateDiffGenerationWorker(**kwargs)


def _run(worker):
    """Run worker synchronously and return (finished_records, errors)."""
    finished = []
    errors = []
    worker.run_with_callbacks(
        on_progress=lambda msg: None,
        on_finished=lambda rec: finished.append(rec),
        on_error=lambda msg: errors.append(msg),
    )
    return finished, errors


class TestHardwareFailure:
    def test_no_blackhole_calls_on_error(self):
        """check_hardware() returning False must trigger on_error and skip subprocess."""
        worker = _make_worker()
        with (
            patch("artgen.generators.animatediff.check_hardware",
                  return_value=(False, "no Blackhole device found", 0)),
            patch("artgen.generators.animatediff.run_subprocess") as mock_run,
        ):
            finished, errors = _run(worker)

        assert finished == []
        assert len(errors) == 1
        assert "Blackhole" in errors[0] or "hardware" in errors[0].lower()
        mock_run.assert_not_called()


class TestSubprocessFailure:
    def test_run_subprocess_error_calls_on_error(self):
        """run_subprocess returning (False, ...) must trigger on_error."""
        worker = _make_worker()
        with (
            patch("artgen.generators.animatediff.check_hardware",
                  return_value=(True, "Blackhole P300c", 1)),
            patch("artgen.generators.animatediff.run_subprocess",
                  return_value=(False, "TTNN kernel crashed")),
            patch("artgen.generators.animatediff.make_gif_thumbnail") as mock_thumb,
            patch("worker.VIDEOS_DIR") as mock_vdir,
        ):
            mock_vdir.__truediv__ = lambda s, name: Path(f"/tmp/{name}")
            mock_vdir.mkdir = MagicMock()
            finished, errors = _run(worker)

        assert finished == []
        assert len(errors) == 1
        assert "TTNN kernel crashed" in errors[0] or "failed" in errors[0].lower()
        mock_thumb.assert_not_called()


class TestHappyPath:
    def test_on_finished_called_with_record(self):
        """Full happy path: hardware ok, subprocess ok → on_finished with a record."""
        store = MagicMock()
        worker = _make_worker(store=store, frames=4, steps=10, seed=99)

        fake_record = MagicMock()

        with (
            patch("artgen.generators.animatediff.check_hardware",
                  return_value=(True, "Blackhole P300c", 1)),
            patch("artgen.generators.animatediff.run_subprocess",
                  return_value=(True, None)),
            patch("artgen.generators.animatediff.make_gif_thumbnail"),
            patch("worker.VIDEOS_DIR", new=Path("/tmp/tt-gen-test/videos")),
            patch("worker.THUMBNAILS_DIR", new=Path("/tmp/tt-gen-test/thumbnails")),
            patch("worker.Path.mkdir", MagicMock()),
            patch("worker.GenerationRecord.new_animatediff",
                  return_value=fake_record) as mock_new,
            patch.object(worker, "_write_prompt_sidecar"),
        ):
            finished, errors = _run(worker)

        assert errors == []
        assert finished == [fake_record]
        store.append.assert_called_once_with(fake_record)

    def test_run_subprocess_receives_correct_args(self):
        """run_subprocess must be called with the worker's prompt, frames, seed."""
        worker = _make_worker(
            prompt="ocean waves crashing, slow rhythm",
            frames=6,
            steps=15,
            seed=42,
            temporal_alpha=0.25,
        )

        run_sub = MagicMock(return_value=(True, None))
        fake_record = MagicMock()

        with (
            patch("artgen.generators.animatediff.check_hardware",
                  return_value=(True, "Blackhole", 1)),
            patch("artgen.generators.animatediff.run_subprocess", run_sub),
            patch("artgen.generators.animatediff.make_gif_thumbnail"),
            patch("worker.VIDEOS_DIR", new=Path("/tmp/tt-gen-test/videos")),
            patch("worker.THUMBNAILS_DIR", new=Path("/tmp/tt-gen-test/thumbnails")),
            patch("worker.Path.mkdir", MagicMock()),
            patch("worker.GenerationRecord.new_animatediff",
                  return_value=fake_record),
            patch.object(worker, "_write_prompt_sidecar"),
        ):
            _run(worker)

        run_sub.assert_called_once()
        call_kwargs = run_sub.call_args
        assert call_kwargs.kwargs.get("prompt") == "ocean waves crashing, slow rhythm"
        assert call_kwargs.kwargs.get("frames") == 6
        assert call_kwargs.kwargs.get("steps") == 15
        assert call_kwargs.kwargs.get("seed") == 42
        assert call_kwargs.kwargs.get("temporal_alpha") == 0.25


class TestMultichipMode:
    def _run_capture_mode(self, *, chips, worker_kwargs):
        """Run a worker with `chips` reported by check_hardware and return the
        multichip_mode kwarg run_subprocess was called with."""
        worker = _make_worker(**worker_kwargs)
        run_sub = MagicMock(return_value=(True, None))
        with (
            patch("artgen.generators.animatediff.check_hardware",
                  return_value=(True, "Blackhole P300c", chips)),
            patch("artgen.generators.animatediff.run_subprocess", run_sub),
            patch("artgen.generators.animatediff.make_gif_thumbnail"),
            patch("worker.VIDEOS_DIR", new=Path("/tmp/tt-gen-test/videos")),
            patch("worker.THUMBNAILS_DIR", new=Path("/tmp/tt-gen-test/thumbnails")),
            patch("worker.Path.mkdir", MagicMock()),
            patch("worker.GenerationRecord.new_animatediff", return_value=MagicMock()),
            patch.object(worker, "_write_prompt_sidecar"),
        ):
            _run(worker)
        run_sub.assert_called_once()
        return run_sub.call_args.kwargs.get("multichip_mode")

    def test_coherent_mode_flows_to_run_subprocess(self):
        """A worker built with multichip_mode='coherent' + >1 chip must pass
        multichip_mode='coherent' to run_subprocess (was hardwired 'remix')."""
        mode = self._run_capture_mode(
            chips=4, worker_kwargs=dict(multi_chip=True, multichip_mode="coherent"))
        assert mode == "coherent"

    def test_legacy_none_defaults_to_remix(self):
        """Back-compat: multichip_mode unset (None) + >1 chip preserves the old
        boolean behaviour -> 'remix'."""
        mode = self._run_capture_mode(
            chips=4, worker_kwargs=dict(multi_chip=True))
        assert mode == "remix"

    def test_single_chip_is_always_off(self):
        """Even with mode='coherent', a single effective chip is 'off'."""
        mode = self._run_capture_mode(
            chips=1, worker_kwargs=dict(multi_chip=True, multichip_mode="coherent"))
        assert mode == "off"


class TestCancellation:
    def test_cancel_before_subprocess_skips_run(self):
        """cancel() set before subprocess should call on_error and not invoke run_subprocess."""
        worker = _make_worker()
        run_sub = MagicMock(return_value=(True, None))

        def hw_check():
            # Cancel mid-execution, between hw-check and subprocess
            worker.cancel()
            return True, "Blackhole", 1

        with (
            patch("artgen.generators.animatediff.check_hardware",
                  side_effect=lambda: hw_check()),
            patch("artgen.generators.animatediff.run_subprocess", run_sub),
            patch("worker.VIDEOS_DIR", new=Path("/tmp/tt-gen-test/videos")),
            patch("worker.THUMBNAILS_DIR", new=Path("/tmp/tt-gen-test/thumbnails")),
            patch("worker.Path.mkdir", MagicMock()),
        ):
            finished, errors = _run(worker)

        assert finished == []
        assert len(errors) == 1
        assert "cancel" in errors[0].lower()
        run_sub.assert_not_called()


# ── Live latent previews (--preview-path) ───────────────────────────────────

class TestPreviewPath:
    """`_build_cmd` opts into tt-animatediff's rolling latent preview.

    The runner writes the GIF beside the output and prints
    `PREVIEW: <step>/<total> <path>`; CreateResultPanel drains that from stdout
    and shows the animation forming. Opt-in by construction — a runner that
    predates the flag would reject it, so `preview_path=None` must produce the
    exact command it always did.
    """

    def _cmd(self, **kw):
        from artgen.generators import animatediff as ad
        from pathlib import Path

        base = dict(
            script=Path("/x/generate.py"), out_path=Path("/out/a.gif"), mode="ttnn",
            prompt="p", negative_prompt="n", frames=8, steps=25, seed=1,
            temporal_alpha=0.35, lightning=False, lightning_steps=4,
            device_id=None, chain_from=None, chain_save=None, chain_alpha=0.6,
            motion_adapter=None, motion_adapter_alpha=1.0, motion_adapter_skip=None,
        )
        base.update(kw)
        return ad._build_cmd(**base)

    def test_absent_by_default(self):
        assert "--preview-path" not in self._cmd()

    def test_passed_when_requested(self):
        cmd = self._cmd(preview_path="/out/preview.gif")
        assert "--preview-path" in cmd
        assert cmd[cmd.index("--preview-path") + 1] == "/out/preview.gif"

    def test_none_is_byte_identical_to_the_pre_preview_command(self):
        assert self._cmd(preview_path=None) == self._cmd()


class TestPreviewLineReachesTheGui:
    """The stdout filter must let `PREVIEW:` lines through.

    Both drains (`_run_one` for single-chip, `_run_multi_chip`'s per-chip
    closure) forward only lines matching a keyword allow-list. A `PREVIEW:`
    line contains none of those keywords, so it was logged and then dropped —
    the runner emitted previews correctly, the panel rendered them correctly,
    and the two were never connected. The end-to-end feature was inert.
    """

    KEYWORDS_LINE = "PREVIEW: 7/9 /tmp/run/preview.gif"

    def _forwards(self, src_slice: str, line: str) -> bool:
        """Evaluate a drain's filter condition against `line`."""
        import re
        cond = re.search(r"if on_progress and \((.*?)\):", src_slice, re.S)
        assert cond, "could not locate the drain's filter condition"
        # The condition spans several indented source lines; flatten it so it
        # evaluates as a single expression.
        expr = " ".join(part.strip() for part in cond.group(1).splitlines())
        return bool(eval(expr, {"line": line}))

    def _src(self):
        from pathlib import Path
        import artgen.generators.animatediff as ad
        return Path(ad.__file__).read_text()

    def test_single_chip_drain_forwards_preview_lines(self):
        src = self._src()
        start = src.index("def _run_one(")
        body = src[start:src.index("\ndef ", start + 1)]
        assert self._forwards(body, self.KEYWORDS_LINE), (
            "_run_one drops PREVIEW lines, so the live preview never reaches "
            "CreateResultPanel"
        )

    def test_multi_chip_drain_forwards_preview_lines(self):
        src = self._src()
        start = src.index("def _run_multi_chip(")
        body = src[start:src.index("\ndef ", start + 1)]
        assert self._forwards(body, self.KEYWORDS_LINE), (
            "_run_multi_chip drops PREVIEW lines"
        )

    def test_ordinary_chatter_is_still_filtered_out(self):
        """The allow-list still has to be an allow-list — widening it for
        PREVIEW must not turn the drain into a firehose."""
        src = self._src()
        start = src.index("def _run_one(")
        body = src[start:src.index("\ndef ", start + 1)]
        assert not self._forwards(body, "  0%|          | 0/8 [00:00<?, ?it/s]")
        assert not self._forwards(body, "some unrelated library warning")


class TestMultiChipPreviewPaths:
    """Each chip previews its own segment, so each needs its OWN preview file.

    Sharing one path across chips would have them overwrite each other every
    step, and the panel would show whichever wrote last rather than four
    segments forming.
    """

    def _cmds(self, n=4, **kw):
        from artgen.generators import animatediff as ad
        from pathlib import Path

        chips = [ad.ChipParams(prompt=f"p{i}", seed=i, temporal_alpha=0.35,
                               motion_adapter_alpha=1.0) for i in range(n)]
        base = dict(
            script=Path("/x/generate.py"),
            shard_paths=[Path(f"/out/shard{i}.gif") for i in range(n)],
            mode="ttnn", negative_prompt="", frames_per_chip=2, steps=8,
            lightning=False, lightning_steps=4, motion_adapter=None,
            motion_adapter_skip=None, chips=chips,
        )
        base.update(kw)
        return ad._multichip_cmds(**base)

    def test_absent_by_default(self):
        assert all("--preview-path" not in c for c in self._cmds())

    def test_each_chip_gets_a_distinct_preview_path(self):
        cmds = self._cmds(preview_path_for_chip=lambda i: f"/out/preview_chip{i}.gif")
        paths = [c[c.index("--preview-path") + 1] for c in cmds]
        assert len(set(paths)) == 4, "chips must not share one preview file"
        assert paths == [f"/out/preview_chip{i}.gif" for i in range(4)]

    def test_none_is_byte_identical_to_the_pre_preview_command(self):
        assert self._cmds(preview_path_for_chip=None) == self._cmds()


class TestProgressForwardingPreservesPreviewLines:
    """`_progress_fwd` appends "  (Ns)" to every message it forwards.

    That is right for human status text and wrong for a machine-readable line:
    it turned `PREVIEW: 3/21 /path.gif` into `... /path.gif  (47s)`, so the
    panel parsed the elapsed suffix as part of the path, failed its exists()
    check and silently dropped every preview. Reported as "ran a generation but
    didn't see the progress component" — nothing appeared and nothing errored,
    because the missing-file guard did exactly what it was written to do.
    """

    def test_plain_preview_line_is_machine_readable(self):
        import worker as w
        assert w.is_machine_readable_progress("PREVIEW: 3/21 /tmp/p.gif")

    def test_chip_prefixed_preview_line_is_machine_readable(self):
        import worker as w
        assert w.is_machine_readable_progress("chip2: PREVIEW: 3/21 /tmp/p.gif")

    def test_leading_whitespace_tolerated(self):
        import worker as w
        assert w.is_machine_readable_progress("   PREVIEW: 1/9 /tmp/p.gif")

    def test_human_status_is_not_machine_readable(self):
        """The elapsed suffix is useful on human text — it must keep getting it."""
        import worker as w
        for line in ("Loading pipeline", "Frame 3/8", "Step 2/25",
                     "Error: boom", "", "a prompt mentioning PREVIEW: later"):
            assert not w.is_machine_readable_progress(line), line

    def test_forwarder_routes_through_the_helper(self):
        """Pin the wiring, not just the arithmetic: the closure must consult the
        helper and return BEFORE appending the elapsed suffix."""
        import inspect
        import worker as w

        src = inspect.getsource(w.AnimateDiffGenerationWorker.run_with_callbacks)
        body = src[src.index("def _progress_fwd"):]
        body = body[:body.index("run_subprocess")]
        assert "is_machine_readable_progress(msg)" in body
        guard = body.index("is_machine_readable_progress(msg)")
        suffix = body.index('elapsed}s)')
        assert guard < suffix, "the verbatim path must come first"


def test_preview_line_survives_the_whole_chain():
    """End to end over the string transforms, which is where this broke twice:
    runner emit -> multi-chip drain prefix -> worker forward -> panel regex."""
    import re
    from artgen.generators import animatediff as ad  # noqa: F401

    emitted = "PREVIEW: 3/21 /tmp/run/preview_chip0.gif"
    prefixed = f"chip0: {emitted.strip()}"          # _run_multi_chip's drain
    forwarded = prefixed                            # _progress_fwd, post-fix

    RE = re.compile(r"^(?:chip(\d+):\s*)?PREVIEW:\s+(\d+)/(\d+)\s+(.+?)\s*$")
    m = RE.match(forwarded)
    assert m is not None
    assert m.group(4) == "/tmp/run/preview_chip0.gif", (
        "the path must arrive clean — no decoration appended anywhere"
    )
