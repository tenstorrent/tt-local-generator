import importlib.util, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
_ENGINE = Path(__file__).parent.parent / "app" / "pipeline_engine.py"

def _load():
    spec = importlib.util.spec_from_file_location("pipeline_engine", _ENGINE)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

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
