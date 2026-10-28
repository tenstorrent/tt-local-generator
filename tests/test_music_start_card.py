"""The "Start a music model" card: shown when no music server is listed, hidden once one is.

Servers are MusicServer values and the fake clipboard is a seam; nothing touches the network, docker or a chip."""
import sys
from pathlib import Path

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import create_param_panels as cpp
import music_runtime as mr
from create_mediums import Medium


@pytest.fixture(autouse=True)
def _no_real_settings(monkeypatch):
    monkeypatch.setattr(cpp, "_remember_music_server", lambda url: None)
    monkeypatch.setattr(cpp, "_last_music_server", lambda: "")


def _server(hint="stable-audio", url="http://localhost:8795", up=True):
    return mr.MusicServer(url=url, profile=mr.profile_for({"model": hint}), up=up, saved=False, label=hint)


def _panel():
    p = cpp.MusicParamPanel()
    p.build()
    return p


def _texts(w):
    out = []
    c = w.get_first_child()
    while c is not None:
        if isinstance(c, Gtk.Label):
            out.append(c.get_label())
        if isinstance(c, Gtk.Button):
            out.append(c.get_label() or "")
        out += _texts(c)
        c = c.get_next_sibling()
    return out


def _buttons(w, label):
    c = w.get_first_child()
    found = []
    while c is not None:
        if isinstance(c, Gtk.Button) and c.get_label() == label:
            found.append(c)
        found += _buttons(c, label)
        c = c.get_next_sibling()
    return found


# ---- KNOWN_MODELS -------------------------------------------------------------------------------------

def test_known_models_shape():
    models = mr.KNOWN_MODELS
    assert len(models) >= 3
    by_key = {m.key: m for m in models}
    for m in models:
        assert m.label and m.blurb
        if m.command is not None:
            assert isinstance(m.command, str) and m.command.strip()
    assert by_key["stable-audio"].command is None, "no published package: nothing true to copy"
    assert "tt-stable-audio-3-small-music" in by_key["stable-audio"].path
    assert by_key["minimax"].command == "tt-model serve episod/minimax-music3-fiona-crapple-blackhole --detach"
    assert by_key["find-more"].command == "tt-model search music"


def test_default_ports_include_stable_audio():
    assert 20100 in mr.DEFAULT_PORTS and 20000 in mr.DEFAULT_PORTS and 8795 in mr.DEFAULT_PORTS


def test_connect_dialog_and_card_name_the_same_serve_command():
    """One source: the command in the card is the one the docs name."""
    cmd = next(m.command for m in mr.KNOWN_MODELS if m.key == "minimax")
    assert cmd in (Path(__file__).parent.parent / "CLAUDE.md").read_text()
    p = _panel()
    assert any(cmd in t for t in _texts(p._start_card))


# ---- visibility ---------------------------------------------------------------------------------------

def test_card_shown_with_no_servers_and_hidden_once_one_is_up():
    p = _panel()
    p.servers_changed([])
    assert p._start_card.get_visible()
    assert not p._rows["server_url"].get_visible(), "the empty dropdown is hidden"
    p.servers_changed([_server()])
    assert not p._start_card.get_visible()
    assert p._rows["server_url"].get_visible()
    p.servers_changed([])
    assert p._start_card.get_visible()


def test_only_down_servers_keep_dropdown_and_say_server_is_down():
    p = _panel()
    p.servers_changed([_server(up=False)])
    assert not p._start_card.get_visible()
    assert p._rows["server_url"].get_visible()
    assert p._server_hint.get_visible() and p._server_hint.get_label() == "Server is down"
    p.servers_changed([_server()])
    assert not p._server_hint.get_visible()


def test_card_lists_commands_ports_and_stable_audio_path():
    p = _panel()
    p.servers_changed([])
    text = "\n".join(_texts(p._start_card))
    assert "tt-model serve episod/minimax-music3-fiona-crapple-blackhole --detach" in text
    assert "tt-model search music" in text
    assert "~/code/tt-stable-audio-3-small-music" in text and "20100" in text
    assert "gozer" in text
    for port in mr.scan_ports():
        assert str(port) in text
    assert "Connect..." in text
    assert len(_buttons(p._start_card, "Copy")) == 2, "Stable Audio has nothing true to copy"


def test_card_never_shows_a_token():
    p = _panel()
    p.servers_changed([])
    assert "token" not in "\n".join(_texts(p._start_card)).lower()


# ---- Copy ---------------------------------------------------------------------------------------------

