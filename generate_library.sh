#!/usr/bin/env bash
# Generate a full artgen library for browsing.
# 23 landscapes, 23 geometric, 8 each of: skyline, constellation, verse, palette, ansi, circuit, freeform
# Run from the tt-local-generator repo root.
set -euo pipefail

ECTL="python3 tt-ctl artgen"
DONE=0
FAIL=0

run() {
    echo ""
    echo ">>> $*"
    if "$@"; then
        DONE=$((DONE+1))
    else
        echo "  [FAILED — continuing]"
        FAIL=$((FAIL+1))
    fi
}

# ── 23 Landscapes ─────────────────────────────────────────────────────────────
run $ECTL landscape --palette sunset --clouds --stars
run $ECTL landscape --palette sunset --mountains --no-clouds
run $ECTL landscape --palette sunset --glitch
run $ECTL landscape --palette sunset --mountains --clouds

run $ECTL landscape --palette blue --clouds --stars
run $ECTL landscape --palette blue --no-mountains
run $ECTL landscape --palette blue --glitch
run $ECTL landscape --palette blue --mountains --clouds --stars

run $ECTL landscape --palette purple --clouds --mountains
run $ECTL landscape --palette purple --stars --no-clouds
run $ECTL landscape --palette purple --glitch
run $ECTL landscape --palette purple --no-mountains --no-clouds

run $ECTL landscape --palette red --clouds --mountains
run $ECTL landscape --palette red --stars
run $ECTL landscape --palette red --no-mountains
run $ECTL landscape --palette red --glitch

run $ECTL landscape --palette orange --clouds --stars
run $ECTL landscape --palette orange --mountains --no-clouds
run $ECTL landscape --palette orange --glitch
run $ECTL landscape --palette orange --mountains --stars

run $ECTL landscape --palette sunset --no-clouds --no-stars --glitch
run $ECTL landscape --palette blue --no-mountains --stars --glitch
run $ECTL landscape --palette purple --mountains --clouds --stars

# ── 23 Geometric ──────────────────────────────────────────────────────────────
run $ECTL geometric --style mondrian  --geo-palette teal    --complexity low
run $ECTL geometric --style mondrian  --geo-palette mono    --complexity high
run $ECTL geometric --style mondrian  --geo-palette ember   --complexity low
run $ECTL geometric --style mondrian  --geo-palette forest  --complexity high
run $ECTL geometric --style mondrian  --geo-palette teal    --complexity high
run $ECTL geometric --style mondrian  --geo-palette ember   --complexity high

run $ECTL geometric --style circuit   --geo-palette teal    --complexity low
run $ECTL geometric --style circuit   --geo-palette mono    --complexity high
run $ECTL geometric --style circuit   --geo-palette ember   --complexity low
run $ECTL geometric --style circuit   --geo-palette forest  --complexity high
run $ECTL geometric --style circuit   --geo-palette teal    --complexity high
run $ECTL geometric --style circuit   --geo-palette forest  --complexity low

run $ECTL geometric --style recursive --geo-palette teal    --complexity low
run $ECTL geometric --style recursive --geo-palette mono    --complexity high
run $ECTL geometric --style recursive --geo-palette ember   --complexity low
run $ECTL geometric --style recursive --geo-palette forest  --complexity high
run $ECTL geometric --style recursive --geo-palette teal    --complexity high
run $ECTL geometric --style recursive --geo-palette ember   --complexity high

run $ECTL geometric --style weave     --geo-palette teal    --complexity low
run $ECTL geometric --style weave     --geo-palette mono    --complexity high
run $ECTL geometric --style weave     --geo-palette ember   --complexity low
run $ECTL geometric --style weave     --geo-palette forest  --complexity high
run $ECTL geometric --style weave     --geo-palette teal    --complexity high

# ── 8 Skylines ────────────────────────────────────────────────────────────────
run $ECTL skyline --era modern      --density medium --sky night
run $ECTL skyline --era modern      --density high   --sky dusk
run $ECTL skyline --era retro       --density low    --sky night
run $ECTL skyline --era retro       --density medium --sky day
run $ECTL skyline --era retro       --density high   --sky dusk
run $ECTL skyline --era futuristic  --density low    --sky night
run $ECTL skyline --era futuristic  --density medium --sky dusk
run $ECTL skyline --era futuristic  --density high   --sky night

