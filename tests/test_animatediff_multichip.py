"""Unit tests for multi-chip AnimateDiff plan builders, cmd-builder, autovary,
stitch, and mode routing. Pure logic — no hardware, no GTK."""
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
_AD = Path(__file__).parent.parent / "app" / "artgen" / "generators" / "animatediff.py"


def _load():
    spec = importlib.util.spec_from_file_location("ad_gen", _AD)
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules *before* exec_module. Required for `@dataclass`
    # classes to work under `from __future__ import annotations`: dataclasses
    # resolves forward-ref annotations via sys.modules[cls.__module__], which
    # is None (AttributeError) if the module was never registered there.
    sys.modules["ad_gen"] = mod
    spec.loader.exec_module(mod)
    return mod


ad = _load()


class TestRemixPlan:
    def test_seed_spread_and_prompt_inheritance(self):
        plan = ad.build_remix_plan(
            base_prompt="koi pond", base_seed=42,
            base_temporal_alpha=0.35, base_motion_alpha=1.0,
            num_chips=4, per_chip_prompts=["koi dawn", "", None, "koi storm"],
            seed_spread=1, ramp="none",
        )
        assert [c.seed for c in plan] == [42, 43, 44, 45]
        assert [c.prompt for c in plan] == ["koi dawn", "koi pond", "koi pond", "koi storm"]
        assert all(c.temporal_alpha == 0.35 for c in plan)

    def test_seed_spread_zero_gives_identical_seeds(self):
        plan = ad.build_remix_plan(
            base_prompt="x", base_seed=7, base_temporal_alpha=0.3,
            base_motion_alpha=1.0, num_chips=3, seed_spread=0, ramp="none",
        )
        assert [c.seed for c in plan] == [7, 7, 7]

    def test_temporal_ramp_interpolates_across_chips(self):
        plan = ad.build_remix_plan(
            base_prompt="x", base_seed=1, base_temporal_alpha=0.3,
            base_motion_alpha=1.0, num_chips=4, ramp="temporal",
            ramp_lo=0.0, ramp_hi=0.9,
        )
        vals = [round(c.temporal_alpha, 3) for c in plan]
        assert vals == [0.0, 0.3, 0.6, 0.9]        # linspace(0,0.9,4)
        assert all(c.motion_adapter_alpha == 1.0 for c in plan)  # untouched

    def test_motion_ramp_targets_motion_alpha(self):
        plan = ad.build_remix_plan(
            base_prompt="x", base_seed=1, base_temporal_alpha=0.3,
            base_motion_alpha=1.0, num_chips=3, ramp="motion",
            ramp_lo=0.2, ramp_hi=1.0,
        )
        assert [round(c.motion_adapter_alpha, 3) for c in plan] == [0.2, 0.6, 1.0]
        assert all(c.temporal_alpha == 0.3 for c in plan)


