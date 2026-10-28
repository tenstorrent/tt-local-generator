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
    # display order puts Music right after Video and Image
    shuffled = create_mediums.sort_mediums_visual_first(list(reversed(found)))
    assert [m.id for m in shuffled[:3]] == ["video", "image", "music"]


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


def test_token_never_reaches_the_progress_callback(tmp_path):
    """A server that echoes the Authorization header into the job's `stage` must not put the token on screen."""
    with FakeMusicServer(require_token=TOKEN, echo_token_in_stage=True, steps_before_done=3) as fake:
        store, out, _ = _run(fake, _req(), tmp_path, token=TOKEN)
    assert out["done"], out["err"]
    assert any(m.startswith("sampling") for m in out["progress"])      # the echoed stage did arrive, scrubbed
    assert all(TOKEN not in m for m in out["progress"])


@pytest.mark.parametrize("bad", ["/../../x", "../evil", "mp3", ""])
def test_hostile_or_unknown_audio_format_stays_inside_out_dir(tmp_path, bad):
    """The extension is server-supplied: it can neither name a path nor save a file the result panel cannot play."""
    out_dir = tmp_path / "lib"
    store = _Store()
    with FakeMusicServer() as fake:
        w = worker.MusicGenerationWorker(store, _server(fake), _req(), out_dir, poll_s=0.01)
        real = mr.MusicClient._result

        def hostile(self, job_id, view, req):
            r = real(self, job_id, view, req)
            r.audio_format = bad
            return r

        mr.MusicClient._result = hostile
        try:
            done, err = [], []
            w.run_with_callbacks(lambda m: None, done.append, err.append)
        finally:
            mr.MusicClient._result = real
    written = [p for p in tmp_path.rglob("*") if p.is_file() and "media_store" not in p.parts]
    assert all(out_dir in p.parents for p in written), written
    assert done and Path(done[0].file_path).suffix in (".flac", ".wav")      # fell back to the profile's own format
    assert not err


def test_unplayable_profile_format_fails_cleanly(tmp_path):
    import dataclasses
    with FakeMusicServer() as fake:
        srv = _server(fake)
        srv = dataclasses.replace(srv, profile=dataclasses.replace(srv.profile, formats=("mp3",)))
        real = mr.MusicClient._result

        def mp3(self, job_id, view, req):
            r = real(self, job_id, view, req)
            r.audio_format = "mp3"
            return r

        mr.MusicClient._result = mp3
        try:
            done, err = [], []
            worker.MusicGenerationWorker(_Store(), srv, _req(), tmp_path / "lib", poll_s=0.01).run_with_callbacks(
                lambda m: None, done.append, err.append)
        finally:
            mr.MusicClient._result = real
    assert done == [] and len(err) == 1 and "FLAC or WAV" in err[0]
    assert not list(tmp_path.rglob("*.mp3"))


# These tests use the "music" (MiniMax-style) fake, which rejects empty lyrics, so they pass lyrics="la".
def test_worker_sets_waveform_thumbnail_at_save_time(tmp_path, monkeypatch):
    import audio_thumb

    def fake_thumb(audio, out, size=(320, 180)):
        Path(out).write_bytes(b"png")
        return out

    monkeypatch.setattr(audio_thumb, "make_waveform_thumbnail", fake_thumb)
    with FakeMusicServer(style="music") as fake:
        store, out, _ = _run(fake, _req(lyrics="la"), tmp_path)
    rec = out["done"][0]
    assert rec.thumbnail_path.endswith(".png") and Path(rec.thumbnail_path).read_bytes() == b"png"


def test_worker_thumbnail_failure_leaves_empty_path_and_still_saves(tmp_path, monkeypatch):
    import audio_thumb

    monkeypatch.setattr(audio_thumb, "make_waveform_thumbnail", lambda *a, **k: None)
    with FakeMusicServer(style="music") as fake:
        store, out, _ = _run(fake, _req(lyrics="la"), tmp_path)
    assert out["err"] == [] and out["done"][0].thumbnail_path == ""
    assert store.records == [out["done"][0]]


def test_worker_thumbnail_exception_never_reaches_the_user(tmp_path, monkeypatch):
    import audio_thumb

    def boom(*a, **k):
        raise RuntimeError("pillow exploded")

    monkeypatch.setattr(audio_thumb, "make_waveform_thumbnail", boom)
    with FakeMusicServer(style="music") as fake:
        store, out, _ = _run(fake, _req(lyrics="la"), tmp_path)
    assert out["err"] == [] and out["done"][0].thumbnail_path == ""
