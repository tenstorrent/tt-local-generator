"""Guard tests proving the autouse media_store isolation fixture in conftest.py
keeps every test off the user's real ~/.local/share/tt-video-gen store.

Root cause of a real incident: media_store.py exposes a lazy singleton proxy
(`media_store = _MediaStoreProxy()`) that opens `~/.local/share/tt-video-gen/media.db`
(the REAL production DB) on first attribute access. Unlike pipeline_store, which
already had an autouse `_isolate_pipeline_store` fixture in conftest.py, there was
no equivalent for media_store. Tests that exercise `_cmd_animatediff` (mocking
`run_subprocess` so no real gif is ever written) still called `_ms.add(rec)`,
inserting a stub artgen record into the production DB on every run. Repeated
test runs accreted ~200 stub records before this was caught.

These tests are written so that if `_isolate_media_store` is removed from
conftest.py, they fail (proven manually during development by running this
file with a scratch $HOME so the "real" dir stood in for a throwaway path
instead of risking the developer's actual store).
"""
import argparse
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import media_store as _ms  # noqa: E402
from artgen import cli as artgen_cli  # noqa: E402
import artgen.generators.animatediff as ad  # noqa: E402

# Computed at call time (not import time) so it always reflects whatever
# Path.home() resolves to in the process actually running the test.
def _real_storage_dir() -> Path:
    return Path.home() / ".local" / "share" / "tt-video-gen"


def test_media_store_singleton_resolves_under_tmp_path(tmp_path):
    """The proxy's underlying singleton must be rooted under this test's
    tmp_path, never under the real ~/.local/share/tt-video-gen."""
    store = _ms._get_media_store()
    resolved = Path(store._db_path).resolve()
    real_dir = _real_storage_dir().resolve()

    assert str(resolved).startswith(str(tmp_path.resolve())), (
        f"media_store resolved to {resolved}, expected somewhere under {tmp_path} "
        "-- the autouse isolation fixture is not redirecting the singleton"
    )
    assert not str(resolved).startswith(str(real_dir)), (
        f"media_store resolved to {resolved}, which is under the REAL storage "
        f"dir {real_dir} -- this test would have polluted production data"
    )


def test_animatediff_cli_route_does_not_touch_real_storage_dir(tmp_path, monkeypatch):
    """Mirrors tests/test_animatediff_cli.py's mocking of run_subprocess (success,
    no real gif written) and proves a full CLI route -- including the media_store
    .add()/.ensure_auto_playlists() calls -- never reaches the real storage dir.

    Uses a unique marker prompt + a direct sqlite read of the *real* db file
    (independent of the proxy/singleton) rather than an mtime comparison --
    mtime resolution on some filesystems is coarse enough (1s) to mask a
    same-second write, which would make this guard falsely pass.
    """
    monkeypatch.chdir(tmp_path)
    marker = f"isolation-guard-marker-{uuid.uuid4()}"

    mock_run = MagicMock(return_value=(True, ""))
    monkeypatch.setattr(ad, "run_subprocess", mock_run)
    monkeypatch.setattr(ad, "check_hardware", lambda: (True, "ok", 1))
    monkeypatch.setattr(ad, "make_gif_thumbnail", lambda *a, **k: None)

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=None)
    ad.AnimateDiffGenerator().add_args(parser)
    args = parser.parse_args(["--prompt", marker])

    artgen_cli._cmd_animatediff(args)

    assert mock_run.call_count == 1

    real_db = _real_storage_dir() / "media.db"
    found_in_real_db = False
    if real_db.exists():
        conn = sqlite3.connect(str(real_db))
        try:
            row = conn.execute(
                "SELECT 1 FROM media WHERE prompt LIKE ? LIMIT 1", (f"{marker}%",)
            ).fetchone()
            found_in_real_db = row is not None
        finally:
            conn.close()
    assert not found_in_real_db, (
        f"marker prompt {marker!r} was written into the REAL media.db at "
        f"{real_db} -- media_store isolation fixture failed to redirect the singleton"
    )

    # The record must still be discoverable -- just in the ISOLATED store,
    # proving the test didn't just silently fail to record anything at all.
    isolated_records = _ms.media_store.query(
        media_type="artgen", generator_type="animatediff"
    )
    assert any(
        r.prompt.startswith(marker) for r in isolated_records
    ), "expected record was not found in the isolated media store"
