"""Unit tests for log viewer pure-Python helpers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def test_detect_log_path_from_full_log_line():
    from log_viewer import detect_log_path
    msg = "generate_blackhole_v2.py exited with rc=1\n\nLast output:\nfoo\n\nFull log: /home/ttuser/code/tt-local-generator/logs/animatediff/run_20260529_072047_001b89f4.log"
    assert detect_log_path(msg) == "/home/ttuser/code/tt-local-generator/logs/animatediff/run_20260529_072047_001b89f4.log"


def test_detect_log_path_missing():
    from log_viewer import detect_log_path
    assert detect_log_path("Something went wrong") is None


def test_detect_log_path_log_prefix():
    from log_viewer import detect_log_path
    msg = "Script exited 0 but no output file\nLog: /tmp/animatediff.log"
    assert detect_log_path(msg) == "/tmp/animatediff.log"


def test_shorten_error_truncates_at_80():
    from log_viewer import shorten_error
    long_msg = "A" * 100 + "\nSecond line"
    result = shorten_error(long_msg)
    assert len(result) <= 83  # 80 + "..."
    assert result.endswith("…")


def test_shorten_error_short_message_unchanged():
    from log_viewer import shorten_error
    msg = "AnimateDiff requires Blackhole hardware"
    assert shorten_error(msg) == msg


def test_shorten_error_strips_log_path():
    from log_viewer import shorten_error
    msg = "generate_blackhole_v2.py exited with rc=1\n\nFull log: /very/long/path.log"
    result = shorten_error(msg)
    assert "/very/long/path.log" not in result


def test_parse_run_log_name_returns_display():
    from log_viewer import parse_run_log_name
    name = "run_20260529_072047_20260529_142047_001b89f4.log"
    display, ts_str = parse_run_log_name(name)
    assert "07:20" in display
    assert ts_str  # non-empty date string


def test_parse_run_log_name_unrecognised():
    from log_viewer import parse_run_log_name
    display, ts_str = parse_run_log_name("animatediff.log")
    assert display == "animatediff"
    assert ts_str == ""


def test_parse_server_log_name_extracts_model():
    from log_viewer import parse_server_log_name
    name = "media_2026-05-06_08-14-06_SkyReels-V2-I2V-14B-540P_p300x2_server.log"
    model, date_str = parse_server_log_name(name)
    assert model == "SkyReels-V2-I2V-14B-540P"
    assert "May" in date_str or "2026" in date_str


def test_parse_server_log_name_wan():
    from log_viewer import parse_server_log_name
    name = "media_2026-05-06_09-10-02_Wan2.2-T2V-A14B-Diffusers_p300x2_server.log"
    model, _ = parse_server_log_name(name)
    assert model == "Wan2.2-T2V-A14B-Diffusers"


def test_is_error_log_rc1():
    from log_viewer import is_error_log
    content = "some output\nexited with rc=1\nmore"
    assert is_error_log(content) is True


def test_is_error_log_traceback():
    from log_viewer import is_error_log
    content = "running...\nTraceback (most recent call last):\n  File foo"
    assert is_error_log(content) is True


def test_is_error_log_clean():
    from log_viewer import is_error_log
    content = "# animatediff run\nSaved 4 frames to /tmp/out.gif\n"
    assert is_error_log(content) is False


def test_collect_log_files_animatediff(tmp_path):
    from log_viewer import collect_log_files
    logs_dir = tmp_path / "logs" / "animatediff"
    logs_dir.mkdir(parents=True)
    (logs_dir / "run_20260529_072047_20260529_142047_001b89f4.log").write_text("exited with rc=1")
    (logs_dir / "run_20260529_081148_20260529_151148_70059cab.log").write_text("Saved 4 frames")
    (logs_dir / "animatediff.log").write_text("module log")

    files = collect_log_files(repo_root=tmp_path, prompt_log=None, animatediff_log_dir=logs_dir)
    ad_section = next(s for s in files if s["section"] == "ANIMATEDIFF")
    assert len(ad_section["files"]) == 2   # run logs only, not animatediff.log
    names = [f["name"] for f in ad_section["files"]]
    assert names[0] > names[1]   # sorted newest first (lexicographic desc)
