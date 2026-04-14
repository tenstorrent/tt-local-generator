#!/usr/bin/env bash
# bin/setup_macos.sh — Install tt-local-generator GUI dependencies on macOS.
#
# Installs GTK4, PyGObject, GStreamer, ffmpeg, and pip packages needed to run
# the tt-local-generator GUI against a remote inference server:
#
#   ./tt-gen --server http://your-tenstorrent-machine:8000
#
# Creates .venv/ at the repo root using --system-site-packages so the venv
# inherits gi/PyGObject from Homebrew while keeping pip installs isolated
# (required because PEP 668 blocks direct pip installs into Homebrew Python
# on macOS 13+).  tt-gen detects and uses .venv/bin/python3 automatically.
#
# Usage:
#   ./bin/setup_macos.sh
#
# Requirements:
#   - macOS 13 (Ventura) or later
#   - Homebrew (https://brew.sh) — installed by this script if missing
#   - Internet access

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BOLD}[setup]${NC} $*"; }
success() { echo -e "${GREEN}[setup]${NC} $*"; }
warn()    { echo -e "${YELLOW}[setup]${NC} $*"; }
die()     { echo -e "${RED}[setup] ERROR:${NC} $*" >&2; exit 1; }

# ── Platform check ───────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]] || die "This script is for macOS only."

# Resolve repo root (one level up from bin/)
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_DIR/.venv"

info "tt-local-generator macOS setup"
echo ""

# ── Homebrew ─────────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for the rest of this script (Apple Silicon vs Intel)
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    success "Homebrew installed."
else
    info "Homebrew already installed — updating..."
    brew update --quiet
fi

echo ""
info "Installing brew packages..."

# ── Python ───────────────────────────────────────────────────────────────────
# Install a known-good Python version; Homebrew tracks latest 3.x.
brew install python3

# ── GTK4 + introspection typelibs ────────────────────────────────────────────
# gtk4 pulls in glib, gobject-introspection, harfbuzz, cairo, pango,
# gdk-pixbuf, and libadwaita as dependencies.  pango and gdk-pixbuf are
# listed explicitly for clarity.
brew install gtk4
brew install gdk-pixbuf
brew install pango

# ── PyGObject (python3 gi bindings) ──────────────────────────────────────────
# pygobject3 places gi/ into the Homebrew Python's site-packages.
# The .venv created below inherits it via --system-site-packages.
brew install pygobject3

# ── GStreamer (video playback via Gtk.Video) ──────────────────────────────────
# GTK4's Gtk.Video widget delegates to GStreamer.  gst-libav provides the
# libavcodec-based H.264 decoder (avdec_h264) used for MP4 playback.
brew install gstreamer
brew install gst-plugins-base
brew install gst-plugins-good
brew install gst-plugins-bad
brew install gst-libav

# ── ffmpeg (thumbnail & still-frame extraction) ───────────────────────────────
brew install ffmpeg

success "All brew packages installed."

# ── Virtual environment ───────────────────────────────────────────────────────
# Create .venv with --system-site-packages so gi (PyGObject, installed by
# brew into the Homebrew Python) is visible inside the venv.  pip installs
# (requests, markovify) go into the venv, keeping the Homebrew Python clean
# and satisfying PEP 668's externally-managed-environment restriction.
echo ""
info "Setting up Python virtual environment at $VENV_DIR ..."

BREW_PYTHON=$(command -v python3)
info "Base interpreter: $BREW_PYTHON ($(${BREW_PYTHON} --version))"

if [[ -d "$VENV_DIR" ]]; then
    warn ".venv already exists — upgrading pip and packages only."
else
    "$BREW_PYTHON" -m venv --system-site-packages "$VENV_DIR"
    success "Created $VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python3"

"$VENV_PYTHON" -m pip install --upgrade pip --quiet

# ── Python packages (requirements.txt) ───────────────────────────────────────
info "Installing pip packages into venv..."
"$VENV_PYTHON" -m pip install requests PyYAML markovify --quiet
success "pip packages installed."

# ── Verification ─────────────────────────────────────────────────────────────
echo ""
info "Verifying installation (using $VENV_PYTHON)..."

FAILED=0

check() {
    local label="$1"; shift
    if "$VENV_PYTHON" -c "$@" &>/dev/null; then
        success "  $label"
    else
        warn "  MISSING: $label"
        FAILED=1
    fi
}

check "gi (PyGObject)"       "import gi"
check "GTK 4.0"              "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk"
check "GdkPixbuf 2.0"        "import gi; gi.require_version('GdkPixbuf','2.0'); from gi.repository import GdkPixbuf"
check "Pango 1.0"            "import gi; gi.require_version('Pango','1.0'); from gi.repository import Pango"
check "requests"             "import requests"
check "PyYAML"               "import yaml"
check "markovify (optional)" "import markovify"

if command -v ffmpeg &>/dev/null; then
    success "  ffmpeg ($(ffmpeg -version 2>&1 | head -1 | awk '{print $3}'))"
else
    warn "  MISSING: ffmpeg"
    FAILED=1
fi

echo ""
if [[ $FAILED -eq 0 ]]; then
    success "All checks passed!"
    echo ""
    echo -e "  ${BOLD}Run the app:${NC}"
    echo "    ./tt-gen --server http://your-tenstorrent-machine:8000"
else
    warn "Some checks failed — see above.  Re-run this script or install missing items manually."
    echo ""
    echo "  Troubleshooting: https://pygobject.gnome.org/getting_started.html#macos-getting-started"
fi
echo ""
