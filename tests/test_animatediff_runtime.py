"""AnimateDiff runtime resolution: local tt-metal first, served container second.

The wiring tests drive the real `run_subprocess` with no local python_env, so
they fail if the fallback is not actually reached from the caller.
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import animatediff_runtime as rt
from artgen.generators import animatediff as ad


def _mk_env(root: Path) -> Path:
    py = root / "tt-metal" / "python_env" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    return root / "tt-metal"


def test_find_tt_metal_prefers_env_over_home(tmp_path):
    home = tmp_path / "home"
    _mk_env(home)
    other = _mk_env(tmp_path / "other")
    assert rt.find_tt_metal({"TT_METAL_HOME": str(other)}, home) == other
    assert rt.find_tt_metal({}, home) == home / "tt-metal"


def test_find_tt_metal_none_when_no_python_env(tmp_path):
    (tmp_path / "tt-metal").mkdir()  # directory without python_env
    assert rt.find_tt_metal({}, tmp_path) is None


def test_find_served_skips_non_animatediff_servers():
    # Port 8000 is a media server (Wan2.2) answering the same route.
    url = rt.find_served(ports_fn=lambda: [8000, 20001],
                         probe=lambda u: u.endswith(":20001"))
    assert url == "http://localhost:20001"
    assert rt.find_served(ports_fn=lambda: [8000], probe=lambda u: False) is None


def test_start_served_reports_missing_launcher(monkeypatch):
    monkeypatch.setattr(rt, "served_launcher_available", lambda: False)
    url, err = rt.start_served()
    assert url is None and "tt-model" in err


def test_start_served_waits_until_probe_passes(monkeypatch):
    monkeypatch.setattr(rt, "served_launcher_available", lambda: True)

    class P:
        returncode = 0
        stdout = stderr = ""
    monkeypatch.setattr(rt.subprocess, "run", lambda *a, **k: P())
    calls = {"n": 0}

    def probe(u):
        calls["n"] += 1
        return calls["n"] >= 3
    t = {"v": 0.0}
    url, err = rt.start_served(ports_fn=lambda: [20001], probe=probe,
                               sleep=lambda s: t.__setitem__("v", t["v"] + s),
                               clock=lambda: t["v"], wait_s=100, poll_s=5)
    assert url == "http://localhost:20001" and err == ""


def test_generate_served_writes_gif(tmp_path, monkeypatch):
    gif = b"GIF89a-fake"
    seen = {}

    def fake(url, timeout=2.0, data=None):
        seen["url"], seen["data"] = url, data
        return {"data": [{"b64_json": base64.b64encode(gif).decode()}]}
    monkeypatch.setattr(rt, "_http_json", fake)
    out = tmp_path / "o" / "x.gif"
    ok, err = rt.generate_served("http://h:1", out, "a koi", "bad", 8, 10, 7, 0.35)
    assert ok and out.read_bytes() == gif
    assert seen["url"].endswith("/v1/videos/generations")
    assert b'"num_frames": 8' in seen["data"]


def test_generate_served_reports_failure(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr(rt, "_http_json", boom)
    ok, err = rt.generate_served("http://h:1", tmp_path / "x.gif", "p", "", 8, 10, 1, 0.3)
    assert not ok and "refused" in err


# ── wiring through the real run_subprocess ──────────────────────────────────

def _no_local(monkeypatch, tmp_path):
    monkeypatch.setattr(ad, "_PYTHON", tmp_path / "absent" / "python")


def test_run_subprocess_uses_served_when_no_local_env(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    monkeypatch.setattr(rt, "find_served", lambda *a, **k: "http://h:1")
    got = {}

    def fake_gen(url, out_path, *a, **k):
        got["url"] = url
        out_path.write_bytes(b"GIF")
        return True, ""
    monkeypatch.setattr(rt, "generate_served", fake_gen)
    msgs = []
    out = tmp_path / "x.gif"
    ok, err = ad.run_subprocess("p", out, frames=8, steps=5, on_progress=msgs.append,
                                multichip_mode="remix", num_chips=4)
    assert ok, err
    assert got["url"] == "http://h:1" and out.exists()
    assert any("ignores" in m and "multichip" in m for m in msgs)


def test_run_subprocess_missing_everywhere_gives_actionable_error(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    monkeypatch.setattr(rt, "find_served", lambda *a, **k: None)
    monkeypatch.setattr(rt, "served_launcher_available", lambda: False)
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif")
    assert not ok and "tt-model serve" in err and "tt-installer" in err


def test_run_subprocess_cpu_mode_does_not_use_served(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", mode="cpu")
    assert not ok and "Blackhole only" in err


# ── served previews ─────────────────────────────────────────────────────────

import threading
import urllib.error


def test_poll_previews_relays_new_steps_only(tmp_path):
    stop = threading.Event()
    replies = iter([
        {"step": 3, "total": 3, "gif_b64": base64.b64encode(b"S").decode(),
         "running": False},  # stale frame from the previous request
        {"step": 1, "total": 3, "gif_b64": base64.b64encode(b"A").decode(), "running": True},
        {"step": 1, "total": 3, "gif_b64": base64.b64encode(b"A").decode(), "running": True},
        {"step": 2, "total": 3, "gif_b64": base64.b64encode(b"B").decode(), "running": True},
    ])
    lines = []

    def fetch(url):
        try:
            return next(replies)
        except StopIteration:
            stop.set()
            return {"step": 0}
    p = tmp_path / "x_preview.gif"
    rt.poll_previews("http://h:1", p, lines.append, stop, interval_s=0, fetch=fetch)
    assert lines == [f"PREVIEW: 1/3 {p}", f"PREVIEW: 2/3 {p}"]
    assert p.read_bytes() == b"B"


def test_poll_previews_stops_quietly_on_old_server(tmp_path):
    def fetch(url):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    lines = []
    rt.poll_previews("http://h:1", tmp_path / "p.gif", lines.append,
                     threading.Event(), interval_s=0, fetch=fetch)
    assert lines == []


def test_served_run_passes_preview_path_to_generate(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    monkeypatch.setattr(rt, "find_served", lambda *a, **k: "http://h:1")
    got = {}

    def fake_gen(url, out_path, *a, **k):
        got.update(k)
        out_path.write_bytes(b"GIF")
        return True, ""
    monkeypatch.setattr(rt, "generate_served", fake_gen)
    out = tmp_path / "x.gif"
    ok, _ = ad.run_subprocess("p", out, on_progress=lambda m: None)
    assert ok and got["preview_path"] == tmp_path / "x_preview.gif"
    got.clear()
    ad.run_subprocess("p", out)  # nobody draining progress: no preview polling
    assert got["preview_path"] is None


def test_post_asks_for_preview_and_forwards_schedule(tmp_path, monkeypatch):
    seen = {}

    def fake(url, timeout=2.0, data=None):
        import json
        seen.update(json.loads(data))
        return {"data": [{"b64_json": base64.b64encode(b"G").decode()}]}
    monkeypatch.setattr(rt, "_http_json", fake)
    ok, _ = rt._post_generation("http://h:1", tmp_path / "a.gif", "p", "", 8, 5, 1, 0.3, 10,
                                want_preview=True, prompt_schedule=[(4, "dusk")])
    assert ok and seen["preview"] is True
    assert seen["prompt_schedule"] == [{"frame": 4, "prompt": "dusk"}]
    seen.clear()
    rt._post_generation("http://h:1", tmp_path / "b.gif", "p", "", 8, 5, 1, 0.3, 10)
    assert "preview" not in seen and "prompt_schedule" not in seen


# ── served sampler / chain / motion adapter ─────────────────────────────────

def test_chain_name_is_stable_and_server_legal(tmp_path):
    import re
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}",
                        rt.chain_name_for(tmp_path / "\u00e9t\u00e9.pt"))
    n = rt.chain_name_for(tmp_path / "my chain!.pt")
    assert n == rt.chain_name_for(tmp_path / "my chain!.pt")
    assert n != rt.chain_name_for(tmp_path / "other.pt")
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", n)


def _capture_post(monkeypatch, adapter_on=True):
    seen = {}

    def fake(url, timeout=2.0, data=None):
        import json
        if data is None:  # /v1/models
            return {"data": [], "motion_adapter": adapter_on}
        seen.update(json.loads(data))
        return {"data": [{"b64_json": base64.b64encode(b"G").decode()}]}
    monkeypatch.setattr(rt, "_http_json", fake)
    monkeypatch.setattr(rt, "find_served", lambda *a, **k: "http://h:1")
    return seen


def test_served_run_maps_lightning_chain_and_motion_adapter(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    seen = _capture_post(monkeypatch)
    ok, err = ad.run_subprocess(
        "p", tmp_path / "x.gif", lightning=True,
        chain_save=str(tmp_path / "a.pt"), chain_from=str(tmp_path / "b.pt"),
        chain_alpha=0.4, motion_adapter="default", motion_adapter_alpha=0.7,
        motion_adapter_skip=["up1", "up2"])
    assert ok, err
    assert seen["sampler"] == "euler"
    assert seen["chain_save"] == rt.chain_name_for(tmp_path / "a.pt")
    assert seen["chain_from"] == rt.chain_name_for(tmp_path / "b.pt")
    assert seen["chain_alpha"] == 0.4
    assert seen["motion_adapter"] is True and seen["motion_adapter_skip"] == ["up1", "up2"]


def test_served_run_drops_motion_adapter_when_server_lacks_it(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    seen = _capture_post(monkeypatch, adapter_on=False)
    msgs = []
    ok, _ = ad.run_subprocess("p", tmp_path / "x.gif", motion_adapter="default",
                              on_progress=msgs.append)
    assert ok and "motion_adapter" not in seen
    assert any("motion adapter" in m for m in msgs)


# ── PR review fixes ─────────────────────────────────────────────────────────

def test_no_server_does_not_start_container_without_opt_in(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    monkeypatch.setattr(rt, "find_served", lambda *a, **k: None)
    monkeypatch.setattr(rt, "served_launcher_available", lambda: True)
    monkeypatch.delenv(rt.AUTOSTART_ENV, raising=False)
    started = []
    monkeypatch.setattr(rt, "start_served", lambda **k: started.append(1) or (None, "x"))
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif")
    assert not ok and not started and "tt-model serve" in err


def test_autostart_only_with_explicit_opt_in(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    monkeypatch.setattr(rt, "find_served", lambda *a, **k: None)
    monkeypatch.setattr(rt, "served_launcher_available", lambda: True)
    monkeypatch.setenv(rt.AUTOSTART_ENV, "1")
    started = []
    monkeypatch.setattr(rt, "start_served", lambda **k: started.append(1) or (None, "boom"))
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif")
    assert not ok and started and "boom" in err


def test_discovery_survives_malformed_models_response(monkeypatch):
    for bad in ({"data": None}, {"data": ["str", 3]}, {"data": {"a": 1}}, []):
        monkeypatch.setattr(rt, "_http_json", lambda *a, _b=bad, **k: _b)
        assert rt._is_animatediff_server("http://h:1") is False
    monkeypatch.setattr(rt, "_http_json", lambda *a, **k: {"data": [
        "junk", {"id": "CompVis/stable-diffusion-v1-4"}]})
    assert rt._is_animatediff_server("http://h:1") is True


def test_panel_empty_string_motion_adapter_still_enables_it(tmp_path, monkeypatch):
    # The Create panel sends "" for "adapter on, default weights".
    _no_local(monkeypatch, tmp_path)
    seen = _capture_post(monkeypatch)
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", motion_adapter="")
    assert ok, err
    assert seen["motion_adapter"] is True


def test_panel_bool_chain_save_keys_on_output_file(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    seen = _capture_post(monkeypatch)
    out = tmp_path / "x.gif"
    ok, err = ad.run_subprocess("p", out, chain_save=True)
    assert ok, err
    assert seen["chain_save"] == rt.chain_name_for(out)


# ── served LoRA variants ────────────────────────────────────────────────────

def _capture_post_with_loras(monkeypatch, loras):
    seen = {}

    def fake(url, timeout=2.0, data=None):
        import json
        if data is None:  # /v1/models
            return {"data": [], "loras": loras, "motion_adapter": False}
        seen.update(json.loads(data))
        return {"data": [{"b64_json": base64.b64encode(b"G").decode()}]}
    monkeypatch.setattr(rt, "_http_json", fake)
    monkeypatch.setattr(rt, "find_served", lambda *a, **k: "http://h:1")
    return seen


def test_served_loras_lists_the_catalog_and_survives_bad_answers(monkeypatch):
    monkeypatch.setattr(rt, "_http_json", lambda *a, **k: {"loras": ["pixel", "lcm"]})
    assert rt.served_loras("http://h:1") == ["pixel", "lcm"]
    for bad in ({}, {"loras": None}, {"loras": "pixel"}, {"loras": [3, None]}):
        monkeypatch.setattr(rt, "_http_json", lambda *a, _b=bad, **k: _b)
        assert rt.served_loras("http://h:1") == []

    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(rt, "_http_json", boom)
    assert rt.served_loras("http://h:1") == []


def test_served_run_sends_the_named_lora(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    seen = _capture_post_with_loras(monkeypatch, ["pixel", "lcm"])
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", lora="pixel")
    assert ok, err
    assert seen["lora"] == "pixel"
    seen.clear()
    ok, err = ad.run_subprocess("p", tmp_path / "y.gif")
    assert ok, err
    assert "lora" not in seen


def test_served_run_refuses_an_unknown_lora_and_lists_the_catalog(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    seen = _capture_post_with_loras(monkeypatch, ["pixel", "lcm"])
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", lora="storybook")
    assert not ok
    assert "storybook" in err and "pixel" in err and "lcm" in err
    assert not seen, "an unknown LoRA must not reach the generation endpoint"


def test_served_run_explains_a_server_with_no_catalog(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    seen = _capture_post_with_loras(monkeypatch, [])
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", lora="pixel")
    assert not ok and not seen
    assert "no LoRA catalog" in err and "plain" in err


def test_local_runtime_says_it_does_not_apply_a_lora(tmp_path, monkeypatch):
    """With a local tt-metal the LoRA is not forwarded; that must be said, not dropped."""
    py = tmp_path / "python_env" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(ad, "_PYTHON", py)
    ran = []
    monkeypatch.setattr(ad, "_run_one", lambda cmd, **k: ran.append(cmd) or (True, ""))
    msgs = []
    ad.run_subprocess("p", tmp_path / "x.gif", lora="pixel", on_progress=msgs.append,
                      script=tmp_path / "gen.py")
    assert any("LoRA" in m for m in msgs)
    assert ran and not any("lora" in " ".join(map(str, c)).lower() for c in ran)
