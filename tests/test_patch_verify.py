import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import patch_verify as pv
import patch_manifest as pm
import pytest


def _fixture_vendor(tmp_path, *, anchor=True, yaml=True):
    """Build a minimal vendor tree that satisfies the declared inject/append entries."""
    root = tmp_path / "tt-inference-server"
    wf = root / "workflows"
    wf.mkdir(parents=True)
    rds = wf / "run_docker_server.py"
    body = "def build():\n"
    if anchor:
        body += "    for key, value in docker_env_vars.items():\n        pass\n"
    rds.write_text(body)
    catalog = wf / "model_specs" / "dev"
    catalog.mkdir(parents=True)
    if yaml:
        (catalog / "video.yaml").write_text("- weights:\n    - X/Y\n")
    return root


# Only the declared (inject/append) entries, so tests don't depend on the real
# bind-mount patch files under patches/.
_DECLARED = tuple(e for e in pm.PATCHES if e.kind in ("inject", "append"))


def test_verify_passes_when_anchor_and_yaml_present(tmp_path):
    root = _fixture_vendor(tmp_path)
    results = pv.verify(root, _DECLARED)
    assert [r for r in results if r.level == "error" and not r.ok] == []


def test_missing_anchor_is_a_loud_error(tmp_path):
    # This is the Steps-7/8/9 regression: the anchor moved/vanished upstream.
    root = _fixture_vendor(tmp_path, anchor=False)
    results = pv.verify(root, _DECLARED)
    errs = [r for r in results if r.level == "error" and not r.ok]
    assert any("inject-run-docker-server-mounts" in r.id for r in errs)


def test_require_raises_patcherror_on_missing_anchor(tmp_path):
    root = _fixture_vendor(tmp_path, anchor=False)
    with pytest.raises(pv.PatchError):
        pv.require(root, _DECLARED)


def test_missing_append_target_is_a_loud_error(tmp_path):
    root = _fixture_vendor(tmp_path, yaml=False)
    results = pv.verify(root, _DECLARED)
    errs = [r for r in results if r.level == "error" and not r.ok]
    assert any(r.id.startswith("append-") for r in errs)


def test_broken_bind_mount_patch_fails(tmp_path):
    bad = pm.PatchEntry(
        id="bind-broken", kind="bind_mount",
        target="patches/does_not_exist_broken.py",
        dest="tt-metal/server/x.py",
    )
    # A missing source file is an error (manifest_issues also catches this).
    results = pv.verify(tmp_path / "tt-inference-server", (bad,))
    assert any(not r.ok and r.level == "error" for r in results)


def test_version_at_most_boundaries():
    assert pv.version_at_most("0.19.0", "0.19.0") is True
    assert pv.version_at_most("0.18.9", "0.19.0") is True
    assert pv.version_at_most("0.20.0", "0.19.0") is False
    assert pv.version_at_most("0.19.0rc1", "0.19.0") is True
    assert pv.version_at_most("0.19", "0.19.0") is True


def test_cli_exits_nonzero_on_missing_anchor(tmp_path):
    root = _fixture_vendor(tmp_path, anchor=False)
    rc = pv.main(["--vendor", str(root)])
    assert rc != 0


def test_cli_manifest_only_passes_on_real_manifest():
    assert pv.main(["--manifest-only"]) == 0


def test_current_vendor_version_reads_sibling_file(tmp_path):
    vroot = tmp_path / "tt-inference-server"
    vroot.mkdir(parents=True)
    (tmp_path / "VENDOR_VERSION").write_text("0.19.0\n")
    assert pv._current_vendor_version(vroot) == "0.19.0"


def test_absorbed_patch_warns_not_errors(tmp_path):
    vroot = tmp_path / "tt-inference-server"
    (vroot / "workflows").mkdir(parents=True)
    (tmp_path / "VENDOR_VERSION").write_text("0.30.0\n")  # above the ceiling
    entry = pm.PatchEntry(
        id="bind-ceiling", kind="bind_mount",
        target="app/patch_verify.py",  # any real, compilable file under repo root
        dest="tt-metal/server/x.py", version_ceiling="0.19.0",
    )
    results = pv.verify(vroot, (entry,))
    assert any(r.level == "warning" and not r.ok for r in results)
    assert [r for r in results if r.level == "error" and not r.ok] == []


def test_full_manifest_verify_surfaces_orphans_as_warnings(tmp_path):
    # A full-manifest run (entries=None) appends a warning per orphaned patch
    # file and never an error for them.
    root = tmp_path / "tt-inference-server"
    (root / "workflows" / "model_specs" / "dev").mkdir(parents=True)
    (root / "workflows" / "run_docker_server.py").write_text(
        "    for key, value in docker_env_vars.items():\n")
    (root / "workflows" / "model_specs" / "dev" / "video.yaml").write_text("- x\n")
    results = pv.verify(root)  # entries=None -> real manifest + orphan scan
    orphan_warns = [r for r in results if r.id.startswith("orphan:")]
    assert orphan_warns, "orphaned patches must be surfaced"
    assert all(r.level == "warning" for r in orphan_warns)
    # Orphans are warnings, so the gate (errors only) still passes.
    assert [r for r in results if r.level == "error" and not r.ok] == []
