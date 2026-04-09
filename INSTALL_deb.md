## tt-local-generator — install guide

### Before you install

Log in to HuggingFace first. The model packages read the saved token automatically so you won't be prompted during install.

```bash
pip install huggingface_hub
huggingface-cli login          # paste your token; saves to ~/.cache/huggingface/token
```

---

### Install

```bash
# 1. Main app (sets up Docker repo, /opt/tenstorrent/models, docker group, etc.)
sudo dpkg -i tt-local-generator_0.2.0_amd64.deb
sudo apt-get install -f          # pulls in any missing apt dependencies

# 2. Models
sudo dpkg -i tt-model-qwen3_0.2.0_all.deb       # ~1.2 GB — downloads at install time
sudo dpkg -i tt-model-wan2-t2v_0.2.0_all.deb    # ~118 GB — takes a while
```

If a download is interrupted, retry with:

```bash
tt-local-gen-download-model --repo Qwen/Qwen3-0.6B
tt-local-gen-download-model --repo Wan-AI/Wan2.2-T2V-A14B-Diffusers
```

---

### First launch

```bash
newgrp docker          # activate docker group without logging out (or just relog)
tt-local-gen
```

Click **Servers ▸ Start** in the app. The status bar at the bottom tracks startup progress live (~5 min on first run after weights are cached). The prompt server starts automatically in the background.