class TestStitch:
    @pytest.fixture(autouse=True)
    def _need_pil(self):
        # CI runs without Pillow; these stitch tests (and _stitch_gifs itself)
        # require it. Skip rather than hard-fail — matches the repo convention
        # for other Pillow-dependent tests (see test_forge_plugins.py).
        pytest.importorskip("PIL")

    def _make_gif(self, path, n, color, duration=80):
        """Build an n-frame GIF where every frame's center pixel is exactly
        `color` (what the tests check), but frames are not pixel-identical to
        each other. Pillow's GIF encoder silently merges consecutive frames
        that ARE pixel-identical (summing their duration into one frame), so
        an all-solid-color fixture would collapse to far fewer frames than
        requested — a stitching would never intentionally produce, but a
        naive fixture would. A distinct corner-pixel marker per frame index
        avoids that collapse without affecting the center-pixel assertions.
        """
        from PIL import Image
        frames = []
        for i in range(n):
            img = Image.new("RGB", (8, 8), color)
            img.putpixel((0, 0), (i * 37 % 256, i * 37 % 256, i * 37 % 256))
            frames.append(img)
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=duration, loop=0, format="GIF")

    def test_concatenates_in_order_and_preserves_duration(self, tmp_path):
        a = tmp_path / "a.gif"; b = tmp_path / "b.gif"; out = tmp_path / "out.gif"
        self._make_gif(a, 3, (200, 0, 0), duration=80)
        self._make_gif(b, 2, (0, 0, 200), duration=80)
        assert ad._stitch_gifs([a, b], out) is True
        from PIL import Image
        with Image.open(out) as img:
            assert img.n_frames == 5                       # 3 + 2, concatenated
            img.seek(0)
            assert img.info.get("duration") == 80          # duration preserved

    def test_interleave_round_robins_frames(self, tmp_path):
        a = tmp_path / "a.gif"; b = tmp_path / "b.gif"; out = tmp_path / "out.gif"
        self._make_gif(a, 2, (200, 0, 0), duration=80)   # red
        self._make_gif(b, 2, (0, 0, 200), duration=80)   # blue
        assert ad._stitch_gifs([a, b], out, interleave=True) is True
        from PIL import Image
        with Image.open(out) as img:
            assert img.n_frames == 4
            expected = [(200, 0, 0), (0, 0, 200), (200, 0, 0), (0, 0, 200)]
            for i, exp in enumerate(expected):
                img.seek(i)
                px = img.convert("RGB").getpixel((4, 4))
                assert px == exp, f"frame {i}: expected {exp}, got {px}"

    def test_interleave_unequal_lengths(self, tmp_path):
        a = tmp_path / "a.gif"; b = tmp_path / "b.gif"; out = tmp_path / "out.gif"
        self._make_gif(a, 3, (200, 0, 0), duration=80)   # red, 3 frames
        self._make_gif(b, 1, (0, 0, 200), duration=80)   # blue, 1 frame
        assert ad._stitch_gifs([a, b], out, interleave=True) is True
        from PIL import Image
        with Image.open(out) as img:
            assert img.n_frames == 4
            # order: A0, B0, A1, A2 (B exhausted after its single frame)
            expected = [(200, 0, 0), (0, 0, 200), (200, 0, 0), (200, 0, 0)]
            for i, exp in enumerate(expected):
                img.seek(i)
                px = img.convert("RGB").getpixel((4, 4))
                assert px == exp, f"frame {i}: expected {exp}, got {px}"


class TestMultichipCmds:
    def test_each_chip_gets_its_own_prompt_seed_and_device(self, tmp_path):
        chips = [
            ad.ChipParams(prompt="dawn", seed=42, temporal_alpha=0.1, motion_adapter_alpha=1.0),
            ad.ChipParams(prompt="storm", seed=43, temporal_alpha=0.9, motion_adapter_alpha=0.5),
        ]
        shards = [tmp_path / "s0.gif", tmp_path / "s1.gif"]
        cmds = ad._multichip_cmds(
            script=Path("generate.py"), shard_paths=shards, mode="blackhole",
            negative_prompt="blurry", frames_per_chip=8, steps=4,
            lightning=True, lightning_steps=4, motion_adapter=None,
            motion_adapter_skip=None, chips=chips,
        )
        assert len(cmds) == 2
        def val(cmd, flag):
            return cmd[cmd.index(flag) + 1]
        assert val(cmds[0], "--prompt") == "dawn"
        assert val(cmds[1], "--prompt") == "storm"
        assert val(cmds[0], "--seed") == "42"
        assert val(cmds[1], "--seed") == "43"
        assert val(cmds[0], "--device-id") == "0"
        assert val(cmds[1], "--device-id") == "1"
        assert val(cmds[0], "--temporal-alpha") == "0.1"
        assert val(cmds[1], "--temporal-alpha") == "0.9"


class TestCoherent:
    def test_segment_chain_flags(self):
        segs = ad.build_coherent_segments(num_segments=4, frames_per_segment=8, base_seed=42)
        assert [s["index"] for s in segs] == [0, 1, 2, 3]
        assert all(s["frames"] == 8 and s["seed"] == 42 for s in segs)
        assert segs[0]["chain_from"] is False and segs[0]["chain_save"] is True
        assert segs[1]["chain_from"] is True and segs[1]["chain_save"] is True
        assert segs[-1]["chain_from"] is True and segs[-1]["chain_save"] is False

    def test_single_segment_has_no_chaining(self):
        segs = ad.build_coherent_segments(num_segments=1, frames_per_segment=8, base_seed=1)
        assert segs[0]["chain_from"] is False and segs[0]["chain_save"] is False


