# Docker image bundles (optional, not shipped)

This directory is a place to keep an **optional, locally-built** offline bundle
of a `tt-media-inference-server` image (a `docker save … | gzip` tarball) for
air-gapped bootstrap.

**Nothing ships here by default.** `*.tar.gz` bundles are git-ignored
(`.gitignore`), are **not** included in the `.deb`, and nothing in the app loads
one automatically. At runtime the app **pulls the image from GHCR by tag**
(`ghcr.io/tenstorrent/tt-media-inference-server:<version>` — see
`bin/start_*.sh` and the vendored `tt-inference-server`), so an online host
never needs a bundle here.

A stale 7.9 GB `0.11.1-bac8b34` bundle used to live here (and left an orphaned
Git LFS object behind); both were removed in v0.92.1 to reclaim ~15 GB, since
the app pulls newer images (0.17/0.18+) from GHCR.

## Building a bundle (only if you need offline install)

```bash
docker save ghcr.io/tenstorrent/tt-media-inference-server:<tag> \
  | gzip -1 > docker/tt-media-inference-server-<tag>.tar.gz
```

Load it on the target with:

```bash
docker load -i docker/tt-media-inference-server-<tag>.tar.gz
```

Bundle the tag that matches the vendored server (`vendor/VENDOR_SHA` /
`vendor/VENDOR_VERSION`), not an older one.
