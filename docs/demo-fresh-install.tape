# demo-fresh-install.tape
# To re-record: cd docs && vhs demo-fresh-install.tape
# Output: docs/assets/demo-fresh-install.gif

Output assets/demo-fresh-install.gif

Source vhs-defaults.tape
Set Shell "bash"
Set Width 1400
Set Height 800

# ── Start container (hidden — HF_TOKEN must not appear on screen) ─────────────
Hide
Type "docker rm -f tt-qs-demo 2>/dev/null; docker run -d --name tt-qs-demo -e HF_TOKEN=$HF_TOKEN -e HF_HUB_CACHE=/home/ttuser/.cache/huggingface/hub -e PIP_CACHE_DIR=/pip-cache -v $HOME/.cache/huggingface/hub:/home/ttuser/.cache/huggingface/hub:ro -v $HOME/.cache/pip:/pip-cache:rw tenstorrent/qb2-env:latest sleep 600"
Enter
Sleep 4s
Type "docker exec -it tt-qs-demo bash"
Enter
Sleep 2s
Show
Ctrl+L

# ── Clone ─────────────────────────────────────────────────────────────────────
Type "mkdir -p ~/code && git clone --depth 1 https://github.com/tenstorrent/tt-local-generator.git ~/code/tt-local-generator"
Enter
Sleep 10s

# ── Status check ─────────────────────────────────────────────────────────────
Type "cd ~/code/tt-local-generator && ./bin/quickstart.sh --status"
Enter
Sleep 5s
Sleep 2s

# ── Full quickstart ───────────────────────────────────────────────────────────
Type "./bin/quickstart.sh"
Enter
Sleep 14s
Sleep 10s
Sleep 8s
Sleep 14s
Sleep 18s
Sleep 6s

# ── Health check ─────────────────────────────────────────────────────────────
Type "curl -s http://localhost:8001/health | python3 -m json.tool"
Enter
Sleep 5s
Sleep 4s

# ── Cleanup (hidden) ─────────────────────────────────────────────────────────
Hide
Type "exit"
Enter
Sleep 1s
Type "docker rm -f tt-qs-demo 2>/dev/null; true"
Enter
Sleep 3s
