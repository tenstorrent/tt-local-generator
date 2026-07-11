import base64
import importlib.util
import io
import sys
import urllib.error
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
_ENGINE = Path(__file__).parent.parent / "app" / "pipeline_engine.py"

def _load():
    spec = importlib.util.spec_from_file_location("pipeline_engine", _ENGINE)
    m = importlib.util.module_from_spec(spec)
    # Register in sys.modules before exec so dataclasses defined in the module
    # (with `from __future__ import annotations`) can resolve their own module.
    sys.modules["pipeline_engine"] = m
    spec.loader.exec_module(m); return m

eng = _load()
_FIX = Path(__file__).parent / "fixtures" / "mini_pipeline.json"

def test_load_spec_strips_metadata_keeps_nodes():
    spec = eng.load_spec(str(_FIX))
    assert set(spec) == {"1", "2", "3"}
    assert spec["1"]["class_type"] == "TTLGTextToImage"

def test_topo_order_respects_wires():
    spec = eng.load_spec(str(_FIX))
    order = eng.topo_order(spec)
    assert order.index("1") < order.index("2") < order.index("3")

def test_topo_order_detects_cycle():
    spec = {"1": {"class_type": "X", "inputs": {"a": ["2", "k"]}},
            "2": {"class_type": "X", "inputs": {"a": ["1", "k"]}}}
    with pytest.raises(ValueError):
        eng.topo_order(spec)

def test_topo_order_detects_dangling_wire():
    spec = {"1": {"class_type": "X", "inputs": {"a": ["99", "k"]}}}
    with pytest.raises(ValueError):
        eng.topo_order(spec)

def test_resolve_inputs_substitutes_wires():
    results = {"1": {"image_path": "/tmp/x.png"}}
    out = eng.resolve_inputs({"caption": ["1", "image_path"], "lit": 5}, results)
    assert out == {"caption": "/tmp/x.png", "lit": 5}


# ── nested-wire support (Fix 1) ──────────────────────────────────────────────
# The 1964 spec's TTLGAddToPlaylist node nests wires inside a list-of-dicts
# ("artifacts") and a dict ("metadata"). These must be detected as deps by
# topo_order and resolved by resolve_inputs, just like top-level wires.

_NESTED_SPEC = {
    "1": {"class_type": "TTLGTextToImage", "inputs": {}},
    "2": {"class_type": "TTLGRemoveBackground", "inputs": {}},
    "3": {"class_type": "TTLGCaptionImage", "inputs": {}},
    # Node id "0" sorts alphabetically BEFORE its wired sources ("1", "2", "3").
    # Kahn's algorithm processes ready nodes in sorted order, so if the nested
    # wires below are not detected as deps, "0" would (wrongly) run first —
    # this makes the ordering assertion a genuine regression test rather than
    # one that happens to pass due to id ordering coincidence.
    "0": {"class_type": "TTLGAddToPlaylist", "inputs": {
        "playlist_name": "fair",
        "artifacts": [
            {"label": "a", "path": ["1", "image_path"], "type": "image"},
            {"label": "b", "path": ["2", "fg_path"], "type": "image"},
        ],
        "metadata": {"cap": ["3", "caption"]},
    }},
}


def test_topo_order_detects_nested_wire_deps():
    order = eng.topo_order(_NESTED_SPEC)
    for src in ("1", "2", "3"):
        assert order.index(src) < order.index("0")


def test_resolve_inputs_resolves_nested_wires():
    results = {
        "1": {"image_path": "/img1.png"},
        "2": {"fg_path": "/fg2.png"},
        "3": {"caption": "a caption"},
    }
    out = eng.resolve_inputs(_NESTED_SPEC["0"]["inputs"], results)
    assert out["playlist_name"] == "fair"
    assert out["artifacts"] == [
        {"label": "a", "path": "/img1.png", "type": "image"},
        {"label": "b", "path": "/fg2.png", "type": "image"},
    ]
    assert out["metadata"] == {"cap": "a caption"}
    # original inputs dict must not be mutated
    assert _NESTED_SPEC["0"]["inputs"]["artifacts"][0]["path"] == ["1", "image_path"]
    assert _NESTED_SPEC["0"]["inputs"]["metadata"]["cap"] == ["3", "caption"]

def test_dry_run_emits_signals_and_publishes_keys():
    spec = eng.load_spec(str(_FIX))
    lines = []
    results = eng.run(spec, dry_run=True, emit=lines.append)
    # every node ran and published its documented dry-run output
    assert "image_path" in results["1"]
    assert results["2"]["prompt"]           # PromptCompose fills prompt
    assert "text" in results["3"]
    # signals: a running+done per node, in topo order
    assert any(l == "NODE:1:running:" or l.startswith("NODE:1:running") for l in lines)
    assert any(l.startswith("NODE:3:done") for l in lines)


# ── Task 2: real node handlers (dry-run keys + mocked-helper real paths) ───────

def _ctx(tmp="/tmp/out", dry=False, emit=None):
    return eng._Ctx(Path(tmp), dry, emit or (lambda s: None))


# ---- TTLGTextToImage ----

# ── _media_image_request backoff fidelity (Fix 2) ────────────────────────────
# bin/run_workflow.sh:218-223 backs off 10s on ANY non-success submit outcome
# (empty response, missing id, HTTP error, exception) — not just exceptions.

