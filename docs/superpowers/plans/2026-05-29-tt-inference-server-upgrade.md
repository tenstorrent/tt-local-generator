# tt-inference-server Upgrade Audit Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a written compatibility report documenting which patches still apply cleanly to v0.15.0, which need updating, and what new features in v0.15.0 we should adopt — before touching any hardware.

**Architecture:** Pure read-only audit. No hardware changes, no service restarts, no vendor/ modifications until the report is approved. Clone the new release to a temp directory, dry-run every patch, diff the files our scripts depend on, and produce a structured report in `docs/upgrade-report-0.15.0.md`.

**Tech Stack:** bash, python3, git, GitHub API (gh cli), diff utilities. No tt-metal, no Docker.

---

## Background: What We Pin and Why

| Thing | Pinned value | Location |
|---|---|---|
| Source commit | `b5589534` (0.11.1) | `vendor/VENDOR_SHA` |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34` | `bin/start_wan_qb2.sh` |
| Latest release | `v0.15.0` → image `0.15.0-25891d3` | 232 commits ahead |

We have 4 types of patches in `patches/`:
1. **`patches/tt_dit/`** — DiT pipeline hotfixes bind-mounted into container (dev_mode only)
2. **`patches/media_server_config/`** — device config overrides (constants.py, runner files, etc.)
3. **`run_docker_server.py` HF_HOME injection** — adds `HF_HOME`/`HF_HUB_CACHE` bind-mount
4. **`model_spec.py` SkyReels injection** — adds `ModelSpecTemplate` entries for SkyReels V2 models

**Key pre-audit findings (do not re-investigate, just use):**
- `run_docker_server.py` anchor `"for key, value in docker_env_vars.items():"` **still present** in v0.15.0 (line structure intact, 903 lines vs ~700 in 0.11.1)
- HF_HOME block is **not** in v0.15.0 — patch still needed
- `media_server_config` block is **not** in v0.15.0 — patch still needed
- `tt_dit_patches_dir` block is **not** in v0.15.0 — patch still needed
- SkyReels is **absent** from `constants.py` and `model_spec.py` in v0.15.0 — injections still needed
- 300 files changed between our pin and v0.15.0; only `release_model_spec.json` and `tests/test_model_specification.py` overlap with patch-adjacent files
- `release_model_spec.json` in v0.15.0 confirms: Wan2.2-T2V, Mochi-1, FLUX.1-dev are all present; SkyReels is not

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Create | `/tmp/tt-infer-audit/` | Temp checkout of v0.15.0 for dry-run |
| Create | `docs/upgrade-report-0.15.0.md` | Compatibility report (the deliverable) |
| Read | `patches/` (all files) | Current patch content |
| Read | `bin/apply_patches.sh` | Understand each patch step |
| Read | `bin/setup_vendor.sh` | Understand vendor clone process |
| Read | `bin/start_wan_qb2.sh`, `start_skyreels_i2v.sh`, etc. | Docker image references |

---

## Task 1: Clone v0.15.0 to a temp audit directory

**Files:**
- Create: `/tmp/tt-infer-audit/tt-inference-server/` (temp, not committed)

Clone the exact latest release tag to a temp location. This is **read-only** — we never write back. No changes to `vendor/` until explicitly approved.

- [ ] **Step 1: Clone**

```bash
mkdir -p /tmp/tt-infer-audit
git clone --depth=1 --branch v0.15.0 \
    https://github.com/tenstorrent/tt-inference-server.git \
    /tmp/tt-infer-audit/tt-inference-server
echo "Cloned. SHA: $(git -C /tmp/tt-infer-audit/tt-inference-server rev-parse HEAD)"
```

Expected output: clones ~50MB, prints a SHA beginning with `25891d3...`

- [ ] **Step 2: Verify key files exist**

```bash
for f in \
    workflows/run_docker_server.py \
    tt-media-server/config/constants.py \
    tt-media-server/domain/video_generate_request.py \
    tt-media-server/tt_model_runners/dit_runners.py \
    tt-media-server/tt_model_runners/runner_fabric.py \
    workflows/model_spec.py \
    release_model_spec.json; do
    [ -f "/tmp/tt-infer-audit/tt-inference-server/$f" ] \
        && echo "OK  $f" \
        || echo "MISSING  $f"
