# quickstart.sh demo
# Scene 1: fresh clone → status check (shows what a clean-install would see)
# Scene 2: full run on real repo (idempotent — everything already in order)
# Output: docs/assets/demo-quickstart.gif

Output docs/assets/demo-quickstart.gif

Set Shell "bash"
Set FontSize 14
Set FontFamily "Hack"
Set Width 1200
Set Height 680
Set Theme "Dracula"
Set Padding 12
Set TypingSpeed 45ms
Set PlaybackSpeed 1.0

# ── Scene 1: Fresh clone ─────────────────────────────────────────────────────
Hide
Type "cd /tmp && rm -rf tt-quickstart-demo"
Enter
Sleep 500ms
Show

Type "# Fresh clone of feat/quickstart branch"
Enter
Sleep 300ms
Type "git clone --branch feat/quickstart --depth 1 file:///home/ttuser/code/tt-local-generator tt-quickstart-demo 2>&1 | tail -3"
Enter
Sleep 4s

Type "cd tt-quickstart-demo"
Enter
Sleep 400ms

Type "# Check what state we'd start from on a new machine"
Enter
Sleep 300ms
Type "./bin/quickstart.sh --status"
Enter
Sleep 8s

Sleep 1s

# ── Scene 2: Full run on real repo (idempotent path) ─────────────────────────
Type "# Now on the real repo — everything already set up"
Enter
Sleep 300ms
Type "cd ~/code/tt-local-generator"
Enter
Sleep 400ms

Type "./bin/quickstart.sh"
Enter
Sleep 14s

# ── Scene 3: Health check ─────────────────────────────────────────────────────
Type "curl -s http://localhost:8001/health | python3 -m json.tool"
Enter
Sleep 2s

# Final pause
Sleep 3s
