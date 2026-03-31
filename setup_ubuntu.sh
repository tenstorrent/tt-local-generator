#!/usr/bin/env bash
# setup_ubuntu.sh — One-shot setup for tt-local-generator on Ubuntu 24.04 (GNOME)
#
# Installs all dependencies needed to run Wan2.2-T2V-A14B-Diffusers via
# tt-inference-server and the tt-local-generator GTK4 UI.
#
# Usage:
#   chmod +x setup_ubuntu.sh
#   ./setup_ubuntu.sh
#
# What this script installs:
#   - Docker Engine (official Docker repo)
#   - nvidia-container-toolkit (for GPU passthrough inside Docker)
#   - Python dependencies: python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, requests
#   - GStreamer plugins for inline GTK4 video playback
#   - ffmpeg (for thumbnail extraction)
#   - tt-inference-server repo (cloned to ~/code/tt-inference-server)
#   - tt-local-generator repo (cloned to ~/code/tt-local-generator)
#   - tt-inference-server Python requirements (system python3)
#
# Assumptions:
#   - Ubuntu 24.04 LTS (Noble), x86_64
#   - Running as a non-root user with sudo access
#   - Internet access (package repos + GitHub)
#   - NVIDIA GPU present (for nvidia-container-toolkit)
#     If no NVIDIA GPU, omit that section and use --no-gpus flag instead
#
# After this script:
#   1. Add yourself to the docker group: newgrp docker  (or log out/in)
#   2. Copy tt-inference-server/.env.default → .env and set JWT_SECRET, API keys
#   3. Pre-download the model weights (see GUIDE.md, section 3)
#   4. Run: cd ~/code/tt-local-generator && ./start_wan.sh
#   5. Once "Application startup complete" appears, run the UI:
#        /usr/bin/python3 ~/code/tt-local-generator/main.py

set -euo pipefail

echo "============================================================"
echo "  tt-local-generator setup  —  Ubuntu 24.04"
echo "============================================================"
echo ""

# ── 0. Pre-flight checks ──────────────────────────────────────────────────────

if [[ "$(id -u)" -eq 0 ]]; then
    echo "ERROR: Do not run this script as root. Run as your normal user (sudo will"
    echo "       be invoked where needed)."
    exit 1
fi

ARCH=$(uname -m)
if [[ "$ARCH" != "x86_64" ]]; then
    echo "ERROR: This script targets x86_64. Found: $ARCH"
    exit 1
fi

UBUNTU_VER=$(. /etc/os-release && echo "$VERSION_ID")
if [[ "$UBUNTU_VER" != "24.04" ]]; then
    echo "WARNING: This script was written for Ubuntu 24.04. You have $UBUNTU_VER."
    echo "         Proceeding anyway — some apt packages may differ."
    sleep 2
fi

echo "Running as: $(whoami)  on  Ubuntu $UBUNTU_VER  ($ARCH)"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────

echo "── Step 1: System packages ─────────────────────────────────"
sudo apt-get update -qq

# Core utilities
sudo apt-get install -y \
    curl \
    git \
    ca-certificates \
    gnupg \
    lsb-release \
    software-properties-common \
    apt-transport-https

# GTK4 + PyGObject bindings (must be system python3, NOT a venv)
sudo apt-get install -y \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    python3-requests

# GStreamer plugins needed by Gtk.Video for H.264 playback
# libgtk-4-media-gstreamer bridges GTK4's media API to GStreamer
# gstreamer1.0-libav supplies the H.264/AAC decoder (ffmpeg-based)
sudo apt-get install -y \
    libgtk-4-media-gstreamer \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav

# ffmpeg for thumbnail extraction (first frame of each generated video)
sudo apt-get install -y ffmpeg

echo "  ✓ System packages installed"
echo ""

# ── 2. Docker Engine ──────────────────────────────────────────────────────────
# Official Docker CE repo — Ubuntu's snap docker is not suitable for GPU workloads.

echo "── Step 2: Docker Engine ───────────────────────────────────"

if command -v docker &>/dev/null; then
    DOCKER_VER=$(docker --version | awk '{print $3}' | tr -d ,)
    echo "  ✓ Docker already installed ($DOCKER_VER) — skipping"
else
    echo "  Installing Docker CE from official repo…"

    # Add Docker's GPG key
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    # Add the Docker apt repository
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -qq
    sudo apt-get install -y \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin

    echo "  ✓ Docker CE installed"
fi

# Add current user to the docker group so we don't need sudo for every command.
# Effect takes place on next login (or: newgrp docker).
if ! groups | grep -q docker; then
    sudo usermod -aG docker "$USER"
    echo "  ✓ Added $USER to docker group"
    echo "  NOTE: Run 'newgrp docker' in your current shell, or log out and back in"
    echo "        before running ./start_wan.sh"
fi
echo ""

# ── 3. NVIDIA Container Toolkit (GPU passthrough into Docker) ─────────────────

echo "── Step 3: NVIDIA Container Toolkit ───────────────────────"

