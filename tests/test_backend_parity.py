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
    for key in ("wan2.2", "wan2.2-i2v", "skyreels", "mochi"):
        assert pe._backend_for("TTLGImageToVideo", {"model": key}).key == key


def test_backend_for_disambiguates_substring_shadowed_video_keys():
    # Regression: "wan2.2" is a substring of "wan2.2-i2v"; the exact-match pass
    # in _match_server_key must win so wan2.2-i2v is never shadowed by wan2.2
    # (mirrors the flux/flux-dev case covered by the image sweep above).
    assert pe._backend_for("TTLGImageToVideo", {"model": "wan2.2-i2v"}).key == "wan2.2-i2v"
    assert pe._backend_for("TTLGImageToVideo", {"model": "wan2.2"}).key == "wan2.2"