class TestAutovary:
    def test_parses_n_lines(self):
        call_fn = lambda *a, **k: "koi at dawn\nkoi in a storm\nkoi at night\nkoi in fog\n"
        out = ad._autovary_prompts("koi pond", 4, call_fn)
        assert out == ["koi at dawn", "koi in a storm", "koi at night", "koi in fog"]

    def test_pads_when_model_returns_too_few(self):
        call_fn = lambda *a, **k: "only one line"
        out = ad._autovary_prompts("base", 3, call_fn)
        assert out == ["only one line", "base", "base"]

    def test_falls_back_to_base_on_error(self):
        def call_fn(*a, **k):
            raise RuntimeError("no LLM")
        assert ad._autovary_prompts("base", 4, call_fn) == ["base"] * 4


class TestModeRouting:
    def _common(self, monkeypatch):
        calls = {}
        monkeypatch.setattr(ad, "_run_multi_chip", lambda **k: calls.setdefault("remix", k) or (True, ""))
        monkeypatch.setattr(ad, "_run_coherent_chain", lambda **k: calls.setdefault("coherent", k) or (True, ""))
        monkeypatch.setattr(ad, "check_hardware", lambda: (True, "bh", 4))
        # run_subprocess early-returns if the tt-metal Python env is missing
        # (true in CI). Point it at a path that always exists so routing runs.
        monkeypatch.setattr(ad, "_PYTHON", Path(sys.executable))
        return calls

    def test_remix_routes_to_multi_chip_with_plan(self, monkeypatch, tmp_path):
        calls = self._common(monkeypatch)
        ad.run_subprocess(
            script=Path("g.py"), out_path=tmp_path / "o.gif", mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None,
            multichip_mode="remix", per_chip_prompts=["a", "b", "", ""], seed_spread=1,
        )
        assert "remix" in calls
        chips = calls["remix"]["chips"]
        assert [c.prompt for c in chips] == ["a", "b", "koi", "koi"]
        assert [c.seed for c in chips] == [42, 43, 44, 45]

    def test_remix_passes_interleave_from_stitch_order(self, monkeypatch, tmp_path):
        calls = self._common(monkeypatch)
        ad.run_subprocess(
            script=Path("g.py"), out_path=tmp_path / "o.gif", mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None,
            multichip_mode="remix", stitch_order="concatenate",
        )
        assert calls["remix"]["interleave"] is False

        calls2 = self._common(monkeypatch)
        ad.run_subprocess(
            script=Path("g.py"), out_path=tmp_path / "o2.gif", mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None,
            multichip_mode="remix", stitch_order="interleave",
        )
        assert calls2["remix"]["interleave"] is True

    def test_coherent_routes_to_chain(self, monkeypatch, tmp_path):
        calls = self._common(monkeypatch)
        ad.run_subprocess(
            script=Path("g.py"), out_path=tmp_path / "o.gif", mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None, multichip_mode="coherent",
        )
        assert "coherent" in calls
        assert calls["coherent"]["num_segments"] == 4
        # Coherent must be N×-longer, not the same length divided: each
        # segment renders the FULL requested frame count, so `frames` must
        # reach _run_coherent_chain unchanged (not frames // num_segments).
        assert calls["coherent"]["frames"] == 16

    def test_coherent_routes_even_when_frames_not_divisible_by_chips(self, monkeypatch, tmp_path):
        # The frames % num_chips == 0 guard is a REMIX-only constraint (remix
        # splits `frames` into per-chip shards). Coherent renders the full
        # frame count per segment, so it must route regardless of divisibility.
        calls = self._common(monkeypatch)
        ad.run_subprocess(
            script=Path("g.py"), out_path=tmp_path / "o.gif", mode="blackhole",
            prompt="koi", negative_prompt="", frames=15, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None, multichip_mode="coherent",
        )
        assert "coherent" in calls
        assert calls["coherent"]["num_segments"] == 4
        assert calls["coherent"]["frames"] == 15

    def test_remix_falls_back_to_single_chip_when_frames_not_divisible(self, monkeypatch, tmp_path):
        # Remix DOES require divisibility — non-divisible frames must fall
        # through to the single-chip path instead of routing to _run_multi_chip.
        calls = self._common(monkeypatch)
        monkeypatch.setattr(ad, "_run_one", lambda *a, **k: (True, ""))
        ad.run_subprocess(
            script=Path("g.py"), out_path=tmp_path / "o.gif", mode="blackhole",
            prompt="koi", negative_prompt="", frames=15, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None, multichip_mode="remix",
        )
        assert "remix" not in calls and "coherent" not in calls

    def test_off_does_not_route_multichip(self, monkeypatch, tmp_path):
        calls = self._common(monkeypatch)
        # single-chip path will try to Popen; patch _run_one to avoid real exec
        monkeypatch.setattr(ad, "_run_one", lambda *a, **k: (True, ""))
        ad.run_subprocess(
            script=Path("g.py"), out_path=tmp_path / "o.gif", mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None, multichip_mode="off",
        )
        assert "remix" not in calls and "coherent" not in calls


