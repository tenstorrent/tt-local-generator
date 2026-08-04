# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for create_mediums.py — the pure medium-chip discovery core behind the
unified Create surface (Create-surface plan, Task 2).

`discover_mediums` takes every external dependency (the list of artgen
generator names) as an injected argument, so this whole file exercises it
with plain fakes — no artgen import, no disk/network I/O. `default_mediums`
(the thin real-deps wrapper) is exercised separately with `artgen.all_names`
patched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import create_mediums as cm


# ── Animate + AnimateDiff dropped (Task 2) ────────────────────────────────────

def test_animate_and_animatediff_are_not_mediums():
    ids = {m.id for m in cm.default_mediums()}
    assert "video" in ids and "image" in ids
    assert "animate" not in ids          # folded into Video as a model
    assert "animatediff" not in ids      # folded into Video as a model
    # other artgen kinds untouched
    assert "verse" in ids and "ansi" in ids and "palette" in ids


def test_sort_mediums_visual_first_orders_visual_then_textual():
    """Display sort: photographic/scenic/color kinds lead, ANSI bridges, and
    the pure-text kinds (verse/freeform/codeart) come last — without mutating
    the caller's list."""
    ms = cm.default_mediums()
    ordered = cm.sort_mediums_visual_first(ms)
    ids = [m.id for m in ordered]
    # image/video lead; the text kinds trail every visual kind.
    assert ids[0] == "image" and ids[1] == "video"
    for text_id in ("verse", "freeform", "codeart"):
        if text_id in ids:
            for visual_id in ("landscape", "palette", "ansi"):
                if visual_id in ids:
                    assert ids.index(visual_id) < ids.index(text_id)
    # Pure display sort — same set, never fewer/more mediums.
    assert {m.id for m in ordered} == {m.id for m in ms}


def test_sort_mediums_visual_first_is_stable_and_safe():
    from create_mediums import Medium
    a = Medium(id="image", label="Image", icon="", kind="image", source="native")
    b = Medium(id="video", label="Video", icon="", kind="video", source="native")
    # Unknown text-kind medium must sort AFTER known visual ones.
    z = Medium(id="mystery", label="Mystery", icon="", kind="text", source="artgen")
    out = cm.sort_mediums_visual_first([z, a, b])
    assert [m.id for m in out] == ["image", "video", "mystery"]


def test_discover_mediums_filters_animatediff_name():
    ms = cm.discover_mediums(artgen_names=["verse", "animatediff", "ansi"])
    ids = [m.id for m in ms]
    assert "animatediff" not in ids
    assert ids[:1] == ["image"]          # native still first
    assert "verse" in ids and "ansi" in ids


# ── Native mediums: always present, always first, deterministic order ────────

def test_native_mediums_present_first_and_in_order():
    mediums = cm.discover_mediums(artgen_names=[])
    assert [m.id for m in mediums] == ["image", "video"]


def test_native_mediums_have_expected_kind_and_source():
    mediums = {m.id: m for m in cm.discover_mediums(artgen_names=[])}
    assert mediums["image"].kind == "image"
    assert mediums["video"].kind == "video"
    for m in mediums.values():
        assert m.source == "native"
        assert m.generator is None
        assert m.icon  # every native medium has some icon
        assert m.label  # every native medium has some label


def test_empty_artgen_names_returns_only_native():
    mediums = cm.discover_mediums(artgen_names=[])
    assert len(mediums) == 2
    assert all(m.source == "native" for m in mediums)


# ── One Medium per artgen generator, in the given order ──────────────────────

def test_one_medium_per_artgen_generator_name():
    names = ["verse", "landscape"]
    mediums = cm.discover_mediums(artgen_names=names)
    artgen_mediums = [m for m in mediums if m.source == "artgen"]
    assert [m.id for m in artgen_mediums] == names
    for m, name in zip(artgen_mediums, names):
        assert m.generator == name
        assert m.source == "artgen"


