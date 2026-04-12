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