class TestBuildCmdPromptSchedule:
    """Task 6c Part A: _build_cmd must forward prompt_schedule keyframes as
    repeated --prompt-schedule FRAME:PROMPT args, in declaration order."""

    def _base_kwargs(self, tmp_path):
        return dict(
            script=Path("generate.py"), out_path=tmp_path / "o.gif", mode="blackhole",
            prompt="koi", negative_prompt="blurry", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, lightning=False, lightning_steps=4, device_id=None,
            chain_from=None, chain_save=None, chain_alpha=0.6,
            motion_adapter=None, motion_adapter_alpha=1.0, motion_adapter_skip=None,
        )

    def test_prompt_schedule_appends_flag_per_keyframe_in_order(self, tmp_path):
        cmd = ad._build_cmd(
            **self._base_kwargs(tmp_path),
            prompt_schedule=[(0, "spring meadow"), (16, "snowfall")],
        )
        idxs = [i for i, a in enumerate(cmd) if a == "--prompt-schedule"]
        assert len(idxs) == 2
        assert cmd[idxs[0] + 1] == "0:spring meadow"
        assert cmd[idxs[1] + 1] == "16:snowfall"
        assert idxs[0] < idxs[1]        # declaration order preserved

    def test_no_prompt_schedule_flag_when_none(self, tmp_path):
        cmd = ad._build_cmd(**self._base_kwargs(tmp_path), prompt_schedule=None)
        assert "--prompt-schedule" not in cmd

    def test_prompt_schedule_defaults_to_none(self, tmp_path):
        # Omitting the kwarg entirely must behave exactly like passing None.
        cmd = ad._build_cmd(**self._base_kwargs(tmp_path))
        assert "--prompt-schedule" not in cmd


class TestMultichipCmdsPromptSchedule:
    """Every chip in a remix multi-chip plan gets the SAME prompt_schedule —
    prompt travel is a time-axis feature within each chip's own frames;
    per-chip prompts remain the spatial lever and the two coexist."""

    def test_each_chip_gets_same_prompt_schedule(self, tmp_path):
        chips = [
            ad.ChipParams(prompt="dawn", seed=42, temporal_alpha=0.1, motion_adapter_alpha=1.0),
            ad.ChipParams(prompt="storm", seed=43, temporal_alpha=0.9, motion_adapter_alpha=0.5),
        ]
        shards = [tmp_path / "s0.gif", tmp_path / "s1.gif"]
        cmds = ad._multichip_cmds(
            script=Path("generate.py"), shard_paths=shards, mode="blackhole",
            negative_prompt="blurry", frames_per_chip=8, steps=4,
            lightning=False, lightning_steps=4, motion_adapter=None,
            motion_adapter_skip=None, chips=chips,
            prompt_schedule=[(0, "a"), (4, "b")],
        )
        assert len(cmds) == 2
        for cmd in cmds:
            idxs = [i for i, a in enumerate(cmd) if a == "--prompt-schedule"]
            assert len(idxs) == 2
            assert cmd[idxs[0] + 1] == "0:a"
            assert cmd[idxs[1] + 1] == "4:b"

    def test_no_prompt_schedule_flag_when_none(self, tmp_path):
        chips = [ad.ChipParams(prompt="dawn", seed=42, temporal_alpha=0.1, motion_adapter_alpha=1.0)]
        shards = [tmp_path / "s0.gif"]
        cmds = ad._multichip_cmds(
            script=Path("generate.py"), shard_paths=shards, mode="blackhole",
            negative_prompt="blurry", frames_per_chip=8, steps=4,
            lightning=False, lightning_steps=4, motion_adapter=None,
            motion_adapter_skip=None, chips=chips, prompt_schedule=None,
        )
        assert "--prompt-schedule" not in cmds[0]


