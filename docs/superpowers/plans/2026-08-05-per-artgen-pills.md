# Per-Artgen-Type Modifier Pills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each artgen type its own curated Direction-pill vocabulary in Create — plus a shared cross-type mood bank and a 🎲 Surprise chip — instead of the wrong photo/video pills (or none).

**Architecture:** Data-driven. New `for: [<artgen-type>]` + `for: [artgen]` categories in `app/config/prompt_chips.yaml`; `chip_config` gains a Surprise-chip field + a `load_chips_for_artgen(type)` merge (type banks + shared mood bank); `ModifierPills` renders the Surprise chip and can load an artgen bank; `RoleZonePanel` keys artgen mediums by `medium.id` instead of `medium.kind`. Native mediums are untouched.

**Tech Stack:** Python 3 (system `/usr/bin/python3`), GTK4/PyGObject, PyYAML, pytest under `xvfb`.

## Global Constraints

- **`collect()`/`_collect_params()` output stays byte-for-byte identical** — pills only append `applied_text()` to the brief at generate time; which bank a medium shows is display-only.
- Native mediums (image/video) show the SAME banks as today; existing photo/video YAML categories are untouched; `load_chips("image"/"video"/"animate")` behavior is unchanged.
- YAML schema additions are backward-compatible: `surprise: true` (a chip needing no `text`) and new `for: [<artgen-type>]`/`for: [artgen]` categories; existing chips parse unchanged. A NON-surprise chip still REQUIRES `text` (the existing ValueError test must keep passing).
- The tab-filtering rule is unchanged: a category with no `for:` still defaults to `_ALL_TABS = {video, image, animate}`, so artgen-type categories (tagged `for: [palette]` etc.) never leak into photo tabs, and photo categories never leak into artgen types.
- `ModifierPills` loads via the module-level seam(s) in `create_param_panels` (`load_chips_for_kind`, and the new `load_chips_for_artgen_kind`) so tests monkeypatch the seam, not the YAML.
- Surprise randomness goes through a module-level `_pick_surprise(pool)` so tests inject a deterministic pick.
- System `/usr/bin/python3`; pure `chip_config` tests headless; GTK tests under `xvfb`. Version bump (VERSION 0.73.0 → 0.74.0) + `debian/changelog`.
- Known-flake deselects: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`, `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`, `tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen`.

## File Structure

- `app/chip_config.py` (modify) — `ChipEntry.surprise`; surprise chips need no `text`; `load_chips_for_artgen(type)`; `surprise_pool(category)`.
- `app/config/prompt_chips.yaml` (modify) — per-artgen-type categories + a shared `for: [artgen]` mood bank + a Surprise chip per bank.
- `app/create_param_panels.py` (modify) — `load_chips_for_artgen_kind` seam; `ModifierPills(kind, *, artgen=False)` + Surprise-chip rendering + `_pick_surprise`; `RoleZonePanel` keys artgen mediums by `medium.id`.
- Tests in `tests/`.

---

### Task 1: `chip_config` — Surprise chips + artgen loader

**Files:**
- Modify: `app/chip_config.py`
- Test: `tests/test_chip_config.py` (extend)

**Interfaces — Produces:**
- `ChipEntry.surprise: bool = False`.
- `load_chips(tab)` accepts a chip with `surprise: true` and no `text` (text defaults `""`, `surprise=True`); a non-surprise chip still requires `text`.
- `load_chips_for_artgen(artgen_type, config_path=None) -> list[ChipCategory]` = `load_chips(artgen_type) + load_chips("artgen")`, deduped by category name (type categories first; shared "artgen" categories appended only if their name isn't already present).
- `surprise_pool(category: ChipCategory) -> list[str]` — `.text` of every non-surprise chip in the category.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chip_config.py  (add; keep existing tests + the _yaml helper)
from chip_config import load_chips_for_artgen, surprise_pool  # add to imports


def test_surprise_chip_needs_no_text(tmp_path):
    cfg = _yaml(
        "- name: Mood\n"
        "  for: [palette]\n"
        "  chips:\n"
        "    - {label: '🎲 Surprise', surprise: true}\n"
        "    - {label: 'moody', text: 'moody'}\n",
        tmp_path)
    cats = load_chips("palette", cfg)
    chips = cats[0].chips
    assert chips[0].surprise is True and chips[0].text == ""
    assert chips[1].surprise is False


def test_non_surprise_chip_still_requires_text(tmp_path):
    cfg = _yaml("- name: X\n  for: [palette]\n  chips:\n    - {label: 'a'}\n", tmp_path)
    with pytest.raises(ValueError):
        load_chips("palette", cfg)


def test_load_chips_for_artgen_merges_type_and_shared(tmp_path):
    cfg = _yaml(
        "- name: Palette Mood\n  for: [palette]\n  chips:\n    - {label: 'moody', text: 'moody'}\n"
        "- name: Feeling\n  for: [artgen]\n  chips:\n    - {label: 'serene', text: 'serene'}\n"
        "- name: Camera\n  for: [video, image]\n  chips:\n    - {label: 'cine', text: 'cinematic'}\n",
        tmp_path)
    cats = load_chips_for_artgen("palette", cfg)
    names = [c.name for c in cats]
    assert "Palette Mood" in names and "Feeling" in names   # type + shared
    assert "Camera" not in names                             # photo bank excluded


def test_load_chips_for_artgen_type_with_no_banks_gets_shared_only(tmp_path):
    cfg = _yaml("- name: Feeling\n  for: [artgen]\n  chips:\n    - {label: 'serene', text: 'serene'}\n", tmp_path)
    cats = load_chips_for_artgen("verse", cfg)
    assert [c.name for c in cats] == ["Feeling"]


def test_surprise_pool_excludes_surprise_chip(tmp_path):
    cfg = _yaml(
        "- name: Mood\n  for: [palette]\n  chips:\n"
        "    - {label: '🎲 Surprise', surprise: true}\n"
        "    - {label: 'moody', text: 'moody'}\n    - {label: 'lush', text: 'lush'}\n",
        tmp_path)
    cat = load_chips("palette", cfg)[0]
    assert surprise_pool(cat) == ["moody", "lush"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_chip_config.py -q`
