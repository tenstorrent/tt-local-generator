"""AnimateDiff runtime resolution: local tt-metal first, served container second.

The wiring tests drive the real `run_subprocess` with no local python_env, so
they fail if the fallback is not actually reached from the caller.
"""
import base64
import json
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
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: ("http://h:1", {}))
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
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: (None, {}))
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
         "running": False, "request_id": "mine"},  # stale frame from the previous request
        {"step": 1, "total": 3, "gif_b64": base64.b64encode(b"A").decode(), "running": True, "request_id": "mine"},
        {"step": 1, "total": 3, "gif_b64": base64.b64encode(b"A").decode(), "running": True, "request_id": "mine"},
        {"step": 2, "total": 3, "gif_b64": base64.b64encode(b"B").decode(), "running": True, "request_id": "mine"},
    ])
    lines = []

    def fetch(url):
        try:
            return next(replies)
        except StopIteration:
            stop.set()
            return {"step": 0}
    p = tmp_path / "x_preview.gif"
    rt.poll_previews("http://h:1", p, lines.append, stop, "mine", interval_s=0, fetch=fetch)
    assert lines == [f"PREVIEW: 1/3 {p}", f"PREVIEW: 2/3 {p}"]
    assert p.read_bytes() == b"B"


def test_poll_previews_ignores_another_clients_generation(tmp_path):
    """Review finding: the server keeps one global preview and queues requests on a device lock.
    While this request waited, the poller showed another client's step-20 frame and set its step
    watermark to 20, so once this request started every one of its own previews (steps 1-19) was
    thrown away. Ownership is the request id, not the server's running flag."""
    stop = threading.Event()

    def frame(step, rid, running=True):
        return {"step": step, "total": 25, "running": running, "request_id": rid,
                "gif_b64": base64.b64encode(f"{rid}{step}".encode()).decode()}
    replies = iter([
        frame(20, "other"),            # another client's generation is running; mine is queued
        frame(21, "other"),
        frame(1, "mine"),              # mine starts: lower step numbers than the other client's
        frame(2, "mine"),
        frame(25, "other", running=False),  # a stale frame left by someone else
    ])
    lines = []

    def fetch(url):
        try:
            return next(replies)
        except StopIteration:
            stop.set()
            return {"step": 0}
    p = tmp_path / "x_preview.gif"
    rt.poll_previews("http://h:1", p, lines.append, stop, "mine", interval_s=0, fetch=fetch)
    assert lines == [f"PREVIEW: 1/25 {p}", f"PREVIEW: 2/25 {p}"]
    assert p.read_bytes() == b"mine2"


def test_a_served_run_names_its_request_and_polls_for_that_name(tmp_path, monkeypatch):
    # Wiring: poll_previews can filter correctly while generate_served never sends an id, or
    # sends one id to the server and a different one to the poller.
    seen = {}

    def fake_http(url, timeout=2.0, data=None):
        seen["body"] = json.loads(data.decode())
        return {"data": [{"b64_json": base64.b64encode(b"G").decode()}]}

    def fake_poll(base_url, preview_path, on_progress, stop, request_id, **kw):
        seen["polled_for"] = request_id

    monkeypatch.setattr(rt, "_http_json", fake_http)
    monkeypatch.setattr(rt, "poll_previews", fake_poll)
    rt.generate_served("http://h:1", tmp_path / "o.gif", "p", "", 8, 10, 1, 0.3,
                       on_progress=lambda line: None, preview_path=tmp_path / "pv.gif")
    assert seen["body"]["request_id"] and seen["polled_for"] == seen["body"]["request_id"]
    first = seen["body"]["request_id"]
    rt.generate_served("http://h:1", tmp_path / "o.gif", "p", "", 8, 10, 1, 0.3,
                       on_progress=lambda line: None, preview_path=tmp_path / "pv.gif")
    assert seen["body"]["request_id"] != first, "two generations must not share an id"


def test_poll_previews_stops_quietly_on_old_server(tmp_path):
    def fetch(url):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    lines = []
    rt.poll_previews("http://h:1", tmp_path / "p.gif", lines.append,
                     threading.Event(), "mine", interval_s=0, fetch=fetch)
    assert lines == []


def test_served_run_passes_preview_path_to_generate(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: ("http://h:1", {}))
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
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: ("http://h:1", rt.served_models("http://h:1")))
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
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: (None, {}))
    monkeypatch.setattr(rt, "served_launcher_available", lambda: True)
    monkeypatch.delenv(rt.AUTOSTART_ENV, raising=False)
    started = []
    monkeypatch.setattr(rt, "start_served", lambda **k: started.append(1) or (None, "x"))
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif")
    assert not ok and not started and "tt-model serve" in err


def test_autostart_only_with_explicit_opt_in(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: (None, {}))
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
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: ("http://h:1", rt.served_models("http://h:1")))
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


