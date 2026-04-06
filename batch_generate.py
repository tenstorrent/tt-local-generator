#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
batch_generate.py — one-shot batch runner.

Generates N prompts per director, runs each through tt-ctl run, then suspends.
Progress is written to batch_generate.log in the same directory.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(HERE / "batch_generate.log", mode="w"),
    ],
)
log = logging.getLogger(__name__)

# ── Director definitions ────────────────────────────────────────────────────────
# Format matches CINEMATIC_DIRECTORS: "Name — visual signature"
DIRECTORS = [
    ("Fellini",       "Fellini — carnival dreamscape, baroque crowd, memory dissolve"),
    ("Hitchcock",     "Hitchcock — voyeuristic high-angle thriller, chiaroscuro"),
    ("Wes Anderson",  "Wes Anderson — symmetry, pastel, nostalgia rendered as grief"),
    ("Spielberg",     "Spielberg — golden hour suburbia, kinetic wonder, backlit silhouette against open sky"),
    ("Disney",        "Disney — classical storybook palette, enchanted wonder, fairy tale transformation in warm light"),
]

PROMPTS_PER_DIRECTOR = 4   # 5 × 4 = 20 total
STEPS = 30                 # Standard quality (server timeout ~1000s; 80 steps exceeded it)
MODEL = "video"
SERVER = "http://localhost:8000"


def generate_prompt(director_pin: str) -> str:
    """Generate one algorithmic prompt with the given director pinned as style slot."""
    result = subprocess.run(
        [
            "python3", str(HERE / "generate_prompt.py"),
            "--type", "video",
            "--mode", "algo",
            "--no-enhance",          # LLM server may not be running overnight
            "--director", director_pin,
        ],
        capture_output=True,
        text=True,
        cwd=str(HERE),
    )
    if result.returncode != 0:
        raise RuntimeError(f"generate_prompt.py failed:\n{result.stderr.strip()}")
    return json.loads(result.stdout.strip())["prompt"]


def run_generation(prompt: str, n: int, total: int) -> bool:
    """Run one tt-ctl generation.  Returns True on success."""
    log.info("[%d/%d]  Running: %s", n, total, prompt[:90] + ("…" if len(prompt) > 90 else ""))
    result = subprocess.run(
        [
            "python3", str(HERE / "tt-ctl"),
            "--server", SERVER,
            "run", prompt,
            "--model", MODEL,
            "--steps", str(STEPS),
        ],
        cwd=str(HERE),
    )
    if result.returncode != 0:
        log.error("[%d/%d]  FAILED (exit %d)", n, total, result.returncode)
        return False
    log.info("[%d/%d]  Done.", n, total)
    return True


def main() -> None:
    total = len(DIRECTORS) * PROMPTS_PER_DIRECTOR
    log.info("=== Batch generation start: %d prompts, %d steps ===", total, STEPS)
    log.info("Directors: %s", ", ".join(d[0] for d in DIRECTORS))

    jobs: list[tuple[str, str]] = []
    for display, director_pin in DIRECTORS:
        for i in range(PROMPTS_PER_DIRECTOR):
            try:
                prompt = generate_prompt(director_pin)
                jobs.append((display, prompt))
                log.info("  Generated [%s #%d]: %s", display, i + 1, prompt[:80])
            except Exception as exc:
                log.error("  Prompt generation failed for %s: %s", display, exc)
                # Use a fallback minimal prompt so we don't lose a slot
                jobs.append((display, f"{director_pin}, cinematic"))

    log.info("")
    log.info("=== Starting generation run (%d jobs) ===", len(jobs))

    successes = 0
    for n, (display, prompt) in enumerate(jobs, start=1):
        log.info("--- %s ---", display)
        if run_generation(prompt, n, len(jobs)):
            successes += 1

    log.info("")
    log.info("=== Batch complete: %d/%d succeeded ===", successes, len(jobs))
    log.info("Suspending system...")
    subprocess.run(["systemctl", "suspend"])


if __name__ == "__main__":
    main()
