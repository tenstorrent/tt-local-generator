import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _reload_with_env(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TTLG_PIPELINE_MODE", raising=False)
    else:
        monkeypatch.setenv("TTLG_PIPELINE_MODE", value)
    import app_settings
    return importlib.reload(app_settings)


def test_pipeline_mode_defaults_off(monkeypatch):
    mod = _reload_with_env(monkeypatch, None)
    assert mod.PIPELINE_MODE_ENABLED is False


def test_pipeline_mode_env_truthy(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "on", " On "):
        mod = _reload_with_env(monkeypatch, v)
        assert mod.PIPELINE_MODE_ENABLED is True, v


def test_pipeline_mode_env_falsey(monkeypatch):
    for v in ("0", "false", "no", "off", ""):
        mod = _reload_with_env(monkeypatch, v)
        assert mod.PIPELINE_MODE_ENABLED is False, v


def teardown_module(_m):
    # leave app_settings in its default (env-unset) state for other tests
    import os, importlib, app_settings
    os.environ.pop("TTLG_PIPELINE_MODE", None)
    importlib.reload(app_settings)