def test_served_package_is_the_container_repo_not_the_thin_bundle():
    """episod/tt-animatediff is the v6 thin bundle and the diffusers pipeline. The container
    package, the one `tt-model serve` runs under docker, lives in its own repo."""
    assert rt.SERVED_PACKAGE == "episod/tt-animatediff-container-v5.1"
    assert rt.SERVED_PACKAGE in rt.NOT_RUNNING_MESSAGE and rt.SERVED_PACKAGE in rt.MISSING_MESSAGE


# ── review round (josh, PR #29) ─────────────────────────────────────────────

def _local_present(monkeypatch, tmp_path):
    py = tmp_path / "tt-metal" / "python_env" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(ad, "_PYTHON", py)


def test_a_lora_run_goes_to_a_running_server_even_with_a_local_tt_metal(tmp_path, monkeypatch):
    """With ~/tt-metal present the LoRA used to be dropped with a note, and generate.py then
    competed with the container for the chips the container already held."""
    _local_present(monkeypatch, tmp_path)
    monkeypatch.setattr(rt, "find_served", lambda *a, **k: "http://h:1")
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: ("http://h:1", {"loras": ["pixel"]}))
    sent = {}

    def fake_gen(url, out_path, *a, **k):
        sent.update(k.get("extra") or {})
        out_path.write_bytes(b"GIF")
        return True, ""
    monkeypatch.setattr(rt, "generate_served", fake_gen)
    local = []
    monkeypatch.setattr(ad, "_run_one", lambda *a, **k: local.append(1) or (True, ""))
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", lora="pixel")
    assert ok, err
    assert sent["lora"] == "pixel" and not local


def test_a_run_without_a_lora_still_prefers_the_local_tt_metal(tmp_path, monkeypatch):
    _local_present(monkeypatch, tmp_path)
    probed = []
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: probed.append(1) or ("http://h:1", {}))
    served = []
    monkeypatch.setattr(ad, "_run_served", lambda **k: served.append(1) or (True, ""))
    monkeypatch.setattr(ad, "_run_one", lambda *a, **k: (False, "stop here"))
    ad.run_subprocess("p", tmp_path / "x.gif")
    assert not served and not probed


def test_a_lora_run_with_no_server_says_so_and_uses_the_local_runner(tmp_path, monkeypatch):
    _local_present(monkeypatch, tmp_path)
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: (None, {}))
    monkeypatch.setattr(ad, "_run_one", lambda *a, **k: (False, "stop here"))
    msgs = []
    ad.run_subprocess("p", tmp_path / "x.gif", lora="pixel", on_progress=msgs.append)
    assert any("pixel" in m and "none is running" in m for m in msgs)


def test_a_lora_run_looks_the_server_up_once(tmp_path, monkeypatch):
    """Routing and the run used to each do a `docker ps` plus probe, and a server that stopped
    between the two failed the run instead of falling back to local."""
    _local_present(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(rt, "find_served_models",
                        lambda *a, **k: calls.append(1) or ("http://h:1", {"loras": ["pixel"]}))
    monkeypatch.setattr(rt, "generate_served",
                        lambda url, out_path, *a, **k: (out_path.write_bytes(b"GIF"), (True, ""))[1])
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", lora="pixel")
    assert ok, err
    assert len(calls) == 1


def test_a_chain_marker_goes_to_the_server_not_the_local_runner(tmp_path, monkeypatch):
    """The marker holds a server-side chain name; generate.py cannot load it as latents."""
    _local_present(monkeypatch, tmp_path)
    marker = tmp_path / "chain.pt"
    rt.write_chain_marker(marker, "chain-abc")
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: ("http://h:1", {}))
    sent = {}

    def fake_gen(url, out_path, *a, **k):
        sent.update(k.get("extra") or {})
        out_path.write_bytes(b"GIF")
        return True, ""
    monkeypatch.setattr(rt, "generate_served", fake_gen)
    local = []
    monkeypatch.setattr(ad, "_run_one", lambda *a, **k: local.append(1) or (True, ""))
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", chain_from=str(marker))
    assert ok, err
    assert sent["chain_from"] == "chain-abc" and not local


def test_a_chain_marker_with_no_server_fails_clearly(tmp_path, monkeypatch):
    _local_present(monkeypatch, tmp_path)
    marker = tmp_path / "chain.pt"
    rt.write_chain_marker(marker, "chain-abc")
    monkeypatch.setattr(rt, "find_served_models", lambda *a, **k: (None, {}))
    local = []
    monkeypatch.setattr(ad, "_run_one", lambda *a, **k: local.append(1) or (True, ""))
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", chain_from=str(marker))
    assert not ok and "AnimateDiff server" in err and not local


