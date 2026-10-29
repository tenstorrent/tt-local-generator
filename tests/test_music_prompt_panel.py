"""The Music panel's prompt tools (Task 7): the wand, Write the prompt, Smooth with AI, Styles, Undo, the result
strip, and the "In the style of" chip that now translates its value.

Everything runs against the shipped card library with no network: the LLM check is answered by an injected
callable, and Smooth uses an injected smoother. Run under xvfb with GDK_BACKEND=x11."""
import sys
import threading
import time
from pathlib import Path

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import create_param_panels as cpp
import music_prompts
import music_runtime as mr
from chip_config import ChipCategory, ChipEntry
from create_mediums import Medium

MUSIC = Medium(id="music", label="Music", icon="M", kind="audio", source="music", generator=None, uses_llm=False)


@pytest.fixture(autouse=True)
def _no_real_settings(monkeypatch):
    monkeypatch.setattr(cpp, "_remember_music_server", lambda url: None)
    monkeypatch.setattr(cpp, "_last_music_server", lambda: "")
    monkeypatch.setattr(cpp, "INSTRUMENTAL_SWITCH_CONFIRMED", True)


def _server(model_hint):
    return mr.MusicServer(url="http://localhost:8795", profile=mr.profile_for({"model": model_hint}), up=True,
                          saved=False, label=model_hint)


class _TopEntry:
    """Stands in for CreateView's top prompt entry: the getter/setter pair the panel is given."""

    def __init__(self, text=""):
        self.text = text
        self.sets = []

    def get(self):
        return self.text

    def set(self, text):
        self.sets.append(text)
        self.text = text


def _panel(model_hint="stable-audio", text="", zoned=False, **kw):
    p = cpp.MusicParamPanel(**kw)
    if zoned:
        cpp.RoleZonePanel(p, MUSIC)
    else:
        p.build()
    p.set_server(_server(model_hint) if model_hint else None)
    top = _TopEntry(text)
    p.set_prompt_getter(top.get)
    p.set_prompt_setter(top.set)
    return p, top


def _strip(p):
    return p._result_label.get_label()


def _wait_for(cond, timeout=10.0):
    """Run the GLib main loop until `cond()` holds (worker results arrive through GLib.idle_add)."""
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("timed out waiting for the worker result")
        ctx.iteration(False)
        time.sleep(0.005)


def _descendants(w):
    c = w.get_first_child()
    while c is not None:
        yield c
        yield from _descendants(c)
        c = c.get_next_sibling()


# ── wand ────────────────────────────────────────────────────────────────────


def test_wand_button_fills_the_prompt_via_the_setter():
    p, top = _panel("stable-audio")
    assert p._wand_btn.get_tooltip_text() == "Surprise me"
    p._wand_btn.emit("clicked")
    assert top.sets and top.text.strip()
    assert p.collect()["prompt"] == top.text.strip()


def test_wand_on_stable_audio_profile_leaves_lyrics_alone_and_shows_no_vocal(monkeypatch):
    lib = music_prompts.load_library()
    p, top = _panel("stable-audio")
    p._lyrics_buffer.set_text("my own words")
    for _ in range(12):
        p._wand_btn.emit("clicked")
        t = p._last_translation
        assert not any(lib.cards[c].kind == "vocal" for c in t.used_cards if c in lib.cards)
        assert "sing" not in top.text.lower() and "vocal" not in top.text.lower()
        # The strip may list a card's "avoid" entries ("emotive vocal runs"); it never reports a vocal layer.
        assert "cannot sing" not in _strip(p) and "lead line" not in _strip(p)
        assert p._lyrics_text() == "my own words"

    # Even a result that carries lyrics never reaches the lyrics box of a model without lyrics,
    # whether the box is full (and the user would say yes) or empty.
    monkeypatch.setattr(music_prompts, "wand", lambda *a, **k: _fake(prompt="ambient pads", lyrics="[Verse]\nla"))
    p.confirm_replace_lyrics = lambda msg: True
    p._wand_btn.emit("clicked")
    assert p._lyrics_text() == "my own words"
    p._lyrics_buffer.set_text("")
    p._wand_btn.emit("clicked")
    assert p._lyrics_text() == ""


