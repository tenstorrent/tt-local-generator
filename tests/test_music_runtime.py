"""music_runtime: profiles from a server's health reply, and the job client, against a fake server."""
import dataclasses
import pytest

from fake_music_server import FakeMusicServer, STYLES
import music_runtime as mr
from music_runtime import MusicClient, MusicRequest, ServerBusy, ServerDown, MusicServerError, profile_for


STABLE_HEALTH = STYLES["audio"]["health"]
MINIMAX_HEALTH = STYLES["music"]["health"]


def client_for(srv, style="audio", token=None):
    health = srv.health_body
    return MusicClient(srv.url, profile_for(health), token=token, timeout=5.0)


def req(**kw):
    base = dict(prompt="warm folk", lyrics=None, seconds=10, seed=7, steps=None, cfg_scale=None)
    base.update(kw)
    return MusicRequest(**base)


def test_profile_from_health_reads_limits():
    p = profile_for(STABLE_HEALTH)
    assert p.min_seconds == 1 and p.max_seconds == 120 and p.cfg_default == 1.0
    assert p.jobs_prefix == "/v1/audio" and p.text_fields == ("prompt",) and not p.has_lyrics
    assert p.steps_default == 8 and p.steps_max == 100 and (p.cfg_min, p.cfg_max) == (1.0, 20.0)


def test_profile_table_supplies_lyrics_for_minimax():
    p = profile_for(MINIMAX_HEALTH)          # health has no limits, so the table must
    assert p.has_lyrics and p.text_fields == ("caption", "lyrics")
    assert p.jobs_prefix == "/v1/music" and (p.min_seconds, p.max_seconds) == (5, 60)
    assert "fiona crapple" in p.triggers and "MiniMax-Music3" in p.credit


def test_unknown_server_gets_generic_profile():
    p = profile_for({"status": "ok", "model": "mystery-synth"})
    assert p.text_fields == ("prompt",) and not p.has_lyrics and p.min_seconds < p.max_seconds
    assert p.triggers == ()


def test_schema_with_paths_sets_the_prefix():
    schema = {"paths": {"/v1/music/jobs": {}, "/v1/music/jobs/{id}": {}}}
    assert profile_for({"status": "ok", "model": "mystery-synth"}, schema).jobs_prefix == "/v1/music"


def test_generate_round_trip_returns_audio_and_seed():
    with FakeMusicServer("audio") as srv:
        seen = []
        res = client_for(srv).generate(req(steps=4, cfg_scale=2.0), on_progress=seen.append, poll_s=0.01)
    assert res.audio.startswith(b"fLaC") and res.audio_format == "flac" and res.seed == 7
    assert res.seconds_got == 10 and res.ended is False and res.job_id
    assert srv.submitted[0] == {"prompt": "warm folk", "duration": 10, "seed": 7, "steps": 4, "cfg_scale": 2.0}
    assert seen


def test_minimax_wire_names_and_lyrics():
    with FakeMusicServer("music") as srv:
        r = req(prompt="fiona crapple, warm", lyrics="[Verse 1]\nla", seconds=20)
        res = client_for(srv, "music").generate(r, poll_s=0.01)
    body = srv.submitted[0]
    assert body["input"] == "[Verse 1]\nla" and body["instructions"] == "fiona crapple, warm" and body["audio_duration"] == 20
    assert body["response_format"] == "flac" and res.seed == 7


def test_instrumental_sends_the_placeholder():
    with FakeMusicServer("music") as srv:
        client_for(srv, "music").generate(req(lyrics=None, instrumental=True, seconds=10), poll_s=0.01)
    assert srv.submitted[0]["input"] == mr.INSTRUMENTAL_PLACEHOLDER


def test_busy_raises_serverbusy_with_plain_message():
    with FakeMusicServer("audio", busy=True) as srv:
        with pytest.raises(ServerBusy) as e:
            client_for(srv).submit(req())
    assert "busy" in str(e.value).lower() and "429" not in str(e.value)


def test_down_raises_serverdown():
    c = MusicClient("http://127.0.0.1:1", profile_for(STABLE_HEALTH), timeout=1.0)
    with pytest.raises(ServerDown):
        c.health()
    with FakeMusicServer("audio", down_after_submit=True) as srv:
        with pytest.raises(ServerDown):
            client_for(srv).generate(req(), poll_s=0.01)


def test_abandoned_job_is_cancelled():
    with FakeMusicServer("audio", steps_before_done=50) as srv:
        calls = iter([False, True])
        with pytest.raises(MusicServerError):
            client_for(srv).generate(req(), poll_s=0.01, should_cancel=lambda: next(calls, True))
    assert srv.cancelled == ["job001"]


def test_ended_early_reports_seconds_got_and_ended():
    with FakeMusicServer("music", ended_early=True) as srv:
        res = client_for(srv, "music").generate(req(lyrics="la", seconds=20), poll_s=0.01)
    assert res.seconds_got == 5.2 and res.ended is True


def test_token_sent_as_bearer_and_absent_from_errors():
    token = "sekrit-token-123"
    with FakeMusicServer("audio", require_token=token) as srv:
        assert client_for(srv, token=token).health()["status"] == "ok"
        with pytest.raises(MusicServerError) as e:
            client_for(srv, token="wrong-token-999").health()
        assert "wrong-token-999" not in str(e.value)
    with FakeMusicServer("audio", error_500_echoes_headers=True) as srv:
        with pytest.raises(MusicServerError) as e:
            client_for(srv, token=token).generate(req(), poll_s=0.01)
    assert token not in str(e.value) and token not in repr(e.value)


@pytest.mark.parametrize("kw", [dict(seconds=0.5), dict(seconds=500), dict(steps=0), dict(steps=1000), dict(cfg_scale=99.0), dict(prompt="  ")])
def test_request_refused_before_submit_when_out_of_range(kw):
    with FakeMusicServer("audio") as srv:
        with pytest.raises(ValueError):
            client_for(srv).submit(req(**kw))
    assert srv.submitted == []


def test_minimax_needs_lyrics_unless_instrumental():
    with FakeMusicServer("music") as srv:
        with pytest.raises(ValueError):
            client_for(srv, "music").submit(req(lyrics="  ", seconds=10))
    assert srv.submitted == []


def test_profile_table_sets_max_chars_and_dialect():
    mm, sa = profile_for(MINIMAX_HEALTH), profile_for(STABLE_HEALTH)
    assert (mm.max_chars, mm.dialect) == (1000, "caption+lyrics")
    assert (sa.max_chars, sa.dialect) == (2000, "tags")
    generic = profile_for({"status": "ok", "model": "mystery-synth"})
    assert (generic.max_chars, generic.dialect) == (None, "")
    # positional construction (the fields before the new ones) still works
    old = mr.MusicProfile(*[getattr(generic, f.name) for f in dataclasses.fields(generic)][:-2])
    assert old.max_chars is None and old.dialect == ""
