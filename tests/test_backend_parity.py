import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pipeline_engine as pe
import model_picker as mp
import server_manager as sm


def test_backend_for_routes_every_offered_image_model():
    for key, _name, _benefit, _dot in mp.picker_entries("image", has_service=False):
        spec = pe._backend_for("TTLGTextToImage", {"model": key})
        assert spec is not None
        # The chosen key must route to a real server, not silently to a wrong default.
        assert spec.key == key, f"{key} routed to {spec.key}"


def test_backend_for_video_keys():
    for key in ("wan2.2", "skyreels", "mochi"):
        assert pe._backend_for("TTLGImageToVideo", {"model": key}).key == key
