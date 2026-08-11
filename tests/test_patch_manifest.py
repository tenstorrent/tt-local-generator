import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import patch_manifest as pm

def test_manifest_has_the_inject_and_append_entries():
    ids = {e.id for e in pm.PATCHES}
    assert "inject-run-docker-server-mounts" in ids
    assert "append-skyreels-video-yaml" in ids
    assert "append-animate-video-yaml" in ids

def test_inject_entry_carries_the_docker_env_vars_anchor():
    e = next(e for e in pm.PATCHES if e.id == "inject-run-docker-server-mounts")
    assert e.kind == "inject"
    assert e.target == "workflows/run_docker_server.py"
    assert "    for key, value in docker_env_vars.items():" in e.anchors

def test_append_entries_target_video_yaml_with_no_anchor():
    for eid in ("append-skyreels-video-yaml", "append-animate-video-yaml"):
        e = next(e for e in pm.PATCHES if e.id == eid)
        assert e.kind == "append"
        assert e.target == "workflows/model_specs/dev/video.yaml"
        assert e.anchors == ()

def test_bind_mounts_are_discovered_with_correct_dest():
    by_target = {e.target: e for e in pm.PATCHES if e.kind == "bind_mount"}
    # constants.py overlays the server config module
    c = by_target["patches/media_server_config/config/constants.py"]
    assert c.dest == "tt-metal/server/config/constants.py"
    # a tt_dit pipeline overlays under tt-metal/models/tt_dit/
    w = by_target["patches/tt_dit/pipelines/wan/pipeline_wan.py"]
    assert w.dest == "tt-metal/models/tt_dit/pipelines/wan/pipeline_wan.py"

def test_manifest_is_internally_consistent():
    assert pm.manifest_issues() == []


def test_models_tree_is_orphaned_not_a_bind_mount():
    # patches/models/ has no apply_patches.sh mount loop, so its files must NOT
    # be bind_mount entries (that would report false-green); they must surface
    # as orphans instead.
    bind_targets = [e.target for e in pm.PATCHES if e.kind == "bind_mount"]
    assert not any(t.startswith("patches/models/") for t in bind_targets)
    orphans = pm.orphaned_patch_files()
    assert any(o.startswith("patches/models/") for o in orphans)
    assert len(orphans) >= 2  # t5 encoder + motif pipeline today
