# Upgrading tt-local-generator

## 0.5.x → 0.6.0 (tt-inference-server v0.15.0)

This release upgrades the bundled inference server from v0.11.1 to v0.15.0.
The Docker image tag changes from `0.11.1-bac8b34` to `0.15.0-25891d3`.

---

### Path A — .deb package

```bash
sudo apt install ./tt-local-generator_0.6.0_amd64.deb
```

The postinst script pulls the new Docker image automatically. No other steps needed.

If the old image is still running, stop it first:

```bash
./bin/start_wan_qb2.sh --stop    # or whichever model you were using
```

---

### Path B — Git clone

```bash
cd ~/code/tt-local-generator
git pull
./bin/apply_patches.sh           # re-applies patches to updated vendor/
```

Stop any running server before restarting:

```bash
./bin/start_wan_qb2.sh --stop
./bin/start_wan_qb2.sh           # starts with the new 0.15.0 image
```

The first start after the upgrade will pull the new Docker image (~1-2 GB delta).

---

### Path C — GUI (tt-gen)

1. Pull the updated code (git pull or install new .deb)
2. Use **Servers ▾ → Stop** to stop any running container
3. Use **Servers ▾ → Start** — the new image is pulled and started automatically

---

### Known issues fixed in this release

| Symptom | Root cause | Fix |
|---|---|---|
| `PermissionError: /tmp/prometheus_multiproc/gauge_livesum_*.db` on first generation | Directory created as root; workers run as `container_app_user` | `chmod 777` applied automatically after container start |
| Server returns HTTP 401 on every request | v0.15.0 enables auth by default | `--no-auth` added to all start scripts |
| `FileNotFoundError` for model weight blobs | HF cache directory is a symlink to `/mnt/bonus`; Docker doesn't follow symlinks outside the bind-mount | `/mnt/bonus` is now bind-mounted read-only |

---

### If your HF cache is not at `~/.cache/huggingface`

If your weights live on a separate volume (e.g. `/mnt/bonus/models/`) and your
`~/.cache/huggingface/hub/` contains symlinks pointing there, make sure
`/mnt/bonus` exists and is accessible. The start scripts now mount it
automatically; if your symlink target is a different path, edit
`bin/apply_patches.sh` and update the `--volume` line for `/mnt/bonus`.

---

### Rolling back to v0.11.1

Edit `bin/start_wan_qb2.sh` (and other start scripts) and change:

```
DOCKER_IMAGE="ghcr.io/tenstorrent/tt-media-inference-server:0.15.0-25891d3"
```

back to:

```
DOCKER_IMAGE="ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34"
```

Then re-run `./bin/apply_patches.sh` to restore the v0.11.1-compatible patches.
The v0.11.1 image is still available on GHCR; no local changes are needed to
`vendor/VENDOR_SHA`.
