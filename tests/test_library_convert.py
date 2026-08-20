#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Tests for app/library_convert.py -- the `tt-ctl convert-library` engine.

Two conversions, both idempotent and fail-soft:
  1. media-type fold: legacy animatediff/animate rows -> media_type='video'
     (reuses media_store.fold_media_types -- the SAME SQL the app's own
     startup migration runs, just invoked ungated).
  2. stale-thumbnail regeneration: re-render every non-gif artgen thumbnail
     with the current renderer (artgen_thumb.make_thumbnail).

`analyze()` must be strictly read-only; `apply()` performs the work and
returns a report dict. Both are exercised against a bare sqlite3-seeded DB
(mirroring tests/test_media_store.py's seeding pattern) so this suite never
depends on a running MediaStore/GTK.
"""
import importlib.machinery
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _seed_db(db_path: Path, artgen_dir: Path):
    """Seed a raw sqlite DB (pre-MediaStore, pre-migration) with a mix of
    rows exercising both fold-pending and thumbs-pending detection, plus
    rows that must NOT be touched by either conversion."""
    from media_store import _SCHEMA

    artgen_dir.mkdir(parents=True, exist_ok=True)

    # Real source files for the artgen rows whose thumbnails should be
    # regenerated (and one that intentionally has no on-disk file, and one
    # with an empty thumbnail_path -- both must be excluded from the count).
    ansi_src = artgen_dir / "ansi1.ans"
    ansi_src.write_text("\x1b[38;5;196m█\x1b[38;5;46m█\n"
                         "\x1b[38;5;46m█\x1b[38;5;196m█\n")

    palette_src = artgen_dir / "pal1.json"
    palette_src.write_text(json.dumps({
        "colors": [{"hex": "#ff0000"}, {"hex": "#00ff00"}, {"hex": "#0000ff"}],
        "lore": "a test palette",
    }))

    verse_src = artgen_dir / "verse1.txt"
    verse_src.write_text("the forge\nsleeps\nin ash\n")

    codeart_src = artgen_dir / "code1.py"
    codeart_src.write_text("def forge():\n    pass\n")

    gif_src = artgen_dir / "ad_artgen.gif"
    gif_src.write_bytes(b"GIF89a-not-a-real-gif-but-has-an-extension")

    def _placeholder_thumb(name: str) -> Path:
        p = artgen_dir / "thumbnails" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"stale-garbage-not-a-real-image")
        return p

    ansi_thumb = _placeholder_thumb("ansi1_thumb.png")
    palette_thumb = _placeholder_thumb("pal1_thumb.png")
    verse_thumb = _placeholder_thumb("verse1_thumb.png")

    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)

    def ins(id_, media_type, generator_type, file_path, thumbnail_path=""):
        con.execute(
            "INSERT INTO media (id,media_type,created_at,file_path,thumbnail_path,"
            "prompt,model_id,generator_type,params,starred) VALUES "
            "(?,?,?,?,?,?,?,?,?,0)",
            (id_, media_type, "2026-01-01T00:00:00", file_path, thumbnail_path,
             "", "m", generator_type, "{}"),
        )

    # -- fold-pending rows --------------------------------------------------
    ins("ad_native", "animatediff", None, "/nonexistent/ad_native.gif")
    ins("ad_artgen", "artgen", "animatediff", str(gif_src))
    ins("anim", "animate", None, "/nonexistent/anim.mp4")
    # -- untouched rows -------------------------------------------------------
    ins("vid", "video", None, "/nonexistent/vid.mp4")
    ins("img", "image", None, "/nonexistent/img.png")
    # -- thumbs-pending rows --------------------------------------------------
    ins("ansi1", "artgen", "ansi", str(ansi_src), str(ansi_thumb))
    ins("pal1", "artgen", "palette", str(palette_src), str(palette_thumb))
    ins("verse1", "artgen", "verse", str(verse_src), str(verse_thumb))
    # -- excluded from thumbs-pending ------------------------------------------
    ins("nofile", "artgen", "landscape", "/nonexistent/nofile.svg", str(artgen_dir / "x.png"))
    ins("nothumb", "artgen", "codeart", str(codeart_src), "")

    con.commit()
    con.close()
    return {
        "ansi_thumb": ansi_thumb,
        "palette_thumb": palette_thumb,
        "verse_thumb": verse_thumb,
    }


@pytest.fixture()
def seeded(tmp_path):
    db_path = tmp_path / "media.db"
    artgen_dir = tmp_path / "artgen"
    thumbs = _seed_db(db_path, artgen_dir)
    return db_path, thumbs


# ── media_store.fold_media_types extraction ─────────────────────────────────

def test_fold_media_types_is_module_level_and_reusable(tmp_path):
    """The fold SQL must be a standalone, ungated function so the converter
    can call it directly regardless of PRAGMA user_version."""
    from media_store import fold_media_types, _SCHEMA

    db = tmp_path / "m.db"
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA)
    con.execute(
        "INSERT INTO media (id,media_type,created_at,file_path,thumbnail_path,"
        "prompt,model_id,generator_type,params,starred) VALUES "
        "('a','animatediff','2026-01-01T00:00:00','/x','', '', 'm', NULL, '{}', 0)"
    )
    con.commit()

    rowcount = fold_media_types(con)
    con.commit()

    assert rowcount >= 1
    row = con.execute("SELECT media_type, generator_type FROM media WHERE id='a'").fetchone()
    assert row == ("video", "animatediff")


def test_existing_migration_still_behaves_identically(tmp_path):
    """Regression guard: media_store's own gated startup migration must be
    behavior-identical after the fold SQL is extracted into fold_media_types.
    This mirrors tests/test_media_store.py::test_animatediff_and_animate_fold_into_video."""
    from media_store import MediaStore, _SCHEMA

    db = tmp_path / "m.db"
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA)
    con.execute(
        "INSERT INTO media (id,media_type,created_at,file_path,thumbnail_path,"
        "prompt,model_id,generator_type,params,starred) VALUES "
        "('ad','animatediff','2026-01-01T00:00:00','/x','', '', 'm', NULL, '{}', 0)"
    )
    con.commit()
    con.close()

    ms = MediaStore(db_path=db)
    row = ms._conn.execute("SELECT media_type, generator_type FROM media WHERE id='ad'").fetchone()
    assert row == ("video", "animatediff")
    assert ms._conn.execute("PRAGMA user_version").fetchone()[0] == 1


# ── analyze() ────────────────────────────────────────────────────────────────

def test_analyze_reports_fold_pending_count(seeded):
    from library_convert import analyze
    db_path, _ = seeded
    report = analyze(db_path)
    assert report["fold_pending"] == 3  # ad_native, ad_artgen, anim


def test_analyze_reports_thumbs_pending_count(seeded):
    from library_convert import analyze
    db_path, _ = seeded
    report = analyze(db_path)
    # ansi1, pal1, verse1 -- NOT ad_artgen (.gif), NOT nofile (missing src),
    # NOT nothumb (no thumbnail_path)
    assert report["thumbs_pending"] == 3


def test_analyze_writes_nothing(seeded):
    from library_convert import analyze
    db_path, thumbs = seeded
    before_mtime = db_path.stat().st_mtime_ns
    before_thumb_bytes = thumbs["ansi_thumb"].read_bytes()

    analyze(db_path)
    analyze(db_path)

    assert db_path.stat().st_mtime_ns == before_mtime
    assert thumbs["ansi_thumb"].read_bytes() == before_thumb_bytes
    con = sqlite3.connect(db_path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0
    # Rows must be untouched too.
    row = con.execute("SELECT media_type FROM media WHERE id='ad_native'").fetchone()
    assert row == ("animatediff",)


# ── apply() ──────────────────────────────────────────────────────────────────

def test_apply_folds_media_types(seeded):
    from library_convert import apply
    db_path, _ = seeded
    report = apply(db_path, regen_thumbnails=False)
    assert report["folded"] == 3

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT media_type, generator_type FROM media WHERE id='ad_native'").fetchone() == ("video", "animatediff")
    assert con.execute("SELECT media_type, generator_type FROM media WHERE id='ad_artgen'").fetchone() == ("video", "animatediff")
    assert con.execute("SELECT media_type, generator_type FROM media WHERE id='anim'").fetchone() == ("video", "animate")
    # untouched
    assert con.execute("SELECT media_type FROM media WHERE id='vid'").fetchone() == ("video",)
    assert con.execute("SELECT media_type FROM media WHERE id='img'").fetchone() == ("image",)


def test_apply_fold_is_idempotent(seeded):
    from library_convert import apply
    db_path, _ = seeded
    first = apply(db_path, regen_thumbnails=False)
    second = apply(db_path, regen_thumbnails=False)
    assert first["folded"] == 3
    assert second["folded"] == 0


def test_apply_regenerates_stale_thumbnails(seeded):
    from library_convert import apply
    db_path, thumbs = seeded

    before = {name: p.read_bytes() for name, p in thumbs.items()}

    report = apply(db_path, regen_thumbnails=True)

    assert report["thumbs_regenerated"] == 3
    assert report["thumbs_failed"] == 0

    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    for name, p in thumbs.items():
        assert p.exists()
        after_bytes = p.read_bytes()
        assert after_bytes != before[name], f"{name} thumbnail was not rewritten"
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(p))
        assert pixbuf.get_width() > 1
        assert pixbuf.get_height() > 1


def test_apply_skips_thumbnails_when_disabled(seeded):
    from library_convert import apply
    db_path, thumbs = seeded
    before = thumbs["ansi_thumb"].read_bytes()

    report = apply(db_path, regen_thumbnails=False)

    assert report["thumbs_regenerated"] == 0
    assert thumbs["ansi_thumb"].read_bytes() == before


def test_apply_backup_copies_db_before_mutating(seeded, tmp_path):
    from library_convert import apply
    db_path, _ = seeded

    report = apply(db_path, regen_thumbnails=False, backup=True)

    backup_path = Path(report["backup_path"])
    assert backup_path.exists()
    assert backup_path.parent == db_path.parent
    assert backup_path.name.startswith(db_path.name)
    # The backup must be a COMPLETE, usable database captured BEFORE the fold
    # ran -- so it still shows the pre-fold row states. (We verify usability by
    # querying it, not by byte-equality: the online backup API rebuilds the
    # file so it is never byte-identical to the source.)
    bak = sqlite3.connect(backup_path)
    try:
        assert bak.execute(
            "SELECT media_type FROM media WHERE id='ad_native'"
        ).fetchone() == ("animatediff",)
        assert bak.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 10
    finally:
        bak.close()


def test_apply_backup_is_complete_for_wal_mode_db(tmp_path):
    """Regression for the WAL-mode backup bug: a real media.db runs in WAL
    mode (MediaStore sets PRAGMA journal_mode=WAL), so recently-written rows
    can live in the -wal sidecar. A plain shutil.copy2 of only the main .db
    file would silently produce an empty shell. Seed via a REAL MediaStore
    (which engages WAL) and assert the backup is a complete, queryable copy."""
    from media_store import MediaStore, MediaRecord
    from library_convert import apply

    db_path = tmp_path / "media.db"
    ms = MediaStore(db_path=db_path)
    # Confirm the seeding path really is WAL mode (the whole point of this test).
    assert ms._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    ms.add(MediaRecord(
        id="ad", media_type="animatediff", created_at="2026-01-01T00:00:00",
        file_path="/nonexistent/ad.gif", thumbnail_path="", prompt="",
        model_id="animatediff-blackhole", generator_type=None, params="{}", starred=0,
    ))
    # Leave the store's connection OPEN so the WAL is actively held -- exactly
    # the worst case (GUI still running) the file-copy backup silently corrupts.

    report = apply(db_path, regen_thumbnails=False, backup=True)

    backup_path = Path(report["backup_path"])
    bak = sqlite3.connect(backup_path)
    try:
        # The backup must contain the schema AND the row that only ever lived
        # in the WAL -- proving the sidecar data was captured, not lost.
        assert bak.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
        assert bak.execute(
            "SELECT media_type FROM media WHERE id='ad'"
        ).fetchone() == ("animatediff",)
    finally:
        bak.close()


def test_apply_without_backup_has_no_backup_path(seeded):
    from library_convert import apply
    db_path, _ = seeded
    report = apply(db_path, regen_thumbnails=False, backup=False)
    assert report.get("backup_path") in (None, "")


def test_apply_one_bad_record_does_not_abort_run(seeded):
    """A record whose source file vanishes between analyze and apply (or any
    other per-record failure) must be counted as failed/skipped, never crash
    the whole run."""
    from library_convert import apply
    db_path, thumbs = seeded

    # Corrupt one source file's on-disk content so its render can plausibly
    # fail, while leaving the DB row pointing at it (simulates a real-world
    # half-broken library). Even if make_thumbnail degrades to a placeholder
    # instead of raising, this must not affect the other two records.
    con = sqlite3.connect(db_path)
    con.execute("UPDATE media SET file_path='/definitely/does/not/exist.ans' WHERE id='ansi1'")
    con.commit()
    con.close()

    report = apply(db_path, regen_thumbnails=True)

    # The loop walks every media_type='artgen' row (5: ansi1, pal1, verse1,
    # nofile, nothumb) -- ansi1 (now missing its source) joins nofile/nothumb
    # as a skip, but pal1/verse1 still succeed regardless.
    assert report["thumbs_regenerated"] + report["thumbs_failed"] + report["thumbs_skipped"] == 5
    assert report["thumbs_regenerated"] == 2
    assert report["thumbs_failed"] == 0


# ── tt-ctl wiring smoke test ─────────────────────────────────────────────────

_TTCTL_PATH = Path(__file__).parent.parent / "tt-ctl"


def _load_tt_ctl():
    loader = importlib.machinery.SourceFileLoader("tt_ctl_libconv", str(_TTCTL_PATH))
    spec = importlib.util.spec_from_loader("tt_ctl_libconv", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tt_ctl_convert_library_dry_run_smoke(seeded, capsys):
    tt_ctl = _load_tt_ctl()
    db_path, _ = seeded
    args = tt_ctl._build_parser().parse_args(
        ["convert-library", "--db", str(db_path)]
    )
    tt_ctl.cmd_convert_library(args)
    out = capsys.readouterr().out
    assert "dry run" in out.lower()
    assert "--apply" in out
    # Nothing written in dry-run mode.
    con = sqlite3.connect(db_path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0


def test_tt_ctl_convert_library_apply_smoke(seeded, capsys):
    tt_ctl = _load_tt_ctl()
    db_path, _ = seeded
    args = tt_ctl._build_parser().parse_args(
        ["convert-library", "--db", str(db_path), "--apply", "--no-thumbnails"]
    )
    tt_ctl.cmd_convert_library(args)
    out = capsys.readouterr().out
    assert "fold" in out.lower()
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT media_type FROM media WHERE id='ad_native'").fetchone() == ("video",)