def test_artgen_mediums_follow_native_and_preserve_given_order():
    names = ["ansi", "verse", "palette"]
    mediums = cm.discover_mediums(artgen_names=names)
    assert [m.id for m in mediums] == ["image", "video", "ansi", "verse", "palette"]


# ── Kind mapping per the brief's exact table ──────────────────────────────────

def test_kind_mapping_text_generators():
    mediums = {m.id: m for m in cm.discover_mediums(
        artgen_names=["verse", "freeform", "codeart"]
    )}
    assert mediums["verse"].kind == "text"
    assert mediums["freeform"].kind == "text"
    assert mediums["codeart"].kind == "text"


def test_kind_mapping_image_generators():
    names = ["landscape", "constellation", "geometric", "skyline", "circuit", "palette", "ansi"]
    mediums = {m.id: m for m in cm.discover_mediums(artgen_names=names)}
    for name in names:
        assert mediums[name].kind == "image", f"{name} expected kind=image"


def test_kind_mapping_gif_generator():
    # animatediff is now filtered out (folded into Video as a model), so
    # it does not appear in discovered mediums even if in the artgen_names list.
    mediums = {m.id: m for m in cm.discover_mediums(artgen_names=["animatediff"])}
    assert "animatediff" not in mediums


def test_every_artgen_medium_has_label_and_icon():
    names = ["verse", "freeform", "codeart", "landscape", "constellation",
             "geometric", "skyline", "circuit", "palette", "ansi", "animatediff"]
    mediums = cm.discover_mediums(artgen_names=names)
    for m in mediums:
        if m.source == "artgen":
            assert m.label
            assert m.icon


# ── Robustness: never crash ───────────────────────────────────────────────────

def test_unknown_generator_name_defaults_to_image_kind_and_does_not_crash():
    mediums = cm.discover_mediums(artgen_names=["some_future_plugin"])
    artgen = [m for m in mediums if m.source == "artgen"]
    assert len(artgen) == 1
    assert artgen[0].kind == "image"
    assert artgen[0].label
    assert artgen[0].icon


def test_raising_artgen_names_falls_back_to_native_only():
    class _Boom:
        def __iter__(self):
            raise RuntimeError("plugin registry unavailable")

    mediums = cm.discover_mediums(artgen_names=_Boom())
    assert [m.id for m in mediums] == ["image", "video"]


def test_none_artgen_names_falls_back_to_native_only():
    mediums = cm.discover_mediums(artgen_names=None)
    assert [m.id for m in mediums] == ["image", "video"]


def test_one_bad_generator_name_does_not_take_down_the_rest():
    # A non-string, unhashable-ish oddball must not crash discovery of the
    # other, well-formed names.
    mediums = cm.discover_mediums(artgen_names=["verse", 12345, "landscape"])
    ids = [m.id for m in mediums if m.source == "artgen"]
    assert "verse" in ids
    assert "landscape" in ids


# ── native override (dependency injection) ────────────────────────────────────

def test_native_override_replaces_default_native_list():
    custom = [cm.Medium(id="widget", label="Widget", icon="⚙", kind="image",
                         source="native", generator=None)]
    mediums = cm.discover_mediums(artgen_names=[], native=custom)
    assert [m.id for m in mediums] == ["widget"]


# ── default_mediums(): thin real-deps wrapper ─────────────────────────────────

def test_default_mediums_wraps_artgen_all_names(monkeypatch):
    import artgen
    monkeypatch.setattr(artgen, "all_names", lambda: ["verse", "ansi"])
    mediums = cm.default_mediums()
    assert [m.id for m in mediums] == ["image", "video", "verse", "ansi"]