if ! command -v nvidia-smi &>/dev/null; then
    echo "  WARNING: nvidia-smi not found — NVIDIA driver may not be installed."
    echo "           Skipping nvidia-container-toolkit."
    echo "           Install NVIDIA drivers first, then re-run this step manually:"
    echo "             sudo apt-get install -y nvidia-container-toolkit"
    echo "             sudo nvidia-ctk runtime configure --runtime=docker"
    echo "             sudo systemctl restart docker"
else
    if dpkg -l nvidia-container-toolkit &>/dev/null 2>&1; then
        echo "  ✓ nvidia-container-toolkit already installed — skipping"
    else
        echo "  Installing nvidia-container-toolkit…"

        curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
            | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

        curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
            | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
            | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

        sudo apt-get update -qq
        sudo apt-get install -y nvidia-container-toolkit

        # Configure Docker to use the NVIDIA runtime
        sudo nvidia-ctk runtime configure --runtime=docker
        sudo systemctl restart docker

        echo "  ✓ nvidia-container-toolkit installed and Docker runtime configured"
    fi
fi
echo ""

# ── 4. tt-inference-server repo ───────────────────────────────────────────────

echo "── Step 4: tt-inference-server ─────────────────────────────"
mkdir -p ~/code

if [[ -d ~/code/tt-inference-server ]]; then
    echo "  ✓ ~/code/tt-inference-server already exists — skipping clone"
else
    echo "  Cloning tt-inference-server…"
    git clone https://github.com/tenstorrent/tt-inference-server.git ~/code/tt-inference-server
    echo "  ✓ Cloned"
fi

# Install Python requirements using system python3 (NOT in a venv).
# PyGObject (python3-gi) is only visible to the system interpreter.
if [[ -f ~/code/tt-inference-server/requirements.txt ]]; then
    echo "  Installing Python requirements for tt-inference-server…"
    pip3 install --break-system-packages -r ~/code/tt-inference-server/requirements.txt
    echo "  ✓ Requirements installed"
fi

# Create .env from template if not already present
if [[ ! -f ~/code/tt-inference-server/.env ]]; then
    if [[ -f ~/code/tt-inference-server/.env.default ]]; then
        cp ~/code/tt-inference-server/.env.default ~/code/tt-inference-server/.env
        echo "  ✓ Created .env from .env.default"
        echo ""
        echo "  IMPORTANT: Edit ~/code/tt-inference-server/.env and set:"
        echo "    JWT_SECRET=<random 32+ char string>"
        echo "    AUTHORIZATION_TOKEN=<same or different secret for API auth>"
        echo ""
    else
        echo "  NOTE: No .env.default found — you will need to create .env manually."
        echo "  Minimum required:"
        echo "    JWT_SECRET=<random 32+ char string>"
        echo "    AUTHORIZATION_TOKEN=<random 32+ char string>"
    fi
fi
echo ""

# ── 5. tt-local-generator repo ────────────────────────────────────────────────

echo "── Step 5: tt-local-generator ──────────────────────────────"

if [[ -d ~/code/tt-local-generator ]]; then
    echo "  ✓ ~/code/tt-local-generator already exists — skipping clone"
else
    echo "  Cloning tt-local-generator…"
    git clone https://github.com/tsingletaryTT/tt-local-generator.git ~/code/tt-local-generator
    echo "  ✓ Cloned"
fi

# pip-installable dependencies (requests; GTK bindings are already system-installed)
pip3 install --break-system-packages requests
echo "  ✓ Python dependencies installed"
echo ""

# ── 6. Smoke-test GTK4 import ─────────────────────────────────────────────────

echo "── Step 6: Smoke test ──────────────────────────────────────"
/usr/bin/python3 -c "
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
print('  ✓ GTK4 import OK:', Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
"

/usr/bin/python3 -c "import requests; print('  ✓ requests', requests.__version__)"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "============================================================"
echo "  Setup complete!"
echo "============================================================"
echo ""
echo "Next steps:"
echo ""
echo "  1. If the docker group is new, run:  newgrp docker"
echo "     (or log out and back in)"
echo ""
echo "  2. Edit ~/code/tt-inference-server/.env  and set:"
echo "       JWT_SECRET=<long random string>"
echo "       AUTHORIZATION_TOKEN=<long random string>"
echo ""
echo "  3. Pre-download the Wan2.2 model weights (~118 GB):"
echo "       pip3 install --break-system-packages huggingface_hub"
echo "       huggingface-cli download Wan-AI/Wan2.2-T2V-A14B-Diffusers"
echo ""
echo "  4. Start the inference server:"
echo "       cd ~/code/tt-local-generator"
echo "       ./start_wan.sh"
echo "     Wait ~5 min for:  Application startup complete"
echo ""
echo "  5. In a second terminal, launch the UI:"
echo "       /usr/bin/python3 ~/code/tt-local-generator/main.py"
echo ""
echo "  See GUIDE.md for a full walkthrough including troubleshooting."
echo ""