Expected: FAIL (`ImportError` for `load_chips_for_artgen`/`surprise_pool`; surprise chip currently raises on missing text).

- [ ] **Step 3: Write minimal implementation**

In `app/chip_config.py`:
- Add `surprise: bool = False` to `ChipEntry`.
- In `load_chips`, replace the text-required block so surprise chips are exempt:

```python
            text = chip_raw.get("text")
            is_surprise = bool(chip_raw.get("surprise", False))
            if text is None and not is_surprise:
                raise ValueError(
                    f"Chip at category '{cat_name}' index {chip_idx} is missing required field 'text'"
                )
            ...
            if tab in effective_for:
                matched.append(ChipEntry(
                    label=label,
                    text=text if text is not None else "",
                    tip=chip_raw.get("tip", ""),
                    surprise=is_surprise,
                ))
```

- Add after `load_chips`:

```python
def load_chips_for_artgen(artgen_type: str, config_path: "Path | None" = None) -> "list[ChipCategory]":
    """Chip banks for an artgen TYPE: that type's own categories plus the
    shared cross-type 'artgen' mood bank. Deduped by category name (type
    first). A type with no curated banks yields just the shared bank."""
    type_cats = load_chips(artgen_type, config_path)
    seen = {c.name for c in type_cats}
    shared = [c for c in load_chips("artgen", config_path) if c.name not in seen]
    return type_cats + shared


def surprise_pool(category: "ChipCategory") -> "list[str]":
    """The `.text` of every non-surprise chip in *category* — the pool a
    Surprise chip picks from."""
    return [c.text for c in category.chips if not c.surprise and c.text]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_chip_config.py -q`