def test_media_image_request_backs_off_on_missing_id(monkeypatch):
    sleeps = []
    monkeypatch.setattr(eng.time, "sleep", lambda s: sleeps.append(s))

    calls = {"post": 0}

    def fake_post(url, payload, timeout=30):
        calls["post"] += 1
        if calls["post"] == 1:
            return {}  # 200 OK but no "id" — should trigger backoff, not immediate retry
        return {"id": "job1"}

    monkeypatch.setattr(eng, "_post_json", fake_post)
    monkeypatch.setattr(eng, "_get_json", lambda url, timeout=30: {"status": "completed"})
    monkeypatch.setattr(eng, "_download", lambda url, out_path, timeout=300: None)

    out = eng._media_image_request(server="http://x", prompt="p", width=512, height=512,
                                    steps=4, seed=0, out_path="/tmp/out.png")
    assert out == "/tmp/out.png"
    assert calls["post"] == 2
    # The 10s backoff must have fired even though no exception was raised.
    assert sleeps.count(10) == 1


# ── Task 4b: media image/video endpoints are synchronous in v0.18.0 ─────────
#
# Confirmed on QB2 hardware: POST /v1/images/generations is SYNCHRONOUS — it
# returns {"images": ["<base64 JPEG>"]} inline (HTTP 200). There is no image
# status/download endpoint. The pre-fix code assumed resp["id"] existed and
# polled for it; with no "id" present it retried 3x then raised "image job
# submission failed after 3 attempts" — even though the sync response was a
# genuine, immediately-usable success. The unit tests that mocked the async
# contract validated a fiction and never caught this.

# A minimal valid 1x1 PNG, used as "the decoded bytes" for sync-response fixtures.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY"
    "42YAAAAASUVORK5CYII="
)


def test_media_image_request_sync_images_response_writes_bytes(monkeypatch, tmp_path):
    """RED against the pre-fix code: it reads resp.get('id'), finds nothing in
    a sync {"images": [...]} response, retries 3x, and raises — even though
    this is the real, successful v0.18.0 shape. No poll/download should occur."""
    b64 = base64.b64encode(_TINY_PNG).decode()
    monkeypatch.setattr(eng, "_post_json", lambda url, payload, timeout=30: {"images": [b64]})

    def _boom_get(*a, **k):
        raise AssertionError("_get_json must not be called for a sync response")

    def _boom_dl(*a, **k):
        raise AssertionError("_download must not be called for a sync response")

    monkeypatch.setattr(eng, "_get_json", _boom_get)
    monkeypatch.setattr(eng, "_download", _boom_dl)
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)

    out_path = str(tmp_path / "out.png")
    out = eng._media_image_request(server="http://x", prompt="p", width=512, height=512,
                                    steps=4, seed=0, out_path=out_path)
    assert out == out_path
    assert Path(out_path).read_bytes() == _TINY_PNG


def test_media_image_request_sync_strips_data_uri_prefix(monkeypatch, tmp_path):
    b64 = base64.b64encode(_TINY_PNG).decode()
    monkeypatch.setattr(
        eng, "_post_json",
        lambda url, payload, timeout=30: {"images": [f"data:image/png;base64,{b64}"]},
    )
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)

    out_path = str(tmp_path / "out.png")
    eng._media_image_request(server="http://x", prompt="p", width=512, height=512,
                              steps=4, seed=0, out_path=out_path)
    assert Path(out_path).read_bytes() == _TINY_PNG


def test_media_image_request_async_backcompat_still_polls_and_downloads(monkeypatch, tmp_path):
    """Older/other media servers that return {"id": ...} still poll + download."""
    monkeypatch.setattr(eng, "_post_json", lambda url, payload, timeout=30: {"id": "job1"})
    monkeypatch.setattr(eng, "_get_json", lambda url, timeout=30: {"status": "completed"})

    downloaded = {}

    def fake_download(url, out_path, timeout=300):
        downloaded["url"] = url
        Path(out_path).write_bytes(b"async-bytes")

    monkeypatch.setattr(eng, "_download", fake_download)
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)

    out_path = str(tmp_path / "out.png")
    out = eng._media_image_request(server="http://x", prompt="p", width=512, height=512,
                                    steps=4, seed=0, out_path=out_path)
    assert out == out_path
    assert Path(out_path).read_bytes() == b"async-bytes"
    assert downloaded["url"] == "http://x/v1/images/generations/job1/download"


def test_media_image_request_neither_images_nor_id_raises_with_response(monkeypatch):
    monkeypatch.setattr(eng, "_post_json",
                        lambda url, payload, timeout=30: {"detail": "bad request"})
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError) as exc:
        eng._media_image_request(server="http://x", prompt="p", width=512, height=512,
                                  steps=4, seed=0, out_path="/tmp/out.png")
    assert "bad request" in str(exc.value)


def test_media_image_request_surfaces_http_error_body(monkeypatch):
    """A 4xx/5xx from the submit POST must not be blanket-swallowed: its body
    should be visible in the final raised error."""
    def fake_post(url, payload, timeout=30):
        raise urllib.error.HTTPError(url, 422, "Unprocessable",
                                     {}, io.BytesIO(b'{"detail":"bad width"}'))

    monkeypatch.setattr(eng, "_post_json", fake_post)
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError) as exc:
        eng._media_image_request(server="http://x", prompt="p", width=512, height=512,
                                  steps=4, seed=0, out_path="/tmp/out.png")
    assert "bad width" in str(exc.value)


# ---- _media_video_request: sync vs async (shape unverified on hardware — see
# code comment in pipeline_engine.py; will be confirmed once SkyReels runs on
# QB2). Written defensively to mirror the confirmed image contract. ----