def test_start_served_shares_one_deadline_and_probes_at_least_once(monkeypatch):
    """wait_s used to bound the `serve --detach` subprocess and then a second, fresh readiness
    wait, so the worst case was twice the number the error message reports."""
    monkeypatch.setattr(rt, "served_launcher_available", lambda: True)
    t = {"v": 0.0}

    class P:
        returncode = 0
        stdout = stderr = ""

    def slow_serve(*a, **k):
        t["v"] += 100.0  # the detach consumed the whole budget
        return P()
    monkeypatch.setattr(rt.subprocess, "run", slow_serve)
    probes = []
    url, err = rt.start_served(ports_fn=lambda: [1], probe=lambda u: probes.append(u) and False,
                               sleep=lambda s: t.__setitem__("v", t["v"] + s),
                               clock=lambda: t["v"], wait_s=100, poll_s=5)
    assert url is None and "100s" in err
    assert len(probes) == 1, "one last probe after the subprocess, and no fresh wait"
    assert t["v"] == 100.0

    t["v"] = 0.0
    probes.clear()
    monkeypatch.setattr(rt.subprocess, "run", lambda *a, **k: P())
    rt.start_served(ports_fn=lambda: [1], probe=lambda u: False,
                    sleep=lambda s: t.__setitem__("v", t["v"] + s),
                    clock=lambda: t["v"], wait_s=100, poll_s=5)
    assert t["v"] <= 105.0


def test_a_server_that_does_not_name_its_previews_gets_one_note_and_no_polling(tmp_path):
    """The package published first (0.13.0) predates request_id. Its previews cannot be matched
    to this generation, so none are shown; the user is told once instead of seeing nothing."""
    fetched = []

    def fetch(url):
        fetched.append(url)
        return {"step": 3, "total": 25, "running": True, "gif_b64": base64.b64encode(b"x").decode()}
    lines = []
    rt.poll_previews("http://h:1", tmp_path / "p.gif", lines.append,
                     threading.Event(), "mine", interval_s=0, fetch=fetch)
    assert len(fetched) == 1 and len(lines) == 1
    assert "older than 0.13.1" in lines[0] and not (tmp_path / "p.gif").exists()


def test_an_idle_old_server_is_not_reported(tmp_path):
    stop = threading.Event()
    replies = iter([{"step": 0, "running": False}, {"step": 0, "running": False}])

    def fetch(url):
        try:
            return next(replies)
        except StopIteration:
            stop.set()
            return {}
    lines = []
    rt.poll_previews("http://h:1", tmp_path / "p.gif", lines.append, stop, "mine", interval_s=0, fetch=fetch)
    assert lines == []


def test_a_served_chain_save_leaves_a_marker_the_next_run_reads(tmp_path, monkeypatch):
    """The chain lives on the server, so the GUI's "Chain from" file picker had nothing to select."""
    _no_local(monkeypatch, tmp_path)
    seen = _capture_post(monkeypatch)
    saved = tmp_path / "tt_ad_chain_7.pt"
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", chain_save=str(saved))
    assert ok, err
    assert saved.exists()
    assert rt.read_chain_marker(saved) == seen["chain_save"] == rt.chain_name_for(saved)

    seen.clear()
    ok, err = ad.run_subprocess("p", tmp_path / "y.gif", chain_from=str(saved))
    assert ok, err
    assert seen["chain_from"] == rt.chain_name_for(saved)


def test_chain_from_uses_the_name_in_a_marker_and_hashes_any_other_path(tmp_path):
    marker = tmp_path / "picked.pt"
    rt.write_chain_marker(marker, "server-side-name-123")
    assert rt.chain_name_to_continue(marker) == "server-side-name-123"
    real = tmp_path / "latents.pt"
    real.write_bytes(b"PK\x03\x04 not a marker \xff\xfe")
    assert rt.read_chain_marker(real) is None
    assert rt.chain_name_to_continue(real) == rt.chain_name_for(real)
    assert rt.chain_name_to_continue(tmp_path / "absent.pt") == rt.chain_name_for(tmp_path / "absent.pt")


def test_cpu_mode_error_points_only_at_tt_metal(tmp_path, monkeypatch):
    _no_local(monkeypatch, tmp_path)
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", mode="cpu")
    assert not ok and "tt-installer" in err and "tt-model serve" not in err


def test_a_served_run_reads_v1_models_once(tmp_path, monkeypatch):
    """Discovery, the LoRA check and the motion-adapter check read the same document."""
    import artgen

    monkeypatch.setattr(artgen, "_tt_model_host_ports", lambda: [20000])
    _no_local(monkeypatch, tmp_path)
    models_calls = []

    def fake(url, timeout=2.0, data=None):
        if data is None:
            models_calls.append(url)
            return {"data": [{"id": "CompVis/stable-diffusion-v1-4"}], "loras": ["pixel"], "motion_adapter": True}
        return {"data": [{"b64_json": base64.b64encode(b"G").decode()}]}
    monkeypatch.setattr(rt, "_http_json", fake)
    ok, err = ad.run_subprocess("p", tmp_path / "x.gif", lora="pixel", motion_adapter="default")
    assert ok, err
    assert models_calls == ["http://localhost:20000/v1/models"]


def test_postinst_does_not_promise_an_automatic_start():
    """A Generate with no server returns NOT_RUNNING_MESSAGE unless the operator opted in, so the
    installer must not say the package is started on first use."""
    text = (Path(__file__).parent.parent / "debian" / "postinst").read_text()
    assert "started on first use" not in text and "started on demand" not in text
    assert rt.AUTOSTART_ENV in text
