import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import server_manager as sm

def test_flux_dev_serverdef_present_and_image_capable():
    sd = sm.SERVERS["flux-dev"]
    assert sd.script == "start_flux_dev.sh"
    assert sd.capabilities == ("image",)
    assert sd.runner_key == "tt-flux.1-dev"
    assert sd.health_url == "http://localhost:8000/tt-liveness"
    assert sm.SERVERS["flux-dev"] in sm.servers_for_capability("image")

def test_flux_dev_display_name_and_benefit():
    assert sm.display_name_for("flux-dev") == "FLUX.1-dev"
    assert sm.benefit_for("flux-dev")  # non-empty tagline