def test_media_video_request_sync_video_key_writes_bytes(monkeypatch, tmp_path):
    payload_bytes = b"fake-video-bytes"
    b64 = base64.b64encode(payload_bytes).decode()
    monkeypatch.setattr(eng, "_post_json", lambda url, payload, timeout=600: {"video": b64})
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)

    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG\r\n")
    out_path = str(tmp_path / "out.mp4")
    out = eng._media_video_request(server="http://x", model="m", prompt="p", image=str(src),
                                    width=960, height=544, num_frames=33, steps=20, seed=1,
                                    out_path=out_path)
    assert out == out_path
    assert Path(out_path).read_bytes() == payload_bytes


def test_media_video_request_async_backcompat_still_polls_and_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "_post_json", lambda url, payload, timeout=600: {"id": "job1"})
    monkeypatch.setattr(eng, "_get_json", lambda url, timeout=30: {"status": "completed"})

    def fake_download(url, out_path, timeout=300):
        Path(out_path).write_bytes(b"async-video")

    monkeypatch.setattr(eng, "_download", fake_download)
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)

    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG\r\n")
    out_path = str(tmp_path / "out.mp4")
    out = eng._media_video_request(server="http://x", model="m", prompt="p", image=str(src),
                                    width=960, height=544, num_frames=33, steps=20, seed=1,
                                    out_path=out_path)
    assert out == out_path
    assert Path(out_path).read_bytes() == b"async-video"


def test_media_video_request_neither_shape_raises_with_response(monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "_post_json",
                        lambda url, payload, timeout=600: {"detail": "no video for you"})
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)

    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG\r\n")
    with pytest.raises(RuntimeError) as exc:
        eng._media_video_request(server="http://x", model="m", prompt="p", image=str(src),
                                  width=960, height=544, num_frames=33, steps=20, seed=1,
                                  out_path=str(tmp_path / "out.mp4"))
    assert "no video for you" in str(exc.value)


def test_text_to_image_dry_run_key():
    out = eng.HANDLERS["TTLGTextToImage"]("1", {"prompt": "x"}, _ctx(dry=True))
    assert out["image_path"] == "/tmp/out/node1_image.png"


def test_text_to_image_builds_media_request(monkeypatch):
    calls = {}
    def fake(**k):
        calls["req"] = k
        return "/tmp/out.png"
    monkeypatch.setattr(eng, "_media_image_request", fake)
    out = eng.HANDLERS["TTLGTextToImage"]("1",
        {"model": "FLUX.1-schnell", "prompt": "x", "width": 1024, "height": 1024}, _ctx())
    assert out["image_path"] == "/tmp/out.png"
    assert calls["req"]["prompt"] == "x"
    assert calls["req"]["width"] == 1024


def test_text_to_image_forwards_negative_prompt(monkeypatch):
    calls = {}
    def fake(**k):
        calls["req"] = k
        return "/tmp/out.png"
    monkeypatch.setattr(eng, "_media_image_request", fake)
    eng.HANDLERS["TTLGTextToImage"]("1",
        {"prompt": "x", "negative_prompt": "blurry"}, _ctx())
    assert calls["req"]["negative_prompt"] == "blurry"


# ---- TTLGImageToVideo ----

def test_image_to_video_dry_run_key():
    out = eng.HANDLERS["TTLGImageToVideo"]("6", {"prompt": "p"}, _ctx(dry=True))
    assert out["video_path"] == "/tmp/out/node6_video.mp4"


def test_image_to_video_builds_media_request(monkeypatch):
    calls = {}
    def fake(**k):
        calls["req"] = k
        return "/tmp/v.mp4"
    monkeypatch.setattr(eng, "_media_video_request", fake)
    out = eng.HANDLERS["TTLGImageToVideo"]("6", {
        "model": "SkyReels", "prompt": "p", "image": "/tmp/a.png",
        "width": 960, "height": 544, "num_frames": 33, "steps": 20,
        "seed": 1964, "server": "http://x"}, _ctx())
    assert out["video_path"] == "/tmp/v.mp4"
    assert calls["req"]["prompt"] == "p"
    assert calls["req"]["image"] == "/tmp/a.png"
    assert calls["req"]["num_frames"] == 33
    assert calls["req"]["model"] == "SkyReels"


# ---- TTLGCaptionImage ----

def test_caption_dry_run_key():
    out = eng.HANDLERS["TTLGCaptionImage"]("2", {"src": "/tmp/a.png"}, _ctx(dry=True))
    assert "caption" in out


def test_caption_calls_plugin(monkeypatch):
    calls = {}
    def fake(*a):
        calls["args"] = a
        return "a caption"
    monkeypatch.setattr(eng, "_run_plugin", fake)
    out = eng.HANDLERS["TTLGCaptionImage"]("2",
        {"src": "/tmp/a.png", "prompt": "desc"}, _ctx())
    assert out["caption"] == "a caption"
    assert calls["args"] == ("blip", "caption_image", "/tmp/a.png", "desc")


# ---- TTLGRemoveBackground ----

def test_rmbg_dry_run_key():
    out = eng.HANDLERS["TTLGRemoveBackground"]("3", {"src": "/tmp/a.png"}, _ctx(dry=True))
    assert out["fg_path"] == "/tmp/out/node3_fg.png"


def test_rmbg_calls_plugin(monkeypatch):
    calls = {}
    monkeypatch.setattr(eng, "_run_plugin", lambda *a: calls.setdefault("args", a))
    out = eng.HANDLERS["TTLGRemoveBackground"]("3", {"src": "/tmp/a.png"}, _ctx())
    assert out["fg_path"] == "/tmp/out/node3_fg.png"
    assert calls["args"] == ("rmbg", "remove_background", "/tmp/a.png", "/tmp/out/node3_fg.png")


