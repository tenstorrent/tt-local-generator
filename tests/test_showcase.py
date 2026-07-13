"""Tests for showcase.py — turning a finished RunView into a self-contained,
shareable HTML showcase (results + the pipeline recipe).

`build_showcase_html` is pure: given a RunView and an injected `encode_asset`
callable, it never touches disk itself. These tests exercise it with a FAKE
encoder so no PIL / real files are needed for the RED/core assertions. A
handful of separate tests at the bottom exercise the real `default_encode_asset`
against the repo's tiny real PNG fixture (the only place PIL/disk is involved).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import showcase  # noqa: E402
from intent_vocab import Intent  # noqa: E402
from pipeline_view_model import RunView, StepView  # noqa: E402

_FIX_PNG = str(Path(__file__).parent / "fixtures" / "sp_c_run" / "node1_image.png")

# ── Fixture Intents (constructed directly so tests don't depend on the
#    INTENTS registry's exact model_label choices) ────────────────────────────

_IMAGE_INTENT = Intent(
    class_type="TTLGTextToImage", verb="Generate", noun="an image", icon="🖼️",
    outputs=("image_path",), model_label="FLUX",
    input_key="prompt", input_kind="text", output_kind="image",
)
_TEXT_INTENT = Intent(
    class_type="TTLGGenerateText", verb="Write", noun="about it", icon="✍️",
    outputs=("text",), model_label="Llama",
    input_key="caption", input_kind="text", output_kind="text",
)
_PENDING_INTENT = Intent(
    class_type="TTLGEstimateDepth", verb="Read", noun="its depth", icon="🗺️",
    outputs=("depth_path",), model_label=None,
    input_key="src", input_kind="image", output_kind="image",
)


def _make_run_view(**overrides) -> RunView:
    steps = [
        StepView(node_id="1", intent=_IMAGE_INTENT, status="done", artifact_path=_FIX_PNG),
        StepView(node_id="2", intent=_TEXT_INTENT, status="done", artifact_path="/fake/text/node2.txt"),
        StepView(node_id="3", intent=_PENDING_INTENT, status="pending", artifact_path=None),
    ]
    run_view = RunView(
        run_id="22222222-2222-2222-2222-222222222222",
        title="1964 World's Fair",
        created_at="2026-07-11T12:00:00+00:00",
        hero_path=_FIX_PNG,
        steps=steps,
        recipe=["Generate an image", "Write about it", "Read its depth"],
    )
    for key, value in overrides.items():
        setattr(run_view, key, value)
    return run_view


_FAKE_TEXT_INLINE = "A gleaming silver sphere against a bright blue sky."


def _fake_encode_asset(path, kind, max_px=1000):
    """Deterministic fake encoder — no PIL, no disk I/O.

    Accepts (and ignores) the `max_px` hero/gallery size-cap argument so this
    fake stays a drop-in for `build_showcase_html`, which now always passes it
    (as a keyword) to distinguish hero-size vs gallery-thumbnail encoding.
    """
    if path == _FIX_PNG and kind == "image":
        return "data:image/png;base64,QUJD"
    if path == "/fake/text/node2.txt" and kind == "text":
        return _FAKE_TEXT_INLINE
    return None  # covers the pending step's absent artifact, and anything unknown


_IMAGE_INTENT_2 = Intent(
    class_type="TTLGEstimateDepth", verb="Read", noun="its depth map", icon="🗺️",
    outputs=("depth_path",), model_label="MiDaS",
    input_key="src", input_kind="image", output_kind="image",
)


# ── build_showcase_html ───────────────────────────────────────────────────────

def test_contains_title():
    html = showcase.build_showcase_html(_make_run_view(), encode_asset=_fake_encode_asset)
    assert "1964 World" in html
    assert "<title>" in html


def test_contains_all_recipe_labels():
    run_view = _make_run_view()
    html = showcase.build_showcase_html(run_view, encode_asset=_fake_encode_asset)
    for step_label in run_view.recipe:
        assert step_label in html


def test_embeds_fake_image_data_uri():
    html = showcase.build_showcase_html(_make_run_view(), encode_asset=_fake_encode_asset)
    assert "data:image/png;base64,QUJD" in html


def test_embeds_fake_text_inline():
    html = showcase.build_showcase_html(_make_run_view(), encode_asset=_fake_encode_asset)
    assert _FAKE_TEXT_INLINE in html


def test_pending_step_renders_as_placeholder_not_fabricated():
    html = showcase.build_showcase_html(_make_run_view(), encode_asset=_fake_encode_asset)
    # The pending step's intent label must still appear (honestly listed)...
    assert "Read its depth" in html
    # ...but never alongside a fabricated data URI for it — its only legitimate
    # artifact source (encode_asset) returned None for this step.
    assert "placeholder" in html.lower()


def test_no_external_references():
    html = showcase.build_showcase_html(_make_run_view(), encode_asset=_fake_encode_asset)
    lowered = html.lower()
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "//cdn" not in lowered
    assert "fonts.googleapis" not in lowered


def test_footer_credits_tt_local_generator():
    html = showcase.build_showcase_html(_make_run_view(), encode_asset=_fake_encode_asset)
    assert "tt-local-generator" in html
    assert "Tenstorrent" in html


def test_hero_asset_embedded_exactly_once():
    """The hero step's own step also appears first in `run_view.steps`, so a
    naive gallery loop over ALL steps re-encodes/re-embeds the same asset a
    second time. For a video hero this doubles the largest payload in the
    page. The hero figure is the one and only place that data URI belongs.
    """
    html = showcase.build_showcase_html(_make_run_view(), encode_asset=_fake_encode_asset)
    assert html.count("data:image/png;base64,QUJD") == 1


def test_gallery_calls_encoder_with_smaller_max_px_than_hero():
    """Plan: hero embeds at ~1000px, gallery thumbnails at ~680px. Use a spy
    encoder to capture the `max_px` the builder requests for each call — the
    first call (the hero) must ask for the larger size; every later call (the
    gallery tiles) must ask for the smaller one.
    """
    calls = []

    def spy_encode(path, kind, max_px=1000):
        calls.append({"path": path, "kind": kind, "max_px": max_px})
        if kind == "image":
            return "data:image/png;base64,QUJD"
        if kind == "text":
            return _FAKE_TEXT_INLINE
        return None

    run_view = _make_run_view()
    # A second, non-hero image step so there's a real gallery image encode
    # call to compare against the hero's — not just the text snippet.
    run_view.steps.append(
        StepView(node_id="5", intent=_IMAGE_INTENT_2, status="done", artifact_path=_FIX_PNG)
    )

    showcase.build_showcase_html(run_view, encode_asset=spy_encode)

    assert len(calls) == 3  # hero + gallery-text + gallery-image
    assert calls[0]["max_px"] == 1000  # hero, encoded first
    assert all(c["max_px"] == 680 for c in calls[1:])  # every gallery tile


def test_failed_step_also_renders_as_honest_placeholder():
    run_view = _make_run_view()
    run_view.steps.append(
        StepView(node_id="4", intent=_PENDING_INTENT, status="failed", artifact_path=None)
    )
    html = showcase.build_showcase_html(run_view, encode_asset=_fake_encode_asset)
    assert "placeholder" in html.lower()


# ── write_showcase ────────────────────────────────────────────────────────────

def test_write_showcase_creates_file_and_returns_path(tmp_path):
    run_view = _make_run_view()
    dest_dir = tmp_path / "showcases"
    path = showcase.write_showcase(run_view, dest_dir, encode_asset=_fake_encode_asset)
    assert Path(path).is_file()
    assert Path(path).parent == dest_dir
    content = Path(path).read_text(encoding="utf-8")
    assert "1964 World" in content


def test_write_showcase_is_collision_safe(tmp_path):
    run_view = _make_run_view()
    dest_dir = tmp_path / "showcases"
    path1 = showcase.write_showcase(run_view, dest_dir, encode_asset=_fake_encode_asset)
    path2 = showcase.write_showcase(run_view, dest_dir, encode_asset=_fake_encode_asset)
    assert path1 != path2
    assert Path(path1).is_file()
    assert Path(path2).is_file()


# ── default_encode_asset (the one impure function — real PIL, real files) ────

def test_default_encode_asset_image_returns_data_uri():
    uri = showcase.default_encode_asset(_FIX_PNG, "image")
    assert uri is not None
    assert uri.startswith("data:image/")
    assert ";base64," in uri


def test_default_encode_asset_missing_file_returns_none():
    assert showcase.default_encode_asset("/no/such/file.png", "image") is None


def test_default_encode_asset_text_reads_file(tmp_path):
    text_path = tmp_path / "note.txt"
    text_path.write_text("hello showcase", encoding="utf-8")
    assert showcase.default_encode_asset(str(text_path), "text") == "hello showcase"


def test_default_encode_asset_none_path_returns_none():
    assert showcase.default_encode_asset(None, "image") is None


# ── Fan-out: a step with multiple artifacts renders a tile per still ──────────

def test_fanout_step_renders_a_tile_per_artifact():
    from pipeline_view_model import StepView, RunView
    paths = ["/fake/series/a0.png", "/fake/series/a1.png", "/fake/series/a2.png"]
    step = StepView(node_id="1", intent=_IMAGE_INTENT, status="done",
                    artifact_path=paths[0], artifact_paths=tuple(paths))
    run_view = RunView(run_id="r", title="A Series", created_at="",
                       hero_path=paths[0], steps=[step], recipe=["Generate an image"])

    def _echo_encode(path, kind, max_px=1000):
        return f"data:image/png;base64,{path}"   # echo the path so we can count embeds

    html = showcase.build_showcase_html(run_view, encode_asset=_echo_encode)
    # every fan-out still is embedded somewhere on the page...
    for p in paths:
        assert f"data:image/png;base64,{p}" in html
    # ...and the hero still (a0) is embedded exactly once (hero, not hero+gallery)
    assert html.count(f"data:image/png;base64,{paths[0]}") == 1