done
```

All must show `OK`. If any show `MISSING`, the directory structure changed and the corresponding patch/script needs updating — note in report.

- [ ] **Step 3: Record the new image tag**

```bash
grep -r "tt-media-inference-server:" /tmp/tt-infer-audit/tt-inference-server/ \
    --include="*.py" --include="*.sh" --include="*.yml" \
    | grep -o "tt-media-inference-server:[0-9a-z.\-]*" | sort -u
```

Expected: `tt-media-inference-server:0.15.0-25891d3` (or similar). Record this in the report as the new docker image tag.

---

## Task 2: Diff the files our patches replace/override

For each file we have in `patches/media_server_config/`, compare our patched version against the v0.15.0 original to determine: (a) does our change still make sense, (b) have they added conflicting changes upstream.

- [ ] **Step 2a: Diff constants.py**

```bash
diff \
    /home/ttuser/code/tt-local-generator/patches/media_server_config/config/constants.py \
    /tmp/tt-infer-audit/tt-inference-server/tt-media-server/config/constants.py \
    | head -100
```

Record in report:
- Lines we added that are absent upstream (SkyReels entries, WAN_2_2_ANIMATE, P300X2 overrides)
- Lines upstream added since 0.11.1 that we need to merge into our patch

- [ ] **Step 2b: Diff video_generate_request.py**

```bash
diff \
    /home/ttuser/code/tt-local-generator/patches/media_server_config/domain/video_generate_request.py \
    /tmp/tt-infer-audit/tt-inference-server/tt-media-server/domain/video_generate_request.py \
    | head -100
```

- [ ] **Step 2c: Diff dit_runners.py**

```bash
diff \
    /home/ttuser/code/tt-local-generator/patches/media_server_config/tt_model_runners/dit_runners.py \
    /tmp/tt-infer-audit/tt-inference-server/tt-media-server/tt_model_runners/dit_runners.py \
    | head -100
```

- [ ] **Step 2d: Diff runner_fabric.py**

```bash
diff \
    /home/ttuser/code/tt-local-generator/patches/media_server_config/tt_model_runners/runner_fabric.py \
    /tmp/tt-infer-audit/tt-inference-server/tt-media-server/tt_model_runners/runner_fabric.py \
    | head -100
```

- [ ] **Step 2e: Diff skyreels runner files**

```bash
for f in skyreels_i2v_runner.py skyreels_runner.py; do
    echo "=== $f ==="
    new_path="/tmp/tt-infer-audit/tt-inference-server/tt-media-server/tt_model_runners/$f"
    if [ -f "$new_path" ]; then
        diff \
            "/home/ttuser/code/tt-local-generator/patches/media_server_config/tt_model_runners/$f" \
            "$new_path" | head -60
    else
        echo "FILE GONE UPSTREAM — patch file may need to move or be dropped"
        echo "Checking if file moved:"
        find /tmp/tt-infer-audit/tt-inference-server -name "$f" 2>/dev/null
    fi
done
```

- [ ] **Step 2f: Diff tt_dit pipeline files**

```bash
for f in \
    "pipelines/mochi/pipeline_mochi.py" \
    "pipelines/skyreels_v2/__init__.py" \
    "pipelines/skyreels_v2/pipeline_skyreels_i2v.py" \
    "pipelines/skyreels_v2/pipeline_skyreels.py" \
    "pipelines/wan/pipeline_wan_animate.py" \
    "pipelines/wan/pipeline_wan.py"; do
    echo "=== $f ==="
    # tt_dit patches land inside ~/tt-metal/models/tt_dit/ in the container
    # The upstream source is typically not in this repo at all (it's in tt-metal)
    # so we check if the file exists in the new inference-server repo anywhere:
    found=$(find /tmp/tt-infer-audit/tt-inference-server -name "$(basename $f)" 2>/dev/null | head -1)
    if [ -n "$found" ]; then
        echo "  Found at: $found"
        diff "/home/ttuser/code/tt-local-generator/patches/tt_dit/$f" "$found" | head -40
    else
        echo "  Not in tt-inference-server repo (expected — these patch tt-metal, not tt-inference-server)"
    fi