# ---- TTLGEstimateDepth ----

def test_depth_dry_run_key():
    out = eng.HANDLERS["TTLGEstimateDepth"]("4", {"src": "/tmp/a.png"}, _ctx(dry=True))
    assert out["depth_path"] == "/tmp/out/node4_depth.png"


def test_depth_calls_plugin(monkeypatch):
    calls = {}
    monkeypatch.setattr(eng, "_run_plugin", lambda *a: calls.setdefault("args", a))
    out = eng.HANDLERS["TTLGEstimateDepth"]("4", {"src": "/tmp/a.png"}, _ctx())
    assert out["depth_path"] == "/tmp/out/node4_depth.png"
    assert calls["args"] == ("depth", "estimate_depth", "/tmp/a.png", "/tmp/out/node4_depth.png")


# ---- TTLGGenerateText ----

def test_generate_text_dry_run_key():
    out = eng.HANDLERS["TTLGGenerateText"]("7", {"prompt": "p"}, _ctx(dry=True))
    assert "text" in out
    assert out["text"]


def test_generate_text_substitutes_and_calls_llm(monkeypatch):
    calls = {}
    def fake(**k):
        calls["req"] = k
        return "a poem"
    monkeypatch.setattr(eng, "_call_llm", fake)
    out = eng.HANDLERS["TTLGGenerateText"]("7", {
        "model": "llama", "prompt": "about {caption}", "caption": "unisphere",
        "max_tokens": 120, "server": "http://x"}, _ctx())
    assert out["text"] == "a poem"
    assert calls["req"]["prompt"] == "about unisphere"
    assert calls["req"]["model"] == "llama"
    assert calls["req"]["max_tokens"] == 120
    assert calls["req"]["server"] == "http://x"


def test_generate_text_does_not_substitute_reserved(monkeypatch):
    calls = {}
    def fake(**k):
        calls["req"] = k
        return "t"
    monkeypatch.setattr(eng, "_call_llm", fake)
    # {prompt}/{model} are reserved and must survive verbatim in the prompt text
    eng.HANDLERS["TTLGGenerateText"]("7", {
        "model": "llama", "prompt": "keep {model} and {caption}", "caption": "c"}, _ctx())
    assert calls["req"]["prompt"] == "keep {model} and c"


# ---- TTLGSVGRender ----

def test_svg_render_dry_run_key():
    out = eng.HANDLERS["TTLGSVGRender"]("5", {"src": "/tmp/a.svg"}, _ctx(dry=True))
    assert out["png_path"] == "/tmp/out/node5_logo.png"


def test_svg_render_calls_plugin(monkeypatch):
    calls = {}
    monkeypatch.setattr(eng, "_run_plugin", lambda *a: calls.setdefault("args", a))
    out = eng.HANDLERS["TTLGSVGRender"]("5", {"src": "/tmp/a.svg", "size": 512}, _ctx())
    assert out["png_path"] == "/tmp/out/node5_logo.png"
    assert calls["args"] == ("svg_render", "svg_to_png", "/tmp/a.svg",
                             "/tmp/out/node5_logo.png", 512)


# ---- TTLGComposite ----

def test_composite_dry_run_key():
    out = eng.HANDLERS["TTLGComposite"]("6",
        {"background_path": "/tmp/bg.png", "foreground_path": "/tmp/fg.png"}, _ctx(dry=True))
    assert out["image_path"] == "/tmp/out/node6_composite.jpg"


def test_composite_calls_plugin(monkeypatch):
    calls = {}
    monkeypatch.setattr(eng, "_run_plugin", lambda *a: calls.setdefault("args", a))
    out = eng.HANDLERS["TTLGComposite"]("6", {
        "background_path": "/tmp/bg.png", "foreground_path": "/tmp/fg.png",
        "scale": 0.5}, _ctx())
    assert out["image_path"] == "/tmp/out/node6_composite.jpg"
    assert calls["args"] == ("composite", "composite_images", "/tmp/bg.png",
                             "/tmp/fg.png", "/tmp/out/node6_composite.jpg", 0.5)


# ---- TTLGAddToPlaylist ----

def test_add_to_playlist_dry_run_key():
    out = eng.HANDLERS["TTLGAddToPlaylist"]("9", {"playlist_name": "x"}, _ctx(dry=True))
    assert "playlist_id" in out


def test_add_to_playlist_calls_helper(monkeypatch):
    calls = {}
    def fake(name, artifacts, metadata, emit):
        calls["args"] = (name, artifacts, metadata)
        return "pl-123"
    monkeypatch.setattr(eng, "_add_artifacts_to_playlist", fake)
    arts = [{"path": "/tmp/a.png", "type": "image"}]
    out = eng.HANDLERS["TTLGAddToPlaylist"]("9", {
        "playlist_name": "fair", "artifacts": arts, "metadata": {"poem": "p"}}, _ctx())
    assert out["playlist_id"] == "pl-123"
    assert calls["args"][0] == "fair"
    assert calls["args"][1] == arts
    assert calls["args"][2] == {"poem": "p"}


# ---- _run_plugin loader mechanism (real import, lightweight fn) ----

def test_run_plugin_loads_and_calls_real_module():
    res = eng._run_plugin("svg_render", "is_available")
    assert isinstance(res, bool)


# ---- bin/run_workflow.sh is a thin shim over the engine (Task 3) ----

def test_run_workflow_is_a_shim():
    sh = (Path(__file__).parent.parent / "bin" / "run_workflow.sh").read_text()
    assert "pipeline_engine.py" in sh
    assert "SEED_PROMPT_PLACEHOLDER" not in sh   # the stub is gone