def test_copy_puts_the_exact_command_on_the_clipboard_and_reads_copied(monkeypatch):
    got = []
    monkeypatch.setattr(cpp, "_set_clipboard", got.append)
    p = _panel()
    p.servers_changed([])
    first = _buttons(p._start_card, "Copy")[0]
    first.emit("clicked")
    assert got == ["tt-model serve episod/minimax-music3-fiona-crapple-blackhole --detach"]
    assert first.get_label() == "Copied"
    p._start_card._reset_copy_labels()
    assert first.get_label() == "Copy"
    _buttons(p._start_card, "Copy")[1].emit("clicked")
    assert got[-1] == "tt-model search music"


# ---- Check again / Connect ----------------------------------------------------------------------------

def test_check_again_calls_rescan_and_shows_looking_until_servers_arrive():
    p = _panel()
    calls = []
    p.on_rescan = lambda: calls.append(1)
    p.servers_changed([])
    btn = _buttons(p._start_card, "Check again")[0]
    btn.emit("clicked")
    assert calls == [1] and btn.get_label() == "Looking..." and not btn.get_sensitive()
    p.servers_changed([])
    assert btn.get_label() == "Check again" and btn.get_sensitive()


def test_rescan_done_resets_the_button_when_nothing_changed():
    p = _panel()
    p.on_rescan = lambda: None
    p.servers_changed([])
    btn = _buttons(p._start_card, "Check again")[0]
    btn.emit("clicked")
    p.rescan_done()
    assert btn.get_label() == "Check again"


def test_check_again_without_a_callback_does_not_stick():
    p = _panel()
    p.servers_changed([])
    btn = _buttons(p._start_card, "Check again")[0]
    btn.emit("clicked")
    assert btn.get_label() == "Check again"


def test_connect_button_opens_the_connect_dialog(monkeypatch):
    opened = []
    monkeypatch.setattr(cpp.MusicConnectDialog, "present", lambda self: opened.append(self))
    p = _panel()
    p.servers_changed([])
    _buttons(p._start_card, "Connect...")[0].emit("clicked")
    assert len(opened) == 1


def test_create_view_wires_rescan_to_the_owner(monkeypatch):
    import create_view
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))
    music = Medium(id="music", label="Music", icon="M", kind="audio", source="music", generator=None, uses_llm=False)
    image = Medium(id="image", label="Image", icon="I", kind="image", source="native", generator=None)
    class _Inline:
        def __init__(self, target=None, daemon=None):
            self._t = target

        def start(self):
            self._t()

    monkeypatch.setattr(create_view.threading, "Thread", _Inline)
    rescans = []
    view = create_view.CreateView(mediums_fn=lambda: [image, music], health_fn=lambda: {},
                                  on_music_rescan=lambda: rescans.append(1), on_music_selected=lambda: None)
    win = Gtk.Window()
    win.set_child(view)
    view._chip_buttons["music"].set_active(True)
    panel = view._active_panel._panel
    panel.on_rescan()
    assert rescans == [1]
    panel.servers_changed([])
    _buttons(panel._start_card, "Check again")[0].emit("clicked")
    assert rescans == [1, 1]
    view.set_music_servers([])                     # unchanged list: the button must still come back
    assert _buttons(panel._start_card, "Check again")[0].get_label() == "Check again"


# ---- layout -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("servers", ["none", "down-stable-audio", "down-fiona-crapple-merged"])
def test_form_minimum_width_fits_with_the_card_or_hint(servers, monkeypatch):
    import create_view
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))

    class _Inline:
        def __init__(self, target=None, daemon=None):
            self._t = target

        def start(self):
            self._t()

    monkeypatch.setattr(create_view.threading, "Thread", _Inline)
    music = Medium(id="music", label="Music", icon="M", kind="audio", source="music", generator=None, uses_llm=False)
    image = Medium(id="image", label="Image", icon="I", kind="image", source="native", generator=None)
    view = create_view.CreateView(mediums_fn=lambda: [image, music], health_fn=lambda: {})
    win = Gtk.Window()
    win.set_child(view)
    view._chip_buttons["music"].set_active(True)
    if servers == "none":
        view.set_music_servers([])
    else:
        view.set_music_servers([_server(servers.split("down-")[1], url="http://192.168.100.200:20000", up=False)])
    panel = view._active_panel._panel
    assert panel._start_card.get_visible() == (servers == "none")
    host = None

    def find(w):
        nonlocal host
        if "create-panel-host" in w.get_css_classes():
            host = w
        c = w.get_first_child()
        while c is not None:
            find(c)
            c = c.get_next_sibling()

    find(view)
    minimum = host.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    assert minimum < 800, f"Music form needs {minimum}px at minimum"
