#!/usr/bin/env bash
# generate_bbs_batch.sh — Generate a batch of BBS logo screens that never existed.
# Requires: artgen LLM on port 8002 (tt-ctl start artgen-llama-3.1-8b)
#
# Usage:  ./generate_bbs_batch.sh [--base-url URL]
set -euo pipefail

BASE_URL="http://localhost:8002"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-url) BASE_URL="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done
ECTL="./tt-ctl"
LOG="/tmp/bbs_batch.log"

run() {
    local board="$1" tagline="$2" subject="$3"
    echo "── $board"
    $ECTL artgen ansi \
        --ansi-style bbs \
        --board-name "$board" \
        --tagline "$tagline" \
        --subject "$subject" \
        --base-url "$BASE_URL" \
        --max-tokens 4096 \
        >> "$LOG" 2>&1
}

echo "BBS BATCH — $(date)" | tee "$LOG"
echo "18 boards. Estimated time: ~18 min on Llama-3.1-8B."
echo ""

# Cyberpunk / hacker underground
run "PHANTOM EXCHANGE" \
    "where data bleeds and signals scream" \
    "a fractured skull made of circuit traces, neon cyan and hot pink on black void, electric cracks radiating outward"

run "VOID PROTOCOL" \
    "the handshake that ends all handshakes" \
    "a glowing eye inside a cracked computer terminal, green phosphor light leaking through breaks, deep black background"

run "DARK MATRIX BBS" \
    "there is no carrier signal" \
    "cascading columns of green code rain converging into a vortex, cyberpunk cityscape silhouette at base"

run "IRON SKULL" \
    "dial in. drop out." \
    "a chrome mechanical skull front-facing, glowing red eye sockets, circuit-board texture on cranium, near-black background"

# Fantasy / occult
run "NECROMANCER'S KEEP" \
    "the dead log on nightly" \
    "a robed skeletal figure at a glowing terminal, stone tower window behind, purple and gold magic energy, moonlit void"

run "ARCANE PROTOCOL" \
    "binding the digital and the ancient" \
    "a summoning circle that is also a circuit diagram, glowing runes at node points, electric violet on deep black"

run "THE ORACLE" \
    "she knows your password" \
    "a giant disembodied eye surrounded by floating geometric sigils, iris made of code, electric gold and white glow"

run "WYRM GATE BBS" \
    "here there be packets" \
    "a massive serpentine dragon coiled around a glowing modem tower, scales shimmering electric blue, dark storm sky"

# Space / cosmic
run "BINARY SUNSET" \
    "two suns. no mercy." \
    "two dying suns on a cracked alien horizon, silhouette of a lone figure at a terminal, deep crimson and gold void sky"

run "NOVA STATION" \
    "transmitting from the event horizon" \
    "a space station in the moment of explosion, shockwave rings expanding, debris silhouetted against a white nova burst"

run "DARK SIDE BBS" \
    "far side of the signal" \
    "the dark face of a planet filling the frame, single city of lights on the terminator edge, star field behind, blue-black void"

# Gritty / street / underground
run "MIDNIGHT COURIER" \
    "delivering bits nobody ordered" \
    "a courier on a motorcycle in heavy rain, neon city reflections on wet asphalt, motion blur, pink and teal glow"

run "GRAVEYARD SHIFT" \
    "open 11pm to 6am only" \
    "a skeleton in a hoodie hunched over a glowing keyboard at 3am, cigarette smoke curling up, blue monitor light, deep black"

run "THE VELVET UNDERGROUND BBS" \
    "no sysops. no rules. no refunds." \
    "a basement door ajar, pale light leaking out, city street above visible, silhouette of a descending figure, neon graffiti walls"

# Surreal / strange
run "STATIC CHAPEL" \
    "pray to the carrier wave" \
    "a gothic cathedral made entirely of television static and interference patterns, stained glass windows of pure noise, electric white on black"

run "THE MEAT GRID" \
    "where the flesh meets the wire" \
    "organic and digital fused — a human hand reaching through a grid of glowing wires, veins and circuits intertwined, red and cyan"

run "KIPPLE STATION" \
    "entropic data storage since 1987" \
    "a massive pile of obsolete electronics — floppy disks, tangled cables, old monitors — all glowing faintly from within, warm amber on dark"

run "ELECTRIC PSALMS" \
    "the liturgy of the handshake" \
    "a vast dark cathedral where the pews are rows of computer terminals, all screens glowing the same color, a lone figure at the altar"

echo ""
echo "Done. Check results: grep 'saved →' $LOG"
echo "Count: $(grep -c 'saved →' $LOG 2>/dev/null || echo 0) / 18"
