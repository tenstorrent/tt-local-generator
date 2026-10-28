"""Music status segment: resolved from music discovery, never from SERVERS."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import status_segments as ss  # noqa: E402
from model_status import Status  # noqa: E402


class _D:
    def __init__(self, key):
        self.key = key


_CAPS = {
    "prompt": [_D("prompt-server")], "image": [_D("flux")],
    "video": [_D("wan2.2")], "animate": [], "artgen": [_D("artgen-qwen3-8b")],
}


def _states(snap, **kw):
    return ss.segment_states(snap, servers_for_capability=lambda cap: _CAPS.get(cap, []), **kw)


def _srv(url="http://127.0.0.1:20001", up=True, label="", token="sekrit"):
    return SimpleNamespace(url=url, up=up, label=label, token=token, saved=False, profile=None)


def test_music_segment_off_with_no_servers():
    assert _states({})["music"] == Status.OFF
    assert _states({}, music_servers=())["music"] == Status.OFF


def test_music_segment_off_when_only_down_servers():
    assert _states({}, music_servers=[_srv(up=False)])["music"] == Status.OFF


def test_music_segment_ready_for_up_server():
    assert _states({}, music_servers=[_srv(up=False), _srv(up=True)])["music"] == Status.READY


def test_music_segment_ready_only_for_music_servers():
    # a ready video server (and every other server) leaves Music off ...
    snap = {k: Status.READY for k in ("prompt-server", "flux", "wan2.2", "artgen-qwen3-8b")}
    st = _states(snap)
    assert st["music"] == Status.OFF
    # ... and a ready music server leaves Video off
    st = _states({}, music_servers=[_srv()])
    assert st["music"] == Status.READY
    assert st["video"] == Status.OFF and st["image"] == Status.OFF


def test_segments_for_other_functions_unchanged():
    snap = {"flux": Status.STARTING, "wan2.2": Status.READY}
    with_music = _states(snap, music_servers=[_srv()])
    without = _states(snap)
    for k in ("prompt", "image", "video", "artgen"):
        assert with_music[k] == without[k]
    assert with_music["video"] == Status.READY and with_music["image"] == Status.STARTING


def test_music_is_a_fifth_segment_after_art_llm():
    assert ss.SEGMENT_KEYS == ("prompt", "image", "video", "artgen", "music")
    assert segment_label("music") == "Music"


def segment_label(key):
    return next(label for k, label, _c in ss.SEGMENTS if k == key)


def test_music_server_key_belongs_to_no_segment():
    assert ss.segment_for_server_key("wan2.2", servers_for_capability=lambda c: _CAPS.get(c, [])) == "video"
    assert ss.segment_for_server_key("music", servers_for_capability=lambda c: _CAPS.get(c, [])) is None


def test_music_tooltip_names_servers_never_tokens():
    tip = ss.music_tooltip([_srv(label="ACE-Step", token="sekrit"), _srv(url="http://h:2", up=False),
                            _srv(url="http://127.0.0.1:20002", label="")])
    assert "ACE-Step" in tip and "http://127.0.0.1:20002" in tip
    assert "http://h:2" not in tip          # a down server is not listed as up
    assert "sekrit" not in tip
    assert ss.music_tooltip([]) == "Music: no music server found"
