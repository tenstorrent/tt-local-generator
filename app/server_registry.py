"""Model registry: one list of every model the app can start, from either backend.

GUI-free. Standard library plus PyYAML (the loader ``chip_config`` uses).

Two backends:

* ``"boutique"``: a model in ``server_manager.SERVERS``, started by our own
  ``bin/start_*.sh`` script (the script carries our patches). Key = the SERVERS key.
* ``"tt"``: a model the official ``tt`` CLI carries that has no SERVERS entry,
  started with ``tt serve``. Key = the ``tt`` model name.

A model in both defaults to the boutique script. ``config/server_overrides.yaml``
can flip that (``prefer: tt``) and set ``chips``, ``hardware_group`` and ``port``
per key. A missing or malformed override file is logged and ignored.

Nothing here runs ``tt``, docker or gozer, except ``build_registry(catalog=None)``,
which asks ``tt_backend.catalog()`` (a read-only ``tt model list``).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

import server_manager
import tt_backend
from ready_to_run import _ARTGEN_CAPS, _MEDIA_CAPS, _group_of

log = logging.getLogger(__name__)

_DEFAULT_OVERRIDES = Path(__file__).resolve().parent.parent / "config" / "server_overrides.yaml"

# Which `tt model list` name carries each SERVERS model (wire notes, item 5).
# Models `tt` does not carry (skyreels, wan2.2-i2v, animate, prompt-server,
# animatediff) are absent on purpose.
_TT_NAME_FOR_SERVERS = {
    "wan2.2": "Wan2.2-T2V-A14B-Diffusers",
    "flux": "FLUX.1-schnell",
    "flux-dev": "FLUX.1-dev",
    "mochi": "mochi-1-preview",
    "z-image-turbo": "Z-Image-Turbo",
    "motif": "Motif-Image-6B-Preview",
    "artgen-qwen3-8b": "Qwen3-8B",
    "artgen-qwen3-32b": "Qwen3-32B",
    "artgen-llama-3.1-8b": "Llama-3.1-8B-Instruct",
    "artgen-llama-3.3-70b": "Llama-3.3-70B-Instruct",
    "artgen-deepseek-r1-70b": "DeepSeek-R1-Distill-Llama-70B",
    "artgen-qwen2.5-7b": "Qwen2.5-7B-Instruct",
}

# Chips per board configuration string. Only well-known names; anything else is
# unknown (None), never a guess.
_CHIPS_FOR_HARDWARE = {
    "p100": 1, "p150": 1, "p150x2": 2, "p150x4": 4, "p150x8": 8,
    "p300": 2, "p300x2": 4, "n150": 1, "n300": 2, "t3k": 8,
}

# Engines that serve a chat LLM. Catalog rows do not carry a model type
# (TtModel has none), so engines are the only capability signal: a chat engine
# maps to "artgen" (the app's chat-LLM capability). Other engines ("media",
# "tt-dit-server", "forge", ...) could be video, image, audio or embeddings, so
# they get no capability and therefore no hardware group (unknown, not guessed).
_LLM_ENGINES = frozenset({"vllm", "vllm-plugin", "vllm-fork"})

_VALID_PREFER = ("tt", "boutique")


@dataclass(frozen=True)
class ModelEntry:
    key: str
    name: str
    capabilities: tuple[str, ...]
    backend: str                  # "tt" | "boutique"
    tt_name: str | None           # name `tt` knows it by, if it carries it
    sm_key: str | None            # SERVERS key, if our script can start it
    hardware_group: str | None    # models in one group cannot run together; None = unknown
    chips: int | None             # override, else the catalog's single hardware entry, else None
    port: int | None
    served_id: str | None
    runner_key: str | None
    installed: bool
    benefit: str


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _group_for_caps(caps: Sequence[str]) -> str | None:
    """Same convention as ready_to_run._group_of, from a capability list."""
    c = frozenset(caps)
    if c & _MEDIA_CAPS:
        return "media"
    if c & _ARTGEN_CAPS:
        return "artgen"
    return None


def _caps_for_row(m: tt_backend.TtModel) -> tuple[str, ...]:
    if any(e.lower() in _LLM_ENGINES for e in m.engines):
        return ("artgen",)
    return ()


def _chips_from_hardware(hw: Sequence[str]) -> int | None:
    """Chip count from the catalog, only when the row names exactly one board
    configuration and we know its size."""
    return _CHIPS_FOR_HARDWARE.get(hw[0].lower()) if len(hw) == 1 else None


def _port_of(url: str) -> int | None:
    try:
        return urlparse(url).port
    except ValueError:
        return None


def _load_overrides(path: Path | None) -> dict[str, dict]:
    """Read the override file. Returns {key: {valid fields}}; {} on any problem."""
    import yaml  # same loader chip_config uses
    p = path or _DEFAULT_OVERRIDES
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        log.info("server overrides not read (%s): %s", p, exc)
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        log.warning("server overrides ignored, bad YAML in %s: %s", p, exc)
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    if models is None:
        if data is not None:
            log.warning("server overrides ignored: %s has no 'models' mapping", p)
        return {}
    if not isinstance(models, dict):
        log.warning("server overrides ignored: 'models' in %s is not a mapping", p)
        return {}
    out: dict[str, dict] = {}
    for key, spec in models.items():
        if not isinstance(spec, dict):
            log.warning("server override for %r ignored: not a mapping", key)
            continue
        ok: dict = {}
        if spec.get("prefer") in _VALID_PREFER:
            ok["prefer"] = spec["prefer"]
        elif "prefer" in spec:
            log.warning("server override %r: prefer must be tt or boutique", key)
        for f in ("chips", "port"):
            v = spec.get(f)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                ok[f] = v
            elif f in spec:
                log.warning("server override %r: %s must be a positive integer", key, f)
        g = spec.get("hardware_group")
        if isinstance(g, str) and g.strip():
            ok["hardware_group"] = g.strip()
        elif "hardware_group" in spec:
            log.warning("server override %r: hardware_group must be a non-empty string", key)
        out[str(key)] = ok
    return out


def _merge_catalog(catalog: Sequence[tt_backend.TtModel]) -> dict[str, tt_backend.TtModel]:
    """One row per name: a bundle appears once per profile, so merge hardware and engines."""
    merged: dict[str, tt_backend.TtModel] = {}
    for m in catalog:
        old = merged.get(m.name)
        if old is None:
            merged[m.name] = m
            continue
        merged[m.name] = replace(
            old,
            engines=tuple(dict.fromkeys(old.engines + m.engines)),
            hardware=tuple(dict.fromkeys(old.hardware + m.hardware)),
            installed=old.installed or m.installed,
            backends=tuple(dict.fromkeys(old.backends + m.backends)))
    return merged


def build_registry(catalog: Sequence[tt_backend.TtModel] | None = None,
                   overrides_path: Path | None = None) -> dict[str, ModelEntry]:
    """Every startable model, keyed by registry key. Never raises."""
    if catalog is None:
        try:
            catalog = tt_backend.catalog()
        except Exception as exc:  # defensive: catalog() should not raise
            log.warning("tt catalog unavailable: %s", exc)
            catalog = []
    rows = _merge_catalog(catalog)
    over = _load_overrides(overrides_path)
    reg: dict[str, ModelEntry] = {}
    claimed: set[str] = set()      # catalog names that map to a SERVERS entry

    for key, sdef in server_manager.SERVERS.items():
        o = over.get(key, {})
        tt_name = _TT_NAME_FOR_SERVERS.get(key)
        row = rows.get(tt_name) if tt_name else None
        if row is not None:
            claimed.add(row.name)
        backend = "boutique"
        if o.get("prefer") == "tt":
            if row is not None:
                backend = "tt"
            else:
                log.warning("server override %r: prefer tt, but tt does not carry it", key)
        caps = tuple(sdef.capabilities)
        reg[key] = ModelEntry(
            key=key, name=sdef.label, capabilities=caps, backend=backend,
            tt_name=row.name if row is not None else None, sm_key=key,
            hardware_group=o.get("hardware_group", _group_of(key)),
            chips=o.get("chips", _chips_from_hardware(row.hardware) if row else None),
            port=o.get("port", _port_of(sdef.health_url)),
            served_id=sdef.model_id, runner_key=sdef.runner_key,
            installed=True, benefit=sdef.benefit)

    for name, row in rows.items():
        if name in claimed:
            continue
        o = over.get(name, {})
        if o.get("prefer") == "boutique":
            log.warning("server override %r: prefer boutique, but no script serves it", name)
        caps = _caps_for_row(row)
        reg[name] = ModelEntry(
            key=name, name=name, capabilities=caps, backend="tt",
            tt_name=name, sm_key=None,
            hardware_group=o.get("hardware_group", _group_for_caps(caps)),
            chips=o.get("chips", _chips_from_hardware(row.hardware)),
            port=o.get("port"), served_id=None, runner_key=None,
            installed=row.installed, benefit="")
    return reg


def chips_for(entry: ModelEntry, plan: tt_backend.ServePlan | None) -> int | None:
    """Chips a start needs: the dry-run plan first, then the entry (override, else
    the catalog's single hardware entry), else None. Never a guess; the caller
    warns and asks when this is None."""
    if plan is not None and plan.chips:
        return plan.chips
    return entry.chips