done
```

---

## Task 3: Audit `run_docker_server.py` for patch compatibility

Our `apply_patches.sh` modifies `run_docker_server.py` in two places:
1. **tt_dit block** — inserts bind-mount loop for `patches/tt_dit/` (dev_mode only)  
2. **HF_HOME block** — adds `HF_HOME`/`HF_HUB_CACHE` bind-mounts  
3. **media_server_config block** — inserts bind-mount loop for config overrides

- [ ] **Step 3a: Check all three anchor strings still present**

```bash
RDS="/tmp/tt-infer-audit/tt-inference-server/workflows/run_docker_server.py"
python3 - "$RDS" << 'EOF'
import sys
path = sys.argv[1]
text = open(path).read()
anchors = [
    ("HF patch anchor",     "for key, value in docker_env_vars.items():"),
    ("media_server anchor", "for key, value in docker_env_vars.items():"),
    ("Already patched (tt_dit)",    "tt_dit_patches_dir"),
    ("Already patched (HF_HOME)",   "HF_HOME"),
    ("Already patched (msc)",       "media_server_config"),
]
for label, anchor in anchors:
    status = "FOUND" if anchor in text else "MISSING"
    print(f"  {status}: {label!r} ({anchor!r})")
EOF
```

Expected: All three patch anchors `MISSING` (they're what we add), and the insertion anchor `FOUND`.

- [ ] **Step 3b: Check if HF_HOME handling changed in v0.15.0**

```bash
RDS="/tmp/tt-infer-audit/tt-inference-server/workflows/run_docker_server.py"
python3 -c "
text = open('$RDS').read()
lines = text.splitlines()
for i,l in enumerate(lines):
    if any(k in l for k in ('cache_root', 'host_model', 'HF_', 'huggingface', 'bind')):
        print(f'{i+1:4d}: {l}')
" | head -40
```

If v0.15.0 has its own `HF_HOME` handling, our patch may be redundant. Record findings.

- [ ] **Step 3c: Check if the container user/path structure changed**

```bash
RDS="/tmp/tt-infer-audit/tt-inference-server/workflows/run_docker_server.py"
python3 -c "
text = open('$RDS').read()
lines = text.splitlines()
for i,l in enumerate(lines):
    if any(k in l for k in ('user_home_path', 'container_app_user', '/home/')):
        print(f'{i+1:4d}: {l}')
" | head -20
```

Our patches use `{user_home_path}/tt-metal/...`. Record if this variable name changed.

- [ ] **Step 3d: Dry-run apply_patches.sh against the v0.15.0 clone**

```bash
# IMPORTANT: This writes to /tmp — not to vendor/ or the real repo.
# Capture all output to check for errors.
cd /home/ttuser/code/tt-local-generator
bash bin/apply_patches.sh /tmp/tt-infer-audit/tt-inference-server 2>&1 | tee /tmp/patch-dry-run.log
echo "Exit code: $?"
```

Read the output carefully:
- `unchanged:` = patch already matches upstream (may be redundant for that file)
- `updated:` = patch applied over a different upstream version (check the diff)
- `ERROR: could not find insertion anchor` = anchor moved upstream (patch script needs updating)
- `already patched` = idempotency check triggered (expected only if running twice)

- [ ] **Step 3e: Inspect what changed in the patched file**

```bash
# Check if the tt_dit and media_server_config blocks inserted correctly
RDS_PATCHED="/tmp/tt-infer-audit/tt-inference-server/workflows/run_docker_server.py"
python3 -c "
text = open('$RDS_PATCHED').read()
checks = ['tt_dit_patches_dir', 'media_server_config', 'HF_HOME', 'HF_HUB_CACHE']
for c in checks:
    status = 'INSERTED' if c in text else 'MISSING'
    print(f'  {status}: {c!r}')
