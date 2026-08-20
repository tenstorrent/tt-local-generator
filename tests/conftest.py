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


@pytest.fixture(autouse=True)
def _isolate_app_settings(tmp_path, monkeypatch):
    """Redirect app_settings' module-level singleton to tmp_path, and reset
    its in-memory state to DEFAULTS, for every test.

    Added for SP-3d-2 (Create's random/repeat-last/keep seed-mode control,
    `app/create_param_panels.py`'s `SeedModeControl`), the first Create-surface
    code to read/write `app_settings.settings` at all (`seed_mode`). Without
    this, a test that builds an Image/Video/Animate panel and never touches
    the seed-mode dropdown would silently pick up WHATEVER `seed_mode` happens
    to be persisted in the real `~/.local/share/tt-video-gen/settings.json` on
    the machine running the tests — nondeterministic and, if that value ever
    becomes "repeat" (one click in the real app), would flip `collect()`'s
    "seed" to a real history-derived number for every test that doesn't
    explicitly select a mode, breaking them without any code change. Mirrors
    `_isolate_pipeline_store`/`_isolate_media_store` above: prevents test runs
    from writing into (or reading stale state from) the real settings file.

    A fresh `AppSettings()` instance is built (tmp-backed, so it loads as pure
    DEFAULTS) and assigned to EVERY already-imported module-level binding, not
    just `app_settings.settings` itself. This matters because
    `test_app_settings.py::test_new_create_zone_defaults` does
    `importlib.reload(app_settings)`, which re-executes the module body and
    rebinds `app_settings.settings` to a BRAND NEW object — permanently
    orphaning any `from app_settings import settings as X` reference another
    module already bound at import time (e.g. `create_param_panels._settings`,
    captured once at test-collection time, long before any test body runs).
    After that reload, mutating `app_settings.settings`'s `_data` in place (as
    an earlier version of this fixture did) only reaches the NEW object —
    `create_param_panels._settings` keeps pointing at the OLD, permanently
    un-reset one, so seed-mode state written through it (e.g. by one test
    selecting "repeat") silently leaks into every later test's freshly-built
    panel for the rest of the session. Re-patching both names EVERY test
    heals that divergence regardless of which object either currently points
    to.

    `main_window._settings` joined the patch list for the gallery-density
    fix (v0.46.3): `GenerationCard`/`PendingCard`/`_apply_gallery_density`
    read `_settings.get("gallery_density")` at construction/toggle time via
    `main_window._gallery_density()`. Without patching this binding too, a
    test that does `from app_settings import settings as _s; _s.set(...)`
    (the natural way to drive a specific density in a test) silently talks
    to the FRESH per-test instance while `main_window`'s own already-bound
    `_settings` name keeps pointing at whatever object it saw at first
    import — the exact orphaned-reference failure mode described above, just
    for a different module. Caught by
    `test_gallery_card_uniform_size.py::test_generation_card_constructed_while_density_compact_measures_compact`
    going flaky depending on which other tests ran first in the session.
    """
    import app_settings as _as
    import create_param_panels as _cpp
    import main_window as _mw

    safe_file = tmp_path / "settings.json"
    monkeypatch.setattr(_as, "STORAGE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_as, "SETTINGS_FILE", safe_file, raising=False)

    fresh = _as.AppSettings()  # loads from safe_file (doesn't exist) -> DEFAULTS
    monkeypatch.setattr(_as, "settings", fresh, raising=False)
    monkeypatch.setattr(_cpp, "_settings", fresh, raising=False)
    monkeypatch.setattr(_mw, "_settings", fresh, raising=False)


@pytest.fixture(autouse=True)
def _keep_activity_viz_webkit_free(monkeypatch):
    """Force `activity_viz.ActivityVizWidget` to build its WebKit-less stub
    (header/mode-label only, no real `WebKit.WebView`) for every test in the
    suite.

    Root cause this guards against: Pipeline Studio's `LiveRunView.begin()`
    (Stage ambient-machine, v0.75.x) lazily but UNCONDITIONALLY builds a real
    `ActivityVizWidget` the first time a run starts — there's no opt-in Watch
    toggle on that surface, unlike Create's. Constructing a real
    `WebKit.WebView` crashes this sandbox/nested-CI environment outright
    (`bwrap: setting up uid map: Permission denied` -> SIGTRAP that kills the
    whole pytest process, not a catchable Python exception) — the same
    documented "nested-sandbox bwrap" crash class this repo's CLAUDE.md
    already calls out for `CreateResultPanel` (live rendering is verified on
    the real display instead, where WebKit works fine).

    Before this fixture existed, only `test_activity_viz.py`'s own tests
    manually patched `_WEBKIT_OK` per-test; nothing protected the OTHER ~15
    test files that transitively construct a real `LiveRunView`/
    `PipelineStudio` (test_pipeline_library_registration.py,
    test_pipeline_hero.py, test_main_window_pipelines.py, ...) from hitting
    this the moment `begin()` started calling `_ensure_activity_viz()`.
    Global + autouse, mirroring `_isolate_pipeline_store`/`_isolate_media_store`
    above, so no individual test file needs to know this crash class exists.
    """
    import activity_viz
    monkeypatch.setattr(activity_viz, "_WEBKIT_OK", False)
