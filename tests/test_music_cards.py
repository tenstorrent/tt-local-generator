"""Tests for app/music_prompts/cards.py (style cards and loader).

Tests never read the real home directory: user dirs are tmp paths.
"""
import logging
from pathlib import Path

import pytest
import yaml

from music_prompts import cards as C


def _card(id="a", **kw):
    d = {"id": id, "kind": "style", "name": id.title(), "family": "fam", "sound": "A plain sound."}
    d.update(kw)
    return d


def _write(dir_: Path, fname: str, data):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / fname).write_text(yaml.safe_dump(data), encoding="utf-8")


def _empty(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    return d


def test_load_library_reads_shipped_cards(tmp_path):
    lib = C.load_library(user_dir=tmp_path / "none")
    assert {"post-rock", "tortoise", "bluegrass-vocal"} <= set(lib.cards)
    assert lib.card("tortoise").parent == "post-rock"
    assert lib.card("bluegrass-vocal").lyric_themes
    assert lib.card("nope") is None
    assert "post-rock" in lib.families()
    assert [c.id for c in lib.of_kind("artist")] == ["tortoise"]


def test_validate_card_reports_missing_required_fields():
    errs = C.validate_card({})
    for f in ("id", "kind", "name", "family", "sound"):
        assert any(f in e for e in errs)
    assert C.validate_card(_card()) == []
    assert C.validate_card(_card(kind="bogus"))


def test_artist_card_requires_a_parent_style():
    assert any("parent" in e for e in C.validate_card(_card(kind="artist")))
    assert C.validate_card(_card(kind="artist", parent="a")) == []


def test_user_card_overrides_a_shipped_card_with_the_same_id(tmp_path):
    ship, user = tmp_path / "ship", tmp_path / "user"
    _write(ship, "a.yaml", _card("a", sound="shipped"))
    _write(user, "a.yaml", _card("a", sound="user"))
    lib = C.load_library(ship, user)
    assert lib.card("a").sound == "user"


def test_bad_yaml_file_is_skipped_and_logged(tmp_path, caplog):
    ship = tmp_path / "ship"
    _write(ship, "good.yaml", _card("good"))
    (ship / "bad.yaml").write_text("id: [unclosed\n  : :", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        lib = C.load_library(ship, tmp_path / "none")
    assert set(lib.cards) == {"good"}
    assert "bad.yaml" in caplog.text


def test_card_missing_fields_is_skipped_and_others_load(tmp_path, caplog):
    ship = tmp_path / "ship"
    _write(ship, "good.yaml", _card("good"))
    _write(ship, "broken.yaml", {"id": "broken", "kind": "style"})
    with caplog.at_level(logging.WARNING):
        lib = C.load_library(ship, tmp_path / "none")
    assert set(lib.cards) == {"good"}
    assert "broken" in caplog.text


def test_duplicate_id_within_one_directory_keeps_the_first_and_logs(tmp_path, caplog):
    ship = tmp_path / "ship"
    _write(ship, "1.yaml", _card("dup", sound="first"))
    _write(ship, "2.yaml", _card("dup", sound="second"))
    with caplog.at_level(logging.WARNING):
        lib = C.load_library(ship, tmp_path / "none")
    assert lib.card("dup").sound == "first"
    assert "dup" in caplog.text


def test_missing_user_dir_is_fine(tmp_path):
    ship = tmp_path / "ship"
    _write(ship, "a.yaml", _card("a"))
    lib = C.load_library(ship, tmp_path / "does-not-exist")
    assert set(lib.cards) == {"a"}


def test_aliases_are_indexed_case_and_hyphen_insensitively(tmp_path):
    ship = tmp_path / "ship"
    _write(ship, "a.yaml", _card("a", name="Some  Name", aliases=["Post-Rock", "post_rock  band"]))
    lib = C.load_library(ship, tmp_path / "none")
    assert lib.by_alias["post rock"] == ("a",)
    assert lib.by_alias["post rock band"] == ("a",)
    assert lib.by_alias["some name"] == ("a",)
    assert C.normalise_alias("  Foo-Bar_baz  QUX ") == "foo bar baz qux"


def test_every_shipped_card_passes_validate_card():
    shipped = Path(C.__file__).parent / "cards"
    files = sorted(shipped.glob("*.yaml"))
    assert files
    for f in files:
        assert C.validate_card(yaml.safe_load(f.read_text(encoding="utf-8"))) == [], f.name


def test_unknown_goes_with_id_is_logged_not_fatal(tmp_path, caplog):
    ship = tmp_path / "ship"
    _write(ship, "a.yaml", _card("a", goes_with=["b", "ghost"], clashes_with=["fam", "phantom"]))
    _write(ship, "b.yaml", _card("b", family="other"))
    with caplog.at_level(logging.WARNING):
        lib = C.load_library(ship, tmp_path / "none")
    # kept as plain family tags
    assert lib.card("a").goes_with == ("b", "ghost")
    assert "ghost" in caplog.text and "phantom" in caplog.text
    assert "'b'" not in caplog.text  # real id and family names are not reported
    assert "fam" not in caplog.text.replace("family", "")
