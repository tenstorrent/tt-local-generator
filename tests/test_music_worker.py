"""MusicGenerationWorker and the Music medium (Task 5): what is stored, what the user is told, what is cancelled.

Servers are tests/fake_music_server.py on loopback; no chip, docker or the real 8795 / 20000 ports are touched."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).parent))

import create_mediums
import music_runtime as mr
import worker
from fake_music_server import FakeMusicServer

TOKEN = "s3cret-token-value"


class _Store:
    """The slice of MediaStore the worker uses."""

    def __init__(self):
        self.records = []

    def add(self, rec):
        self.records.append(rec)


def _server(fake, token=None):
    health = fake.health_body
    return mr.MusicServer(url=fake.url, profile=mr.profile_for(health), up=True, saved=False, token=token,
                          label=mr.profile_for(health).label)


def _run(fake, request, tmp_path, token=None, **kw):
    store = _Store()
    out = {"done": [], "err": [], "progress": []}
    w = worker.MusicGenerationWorker(store, _server(fake, token), request, tmp_path, token=token, poll_s=0.01, **kw)
    w.run_with_callbacks(on_progress=out["progress"].append, on_finished=out["done"].append, on_error=out["err"].append)
    return store, out, w


def _req(**kw):
    base = dict(prompt="slow synth pads", lyrics=None, seconds=10.0, seed=7, steps=None, cfg_scale=None)
    base.update(kw)
    return mr.MusicRequest(**base)


def test_music_medium_present_and_last():
    mediums = create_mediums.default_mediums()
    assert mediums[-1].id == "music"
    m = mediums[-1]
    assert (m.label, m.icon, m.kind, m.source, m.generator, m.uses_llm) == ("Music", "🎵", "audio", "music", None, False)
    found = create_mediums.discover_mediums(artgen_names=["verse", "ansi"])
    assert found[-1].id == "music"
    shuffled = create_mediums.sort_mediums_visual_first(list(reversed(found)))
    assert shuffled[-1].id == "music"


def test_worker_saves_record_with_audio_type_and_params(tmp_path):
    with FakeMusicServer(style="music") as fake:
        req = _req(lyrics="la la", seconds=12.0)
        store, out, _ = _run(fake, req, tmp_path)
    assert out["err"] == []
    rec = out["done"][0]
    assert store.records == [rec]
    assert rec.media_type == "audio" and rec.generator_type == "music"
    assert rec.model_id == "fiona-crapple-merged" and rec.prompt == "slow synth pads"
    assert Path(rec.file_path).parent == tmp_path and Path(rec.file_path).read_bytes().startswith(b"fLaC")
    p = json.loads(rec.params)
    assert set(p) == {"prompt", "lyrics", "seconds_asked", "seconds_got", "ended", "seed", "steps", "cfg_scale", "server_url",
                      "credit", "source_clip"}
    assert p["seconds_got"] == 12.0 and p["seconds_asked"] == 12.0 and p["seed"] == 7
    assert p["credit"] == "MiniMax-Music3" and p["source_clip"] is None and p["server_url"] == fake.url
    assert p["lyrics"] == "la la"
    assert rec.thumbnail_path == "" and rec.starred == 0


def test_worker_records_ended_early_duration(tmp_path):
    """Review focus 1: a song shorter than asked stores the length actually received."""
    with FakeMusicServer(style="music", ended_early=True) as fake:
        store, out, _ = _run(fake, _req(lyrics="short", seconds=30.0), tmp_path)
    p = json.loads(out["done"][0].params)
    assert p["seconds_asked"] == 30.0 and p["seconds_got"] == 5.2 and p["ended"] is True


def test_worker_busy_calls_on_error_with_plain_message(tmp_path):
    with FakeMusicServer(busy=True) as fake:
        store, out, _ = _run(fake, _req(), tmp_path)
    assert out["done"] == [] and store.records == []
    assert len(out["err"]) == 1
    assert "busy" in out["err"][0].lower() and "Traceback" not in out["err"][0] and "Exception" not in out["err"][0]


def test_worker_server_dies_mid_job_cancels_and_errors(tmp_path):
    """Review focus 3: the server stops answering after accepting the job."""
    with FakeMusicServer(down_after_submit=True) as fake:
        store, out, _ = _run(fake, _req(), tmp_path)
        assert fake.cancelled, "a DELETE for the abandoned job must have been tried"
    assert out["done"] == [] and store.records == []
    assert len(out["err"]) == 1 and "not reachable" in out["err"][0]


def test_worker_cancel_sends_delete_and_says_cancelled(tmp_path):
    with FakeMusicServer(steps_before_done=1000) as fake:
        store = _Store()
        out = {"err": [], "done": []}
        w = worker.MusicGenerationWorker(store, _server(fake), _req(), tmp_path, poll_s=0.01)
        w.cancel()
        w.run_with_callbacks(on_progress=lambda m: None, on_finished=out["done"].append, on_error=out["err"].append)
    assert out["done"] == [] and out["err"] == ["Cancelled."]


def test_record_and_error_text_never_contain_token(tmp_path):
    """Review focus 5: the token reaches the server and nowhere else."""
    with FakeMusicServer(require_token=TOKEN) as fake:
        store, out, _ = _run(fake, _req(), tmp_path, token=TOKEN)
        rec = out["done"][0]
        assert all(TOKEN not in str(v) for v in vars(rec).values())
        assert TOKEN not in rec.params
        assert not any(TOKEN in m for m in out["progress"])
    with FakeMusicServer(require_token="the-real-one", error_500_echoes_headers=True) as fake:
        store, out, _ = _run(fake, _req(), tmp_path, token=TOKEN)
    assert out["err"] and all(TOKEN not in m for m in out["err"])
    with FakeMusicServer(error_500_echoes_headers=True) as fake:
        store, out, _ = _run(fake, _req(), tmp_path, token=TOKEN)
    assert out["err"] and all(TOKEN not in m for m in out["err"])


def test_invalid_request_is_a_plain_error_not_a_crash(tmp_path):
    with FakeMusicServer() as fake:
        store, out, _ = _run(fake, _req(seconds=9999.0), tmp_path)
    assert out["done"] == [] and "between" in out["err"][0]