# ---- engine __main__ --output-dir wiring (Task 3) ----

def test_main_parses_output_dir_and_forwards_to_run(monkeypatch, tmp_path):
    captured = {}

    def fake_run(spec, *, dry_run=False, emit=print, output_dir=None):
        captured["spec"] = spec
        captured["dry_run"] = dry_run
        captured["output_dir"] = output_dir
        return {}

    monkeypatch.setattr(eng, "run", fake_run)
    eng.main([str(_FIX), "--output-dir", str(tmp_path / "out"), "--dry-run"])
    assert captured["output_dir"] == str(tmp_path / "out")
    assert captured["dry_run"] is True
    assert set(captured["spec"]) == {"1", "2", "3"}


def test_main_output_dir_defaults_to_none(monkeypatch):
    captured = {}

    def fake_run(spec, *, dry_run=False, emit=print, output_dir=None):
        captured["output_dir"] = output_dir
        return {}

    monkeypatch.setattr(eng, "run", fake_run)
    eng.main([str(_FIX)])
    assert captured["output_dir"] is None


# ---- Task 4: real 1964 World's Fair spec — dry-run + wiring (Milestone-1 gate) ----
#
# docs/examples/workflows/1964-worlds-fair.json is the real spec exercised by
# bin/run_workflow.sh. Unlike the mini fixture, it has 9 nodes and exercises
# nested-wire resolution (node 9's artifacts[]/metadata). This test loads the
# actual file (not a fixture copy) so it fails the moment the spec's wire keys
# drift from the engine's generic per-class-type output contract.

_REAL_SPEC = (Path(__file__).parent.parent / "docs" / "examples" / "workflows"
              / "1964-worlds-fair.json")


@pytest.mark.skipif(not _REAL_SPEC.exists(),
                     reason="1964-worlds-fair.json not present")
def test_1964_worlds_fair_dry_run():
    spec = eng.load_spec(str(_REAL_SPEC))
    order = eng.topo_order(spec)

    # 1 -> 2 -> 5 -> 6 (image -> caption -> compose -> video)
    assert order.index("1") < order.index("2") < order.index("5") < order.index("6")
    # node 9 (TTLGAddToPlaylist) collects from every other node — must run last.
    for src in ("1", "3", "4", "6", "7", "8", "2"):
        assert order.index(src) < order.index("9")

    r = eng.run(spec, dry_run=True, emit=lambda s: None)

    assert r["1"]["image_path"]
    assert r["6"]["video_path"]
    assert r["8"]["image_path"]
    assert r["7"]["text"]          # the poem
    assert r["9"]["playlist_id"]   # nested artifacts[]/metadata wires resolved
                                    # without KeyError


# ── Task 7: TTLGArtgenGenerate + TTLGAnimateDiff ─────────────────────────────
#
# Both handlers shell out to the repo-root tt-ctl CLI via eng._run_tt_ctl,
# which is mocked here so no subprocess is actually spawned. Real-path tests
# capture the argv passed to the mock and assert on flag names/values.

def test_task7_handlers_registered():
    assert "TTLGArtgenGenerate" in eng.HANDLERS
    assert "TTLGAnimateDiff" in eng.HANDLERS


# ---- TTLGArtgenGenerate ----

def test_artgen_generate_dry_run_text_ext():
    out = eng.HANDLERS["TTLGArtgenGenerate"]("10", {"plugin": "verse"}, _ctx(dry=True))
    assert out["artifact_path"] == "/tmp/out/node10_artifact.txt"
    assert out["text"] == "placeholder artifact text"
    assert "png_path" not in out


def test_artgen_generate_dry_run_raster_ext():
    out = eng.HANDLERS["TTLGArtgenGenerate"]("10",
        {"plugin": "palette", "ext": "png"}, _ctx(dry=True))
    assert out["artifact_path"] == "/tmp/out/node10_artifact.png"
    assert out["png_path"] == out["artifact_path"]


def test_artgen_generate_builds_argv_with_flag_mapping(monkeypatch, tmp_path):
    calls = {}

    def fake_run_tt_ctl(argv, timeout=600):
        calls["argv"] = argv
        out_path = Path(argv[argv.index("--output") + 1])
        out_path.write_text("hello verse", encoding="utf-8")

    monkeypatch.setattr(eng, "_run_tt_ctl", fake_run_tt_ctl)
    out = eng.HANDLERS["TTLGArtgenGenerate"]("11", {
        "plugin": "verse",
        "form": "haiku",
        "theme": "winter forges",
        "export_css": True,
        "no_thing": False,
        "tags": ["a", "b"],
    }, _ctx(tmp=str(tmp_path)))

    argv = calls["argv"]
    assert argv[0] == "artgen"
    assert argv[1] == "verse"
    assert argv[argv.index("--output") + 1] == str(tmp_path / "node11_artifact.txt")
    assert argv[argv.index("--form") + 1] == "haiku"
    assert argv[argv.index("--theme") + 1] == "winter forges"
    assert "--export-css" in argv
    assert "--no-thing" not in argv
    tag_idxs = [i for i, x in enumerate(argv) if x == "--tags"]
    assert [argv[i + 1] for i in tag_idxs] == ["a", "b"]
    assert out["artifact_path"] == str(tmp_path / "node11_artifact.txt")
    assert out["text"] == "hello verse"
    assert "png_path" not in out


