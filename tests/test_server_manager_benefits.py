import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import server_manager as sm


def test_benefit_for_reads_serverdef_field():
    assert "server" in sm.benefit_for("wan2.2").lower()
    assert sm.benefit_for("skyreels")           # non-empty
    assert sm.benefit_for("animate")            # non-empty


def test_benefit_for_synthetic_animatediff_from_fallback_table():
    # animatediff has NO ServerDef — must come from MODEL_BENEFITS
    assert "animatediff" not in sm.SERVERS
    assert "local" in sm.benefit_for("animatediff").lower()


def test_benefit_for_unknown_key_is_empty_string():
    assert sm.benefit_for("no-such-model") == ""


def test_display_name_for_friendly_and_fallback():
    assert sm.display_name_for("wan2.2") == "Wan 2.2"
    assert sm.display_name_for("animatediff") == "AnimateDiff"
    assert sm.display_name_for("animate") == "Animate"
    # unknown key falls back to the raw ServerDef label if present, else the key
    assert sm.display_name_for("flux") == sm.SERVERS["flux"].label
    assert sm.display_name_for("no-such-model") == "no-such-model"


def test_benefit_field_defaults_empty_for_untouched_serverdef():
    assert sm.SERVERS["flux"].benefit == ""
