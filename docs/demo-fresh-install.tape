# demo-fresh-install.tape
#
# Records a fresh tt-local-generator install from source inside the QB2
# Docker image (tenstorrent/qb2-env:latest) using bin/quickstart.sh.
#
# Scene 1: fresh clone + --status (shows the broken-before state)
# Scene 2: full quickstart run — steps 1-7, Qwen3-0.6B validation
#
# HF_TOKEN is never displayed. The wrapper script (run-fresh-install-demo.sh)
# reads it from the environment and passes it to `docker run -e` only.
# The pip wheel cache and HF model cache are bind-mounted from the host so
# the recording runs in ~40s rather than downloading gigabytes.
#
# Output: docs/assets/demo-fresh-install.gif

Output assets/demo-fresh-install.gif

Source vhs-defaults.tape
Set Shell "bash"
Set Width 1400
Set Height 800

# ── Setup (hidden) ────────────────────────────────────────────────────────────
Hide
Type "cd ~/code/tt-local-generator"
Enter
Sleep 500ms
Type "./docs/run-fresh-install-demo.sh stop 2>/dev/null; true"
Enter
Sleep 3s
Type "./docs/run-fresh-install-demo.sh start"
Enter
Sleep 5s
Show
Ctrl+L

# ── Scene 1: clone + status check ────────────────────────────────────────────
Type "# Clone tt-local-generator on a clean QB2 image"
Enter
Sleep 400ms
Type "./docs/run-fresh-install-demo.sh clone"
Enter
Sleep 9s

Type "# Before running quickstart — show what's missing"
Enter
Sleep 400ms
Type "./docs/run-fresh-install-demo.sh status"
Enter
Sleep 3s

Sleep 2s

# ── Scene 2: full quickstart ──────────────────────────────────────────────────
Type "# One command to install, configure, start, and validate"
Enter
Sleep 600ms
Type "./docs/run-fresh-install-demo.sh quickstart"
Enter

# [1] pip deps (torch + accelerate + friends from pip cache ~8s)
Sleep 12s

# [2] vendor clone (~7s shallow clone)
Sleep 10s

# [3] .env created (instant)
Sleep 2s

# [4] patches applied (~3s)
Sleep 5s

# [5] GTK4 warn (instant)
Sleep 2s

# [6] server start + model load from HF cache (~8s)
Sleep 14s

# [7] validation inference (~12s on CPU)
Sleep 16s

# Summary display + pause to read
Sleep 5s

# ── Health check ─────────────────────────────────────────────────────────────
Type "# Server stays running — confirm it's live"
Enter
Sleep 400ms
Type "./docs/run-fresh-install-demo.sh health"
Enter
Sleep 4s

Sleep 4s

# ── Cleanup (hidden) ─────────────────────────────────────────────────────────
Hide
Type "./docs/run-fresh-install-demo.sh stop"
Enter
Sleep 3s
