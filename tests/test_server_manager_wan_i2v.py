import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import server_manager as sm

def test_wan_i2v_serverdef_present_and_video_capable():
    sd = sm.SERVERS["wan2.2-i2v"]
    assert sd.script == "start_wan_i2v.sh"
    assert sd.capabilities == ("video",)
    assert sd.runner_key == "tt-wan2.2-i2v"
    assert sd.health_url == "http://localhost:8000/tt-liveness"
    assert sm.SERVERS["wan2.2-i2v"] in sm.servers_for_capability("video")

def test_wan_i2v_display_name_and_benefit():
    assert sm.display_name_for("wan2.2-i2v") == "Wan2.2 I2V"
    assert sm.benefit_for("wan2.2-i2v")  # non-empty tagline
