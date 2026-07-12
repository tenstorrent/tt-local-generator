import sys
import pytest
from pathlib import Path

# Add app/ to sys.path for all tests so they can import app modules directly
# without each test file repeating this boilerplate.
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


@pytest.fixture(autouse=True)
def _isolate_pipeline_store(tmp_path, monkeypatch):
    """Redirect PipelineStore index to tmp_path for every test.

    Prevents test runs from writing fake run records into the production
    pipeline-index.json, which would corrupt the app's startup state.

    This fixture runs before each test's own monkeypatching, establishing
    a safe default. Tests that do their own pipeline_store monkeypatching
    will override this redirect for their specific tmp_path, which is fine.
    """
    import pipeline_store as _ps
    safe_dir = tmp_path / "pipeline_store"
    safe_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_ps, "_INDEX_PATH", safe_dir / "pipeline-index.json")
    monkeypatch.setattr(_ps, "_RUNS_DIR", safe_dir)


@pytest.fixture(autouse=True)
def _isolate_media_store(tmp_path, monkeypatch):
    """Redirect media_store's lazy singleton (and artgen path helpers) to
    tmp_path for every test.

    Root cause of a real incident: media_store.py exposes a lazy singleton
    proxy (`media_store = _MediaStoreProxy()`) that opens the REAL
    `~/.local/share/tt-video-gen/media.db` on first attribute access. There
    was no equivalent of `_isolate_pipeline_store` (above) for it. Tests that
    exercise a code path calling `_ms.add()` (e.g. the animatediff CLI route,
    which mocks `run_subprocess` so no real gif is ever written but still
    records a "successful" artgen entry) inserted stub rows straight into the
    production DB. Repeated test runs accreted ~200 stub records before this
    was caught.

    Mirrors `_isolate_pipeline_store`: this runs before each test's own
    monkeypatching, establishing a safe tmp-backed store by default. Tests
    that do their own media_store monkeypatching can still override it for
    their own tmp_path, which is fine.

    Two modules need redirecting, not one:
      - media_store.py: owns the `_media_store_singleton` global and its own
        STORAGE_DIR/ARTGEN_DIR/ARTGEN_THUMB_DIR constants.
      - artgen_thumb.py: `make_artgen_path`/`make_thumbnail` are *defined*
        there (media_store.py only re-exports them for backwards
        compatibility), so they close over artgen_thumb's own module-level
        ARTGEN_DIR/ARTGEN_THUMB_DIR constants, not media_store's copies.
        Patching only media_store's constants would leave artifact paths
        (e.g. the animatediff CLI's output .gif path) pointing at the real
        home directory even though DB records went to tmp.
    """
    import media_store as _ms
    import artgen_thumb as _at

    safe_dir = tmp_path / "media_store"
    artgen_dir = safe_dir / "artgen"
    artgen_thumb_dir = artgen_dir / "thumbnails"
    artgen_thumb_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(_ms, "STORAGE_DIR", safe_dir, raising=False)
    monkeypatch.setattr(_ms, "ARTGEN_DIR", artgen_dir, raising=False)
    monkeypatch.setattr(_ms, "ARTGEN_THUMB_DIR", artgen_thumb_dir, raising=False)

    monkeypatch.setattr(_at, "_STORAGE_DIR", safe_dir, raising=False)
    monkeypatch.setattr(_at, "ARTGEN_DIR", artgen_dir, raising=False)
    monkeypatch.setattr(_at, "ARTGEN_THUMB_DIR", artgen_thumb_dir, raising=False)

    # Replace the lazy singleton itself so `media_store.media_store` (the
    # proxy) and `media_store._get_media_store()` both resolve to a store
    # rooted under tmp_path, regardless of which module imported it first.
    store = _ms.MediaStore(safe_dir / "media.db")
    monkeypatch.setattr(_ms, "_media_store_singleton", store, raising=False)