"
```

All four must show `INSERTED`.

---

## Task 4: Audit `model_spec.py` SkyReels injection

`apply_patches.sh` steps 6+7 inject two `ModelSpecTemplate` blocks into `workflows/model_spec.py`: one for SkyReels-V2-DF-1.3B-540P (T2V) and one for SkyReels-V2-I2V-14B-540P (I2V).

- [ ] **Step 4a: Check if SkyReels is now officially in model_spec.py**

```bash
grep -n -i "skyreels\|SkyReels\|SKYREELS" \
    /tmp/tt-infer-audit/tt-inference-server/workflows/model_spec.py | head -20
```

If any results: SkyReels was upstreamed. Read those lines and compare to our injection to see if the signatures match or if we can drop the patch.

If no results: we still need our injection. Proceed to 4b.

- [ ] **Step 4b: Check if the injection anchor still exists in model_spec.py**

Our `apply_patches.sh` searches for a specific injection anchor in `model_spec.py`. Read the script to find that anchor and check:

```bash
grep -n "ModelSpecTemplate\|model_spec_registry\|def get_model_spec\|register\|inject" \
    /tmp/tt-infer-audit/tt-inference-server/workflows/model_spec.py | head -20
```

Then find our injection target from the script:
```bash
grep -A5 "STEP 6\|STEP 7\|SkyReels\|skyreels" \
    /home/ttuser/code/tt-local-generator/bin/apply_patches.sh | head -40
```

Record whether the anchor is still present in v0.15.0.

- [ ] **Step 4c: Dry-run the SkyReels injection**

The apply_patches.sh already ran in Task 3d. Check its log for the SkyReels steps:

```bash
grep -i "skyreels\|step 6\|step 7\|model_spec" /tmp/patch-dry-run.log
```

Record pass/fail.

---

## Task 5: Review release notes for v0.12.0–v0.15.0

Read the release notes for all four releases between 0.11.1 and 0.15.0 to identify:
- New features relevant to our video models (Wan2.2, Mochi, SkyReels, FLUX)
- Breaking API or config changes
- New ModelSpec patterns we should adopt
- New I2V or animate capabilities we might want

- [ ] **Step 5a: Fetch all four release notes**

```bash
for tag in v0.12.0 v0.13.0 v0.14.0 v0.15.0; do
    echo "==== $tag ===="
    gh api "repos/tenstorrent/tt-inference-server/releases/tags/$tag" \
        2>/dev/null | python3 -c "
import json, sys
r = json.load(sys.stdin)
print(r.get('body','(no body)'))
" | head -50
    echo ""
done
```

- [ ] **Step 5b: Check if setup_config / host_model_weights_mount_dir is the new HF_HOME pattern**

v0.15.0's `run_docker_server.py` uses `setup_config.host_model_weights_mount_dir` and `setup_config.cache_root` for model mounting instead of direct HF_HOME env vars. Check if this makes our HF_HOME patch obsolete:

```bash
python3 - << 'EOF'
import json, pathlib
# Check our start scripts — do they pass setup_config to run_docker_server?
for script in ['start_wan_qb2.sh', 'start_mochi.sh', 'start_skyreels_i2v.sh', 'start_flux.sh']:
    path = pathlib.Path(f'/home/ttuser/code/tt-local-generator/bin/{script}')
    if path.exists():
        text = path.read_text()
        has_setup = 'setup_config' in text or 'host_model_weights' in text
        has_hf = 'HF_HOME' in text or 'HF_HUB' in text
        print(f'{script}: setup_config={has_setup}, HF_HOME={has_hf}')
EOF
```

---

## Task 6: Write the compatibility report

Synthesize all findings into a structured document.

- [ ] **Step 6: Write `docs/upgrade-report-0.15.0.md`**

```bash
cat > /home/ttuser/code/tt-local-generator/docs/upgrade-report-0.15.0.md << 'REPORT_EOF'
# tt-inference-server v0.15.0 Compatibility Report

Generated: $(date +%Y-%m-%d)
Pinned: 0.11.1 (commit b5589534, image 0.11.1-bac8b34)
Target: v0.15.0 (commit 25891d3, image 0.15.0-25891d3)
Commits ahead: 232 | Files changed: 300

## Executive Summary

[FILL IN: pass/fail/needs-update per patch after Tasks 1-5]

## Patch Status