def _fake(prompt="", lyrics=None):
    return music_prompts.Translation(prompt, lyrics, (), (), (), "caption+lyrics", ())


# ── Write the prompt ───────────────────────────────────────────────────────


def test_write_the_prompt_translates_the_typed_text_and_keeps_the_users_words():
    p, top = _panel("stable-audio", text="a rainy Tuesday Tortoise-like song")
    p._write_btn.emit("clicked")
    assert "rainy Tuesday" in top.text
    assert "vibraphone" in top.text                         # the Tortoise card's sound
    assert "tortoise" not in top.text.lower()


def test_undo_restores_the_text_before_the_last_wand():
    p, top = _panel("fiona-crapple", text="my first idea")
    assert not p._undo_btn.get_sensitive()
    p._wand_btn.emit("clicked")
    assert top.text != "my first idea"
    assert p._undo_btn.get_sensitive()
    p._undo_btn.emit("clicked")
    assert top.text == "my first idea"
    assert not p._undo_btn.get_sensitive()


def test_lyrics_not_overwritten_when_the_user_has_typed_some_without_confirmation():
    text = "tortoise-like song but someone is singing a bluegrass song on top"
    asked = []
    # Anything other than the literal True is a no.
    p, top = _panel("fiona-crapple", text=text)
    p.confirm_replace_lyrics = lambda msg: (asked.append(msg), "yes")[1]
    p._lyrics_buffer.set_text("my verse")
    p._write_btn.emit("clicked")
    assert asked, "the user was not asked"
    assert p._lyrics_text() == "my verse"
    assert top.text != text                                  # the prompt itself was still written

    # A yes replaces them, and Undo restores both the prompt and the lyrics.
    top.text = text
    p.confirm_replace_lyrics = lambda msg: True
    p._write_btn.emit("clicked")
    assert p._lyrics_text().strip() and p._lyrics_text() != "my verse"
    p._undo_btn.emit("clicked")
    assert top.text == text and p._lyrics_text() == "my verse"

    # An empty lyrics box is filled without asking.
    p._lyrics_buffer.set_text("")
    p.confirm_replace_lyrics = lambda msg: pytest.fail("asked although the lyrics box was empty")
    p._write_btn.emit("clicked")
    assert "[Verse]" in p._lyrics_text()


def test_lyrics_only_write_is_undoable_without_touching_an_earlier_prompt(monkeypatch):
    p, top = _panel("fiona-crapple", text="Tortoise")
    p._write_btn.emit("clicked")
    written = top.text
    assert written != "Tortoise"
    p._lyrics_buffer.set_text("mine")
    monkeypatch.setattr(music_prompts, "translate", lambda *a, **k: _fake(prompt="", lyrics="[Verse]\nnew"))
    p.confirm_replace_lyrics = lambda msg: True
    p._write_btn.emit("clicked")
    assert p._lyrics_text() == "[Verse]\nnew"
    assert p._undo_btn.get_sensitive()
    p._undo_btn.emit("clicked")
    assert p._lyrics_text() == "mine"
    assert top.text == written                     # the earlier Write is not undone with it


def test_result_dropped_when_the_model_changed_during_the_run():
    p, top = _panel("stable-audio", text="Tortoise")
    p.set_llm_available(True)
    p.smooth_fn = lambda text: text
    p._smooth_check.set_active(True)
    p._write_btn.emit("clicked")
    p.set_server(_server("fiona-crapple"))          # before the worker's result is handled
    _wait_for(lambda: p._write_btn.get_sensitive())
    assert top.sets == []
    assert "model changed" in _strip(p)


def test_result_strip_shows_added_phrases_notes_and_inspired_by_names_but_prompt_has_none():
    p, top = _panel("stable-audio", text="Tortoise")
    p._write_btn.emit("clicked")
    t = music_prompts.translate("Tortoise", _server("stable-audio").profile)
    strip = _strip(p)
    assert t.added[0] in strip
    assert "Not asked for" in strip                          # one of the notes
    assert "Inspired by: Tortoise" in strip
    assert "Tortoise" not in top.text and "Millions Now Living" not in top.text


