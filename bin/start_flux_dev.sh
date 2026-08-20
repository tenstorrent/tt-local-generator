#!/usr/bin/env bash
# start_flux_dev.sh — Start the FLUX.1-dev image server on P300x2 (QB2).
#
# Thin wrapper over start_flux.sh, which already supports --dev (FLUX.1-dev,
# higher quality, ~34 GB gated weights) and pins the 0.18.0-c49bb76 media image.
# Forwarding here keeps a single source of truth for the FLUX launch logic —
# any fix to start_flux.sh applies to both schnell and dev automatically.
#
# All flags (--gui, --stop, --restart, …) pass straight through.
set -euo pipefail
_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$_DIR/start_flux.sh" --dev "$@"