def test_artgen_generate_skips_none_valued_inputs(monkeypatch, tmp_path):
    # Regression: the input->flag loop only excluded ("plugin", "ext") and had
    # no `value is None` guard, so a None-valued input (e.g. a spec literal
    # `null` or an unresolved wire) produced a literal `--flag None` on the
    # CLI. The sibling TTLGAnimateDiff handler already guards `value is None`;
    # TTLGArtgenGenerate must do the same.
    calls = {}

    def fake_run_tt_ctl(argv, timeout=600):
        calls["argv"] = argv
        out_path = Path(argv[argv.index("--output") + 1])
        out_path.write_text("hello verse", encoding="utf-8")

    monkeypatch.setattr(eng, "_run_tt_ctl", fake_run_tt_ctl)
    eng.HANDLERS["TTLGArtgenGenerate"]("13", {
        "plugin": "verse",
        "negative_prompt": None,
        "theme": "dusk",
    }, _ctx(tmp=str(tmp_path)))

    argv = calls["argv"]
    assert argv[argv.index("--theme") + 1] == "dusk"
    assert "--negative-prompt" not in argv
    assert "None" not in argv


def test_artgen_generate_sets_png_path_for_raster_ext(monkeypatch, tmp_path):
    def fake_run_tt_ctl(argv, timeout=600):
        out_path = Path(argv[argv.index("--output") + 1])
        out_path.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(eng, "_run_tt_ctl", fake_run_tt_ctl)
    out = eng.HANDLERS["TTLGArtgenGenerate"]("12",
        {"plugin": "palette", "ext": ".png"}, _ctx(tmp=str(tmp_path)))
    assert out["png_path"] == str(tmp_path / "node12_artifact.png")
    assert out["artifact_path"] == out["png_path"]
    assert "text" not in out


# ---- TTLGAnimateDiff ----

def test_animatediff_dry_run_key():
    out = eng.HANDLERS["TTLGAnimateDiff"]("20", {"prompt": "a scene"}, _ctx(dry=True))
    assert out["gif_path"] == "/tmp/out/node20.gif"


def test_animatediff_builds_argv_basic_flags(monkeypatch):
    calls = {}

    def fake_run_tt_ctl(argv, timeout=600):
        calls["argv"] = argv

    monkeypatch.setattr(eng, "_run_tt_ctl", fake_run_tt_ctl)
    out = eng.HANDLERS["TTLGAnimateDiff"]("21", {
        "prompt": "a walk", "frames": 16, "steps": 30, "seed": 7,
        "negative_prompt": "blurry", "multichip_mode": "remix", "loop": "seamless",
        "seed_spread": 2, "ramp": "temporal", "ramp_lo": 0.1, "ramp_hi": 0.9,
        "stitch_order": "concatenate",
    }, _ctx())

    argv = calls["argv"]

    def val(flag):
        return argv[argv.index(flag) + 1]

    assert argv[0:2] == ["artgen", "animatediff"]
    assert val("--output") == "/tmp/out/node21.gif"
    assert val("--prompt") == "a walk"
    assert val("--frames") == "16"
    assert val("--steps") == "30"
    assert val("--seed") == "7"
    assert val("--negative-prompt") == "blurry"
    assert val("--multichip-mode") == "remix"
    assert val("--loop") == "seamless"
    assert val("--seed-spread") == "2"
    assert val("--ramp") == "temporal"
    assert val("--ramp-lo") == "0.1"
    assert val("--ramp-hi") == "0.9"
    assert val("--stitch-order") == "concatenate"
    assert out["gif_path"] == "/tmp/out/node21.gif"


def test_animatediff_per_chip_prompts_repeated(monkeypatch):
    calls = {}
    monkeypatch.setattr(eng, "_run_tt_ctl",
                         lambda argv, timeout=600: calls.__setitem__("argv", argv))
    eng.HANDLERS["TTLGAnimateDiff"]("22", {
        "prompt": "p", "per_chip_prompts": ["chip0 scene", "chip1 scene"],
    }, _ctx())
    argv = calls["argv"]
    idxs = [i for i, x in enumerate(argv) if x == "--per-chip-prompt"]
    assert [argv[i + 1] for i in idxs] == ["chip0 scene", "chip1 scene"]


def test_animatediff_prompt_schedule_pairs_and_strings_both_normalize(monkeypatch):
    calls = {}
    monkeypatch.setattr(eng, "_run_tt_ctl",
                         lambda argv, timeout=600: calls.__setitem__("argv", argv))
    eng.HANDLERS["TTLGAnimateDiff"]("23", {
        "prompt": "p",
        "prompt_schedule": [[0, "spring meadow"], "16:snowfall"],
    }, _ctx())
    argv = calls["argv"]
    idxs = [i for i, x in enumerate(argv) if x == "--prompt-schedule"]
    assert [argv[i + 1] for i in idxs] == ["0:spring meadow", "16:snowfall"]


def test_animatediff_bool_flag_true_emits_bare_flag(monkeypatch):
    calls = {}
    monkeypatch.setattr(eng, "_run_tt_ctl",
                         lambda argv, timeout=600: calls.__setitem__("argv", argv))
    eng.HANDLERS["TTLGAnimateDiff"]("24", {"prompt": "p", "lightning": True}, _ctx())
    assert "--lightning" in calls["argv"]


# ── Task 4b: backend server-switching, optional-node failures, dry-run parity ──
#
# The engine now orchestrates backend switching (Issue 1): before dispatching a
# node it maps the node's class_type + model to a backend (server_manager key,
# or a sentinel), and — when that differs from the currently-active backend —
# stops/resets and starts the right server. In dry-run it only emits log lines.

# ---- _backend_for mapping (deterministic; no hardware) ----