def test_result_strip_asks_for_a_server_when_none_is_picked():
    p, top = _panel(None, text="Tortoise")
    p._write_btn.emit("clicked")
    assert "pick a server to tune the prompt for a model" in _strip(p).lower()
    assert "vibraphone" in top.text                          # the generic dialect still works


# ── Styles ─────────────────────────────────────────────────────────────────


def test_styles_menu_lists_groups_from_menu_tree():
    p, _top = _panel("stable-audio")
    labels = [w.get_label() for w in _descendants(p._styles_box) if isinstance(w, Gtk.Label)]
    for group in music_prompts.menu_tree():
        assert group.title in labels
        assert any(group.items[0].label in (lbl or "") for lbl in labels)


def test_picking_a_style_inserts_its_name_for_the_next_translate():
    p, top = _panel("stable-audio", text="a rainy afternoon")
    items = {i.card_id: i for g in music_prompts.menu_tree() for i in g.items}
    picked = "tortoise"
    p.pick_style(items[picked])
    assert top.text.startswith("a rainy afternoon")
    assert items[picked].label in top.text
    p._write_btn.emit("clicked")
    assert picked in p._last_translation.used_cards


def test_picking_a_style_after_a_trailing_comma_does_not_double_it():
    p, top = _panel("stable-audio", text="jazz, ")
    item = next(i for g in music_prompts.menu_tree() for i in g.items if i.card_id == "tortoise")
    p.pick_style(item)
    assert top.text == f"jazz, {item.label}"


def test_style_buttons_insert_through_a_click():
    p, top = _panel("stable-audio")
    btn = next(w for w in _descendants(p._styles_box) if isinstance(w, Gtk.Button))
    btn.emit("clicked")
    label = btn.get_label().removeprefix("inspired by ")
    assert label and label in top.text


# ── Smooth with AI ─────────────────────────────────────────────────────────


def test_smooth_checkbox_disabled_without_an_llm(monkeypatch):
    monkeypatch.setattr(cpp, "_run_in_thread", lambda fn, done: done(fn()))
    p, _top = _panel("stable-audio", llm_available_fn=lambda: False)
    assert not p._smooth_check.get_active()
    assert not p._smooth_check.get_sensitive()
    assert "chat model" in (p._smooth_check.get_tooltip_text() or "")
    q, _top = _panel("stable-audio", llm_available_fn=lambda: True)
    assert q._smooth_check.get_sensitive() and not q._smooth_check.get_active()


def test_smooth_checkbox_off_until_the_check_answers():
    p, _top = _panel("stable-audio")                       # no check at all: never assume a model is there
    assert not p._smooth_check.get_sensitive()


def test_no_library_work_on_the_gtk_thread_when_smooth_is_on(monkeypatch):
    main = threading.current_thread()
    seen = []
    real = music_prompts.translate

    def spy(*a, **k):
        seen.append((threading.current_thread(), k.get("smooth"), k.get("smooth_fn")))
        return real(*a, **k)

    monkeypatch.setattr(music_prompts, "translate", spy)
    idles = []
    real_idle = GLib.idle_add
    monkeypatch.setattr(cpp.GLib, "idle_add", lambda fn, *a: (idles.append(fn), real_idle(fn, *a))[1])
    p, top = _panel("stable-audio", text="Tortoise")
    p.set_llm_available(True)
    smoother = lambda text: text + ", hazy"                  # noqa: E731
    p.smooth_fn = smoother
    p._smooth_check.set_active(True)
    p._write_btn.emit("clicked")
    assert top.sets == []                                    # nothing written synchronously
    _wait_for(lambda: bool(top.sets))
    assert seen and all(t is not main for t, _s, _f in seen)
    assert seen[0][1] is True and seen[0][2] is smoother
    assert idles, "the result did not come back through GLib.idle_add"
    assert top.text.endswith(", hazy")


# ── the "In the style of" chip ─────────────────────────────────────────────


def _style_of_pills():
    entry = ChipEntry(label="In the style of...", text="in the style of {}", tip="", freetext=True)
    pills = cpp.ModifierPills("music", artgen=True)
    box = pills._build_free_text_chip(entry)
    field, btn = pills._freetext_widgets[id(entry)]
    return pills, field, btn, box


