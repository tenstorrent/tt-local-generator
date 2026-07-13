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


# ── Native mediums: always present, always first, deterministic order ────────

def test_native_mediums_present_first_and_in_order():
    mediums = cm.discover_mediums(artgen_names=[])
    assert [m.id for m in mediums] == ["image", "video", "animate"]


def test_native_mediums_have_expected_kind_and_source():
    mediums = {m.id: m for m in cm.discover_mediums(artgen_names=[])}
    assert mediums["image"].kind == "image"
    assert mediums["video"].kind == "video"
    assert mediums["animate"].kind == "gif"
    for m in mediums.values():
        assert m.source == "native"
        assert m.generator is None
        assert m.icon  # every native medium has some icon
        assert m.label  # every native medium has some label


def test_empty_artgen_names_returns_only_native():
    mediums = cm.discover_mediums(artgen_names=[])
    assert len(mediums) == 3
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
    assert [m.id for m in mediums] == ["image", "video", "animate", "ansi", "verse", "palette"]


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
    mediums = {m.id: m for m in cm.discover_mediums(artgen_names=["animatediff"])}
    assert mediums["animatediff"].kind == "gif"


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
    assert [m.id for m in mediums] == ["image", "video", "animate"]


def test_none_artgen_names_falls_back_to_native_only():
    mediums = cm.discover_mediums(artgen_names=None)
    assert [m.id for m in mediums] == ["image", "video", "animate"]


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
    assert [m.id for m in mediums] == ["image", "video", "animate", "verse", "ansi"]


def test_default_mediums_never_crashes_if_artgen_import_fails(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "artgen":
            raise ImportError("simulated: artgen unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    mediums = cm.default_mediums()
    assert [m.id for m in mediums] == ["image", "video", "animate"]
