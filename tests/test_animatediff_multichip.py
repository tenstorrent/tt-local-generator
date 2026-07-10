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