# ── 8 Constellations ──────────────────────────────────────────────────────────
run $ECTL constellation --culture invented --stars 8
run $ECTL constellation --culture invented --stars 12 --lore
run $ECTL constellation --culture norse    --stars 8  --lore
run $ECTL constellation --culture norse    --stars 10
run $ECTL constellation --culture greek    --stars 9  --lore
run $ECTL constellation --culture greek    --stars 7
run $ECTL constellation --culture random   --stars 11 --lore
run $ECTL constellation --culture random   --stars 6

# ── 8 Verses ──────────────────────────────────────────────────────────────────
run $ECTL verse --form haiku    --theme "the passage of time"        --count 3
run $ECTL verse --form haiku    --theme "winter forges"              --count 5
run $ECTL verse --form lore     --theme "the last silicon dreamer"   --count 3
run $ECTL verse --form lore     --theme "machines that remember rain" --count 2
run $ECTL verse --form epitaph  --theme "a resting place for clocks" --count 4
run $ECTL verse --form epitaph  --theme "abandoned orbital station"  --count 3
run $ECTL verse --form couplet  --theme "entropy as an act of love"  --count 4
run $ECTL verse --form couplet  --theme "deep sea bioluminescence"   --count 3

# ── 8 Palettes ────────────────────────────────────────────────────────────────
run $ECTL palette --mood "volcanic"           --count 6
run $ECTL palette --mood "drowned empire"     --count 7
run $ECTL palette --mood "iron winter"        --count 5
run $ECTL palette --mood "fever dream"        --count 8
run $ECTL palette --mood "cathedral dust"     --count 6
run $ECTL palette --mood "ocean trench"       --count 7
run $ECTL palette --mood "harvest moon"       --count 5
run $ECTL palette --mood "static electricity" --count 6

# ── 8 ANSI ────────────────────────────────────────────────────────────────────
run $ECTL ansi --subject "a mountain at sunset"       --width 60 --ansi-style landscape
run $ECTL ansi --subject "a lighthouse in a storm"    --width 60 --ansi-style scene
run $ECTL ansi --subject "a dragon skull"             --width 50 --ansi-style portrait
run $ECTL ansi --subject "a retro computer terminal"  --width 60 --ansi-style logo
run $ECTL ansi --subject "a black hole"               --width 70 --ansi-style scene
run $ECTL ansi --subject "a cyberpunk city at night"  --width 80 --ansi-style landscape
run $ECTL ansi --subject "a crystal cave"             --width 60 --ansi-style scene
run $ECTL ansi --subject "Tenstorrent chip die"       --width 60 --ansi-style logo --colors 16

# ── 8 Circuits ────────────────────────────────────────────────────────────────
run $ECTL circuit --inputs "A,B"     --gates "and,or"       --depth 1 --circuit-style clean
run $ECTL circuit --inputs "A,B,C"   --gates "and,or"       --depth 2 --circuit-style clean
run $ECTL circuit --inputs "A,B"     --gates "not,xor"      --depth 2 --circuit-style neon
run $ECTL circuit --inputs "X,Y,Z"   --gates "and,or,not"   --depth 3 --circuit-style neon
run $ECTL circuit --inputs "A,B,C,D" --gates "nand,nor"     --depth 2 --circuit-style paper
run $ECTL circuit --inputs "A,B"     --gates "xor,not"      --depth 3 --circuit-style clean
run $ECTL circuit --inputs "P,Q,R"   --gates "and,or,xor"   --depth 2 --circuit-style neon
run $ECTL circuit --inputs "A,B,C"   --gates "nand,xor,not" --depth 3 --circuit-style paper

# ── 8 Freeform ────────────────────────────────────────────────────────────────
run $ECTL freeform --freeform "a circuit diagram of a sad robot as SVG"
run $ECTL freeform --freeform "a Tenstorrent logo rendered as an ASCII art box using only block characters and pipes as SVG text"
run $ECTL freeform --freeform "an architectural cross-section of a neural network visualized as a gothic cathedral, SVG"
run $ECTL freeform --freeform "a map of a fictional archipelago with island names, compass rose, and depth soundings as SVG"
run $ECTL freeform --freeform "a timeline of imaginary civilizations with overlapping eras and key events as SVG"
run $ECTL freeform --freeform "a hand-drawn-style wiring schematic for a perpetual motion machine as SVG"
run $ECTL freeform --freeform "a color palette of 8 colors representing the emotional spectrum of a model training run as JSON with names and hex codes and lore"
run $ECTL freeform --freeform "a haiku sequence about the heat death of a data center, ten verses, plain text"

echo ""
echo "════════════════════════════════════"
echo "  Library generation complete"
echo "  Succeeded: $DONE"
echo "  Failed:    $FAIL"
echo "════════════════════════════════════"
