import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def test_new_create_zone_defaults():
    import importlib
    import app_settings
    importlib.reload(app_settings)
    d = app_settings.DEFAULTS
    assert d["clip_length_slot"] == "standard"
    assert d["preferred_video_model"] == ""
    assert d["seed_mode"] == "random"
    assert d["pinned_seed"] == -1


def test_motion_clips_dir_default_is_empty_string(tmp_path, monkeypatch):
    """motion_clips_dir defaults to '' when not set."""
    monkeypatch.setattr("app_settings.SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("app_settings.STORAGE_DIR", tmp_path)
    from importlib import reload
    import app_settings as _mod
    reload(_mod)
    s = _mod.AppSettings()
    assert s.get("motion_clips_dir") == ""