def test_backend_for_text_to_image_flux():
    spec = eng._backend_for("TTLGTextToImage", {"model": "FLUX.1-schnell"})
    assert spec.key == "flux"
    assert spec.health_url == eng.sm.SERVERS["flux"].health_url
    assert spec.start is True


def test_backend_for_text_to_image_sdxl():
    spec = eng._backend_for("TTLGTextToImage", {"model": "SDXL-base"})
    assert spec.key == "sdxl"


def test_backend_for_text_to_image_default_flux():
    # No recognizable model → default to flux.
    spec = eng._backend_for("TTLGTextToImage", {})
    assert spec.key == "flux"


def test_backend_for_image_to_video_maps_model():
    assert eng._backend_for("TTLGImageToVideo", {"model": "SkyReels-V2-I2V-14B-540P"}).key == "skyreels"
    assert eng._backend_for("TTLGImageToVideo", {"model": "Wan2.2-T2V"}).key == "wan2.2"
    assert eng._backend_for("TTLGImageToVideo", {"model": "Mochi-1-preview"}).key == "mochi"


def test_backend_for_generate_text_maps_to_artgen_key():
    spec = eng._backend_for("TTLGGenerateText",
                            {"model": "meta-llama/Llama-3.3-70B-Instruct"})
    assert spec.key == "artgen-llama-3.3-70b"
    assert spec.health_url == eng.sm.SERVERS["artgen-llama-3.3-70b"].health_url


def test_backend_for_generate_text_qwen3_8b():
    spec = eng._backend_for("TTLGGenerateText", {"model": "Qwen3-8B"})
    assert spec.key == "artgen-qwen3-8b"


def test_backend_for_generate_text_unmapped_model_falls_back_to_detect():
    spec = eng._backend_for("TTLGGenerateText", {"model": "some-unknown-llm"})
    assert spec.key == eng.ARTGEN_DETECT


def test_backend_for_generate_text_no_model_falls_back_to_detect():
    spec = eng._backend_for("TTLGGenerateText", {})
    assert spec.key == eng.ARTGEN_DETECT


def test_backend_for_cpu_nodes_return_none():
    for ct in ("TTLGCaptionImage", "TTLGRemoveBackground", "TTLGEstimateDepth",
               "TTLGPromptCompose", "TTLGSVGRender", "TTLGComposite",
               "TTLGAddToPlaylist"):
        assert eng._backend_for(ct, {}) is None, ct


def test_backend_for_animatediff_is_chips_free():
    spec = eng._backend_for("TTLGAnimateDiff", {"prompt": "x"})
    assert spec.key == eng.CHIPS_FREE
    assert spec.start is False
    assert spec.health_url is None


def test_backend_for_artgen_non_llm_returns_none(monkeypatch):
    monkeypatch.setattr(eng, "_artgen_uses_llm", lambda name: False)
    assert eng._backend_for("TTLGArtgenGenerate", {"plugin": "somealgo"}) is None


def test_backend_for_artgen_llm_with_model(monkeypatch):
    monkeypatch.setattr(eng, "_artgen_uses_llm", lambda name: True)
    spec = eng._backend_for("TTLGArtgenGenerate",
                            {"plugin": "verse", "model": "Qwen3-32B"})
    assert spec.key == "artgen-qwen3-32b"


def test_backend_for_artgen_llm_without_model_detects(monkeypatch):
    monkeypatch.setattr(eng, "_artgen_uses_llm", lambda name: True)
    spec = eng._backend_for("TTLGArtgenGenerate", {"plugin": "verse"})
    assert spec.key == eng.ARTGEN_DETECT


def test_backend_for_unknown_class_type_returns_none():
    assert eng._backend_for("SomethingElse", {}) is None


# ---- switching inside run() (switch helpers mocked) ----

def _stub_handler_helpers(monkeypatch):
    """Stub every network/plugin/store helper so run() can exercise real
    handlers without touching hardware.  Returns nothing; mutates eng."""
    monkeypatch.setattr(eng, "_media_image_request",
                        lambda **k: k["out_path"])
    monkeypatch.setattr(eng, "_media_video_request",
                        lambda **k: k["out_path"])
    monkeypatch.setattr(eng, "_call_llm", lambda **k: "a poem")
    monkeypatch.setattr(eng, "_run_plugin", lambda *a: "a caption")
    monkeypatch.setattr(eng, "_add_artifacts_to_playlist",
                        lambda name, arts, meta, emit: "pl-1")
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)


def test_run_consecutive_same_backend_one_start_no_reset(monkeypatch, tmp_path):
    starts, resets = [], []
    monkeypatch.setattr(eng, "_stop_and_reset",
                        lambda next_key, current, *, dry_run, emit: resets.append((current, next_key)))
    monkeypatch.setattr(eng, "_start_server",
                        lambda key, health_url, max_wait, *, dry_run, emit: starts.append(key))
    _stub_handler_helpers(monkeypatch)

    spec = {
        "1": {"class_type": "TTLGTextToImage",
              "inputs": {"model": "FLUX.1-schnell", "prompt": "p"}},
        "2": {"class_type": "TTLGTextToImage",
              "inputs": {"model": "FLUX.1-schnell", "prompt": ["1", "image_path"]}},
    }
    eng.run(spec, dry_run=False, emit=lambda s: None, output_dir=str(tmp_path))
    assert starts == ["flux"]          # started once
    assert resets == [("", "flux")]    # reset/switch decided once