class TestRunSubprocessPromptScheduleWiring:
    """End-to-end: run_subprocess's prompt_schedule param must reach the
    generate.py argv, for both the single-chip and remix multi-chip paths."""

    def test_single_chip_forwards_prompt_schedule_to_build_cmd(self, monkeypatch, tmp_path):
        captured = {}
        real_build_cmd = ad._build_cmd

        def fake_build_cmd(**kwargs):
            captured.update(kwargs)
            return real_build_cmd(**kwargs)

        monkeypatch.setattr(ad, "_build_cmd", fake_build_cmd)
        monkeypatch.setattr(ad, "_PYTHON", Path(sys.executable))

        out_path = tmp_path / "o.gif"

        def fake_run_one(cmd, **kwargs):
            out_path.write_bytes(b"GIF89a")
            return True, ""

        monkeypatch.setattr(ad, "_run_one", fake_run_one)

        ok, err = ad.run_subprocess(
            script=Path("g.py"), out_path=out_path, mode="blackhole",
            prompt="koi", negative_prompt="", frames=8, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=1, device_id=None, multichip_mode="off",
            prompt_schedule=[(0, "a"), (4, "b")],
        )
        assert ok, err
        assert captured["prompt_schedule"] == [(0, "a"), (4, "b")]

    def test_remix_forwards_prompt_schedule_to_multichip_cmds(self, monkeypatch, tmp_path):
        captured = {}
        real_multichip_cmds = ad._multichip_cmds

        def fake_multichip_cmds(**kwargs):
            captured.update(kwargs)
            return real_multichip_cmds(**kwargs)

        monkeypatch.setattr(ad, "_multichip_cmds", fake_multichip_cmds)

        class FakeProc:
            stdout = ()
            returncode = 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        monkeypatch.setattr(ad.subprocess, "Popen", lambda *a, **k: FakeProc())
        monkeypatch.setattr(ad, "_stitch_gifs", lambda *a, **k: True)

        out_path = tmp_path / "o.gif"
        out_path.write_bytes(b"GIF89a")  # pretend the stitch already produced this

        ok, err = ad.run_subprocess(
            script=Path("g.py"), out_path=out_path, mode="blackhole",
            prompt="koi", negative_prompt="", frames=8, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=2, device_id=None, multichip_mode="remix",
            prompt_schedule=[(0, "spring")],
        )
        assert ok, err
        assert captured["prompt_schedule"] == [(0, "spring")]


class TestSeamlessLoopCrossfade:
    """Task 6c Part B: _apply_seamless_loop crossfades a GIF's tail into its
    head so playback loops without a visible seam."""

    @pytest.fixture(autouse=True)
    def _need_pil(self):
        pytest.importorskip("PIL")

    def _make_gradient_gif(self, path, n, duration=80):
        """n frames, solid color per frame, red channel increasing linearly
        with frame index (0, 32, 64, ...) — a stand-in for a smoothly
        animating clip. Monotonic color lets us assert "moved toward frame 0"
        deterministically: blending any later frame toward an earlier one
        can only pull its red value down, never up.
        """
        from PIL import Image
        frames = []
        for i in range(n):
            frames.append(Image.new("RGB", (8, 8), (i * 32 % 256, 0, 0)))
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=duration, loop=0, format="GIF")

    def test_crossfade_preserves_frame_count_and_moves_tail_toward_head(self, tmp_path):
        from PIL import Image

        gif_path = tmp_path / "loop.gif"
        n = 8
        self._make_gradient_gif(gif_path, n)

        with Image.open(gif_path) as img:
            img.seek(0)
            first_before = img.convert("RGB").getpixel((4, 4))
            img.seek(n - 1)
            last_before = img.convert("RGB").getpixel((4, 4))
        diff_before = sum(abs(a - b) for a, b in zip(first_before, last_before))

        assert ad._apply_seamless_loop(gif_path, 2) is True

        with Image.open(gif_path) as img:
            assert img.n_frames == n           # frame count unchanged
            img.seek(0)
            first_after = img.convert("RGB").getpixel((4, 4))
            img.seek(n - 1)
            last_after = img.convert("RGB").getpixel((4, 4))
        diff_after = sum(abs(a - b) for a, b in zip(first_after, last_after))

        assert diff_after < diff_before    # last frame moved toward the first

    def test_returns_false_on_missing_file(self, tmp_path):
        assert ad._apply_seamless_loop(tmp_path / "does_not_exist.gif", 2) is False