Expected: PASS (all, incl. the pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add app/chip_config.py tests/test_chip_config.py
git commit -m "feat(pills): chip_config surprise chips + load_chips_for_artgen + surprise_pool"
```

---

### Task 2: Curated per-artgen banks + shared mood bank (YAML)

**Files:**
- Modify: `app/config/prompt_chips.yaml`
- Test: `tests/test_prompt_chips_artgen.py` (against the REAL shipped config)

**Interfaces — Consumes:** `chip_config.load_chips_for_artgen` (Task 1).

Add curated categories for the shipped artgen types + one shared mood bank, each with a Surprise chip.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompt_chips_artgen.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from chip_config import load_chips, load_chips_for_artgen


def _labels(cats):
    return [c.label for cat in cats for c in cat.chips]


def test_palette_bank_present_and_photo_absent():
    cats = load_chips_for_artgen("palette")   # real app/config/prompt_chips.yaml
    labels = _labels(cats)
    assert any("moody" in l.lower() or "pastel" in l.lower() for l in labels)
    # a photo-only chip must NOT appear for palette
    assert not any("cinematic" in l.lower() or "aerial" in l.lower() for l in labels)


def test_verse_text_type_gets_banks():
    cats = load_chips_for_artgen("verse")
    assert cats, "verse should get at least the shared mood bank"


def test_every_artgen_bank_has_a_surprise_chip():
    for t in ("palette", "verse", "ansi", "landscape"):
        cats = load_chips_for_artgen(t)
        assert any(c.surprise for cat in cats for c in cat.chips), f"{t} lacks a Surprise chip"


def test_shared_mood_bank_reaches_every_type():
    p = {c.name for c in load_chips_for_artgen("palette")}
    v = {c.name for c in load_chips_for_artgen("verse")}
    assert p & v, "a shared (artgen) category should appear for both"


def test_native_image_bank_excludes_artgen_categories():
    names = {c.name for c in load_chips("image")}
    # the shared artgen mood bank + palette bank must not show on the native image tab
    assert "Feeling" not in names  # (use the exact shared-bank name chosen below)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_prompt_chips_artgen.py -q`
Expected: FAIL (no artgen categories in the YAML yet).

- [ ] **Step 3: Add the YAML categories**

Append to `app/config/prompt_chips.yaml` (curate real, evocative chips; each bank ends with a Surprise chip; the shared bank is named `Feeling` and tagged `for: [artgen]`). Cover **palette, verse, ansi, landscape, codeart, freeform** + the shared `Feeling` bank. Example shape (write full banks per the design):

```yaml
# ── Feeling (shared across all artgen types) ─────────────────────────────────
- name: Feeling
  for: [artgen]
  chips:
    - {label: "🎲 Surprise", surprise: true}
    - {label: "serene", text: "serene", tip: "calm, unhurried"}
    - {label: "chaotic", text: "chaotic energy"}
    - {label: "nostalgic", text: "nostalgic, faded"}
    - {label: "dreamlike", text: "dreamlike, surreal"}
    - {label: "stark", text: "stark, minimal"}
    - {label: "lush", text: "lush, saturated"}

# ── Palette ──────────────────────────────────────────────────────────────────
- name: Mood
  for: [palette]
  chips:
    - {label: "🎲 Surprise", surprise: true}
    - {label: "moody", text: "moody, low-key"}
    - {label: "sun-bleached", text: "sun-bleached"}
    - {label: "neon-noir", text: "neon-noir"}
    - {label: "pastel", text: "soft pastel"}
    - {label: "jewel-tone", text: "rich jewel tones"}
    - {label: "earthy", text: "earthy, natural"}
- name: Era
  for: [palette]
  chips:
    - {label: "Y2K", text: "Y2K"}
    - {label: "art-deco", text: "art-deco"}
    - {label: "vaporwave", text: "vaporwave"}
    - {label: "70s film", text: "1970s film stock"}
- name: Source
  for: [palette]
  chips:
    - {label: "🎲 Surprise", surprise: true}
    - {label: "coral reef", text: "coral reef"}
    - {label: "autumn forest", text: "autumn forest"}
    - {label: "city at dusk", text: "city at dusk"}
    - {label: "desert dawn", text: "desert dawn"}
# ... verse (Form/Tone/Voice), ansi (Scene/Subject/Look), landscape (Biome/Light/Style),
#     codeart, freeform — each with a Surprise chip — per the design doc.
```

(Author the verse/ansi/landscape/codeart/freeform banks fully per the design; keep chip `text` values short, prose-like fragments suitable to append to a brief.)

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_prompt_chips_artgen.py tests/test_chip_config.py -q`
Expected: PASS. (If `test_native_image_bank_excludes_artgen_categories` used a placeholder name, align it to the real shared-bank name `Feeling`.)

- [ ] **Step 5: Commit**

```bash
git add app/config/prompt_chips.yaml tests/test_prompt_chips_artgen.py
git commit -m "feat(pills): curated per-artgen-type chip banks + shared Feeling bank + Surprise chips"
```

---

### Task 3: `ModifierPills` — artgen loader + Surprise chip

**Files:**
- Modify: `app/create_param_panels.py`
- Test: `tests/test_modifier_pills.py` (extend)

**Interfaces — Consumes:** `chip_config.load_chips_for_artgen`, `surprise_pool` (Task 1). **Produces:**
- `load_chips_for_artgen_kind(artgen_type) -> list` — fail-soft seam (mirrors `load_chips_for_kind`).
- `ModifierPills(kind, *, artgen: bool = False)` — `artgen=True` loads via `load_chips_for_artgen_kind(kind)`.
- Surprise chip: an add-chip built from a `surprise` `ChipEntry` applies a random pill from the category's `surprise_pool` and STAYS visible (re-tappable); random pick via module-level `_pick_surprise(pool)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_modifier_pills.py  (add; keep the GTK skip guard + `import create_param_panels as cpp`)
from chip_config import ChipEntry, ChipCategory


def test_artgen_mode_uses_artgen_loader(monkeypatch):
    called = {}
    monkeypatch.setattr(cpp, "load_chips_for_artgen_kind",
                        lambda t: called.setdefault("t", t) or
                        [ChipCategory("Mood", [ChipEntry("moody", "moody")])])
    p = cpp.ModifierPills("palette", artgen=True)
    assert called["t"] == "palette"


def test_surprise_chip_applies_random_from_pool_and_stays(monkeypatch):
    cat = ChipCategory("Mood", [
        ChipEntry(label="🎲 Surprise", text="", surprise=True),
        ChipEntry(label="moody", text="moody"),
        ChipEntry(label="lush", text="lush"),
    ])
    monkeypatch.setattr(cpp, "load_chips_for_kind", lambda k: [cat])
    monkeypatch.setattr(cpp, "_pick_surprise", lambda pool: "lush")
    p = cpp.ModifierPills("image")
    # find + click the surprise add-chip
    btn = p._add_buttons[id(cat.chips[0])]
    btn.emit("clicked")
    assert "lush" in p.applied_text()
    assert btn.get_visible() is True     # surprise chip is re-tappable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_modifier_pills.py -q`
Expected: FAIL (`artgen` kwarg / `load_chips_for_artgen_kind` / `_pick_surprise` don't exist; surprise chip not handled).

- [ ] **Step 3: Write minimal implementation**

In `app/create_param_panels.py`:
- Add the seam + picker beside `load_chips_for_kind`:

```python
import random  # (if not already imported)

def load_chips_for_artgen_kind(artgen_type: str) -> "list":
    """Seam over `chip_config.load_chips_for_artgen` — [] on any error, never raises."""
    try:
        from chip_config import load_chips_for_artgen
        return load_chips_for_artgen(artgen_type)
    except Exception:
        return []


def _pick_surprise(pool: "list[str]") -> "str | None":
    """Random pick from a Surprise pool (its own seam so tests are deterministic)."""
    return random.choice(pool) if pool else None
```

- `ModifierPills.__init__`: add `*, artgen: bool = False`; choose the loader:

```python
    def __init__(self, kind: str, *, artgen: bool = False) -> None:
        ...
        self._kind = kind
        ...
        try:
            self._categories = (
                load_chips_for_artgen_kind(kind) if artgen else load_chips_for_kind(kind)
            ) or []
        except Exception:
            self._categories = []
```

- In `_build_category_box`, when building each chip button, special-case a surprise entry:

```python
        from chip_config import surprise_pool
        pool = surprise_pool(category)
        for entry in category.chips:
            btn = Gtk.Button(label=f"+ {entry.label}")
            btn.add_css_class("create-addchip")
            if entry.tip:
                btn.set_tooltip_text(entry.tip)
            if getattr(entry, "surprise", False):
                btn.add_css_class("create-addchip-surprise")
                btn.connect("clicked", lambda _b, pl=list(pool): self._apply_surprise(pl))
            else:
                btn.connect("clicked", lambda _b, e=entry: self._apply_entry(e))
            self._add_buttons[id(entry)] = btn
            flow.append(btn)
```

- Add `_apply_surprise`:

```python
    def _apply_surprise(self, pool: "list[str]") -> None:
        """Apply a random pill from *pool* (a Surprise tap). Builds a fresh
        ChipEntry so it reads as a normal removable pill; the Surprise add-chip
        is NOT hidden, so it can be tapped again for another."""
        text = _pick_surprise(pool)
        if not text:
            return
        from chip_config import ChipEntry
        self._applied.append(ChipEntry(label=f"🎲 {text}", text=text))
        self._render_applied()
```

(De-dup note: `_apply_entry` hides its add-chip; `_apply_surprise` deliberately does not, so Surprise stays tappable.)

- [ ] **Step 4: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_modifier_pills.py -q`
Expected: PASS (incl. the existing pills tests).

- [ ] **Step 5: Commit**

```bash
git add app/create_param_panels.py tests/test_modifier_pills.py
git commit -m "feat(pills): ModifierPills artgen bank loader + Surprise chip"
```

---

### Task 4: `RoleZonePanel` keys artgen mediums by type

**Files:**
- Modify: `app/create_param_panels.py` (`RoleZonePanel`, the `ModifierPills(medium.kind)` site ~2771)
- Test: `tests/test_role_zone_panel.py` (extend)

**Interfaces — Consumes:** `ModifierPills(kind, *, artgen=True)` (Task 3). **Produces:** an artgen medium's Direction pills come from its type; native mediums unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_role_zone_panel.py  (add; keep the file's GTK guard + imports)
import create_param_panels as cpp
from types import SimpleNamespace


def test_artgen_medium_pills_keyed_by_type(monkeypatch):
    seen = {}
    monkeypatch.setattr(cpp, "load_chips_for_artgen_kind",
                        lambda t: seen.setdefault("artgen", t) or [])
    monkeypatch.setattr(cpp, "load_chips_for_kind",
                        lambda k: seen.setdefault("native", k) or [])
    medium = SimpleNamespace(id="palette", kind="image", source="artgen",
                             label="Palette", icon="🎨", uses_llm=True)
    panel = _make_min_panel()          # the file's existing helper for a CreateParamPanel
    cpp.RoleZonePanel(panel, medium)
    assert seen.get("artgen") == "palette"   # artgen loader called with the TYPE
    assert "native" not in seen              # native loader NOT used for an artgen medium


def test_native_medium_pills_keyed_by_kind(monkeypatch):
    seen = {}
    monkeypatch.setattr(cpp, "load_chips_for_kind", lambda k: seen.setdefault("native", k) or [])
    monkeypatch.setattr(cpp, "load_chips_for_artgen_kind", lambda t: seen.setdefault("artgen", t) or [])
    medium = SimpleNamespace(id="image", kind="image", source="native",
                             label="Image", icon="🖼", uses_llm=False)
    panel = _make_min_panel()
    cpp.RoleZonePanel(panel, medium)
    assert seen.get("native") == "image"
    assert "artgen" not in seen
```

(Use the test file's existing way of constructing a minimal `CreateParamPanel` for `_make_min_panel` — mirror an existing `RoleZonePanel` test in the file; if none, build the simplest real `ArtgenParamPanel`/`CreateParamPanel` the file already uses.)

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_role_zone_panel.py -q -k "keyed_by"`
Expected: FAIL (RoleZonePanel always calls the native loader via `ModifierPills(medium.kind)`).

- [ ] **Step 3: Write minimal implementation**

In `RoleZonePanel.__init__` at the Direction-zone construction (`app/create_param_panels.py:2771`):

```python
        if getattr(medium, "source", "") == "artgen":
            self._modifier_pills = ModifierPills(medium.id, artgen=True)
        else:
            self._modifier_pills = ModifierPills(medium.kind)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_role_zone_panel.py -q`
Expected: PASS (incl. the file's existing collect-equality tests — the bank change is display-only).

- [ ] **Step 5: Commit**

```bash
git add app/create_param_panels.py tests/test_role_zone_panel.py
git commit -m "feat(pills): RoleZonePanel keys artgen mediums' Direction pills by type"
```

---

### Task 5: Finalize — full suite, version, docs

**Files:** `VERSION`, `debian/changelog`, `CLAUDE.md`

- [ ] **Step 1: Full suite**

Run:
```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q \
  --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module \
  --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes \
  --deselect tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen
```
Expected: green.

- [ ] **Step 2: Version + changelog**

- `VERSION`: `0.73.0` → `0.74.0`.
- Prepend a `debian/changelog` `0.74.0` stanza: per-artgen-type Direction pills — each artgen type now shows its own curated chip banks (palette/verse/ansi/landscape/codeart/freeform) plus a shared "Feeling" mood bank and a 🎲 Surprise chip, instead of photo/video pills (or none); native image/video mediums unchanged; `collect()` untouched.

- [ ] **Step 3: CLAUDE.md**

Add a "Per-artgen-type modifier pills (v0.74.0)" note: `chip_config.load_chips_for_artgen`/`surprise_pool` + `ChipEntry.surprise`; the `for: [<type>]`/`for: [artgen]` YAML convention (and that no-`for:` categories still default to photo tabs so nothing leaks); `ModifierPills(kind, artgen=True)` + Surprise chip via `_pick_surprise`; `RoleZonePanel` keys artgen mediums by `medium.id`.

- [ ] **Step 4: Commit**

```bash
git add VERSION debian/changelog CLAUDE.md
git commit -m "chore(pills): VERSION 0.74.0 + changelog + CLAUDE.md for per-artgen pills"
```

---

## Self-Review

**Spec coverage:** per-type banks (Task 2) + shared mood bank (Tasks 1-2, `load_chips_for_artgen`) + Surprise chip (Tasks 1/3) + keying artgen mediums by type (Task 4) + native untouched (Tasks 2/4 tests) + collect invariant (Task 4). ✓

**Placeholder scan:** Task 2 leaves the full verse/ansi/landscape/codeart/freeform chip copy to the implementer (curation), with the shape shown and the load/Surprise/exclusion assertions pinned — acceptable (it's content authoring against a fixed schema, not a logic gap). All logic steps have concrete code.

**Type consistency:** `load_chips_for_artgen(type)` (Task 1) → `load_chips_for_artgen_kind` seam (Task 3) → `ModifierPills(kind, artgen=True)` (Task 3) → `RoleZonePanel` (Task 4). `ChipEntry.surprise` + `surprise_pool` (Task 1) consumed by `ModifierPills._build_category_box`/`_apply_surprise` (Task 3). `_pick_surprise` seam consistent between impl and tests.

**Ordering & risk:** 1 (config) → 2 (data) → 3 (widget) → 4 (wiring) → 5 (finalize). Lowest-risk feature this session; the only integration point is Task 4's one-line branch, guarded by loader-call tests + the existing collect-equality suite.
