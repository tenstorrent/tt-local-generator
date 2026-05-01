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
from datetime import datetime
from pathlib import Path

import artgen
from server_config import server_config

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
        help="Generate art artifacts via LLM (SVG, ANSI, verse, palettes, …)",
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


def cmd_artgen(args) -> None:
    """Handler for 'tt-ctl artgen TYPE ...'."""
    gen_name = getattr(args, "artgen_type", None)
    if not gen_name:
        # No type given — print help by re-invoking with --help
        import subprocess
        subprocess.run([sys.argv[0], "artgen", "--help"])
        return

    # Resolve LLM endpoint
    base_url = getattr(args, "base_url", None) or server_config.base_url("artgen")

    gen = artgen.get(gen_name)

    # Build prompt
    try:
        prompt = gen.build_prompt(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "simulate", False):
        print(f"[--simulate: LLM call skipped — generator: {gen.name}]\n")
        print("PROMPT:")
        print("─" * 60)
        print(prompt)
        return

    # Resolve model
    model_id = getattr(args, "model", None)
    if model_id is None:
        model_id = artgen.detect_model(base_url)
        if model_id:
            print(f"[auto-detected model: {model_id}]")
        else:
            print(
                f"ERROR: no chat model detected at {base_url}\n"
                "  artgen needs a chat/text model on port 8002 (not the diffusion server on 8000).\n"
                "  Start one: python3 app/prompt_server.py --port 8002\n"
                "       or:  vllm serve <model> --port 8002\n"
                "  Override: tt-ctl artgen <type> --base-url http://localhost:8000/v1",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"[artgen: {gen.name} via {model_id} @ {base_url}]", flush=True)

    # Call LLM — verse stashes a system prompt on args via build_prompt()
    system_msg = getattr(args, "_verse_system", None)
    if system_msg:
        try:
            from openai import OpenAI
        except ImportError:
            print("ERROR: openai not installed. Run: pip install openai", file=sys.stderr)
            sys.exit(1)
        client = OpenAI(base_url=base_url, api_key="none")
        resp = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            max_tokens=getattr(args, "max_tokens", 4096),
            temperature=getattr(args, "temperature", 0.7),
        )
        raw = resp.choices[0].message.content or ""
    else:
        raw, _usage = artgen.call_llm(
            prompt, model_id, base_url,
            max_tokens=getattr(args, "max_tokens", 4096),
            temperature=getattr(args, "temperature", 0.7),
        )

    # Parse
    try:
        artifact = gen.parse_output(raw, args)
    except ValueError as e:
        raw_path = Path(getattr(args, "output", None) or gen.default_output()).with_suffix(".raw.txt")
        raw_path.write_text(raw)
        print(f"ERROR: {e}", file=sys.stderr)
        print(f"  Raw LLM output saved → {raw_path}", file=sys.stderr)
        sys.exit(1)

    # Post-process (glitch for landscape, CSS export for palette, etc.)
    artifact = gen.post_process(artifact, args)

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
                thumb_path = Path("")

            params = {k: v for k, v in vars(args).items()
                      if isinstance(v, (str, int, float, bool, type(None)))
                      and k not in ("output", "max_tokens", "temperature")}

            from media_store import MediaRecord
            rec = MediaRecord(
                id=str(uuid.uuid4()),
                media_type="artgen",
                created_at=datetime.now().isoformat(),
                file_path=str(out_path),
                thumbnail_path=str(thumb_path) if thumb_path.exists() else "",
                prompt=prompt[:500],
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