class TestSeamlessLoopWiring:
    """loop="seamless" must trigger _apply_seamless_loop exactly once, right
    before each of run_subprocess's three success returns (single-chip,
    remix-stitched, coherent-stitched); loop="none" must not call it at all;
    and a crossfade failure must not turn a successful run into a failure."""

    def _stub_single_chip(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ad, "_PYTHON", Path(sys.executable))
        out_path = tmp_path / "o.gif"

        def fake_run_one(cmd, **kwargs):
            out_path.write_bytes(b"GIF89a")
            return True, ""

        monkeypatch.setattr(ad, "_run_one", fake_run_one)
        return out_path

    def test_single_chip_applies_seamless_loop_with_correct_k(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(ad, "_apply_seamless_loop", lambda p, k: calls.append((p, k)) or True)
        out_path = self._stub_single_chip(monkeypatch, tmp_path)

        ok, err = ad.run_subprocess(
            script=Path("g.py"), out_path=out_path, mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=1, device_id=None, multichip_mode="off",
            loop="seamless",
        )
        assert ok, err
        assert calls == [(out_path, 4)]     # k = max(1, min(4, 16 // 4)) == 4

    def test_single_chip_skips_seamless_loop_when_loop_none(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(ad, "_apply_seamless_loop", lambda p, k: calls.append((p, k)) or True)
        out_path = self._stub_single_chip(monkeypatch, tmp_path)

        ok, err = ad.run_subprocess(
            script=Path("g.py"), out_path=out_path, mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=1, device_id=None, multichip_mode="off",
            loop="none",
        )
        assert ok, err
        assert calls == []

    def test_seamless_loop_failure_is_non_fatal(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ad, "_apply_seamless_loop", lambda p, k: False)
        out_path = self._stub_single_chip(monkeypatch, tmp_path)

        ok, err = ad.run_subprocess(
            script=Path("g.py"), out_path=out_path, mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=1, device_id=None, multichip_mode="off",
            loop="seamless",
        )
        assert ok       # base GIF is still valid even if the crossfade failed

    def test_remix_applies_seamless_loop_on_stitched_output(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(ad, "_apply_seamless_loop", lambda p, k: calls.append((p, k)) or True)

        class FakeProc:
            stdout = ()
            returncode = 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        monkeypatch.setattr(ad.subprocess, "Popen", lambda *a, **k: FakeProc())
        monkeypatch.setattr(ad, "_stitch_gifs", lambda *a, **k: True)

        out_path = tmp_path / "o.gif"
        out_path.write_bytes(b"GIF89a")

        ok, err = ad.run_subprocess(
            script=Path("g.py"), out_path=out_path, mode="blackhole",
            prompt="koi", negative_prompt="", frames=8, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=2, device_id=None, multichip_mode="remix",
            loop="seamless",
        )
        assert ok, err
        assert calls == [(out_path, 2)]     # k = max(1, min(4, 8 // 4)) == 2

    def test_coherent_applies_seamless_loop_on_stitched_output(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(ad, "_apply_seamless_loop", lambda p, k: calls.append((p, k)) or True)
        monkeypatch.setattr(ad, "_run_one", lambda *a, **k: (True, ""))
        monkeypatch.setattr(ad, "_stitch_gifs", lambda *a, **k: True)

        out_path = tmp_path / "o.gif"
        out_path.write_bytes(b"GIF89a")

        ok, err = ad.run_subprocess(
            script=Path("g.py"), out_path=out_path, mode="blackhole",
            prompt="koi", negative_prompt="", frames=8, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None, multichip_mode="coherent",
            loop="seamless",
        )
        assert ok, err
        # total stitched frames = frames * num_segments = 8 * 4 = 32
        assert calls == [(out_path, 4)]     # k = max(1, min(4, 32 // 4)) == 4