def test_default_mediums_never_crashes_if_artgen_import_fails(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "artgen":
            raise ImportError("simulated: artgen unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    mediums = cm.default_mediums()
    assert [m.id for m in mediums] == ["image", "video"]


# ── uses_llm threading (AnimateDiff-model-fix) ────────────────────────────────
#
# AnimateDiff is an artgen medium whose generator bypasses the chat LLM
# entirely (it's a self-contained Blackhole diffusion GIF generator) -- but
# `CreateView._scoped_model_keys` used to treat every artgen medium as
# chat-LLM-backed, so its scoped "Model" dropdown asked the user to pick a
# chat model it never uses. `Medium.uses_llm` is the pure-core piece that
# threads a generator's real `ArtGenerator.uses_llm` flag through discovery
# so CreateView can tell the two cases apart. Generalized: applies to ANY
# LLM-free artgen generator, not just AnimateDiff.

def test_medium_uses_llm_defaults_true():
    """A bare Medium() with no uses_llm= argument (every existing call site,
    including every native medium and every pre-existing test fixture) must
    keep behaving exactly as before -- LLM-backed."""
    m = cm.Medium(id="widget", label="Widget", icon="⚙", kind="image",
                  source="native", generator=None)
    assert m.uses_llm is True


def test_discover_mediums_uses_llm_for_marks_generator_false():
    """A generator the injected `uses_llm_for` callable reports False for
    gets `uses_llm=False` on its Medium; everything else stays True."""
    def _uses_llm_for(name):
        return name != "codeart"

    mediums = {
        m.id: m for m in cm.discover_mediums(
            artgen_names=["verse", "codeart"], uses_llm_for=_uses_llm_for
        )
    }
    assert mediums["codeart"].uses_llm is False
    assert mediums["verse"].uses_llm is True


def test_discover_mediums_uses_llm_for_default_true_when_not_provided():
    """Omitting `uses_llm_for` entirely (every pre-existing caller/test) must
    default every artgen medium to uses_llm=True -- unaffected by this
    feature, matching the module's existing "additive, never-break-existing-
    callers" discipline."""
    mediums = {
        m.id: m for m in cm.discover_mediums(artgen_names=["verse", "codeart"])
    }
    assert mediums["verse"].uses_llm is True
    assert mediums["codeart"].uses_llm is True


def test_discover_mediums_native_mediums_always_uses_llm_true():
    """Native mediums have no generator at all -- `uses_llm` is unused for
    them and must stay the True default regardless of `uses_llm_for`."""
    mediums = cm.discover_mediums(
        artgen_names=[], uses_llm_for=lambda name: False
    )
    assert all(m.uses_llm is True for m in mediums)


def test_default_mediums_threads_real_uses_llm_flag(monkeypatch):
    """The real-deps wrapper must ask each generator's OWN `uses_llm` flag
    (via `artgen.get(name).uses_llm`, lazy-imported) rather than defaulting
    every artgen medium to True -- this is the piece that actually threads
    the LLM-free generator fix into the real Create surface."""
    import artgen

    class _FakeGen:
        def __init__(self, uses_llm):
            self.uses_llm = uses_llm

    fakes = {"verse": _FakeGen(True), "codeart": _FakeGen(False)}
    monkeypatch.setattr(artgen, "all_names", lambda: ["verse", "codeart"])
    monkeypatch.setattr(artgen, "get", lambda name: fakes[name])

    mediums = {m.id: m for m in cm.default_mediums()}
    assert mediums["verse"].uses_llm is True
    assert mediums["codeart"].uses_llm is False


def test_default_mediums_uses_llm_fails_soft_to_true_on_error(monkeypatch):
    """One bad generator's `.uses_llm` lookup raising must not crash
    discovery -- fails soft to True (the safe assumption, mirrors
    `pipeline_engine._artgen_uses_llm`'s own fail-soft default)."""
    import artgen

    def _boom(name):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(artgen, "all_names", lambda: ["verse"])
    monkeypatch.setattr(artgen, "get", _boom)

    mediums = {m.id: m for m in cm.default_mediums()}
    assert mediums["verse"].uses_llm is True