def test_in_the_style_of_chip_translates_a_known_name_and_falls_back_for_an_unknown_one():
    pills, field, btn, _box = _style_of_pills()
    pills.style_of_fn = lambda value: cpp.style_of_text(value, _server("stable-audio").profile)
    field.set_text("Tortoise")
    btn.emit("clicked")
    applied = pills.applied_text()
    assert "vibraphone" in applied and "tortoise" not in applied.lower()
    field.set_text("Zorblax Q")
    btn.emit("clicked")
    assert pills.applied_text().endswith("in the style of Zorblax Q")


def test_style_of_chip_uses_the_panel_profile_under_role_zone_panel():
    p = cpp.MusicParamPanel()
    z = cpp.RoleZonePanel(p, MUSIC)
    p.set_server(_server("stable-audio"))
    assert z._modifier_pills.style_of_fn("Tortoise").startswith("chicago post-rock")


def test_style_of_chip_without_a_translator_keeps_the_literal_text():
    """Other mediums never had a style-of chip; a bare ModifierPills keeps the old behaviour."""
    pills, field, btn, _box = _style_of_pills()
    pills.style_of_fn = None
    field.set_text("Tortoise")
    btn.emit("clicked")
    assert pills.applied_text() == "in the style of Tortoise"


# ── invariants ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model_hint", ["stable-audio", "fiona-crapple"])
def test_collect_is_unchanged_by_the_new_controls(model_hint):
    p, top = _panel(model_hint, text="a slow piano piece")
    p._lyrics_buffer.set_text("[Verse]\nla la")
    before = p.collect()
    p.set_llm_available(True)
    p._smooth_check.set_active(True)
    p._smooth_check.set_active(False)
    p._result_label.set_label("anything")
    p._undo_btn.set_sensitive(True)
    assert p.collect() == before
    # With the tools removed altogether the dict is the same.
    for w in (p._wand_btn, p._write_btn, p._smooth_check, p._styles_btn, p._undo_btn):
        w.unparent()
    assert p.collect() == before


def test_header_widgets_mount_under_role_zone_panel():
    p = cpp.MusicParamPanel()
    z = cpp.RoleZonePanel(p, MUSIC)
    for w in p.header_widgets():
        assert w.get_parent() is z


class _Inline:
    """The CreateView health refresh runs inline, as in test_create_view_music."""

    def __init__(self, target=None, daemon=None):
        self._t = target

    def start(self):
        self._t()


def _inline_view(monkeypatch):
    import create_view
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))
    monkeypatch.setattr(create_view.threading, "Thread", _Inline)
    image = Medium(id="image", label="Image", icon="I", kind="image", source="native", generator=None)
    return create_view.CreateView(mediums_fn=lambda: [image, MUSIC], health_fn=lambda: {})


def test_panel_minimum_width_still_fits_800px(monkeypatch):
    view = _inline_view(monkeypatch)
    win = Gtk.Window()
    win.set_child(view)
    view._chip_buttons["music"].set_active(True)
    srv = mr.MusicServer(url="http://192.168.100.200:20000", profile=mr.profile_for({"model": "fiona-crapple"}),
                         up=True, saved=False, label="MiniMax")
    view.set_music_servers([srv])
    panel = view._active_panel._panel
    panel._prompt_setter("Tortoise")
    panel._write_btn.emit("clicked")                       # a full result strip
    host = next(w for w in _descendants(view) if "create-panel-host" in w.get_css_classes())
    assert any(w is panel._wand_btn for w in _descendants(host)), "the prompt tools are not mounted"
    minimum = host.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    assert minimum < 800, f"Music form needs {minimum}px at minimum"


def test_create_view_wires_the_prompt_setter_to_the_top_entry(monkeypatch):
    view = _inline_view(monkeypatch)
    view._chip_buttons["music"].set_active(True)
    panel = view._active_panel._panel
    view._prompt_entry.set_text("Tortoise")
    panel._write_btn.emit("clicked")
    assert "vibraphone" in view._prompt_entry.get_text()