| Patch | Status | Action needed |
|---|---|---|
| tt_dit bind-mount block (run_docker_server.py) | [PASS/FAIL] | [none/update anchor/rewrite] |
| HF_HOME bind-mount block (run_docker_server.py) | [PASS/FAIL] | [none/may be obsolete/update] |
| media_server_config bind-mount block (run_docker_server.py) | [PASS/FAIL] | [none/update anchor/rewrite] |
| constants.py override | [PASS/FAIL] | [none/merge upstream changes] |
| video_generate_request.py override | [PASS/FAIL] | [none/merge upstream changes] |
| dit_runners.py override | [PASS/FAIL] | [none/merge upstream changes] |
| runner_fabric.py override | [PASS/FAIL] | [none/merge upstream changes] |
| skyreels_i2v_runner.py override | [PASS/FAIL] | [none/file moved/dropped] |
| skyreels_runner.py override | [PASS/FAIL] | [none/file moved/dropped] |
| model_spec.py SkyReels T2V injection | [PASS/FAIL] | [none/anchor moved/upstreamed] |
| model_spec.py SkyReels I2V injection | [PASS/FAIL] | [none/anchor moved/upstreamed] |

## New Docker Image

- Old: `ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34`
- New: `ghcr.io/tenstorrent/tt-media-inference-server:0.15.0-25891d3`
- Files to update: `bin/start_wan_qb2.sh`, `bin/start_mochi.sh`, `bin/start_flux.sh`,
  `bin/start_skyreels_i2v.sh`, `bin/start_animate.sh`, `debian/postinst`

## Notable Upstream Changes (v0.12.0–v0.15.0)

[FILL IN findings from Task 5 release notes review]

## New Features to Consider Adopting

[FILL IN any new capabilities in v0.12.0–v0.15.0 relevant to our stack]

## Recommendation

[FILL IN: safe to upgrade / needs patch updates first / needs testing first]

## Next Steps (after report approval)

1. Update `vendor/VENDOR_SHA` to `25891d3`
2. Update docker image tag in all start scripts
3. Re-run `./bin/apply_patches.sh` against new vendor
4. Smoke-test on QB2: start_wan_qb2.sh → generate one video
5. Test SkyReels I2V
6. Test Mochi
7. Test FLUX
REPORT_EOF
```

- [ ] **Commit the report skeleton**

```bash
git -C /home/ttuser/code/tt-local-generator add docs/upgrade-report-0.15.0.md
git -C /home/ttuser/code/tt-local-generator commit -m "docs: add v0.15.0 upgrade compatibility report (skeleton — fill in from audit)"
```

- [ ] **Fill in all [FILL IN] sections based on Tasks 1–5 output**

Edit `docs/upgrade-report-0.15.0.md` and replace every `[FILL IN]` and `[PASS/FAIL]` with actual findings. Then commit the completed report.

```bash
git -C /home/ttuser/code/tt-local-generator add docs/upgrade-report-0.15.0.md
git -C /home/ttuser/code/tt-local-generator commit -m "docs: complete v0.15.0 upgrade compatibility report"
git -C /home/ttuser/code/tt-local-generator push origin HEAD
```

---

## What This Plan Does NOT Do

- Does **not** modify `vendor/VENDOR_SHA`
- Does **not** pull the new Docker image
- Does **not** restart any inference servers
- Does **not** run any generation jobs
- Does **not** modify any start scripts

All of the above require explicit human approval after reviewing the report.

---

## Self-Review

**Spec coverage:**
- Clone v0.15.0 to temp → Task 1 ✓
- Diff all 9 patch files against upstream → Tasks 2+3 ✓
- Dry-run apply_patches.sh → Task 3d ✓
- Check model_spec.py injection anchors → Task 4 ✓
- Review release notes v0.12.0–v0.15.0 → Task 5 ✓
- Write compatibility report → Task 6 ✓
- No hardware changes → explicit note in Task 6 ✓

**Placeholder scan:** All `[FILL IN]` in the report template are intentional — they get filled by the executor based on actual findings. No code steps are incomplete.

**Scope:** Read-only audit only. The next plan (if needed) would be "apply v0.15.0 upgrade" and only runs after the report is approved.