def test_run_backend_change_triggers_reset_and_start(monkeypatch, tmp_path):
    starts, resets = [], []
    monkeypatch.setattr(eng, "_stop_and_reset",
                        lambda next_key, current, *, dry_run, emit: resets.append((current, next_key)))
    monkeypatch.setattr(eng, "_start_server",
                        lambda key, health_url, max_wait, *, dry_run, emit: starts.append(key))
    _stub_handler_helpers(monkeypatch)

    spec = {
        "1": {"class_type": "TTLGTextToImage",
              "inputs": {"model": "FLUX.1-schnell", "prompt": "p"}},
        "2": {"class_type": "TTLGImageToVideo",
              "inputs": {"model": "SkyReels-V2-I2V", "prompt": "v",
                         "image": ["1", "image_path"]}},
    }
    eng.run(spec, dry_run=False, emit=lambda s: None, output_dir=str(tmp_path))
    assert starts == ["flux", "skyreels"]
    assert resets == [("", "flux"), ("flux", "skyreels")]


def test_run_1964_real_backend_sequence(monkeypatch, tmp_path):
    """Milestone-1 gate: the real 1964 spec (non-dry) produces the expected
    backend call sequence, resetting only when the backend actually changes."""
    if not _REAL_SPEC.exists():
        pytest.skip("1964-worlds-fair.json not present")

    starts, resets, docker_stops = [], [], []
    monkeypatch.setattr(eng, "_docker_stop_all", lambda: docker_stops.append(1))
    monkeypatch.setattr(eng, "_tt_smi_reset", lambda: resets.append(1))
    monkeypatch.setattr(eng, "_real_start_server",
                        lambda key, health_url, max_wait, emit: starts.append(key))
    _stub_handler_helpers(monkeypatch)

    spec = eng.load_spec(str(_REAL_SPEC))
    eng.run(spec, dry_run=False, emit=lambda s: None, output_dir=str(tmp_path))

    assert starts == ["flux", "skyreels", "artgen-llama-3.3-70b", "flux"]
    # 4 switches, but the very first (from no backend) does NOT reset boards.
    assert len(resets) == 3


# ---- dry-run stays hardware-free but emits intended-switch log lines ----

def test_run_dry_run_no_hardware_but_emits_switch_lines(monkeypatch, tmp_path):
    if not _REAL_SPEC.exists():
        pytest.skip("1964-worlds-fair.json not present")

    def _boom(*a, **k):
        raise AssertionError("hardware primitive invoked during dry-run")

    monkeypatch.setattr(eng, "_docker_stop_all", _boom)
    monkeypatch.setattr(eng, "_tt_smi_reset", _boom)
    monkeypatch.setattr(eng, "_real_start_server", _boom)
    # detect_artgen_endpoint must never be probed in dry-run either.
    import artgen as _ag
    monkeypatch.setattr(_ag, "detect_artgen_endpoint", _boom)

    lines = []
    spec = eng.load_spec(str(_REAL_SPEC))
    eng.run(spec, dry_run=True, emit=lines.append, output_dir=str(tmp_path))

    text = "\n".join(lines)
    assert "[dry-run]" in text
    # Intended switches for the 1964 spec appear as dry-run start lines.
    assert any("flux" in l and "[dry-run]" in l for l in lines)
    assert any("skyreels" in l and "[dry-run]" in l for l in lines)
    assert any("artgen-llama-3.3-70b" in l and "[dry-run]" in l for l in lines)


# ---- Issue 2: honor the optional flag ----

def test_run_optional_node_failure_continues(monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "_stop_and_reset",
                        lambda *a, **k: None)
    monkeypatch.setattr(eng, "_start_server",
                        lambda *a, **k: None)
    monkeypatch.setattr(eng, "_media_image_request", lambda **k: k["out_path"])
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)

    def _boom_plugin(*a):
        raise RuntimeError("rmbg exploded")
    monkeypatch.setattr(eng, "_run_plugin", _boom_plugin)

    spec = {
        "1": {"class_type": "TTLGTextToImage",
              "inputs": {"model": "FLUX.1-schnell", "prompt": "p"}},
        # optional per COMPATIBILITY_MAP; its handler raises
        "2": {"class_type": "TTLGRemoveBackground",
              "inputs": {"src": ["1", "image_path"]}},
    }
    lines = []
    results = eng.run(spec, dry_run=False, emit=lines.append, output_dir=str(tmp_path))
    assert "image_path" in results["1"]     # node 1 completed
    assert results["2"] == {}               # optional node left empty
    assert any(l.startswith("NODE:2:failed") for l in lines)


def test_run_required_node_failure_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "_stop_and_reset", lambda *a, **k: None)
    monkeypatch.setattr(eng, "_start_server", lambda *a, **k: None)
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)

    def _boom(**k):
        raise RuntimeError("image server down")
    monkeypatch.setattr(eng, "_media_image_request", _boom)

    spec = {"1": {"class_type": "TTLGTextToImage",
                  "inputs": {"model": "FLUX.1-schnell", "prompt": "p"}}}
    with pytest.raises(RuntimeError):
        eng.run(spec, dry_run=False, emit=lambda s: None, output_dir=str(tmp_path))


# ---- Nit 5: dry-run/real parity for _h_artgen_generate ----

def test_artgen_generate_dry_run_raster_ext_has_no_text():
    out = eng.HANDLERS["TTLGArtgenGenerate"]("10",
        {"plugin": "palette", "ext": "png"}, _ctx(dry=True))
    assert out["png_path"] == out["artifact_path"]
    assert "text" not in out          # raster dry-run must mirror the real path


def test_artgen_generate_dry_run_text_ext_has_text():
    out = eng.HANDLERS["TTLGArtgenGenerate"]("10",
        {"plugin": "verse", "ext": ".txt"}, _ctx(dry=True))
    assert out["text"] == "placeholder artifact text"
    assert "png_path" not in out
