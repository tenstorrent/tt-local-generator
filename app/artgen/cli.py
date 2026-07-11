"""
artgen CLI — cmd_artgen() and _build_artgen_parser() for tt-ctl.

Uses sub-subparsers so each generator owns its own flag namespace:
  tt-ctl artgen landscape --palette sunset
  tt-ctl artgen constellation --culture norse --stars 8
  tt-ctl artgen verse --form haiku --theme "winter forges"
  tt-ctl artgen --help        (lists all types)
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import artgen
from server_config import server_config

# Args that are scaffolding / not meaningful to store in a record's params.
_PARAMS_SKIP = {
    "output", "max_tokens", "temperature", "timeout",
    "artgen_type", "func", "base_url", "model", "simulate",
    "no_save", "raw", "enhance",
}


def _build_record_params(args) -> dict:
    """Build the params dict stored on an artgen MediaRecord.

    Keeps only primitive-typed, non-scaffolding public args. Generators may
    stash results on underscore-prefixed attrs (dropped by the public filter);
    codeart's safe parse-check result is surfaced explicitly so it is recorded
    on the CLI path for parity with the GUI panel.
    """
    params = {
        k: v for k, v in vars(args).items()
        if isinstance(v, (str, int, float, bool, type(None)))
        and k not in _PARAMS_SKIP
        and not k.startswith("_")
    }
    for key in ("_codeart_compiles", "_codeart_error"):
        if hasattr(args, key):
            params[key] = getattr(args, key)
    return params


# Common flags shared by all generator subcommands
_COMMON_ARGS = [
    ("--output", dict(default=None, metavar="PATH",
                      help="Output file path (default: <type>.<ext> in current directory)")),
    ("--model", dict(default=None, metavar="MODEL_ID",
                     help="Override model ID (default: auto-detect from server)")),
    ("--base-url", dict(default=None, metavar="URL",
                        help="Override LLM endpoint URL (default: server_config artgen entry)")),
    ("--max-tokens", dict(type=int, default=4096, metavar="N",
                          help="Max tokens for LLM response (default: 4096)")),
    ("--temperature", dict(type=float, default=0.7, metavar="T",
                           help="LLM temperature 0.0-1.0 (default: 0.7)")),
    ("--timeout", dict(type=int, default=600, metavar="S",
                       help="HTTP read timeout in seconds (default: 600; raise for slow/large models)")),
    ("--simulate", dict(action="store_true",
                        help="Print the prompt without calling the LLM")),
]


def _build_artgen_parser(sub):
    """
    Register the 'artgen' subcommand on the tt-ctl subparsers object.
    Each generator type becomes its own sub-subcommand with its own flags.
    """
    types_list = "  ".join(artgen.all_names())
    art = sub.add_parser(
        "artgen",
        help="Generate generative art via LLM (SVG, ANSI, verse, palettes, …)",
        description=(
            "Generate generative art artifacts using the currently running LLM.\n\n"
            f"Types: {types_list}\n\n"
            "Examples:\n"
            "  tt-ctl artgen landscape --palette purple --glitch\n"
            "  tt-ctl artgen skyline --era retro --sky dusk\n"
            "  tt-ctl artgen constellation --culture norse --lore\n"
            "  tt-ctl artgen verse --form haiku --theme 'winter forges'\n"
            "  tt-ctl artgen palette --mood 'drowned empire' --export-css\n"
            "  tt-ctl artgen freeform --freeform 'a sad robot circuit diagram' --output robot.svg\n"
            "  tt-ctl artgen landscape --simulate\n"
        ),
    )
    art_sub = art.add_subparsers(dest="artgen_type", metavar="TYPE")

    for g in artgen.all_generators():
        p = art_sub.add_parser(g.name, help=g.description)
        # Common flags on every type
        for flag, kwargs in _COMMON_ARGS:
            p.add_argument(flag, **kwargs)
        # Generator-specific flags
        g.add_args(p)

    return art


def _generate_animate_prompt() -> str:
    """Generate a prompt for AnimateDiff via the prompt engine (with Qwen polish)."""
    import subprocess as _sp
    import json as _json
    _app = Path(__file__).resolve().parent.parent
    gen_script = _app / "generate_prompt.py"
    try:
        r = _sp.run(
            [sys.executable, str(gen_script), "--type", "animatediff"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            data = _json.loads(r.stdout)
            src = data.get("source", "algo")
            print(f"[prompt source: {src}]", flush=True)
            return data["prompt"]
    except Exception as e:
        print(f"  [prompt engine unavailable: {e} — using fallback]")
    return "a person walking through a moonlit forest, cinematic, atmospheric"


def _parse_prompt_schedule(entries: list[str] | None) -> list[tuple[int, str]] | None:
    """Parse repeatable --prompt-schedule FRAME:PROMPT strings into (frame, prompt)
    tuples, preserved in declaration order (callers may rely on that order for
    keyframe sequencing rather than re-sorting by frame index).

    Splits on the FIRST colon only so a prompt containing its own colon (e.g.
    "a scene: cold and blue") survives intact. Raises ValueError with an
    actionable message on a malformed entry (missing colon, non-integer frame
    index, or empty prompt text) — callers should catch this and exit cleanly
    rather than let a traceback surface.
    """
    if not entries:
        return None
    schedule: list[tuple[int, str]] = []
    for entry in entries:
        if ":" not in entry:
            raise ValueError(
                f"Invalid --prompt-schedule entry {entry!r}: expected FRAME:PROMPT "
                "(e.g. --prompt-schedule 0:'spring meadow')"
            )
        frame_str, prompt_str = entry.split(":", 1)
        frame_str = frame_str.strip()
        try:
            frame = int(frame_str)
        except ValueError:
            raise ValueError(
                f"Invalid --prompt-schedule entry {entry!r}: frame index "
                f"{frame_str!r} is not an integer"
            )
        prompt_str = prompt_str.strip()
        if not prompt_str:
            raise ValueError(
                f"Invalid --prompt-schedule entry {entry!r}: prompt text is empty"
            )
        schedule.append((frame, prompt_str))
    return schedule


def _cmd_animatediff(args) -> None:
    """Route for 'tt-ctl artgen animatediff' — uses prompt engine, not LLM artgen."""
    from artgen.generators.animatediff import check_hardware, run_subprocess, make_gif_thumbnail

    ok, hw_msg = check_hardware()
    if not ok:
        print(f"ERROR: {hw_msg}", file=sys.stderr)
        sys.exit(1)
    print(f"[hardware: {hw_msg}]", flush=True)

    try:
        prompt_schedule = _parse_prompt_schedule(getattr(args, "prompt_schedule", None))
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    count = getattr(args, "count", 1)

    for i in range(count):
        iteration = f"{i + 1}/{count}"

        # Prompt: explicit flag wins; otherwise auto-generate via prompt engine
        prompt = getattr(args, "prompt", None) or None
        if not prompt:
            print(f"\n── [{iteration}] Generating prompt …", flush=True)
            prompt = _generate_animate_prompt()
        print(f"[{iteration}] Prompt: {prompt[:100]}", flush=True)

        # Output path
        explicit_out = getattr(args, "output", None)
        if explicit_out and count == 1:
            out_path = Path(explicit_out)
        else:
            try:
                from media_store import make_artgen_path
                out_path = make_artgen_path(str(uuid.uuid4())[:8], ".gif")
            except Exception:
                out_path = Path(f"animatediff_{i:02d}.gif")

        seed = getattr(args, "seed", 42) + i

        ok, err = run_subprocess(
            prompt=prompt,
            out_path=out_path,
            mode=getattr(args, "mode", "blackhole"),
            frames=getattr(args, "frames", 8),
            steps=getattr(args, "steps", 25),
            seed=seed,
            negative_prompt=getattr(args, "negative_prompt", "blurry, low quality"),
            temporal_alpha=getattr(args, "temporal_alpha", 0.35),
            lightning=getattr(args, "lightning", False),
            lightning_steps=getattr(args, "lightning_steps", 4),
            device_id=getattr(args, "device_id", None),
            chain_from=getattr(args, "chain_from", None),
            chain_save=getattr(args, "chain_save", None),
            chain_alpha=getattr(args, "chain_alpha", 0.6),
            motion_adapter=getattr(args, "motion_adapter", None),
            motion_adapter_alpha=getattr(args, "motion_adapter_alpha", 1.0),
            motion_adapter_skip=getattr(args, "motion_adapter_skip", None),
            multichip_mode=getattr(args, "multichip_mode", "off"),
            per_chip_prompts=getattr(args, "per_chip_prompts", None),
            seed_spread=getattr(args, "seed_spread", 1),
            ramp=getattr(args, "ramp", "none"),
            ramp_lo=getattr(args, "ramp_lo", 0.0),
            ramp_hi=getattr(args, "ramp_hi", 1.0),
            stitch_order=getattr(args, "stitch_order", "interleave"),
            prompt_schedule=prompt_schedule,
            loop=getattr(args, "loop", "none"),
            on_progress=lambda msg: print(f"  {msg}", flush=True),
        )

        if not ok:
            print(f"  ERROR: {err}", file=sys.stderr)
            continue

        # Thumbnail
        thumb_path = out_path.parent / "thumbnails" / (out_path.stem + ".jpg")
        make_gif_thumbnail(out_path, thumb_path)

        # Register in media store so GUI picks it up immediately
        try:
            from media_store import media_store as _ms, MediaRecord
            import json as _json
            rec = MediaRecord(
                id=str(uuid.uuid4()),
                media_type="artgen",
                created_at=datetime.now(timezone.utc).isoformat(),
                file_path=str(out_path),
                thumbnail_path=str(thumb_path) if thumb_path.exists() else "",
                prompt=prompt[:500],
                model_id="animatediff-blackhole",
                generator_type="animatediff",
                params=_json.dumps({
                    "frames": getattr(args, "frames", 8),
                    "steps": getattr(args, "steps", 25),
                    "seed": seed,
                    "temporal_alpha": getattr(args, "temporal_alpha", 0.35),
                }),
                starred=0,
            )
            _ms.add(rec)
            _ms.ensure_auto_playlists()
        except Exception as _e:
            print(f"  [media-store: {_e}]")

        print(f"[saved → {out_path}]", flush=True)


def _make_call_fn(model_id: str, base_url: str, args):
    """
    Return a call_fn(prompt, system=None, max_tokens=None) -> str closure.

    Uses artgen.call_llm (stdlib urllib — safe from GTK background threads).
    The system= kwarg lets generators like VerseGenerator pass their own system
    prompt per call without mutating the args namespace.
    The max_tokens override lets multi-pass generators tune each pass budget
    independently (e.g. 1024 for ASCII structure, 8192 for colorisation).
    """
    def _call_fn(prompt, system=None, max_tokens=None):
        raw, _ = artgen.call_llm(
            prompt, model_id, base_url,
            max_tokens=max_tokens or getattr(args, "max_tokens", 4096),
            temperature=getattr(args, "temperature", 0.7),
            timeout=getattr(args, "timeout", 600),
            system=system,
        )
        return raw

    return _call_fn


def cmd_artgen(args) -> None:
    """Handler for 'tt-ctl artgen TYPE ...'."""
    gen_name = getattr(args, "artgen_type", None)
    if not gen_name:
        # No type given — print help by re-invoking with --help
        import subprocess
        subprocess.run([sys.argv[0], "artgen", "--help"])
        return

    # AnimateDiff bypasses the LLM artgen pipeline — uses prompt engine + direct subprocess
    if gen_name == "animatediff":
        _cmd_animatediff(args)
        return

    gen = artgen.get(gen_name)

    if getattr(args, "simulate", False):
        # For multi-pass generators (e.g. ANSI), build_prompt returns pass-1
        # which is the most instructive thing to show for a dry run.
        try:
            prompt = gen.build_prompt(args)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[--simulate: LLM call skipped — generator: {gen.name}]\n")
        print("PROMPT (pass 1):")
        print("─" * 60)
        print(prompt)
        return

    # Resolve LLM endpoint — explicit --base-url wins; otherwise pick the best
    # available: artgen server (8002) first, then prompt-gen (8001, Qwen3-0.6B).
    explicit_url = getattr(args, "base_url", None)
    model_id = getattr(args, "model", None)
    if model_id is None:
        base_url, model_id = artgen.detect_artgen_endpoint(preferred_url=explicit_url)
        if model_id:
            print(f"[auto-detected model: {model_id} @ {base_url}]")
        else:
            print(
                "ERROR: no LLM detected (tried port 8002 and port 8001).\n"
                "  Start an artgen server:  tt-ctl start artgen-qwen3-8b\n"
                "  Or the prompt server:    tt-ctl start prompt-server\n"
                "  Override endpoint:       tt-ctl artgen <type> --base-url http://localhost:8002",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        base_url = explicit_url or server_config.base_url("artgen")

    print(f"[artgen: {gen.name} via {model_id} @ {base_url}]", flush=True)

    # Capture a prompt summary for MediaRecord storage before running the pipeline.
    # For multi-pass generators build_prompt() returns pass-1 (the structural prompt).
    try:
        prompt_summary = gen.build_prompt(args)
    except Exception:
        prompt_summary = ""

    # Run the generator's pipeline (single-pass default; multi-pass for ANSI etc.)
    call_fn = _make_call_fn(model_id, base_url, args)
    try:
        artifact = gen.generate_artifact(args, call_fn)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Save — use media store path when no explicit --output so the GUI picks it up
    explicit_out = getattr(args, "output", None)
    if explicit_out:
        out_path = Path(explicit_out)
        out_path.write_text(artifact, encoding="utf-8")
    else:
        try:
            from media_store import media_store as _ms, make_artgen_path, make_thumbnail
            short_id = str(uuid.uuid4())[:8]
            ext = Path(gen.default_output()).suffix
            out_path = make_artgen_path(short_id, ext)
            out_path.write_text(artifact, encoding="utf-8")

            thumb_dir = out_path.parent / "thumbnails"
            thumb_path = thumb_dir / (out_path.stem + ".png")
            try:
                make_thumbnail(out_path, thumb_path)
            except Exception:
                thumb_path = None

            params = _build_record_params(args)

            from media_store import MediaRecord
            rec = MediaRecord(
                id=str(uuid.uuid4()),
                media_type="artgen",
                created_at=datetime.now(timezone.utc).isoformat(),
                file_path=str(out_path),
                thumbnail_path=str(thumb_path) if thumb_path is not None and thumb_path.exists() else "",
                prompt=prompt_summary[:500],
                model_id=model_id,
                generator_type=gen_name,
                params=json.dumps(params),
                starred=0,
            )
            _ms.add(rec)
            _ms.ensure_auto_playlists()
        except Exception as _e:
            # Graceful fallback: save to cwd without media-store registration
            out_path = Path(gen.default_output())
            out_path.write_text(artifact, encoding="utf-8")
            print(f"  [media-store registration skipped: {_e}]")

    print(f"[saved → {out_path}]")

    if out_path.suffix.lower() == ".svg":
        print(f"  open in browser: file://{out_path.resolve()}")
    elif out_path.suffix.lower() == ".ans":
        print(f"  view in terminal: cat {out_path}")
