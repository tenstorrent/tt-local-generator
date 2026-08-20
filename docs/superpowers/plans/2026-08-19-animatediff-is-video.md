# AnimateDiff is Video — unify the media taxonomy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all AnimateDiff (`.gif`) and Wan2.2-Animate (`.mp4`) media `media_type="video"` — past, present, future — keeping each record's identity (`model_id` + a new `generator_type` stamp) and stats, with gif rendering driven by file extension instead of a bespoke media_type.

**Architecture:** A one-time idempotent DB migration folds existing records to `video`; the record factories write `video` going forward; gif-vs-mp4 rendering (main_window + the TT-TV attractor) switches from `media_type=="animatediff"` to the `.gif` extension. No files or params are modified.

**Tech Stack:** Python 3, sqlite3, GTK4/PyGObject. System `/usr/bin/python3`. Tests via `xvfb-run --auto-servernum /usr/bin/python3 -m pytest` (media_store/history_store tests are GTK-free and can run without xvfb).

## Global Constraints

- **Idempotent, data-only migration.** Files are never modified (gifs stay `.gif`, the animate mp4 stays `.mp4`); thumbnails are not regenerated; **params are left as-is** (the 73 older artgen-path records keep their leaner param set — no back-fill). The migration runs on every `media_store` load and is safe to re-run.
- **Identity preserved.** Each record keeps `model_id` (`animatediff-blackhole` / `wan2.2-animate-14b`) and is stamped `generator_type="animatediff"` / `"animate"` for provenance/filtering.
- **Gif detection is by file extension**, not media_type. After this change nothing keys rendering off `media_type=="animatediff"`.
- **The functional fix is Tasks 1–3.** Task 1 (past data) + Task 2 (future data) + Task 3 (attractor gif-by-extension, which currently has NO `.gif` fallback and would break migrated gifs in TT-TV). Task 4 is the behavior-preserving main_window cleanup + finalize.
- `collect()` / generation logic untouched except the `media_type`/`generator_type` **values** written for AnimateDiff/Animate records.
- Version discipline: minor `VERSION` bump (user-visible taxonomy change). CLAUDE.md updated.
- Spec: `docs/superpowers/specs/2026-08-19-animatediff-is-video-design.md`.

## Confirmed facts (from investigation — don't re-derive)

- `media_store.__init__` runs migrations after `executescript(_SCHEMA)` (calls `_migrate_from_json()`); no `PRAGMA user_version` yet. `self._conn` is the sqlite connection; `self._lock` serialises writes.
- `history_store.GenerationRecord` has NO `generator_type` field; `HistoryStore.append()` hardcodes `generator_type=None` when writing the `MediaRecord`. That's why the 55 native AnimateDiff records have `generator_type=None`.
- `GenerationRecord.new_animate` sets `media_type="animate"` (`history_store.py:150`); `new_animatediff` sets `media_type="animatediff"` (`history_store.py:183`).
- The artgen generation path is **dead for AnimateDiff**: `create_mediums.discover_mediums` does `if key == "animatediff": continue` (folded into Video), so `_create_generate_artgen` never fires for animatediff. Only `new_animatediff`/`new_animate` create these records now.
- main_window `is_gif` sites already fall back to `.gif` (`media_type=="animatediff" or video_path.endswith(".gif")`) at ~2272, 2386, 2881, 3226, 3612 — behavior-preserving to simplify.
- The **attractor** `_load_slot` (`attractor.py:1599`) branches `media_type=="animatediff"` → `_load_animated_gif` (1686/1699) with **no `.gif` fallback**; a `media_type="video"` `.gif` would fall through to the `Gtk.Video` path. `video_records` at `attractor.py:808` is `media_type != "artgen"` (so video-typed records are included). Also `media_type=="animatediff"` at 1782 (gif loop handling). `_model_source=="animatediff"` at 1884 is the CREATE model source (NOT a record media_type) — leave it.

---

### Task 1: Idempotent DB migration (past data → video)

**Files:**
- Modify: `app/media_store.py`
- Test: `tests/test_media_store.py` (or the existing media_store test file — grep first)

**Interfaces:**
- Produces: `MediaStore._migrate_media_types()` — called once from `__init__` after `_migrate_from_json()`; gated by `PRAGMA user_version` (target version `1`); folds AnimateDiff + Wan2.2-Animate rows into `media_type="video"` with a `generator_type` stamp.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_store.py  (imports: from media_store import MediaStore, MediaRecord)
def test_animatediff_and_animate_fold_into_video(tmp_path):
    db = tmp_path / "m.db"
    ms = MediaStore(db_path=db)
    # seed rows across every convention, bypassing add() so we control media_type
    import sqlite3, json
    con = sqlite3.connect(db)
    def ins(id, mt, gt):
        con.execute("INSERT INTO media (id,media_type,created_at,file_path,thumbnail_path,"
                    "prompt,model_id,generator_type,params,starred) VALUES (?,?,?,?,?,?,?,?,?,0)",
                    (id, mt, "2026-01-01T00:00:00", f"/x/{id}", "", "", "m", gt, "{}"))
    ins("ad_native", "animatediff", None)
    ins("ad_artgen", "artgen", "animatediff")
    ins("anim",      "animate", None)
    ins("vid",       "video", None)
    ins("img",       "image", None)
    ins("verse",     "artgen", "verse")
    con.commit(); con.close()
    # re-open: migration runs in __init__
    ms2 = MediaStore(db_path=db)
    def row(id):
        return ms2._conn.execute("SELECT media_type, generator_type FROM media WHERE id=?", (id,)).fetchone()
    assert row("ad_native") == ("video", "animatediff")
    assert row("ad_artgen") == ("video", "animatediff")
    assert row("anim")      == ("video", "animate")
    assert row("vid")       == ("video", None)      # untouched
    assert row("img")       == ("image", None)      # untouched
    assert row("verse")     == ("artgen", "verse")  # untouched
    # idempotent: a third open changes nothing
    ms3 = MediaStore(db_path=db)
    assert ms3._conn.execute("PRAGMA user_version").fetchone()[0] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_media_store.py::test_animatediff_and_animate_fold_into_video -v`
Expected: FAIL (rows still `animatediff`/`artgen`/`animate`).

- [ ] **Step 3: Implement the migration**

In `app/media_store.py`, add a module constant near the top:

```python
_MEDIA_TYPE_MIGRATION_VERSION = 1  # AnimateDiff/Animate -> video
```

In `__init__`, add the call right after `self._migrate_from_json()`:

```python
        self._migrate_media_types()
```

Add the method:

```python
    def _migrate_media_types(self) -> None:
        """Fold AnimateDiff (.gif) and Wan2.2-Animate (.mp4) records into
        media_type='video', stamping generator_type for provenance. Gated by
        PRAGMA user_version so it runs once per DB; the UPDATEs are idempotent
        regardless. Files/params are untouched. See
        docs/superpowers/specs/2026-08-19-animatediff-is-video-design.md."""
        with self._lock:
            ver = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if ver >= _MEDIA_TYPE_MIGRATION_VERSION:
                return
            self._conn.execute(
                "UPDATE media SET media_type='video', generator_type='animatediff' "
                "WHERE media_type='animatediff' OR generator_type='animatediff'")
            self._conn.execute(
                "UPDATE media SET media_type='video', generator_type='animate' "
                "WHERE media_type='animate' OR generator_type='animate'")
            self._conn.execute(f"PRAGMA user_version={_MEDIA_TYPE_MIGRATION_VERSION}")
            self._conn.commit()
```

(`PRAGMA user_version` can't be parameterised; the value is an int constant, so the f-string is safe.)

- [ ] **Step 4: Run to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_media_store.py -v`
Expected: PASS (new test + existing media_store tests).

- [ ] **Step 5: Commit**

```bash
git add app/media_store.py tests/test_media_store.py
git commit -m "feat(media): fold AnimateDiff/Animate records into media_type=video (idempotent migration)"
```

---

### Task 2: Record factories write video (future data)

**Files:**
- Modify: `app/history_store.py`
- Test: `tests/test_history_store.py` (grep for the existing history_store test file)

**Interfaces:**
- Consumes: nothing new.
- Produces: `GenerationRecord.generator_type: "str | None" = None` (new dataclass field); `new_animatediff(...)` → `media_type="video"`, `generator_type="animatediff"`; `new_animate(...)` → `media_type="video"`, `generator_type="animate"`; `HistoryStore.append()` persists `record.generator_type` (instead of hardcoded `None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_history_store.py
def test_new_animatediff_and_animate_are_video_with_generator_type():
    from history_store import GenerationRecord
    ad = GenerationRecord.new_animatediff(job_id="j1", prompt="p", negative_prompt="",
                                          num_inference_steps=6, seed=42,
                                          video_path="/x/a.gif", thumbnail_path="/x/a.png")
    assert ad.media_type == "video"
    assert ad.generator_type == "animatediff"
    assert ad.video_path.endswith(".gif")
    an = GenerationRecord.new_animate(job_id="j2", prompt="p", negative_prompt="",
                                      num_inference_steps=20, seed=1)
    assert an.media_type == "video"
    assert an.generator_type == "animate"
    assert an.video_path.endswith(".mp4")

def test_append_persists_generator_type(tmp_path, monkeypatch):
    # point media_store at a temp db, append an animatediff record, read it back
    import media_store
    ms = media_store.MediaStore(db_path=tmp_path / "m.db")
    monkeypatch.setattr(media_store, "media_store", ms)
    from history_store import HistoryStore, GenerationRecord
    hs = HistoryStore()   # or however the file constructs it; match existing tests
    rec = GenerationRecord.new_animatediff(job_id="j3", prompt="p", negative_prompt="",
                                           num_inference_steps=6, seed=42,
                                           video_path="/x/b.gif", thumbnail_path="/x/b.png")
    hs.append(rec)
    got = ms.get("j3")
    assert got.media_type == "video"
    assert got.generator_type == "animatediff"
```

(Match the existing history_store test file's construction pattern — grep it; if `HistoryStore()` needs args or the media_store singleton is patched differently, mirror what the current tests do.)

- [ ] **Step 2: Run to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_history_store.py -q -k "generator_type"`
Expected: FAIL (`media_type` still `animatediff`/`animate`; no `generator_type` field).

- [ ] **Step 3: Implement**

3a. Add the field to `GenerationRecord` (after `starred`, so it's keyword-defaulted):

```python
    generator_type: "str | None" = None   # provenance for records folded into video (e.g. "animatediff", "animate")
```

3b. `new_animatediff(...)`: change `media_type="animatediff"` → `media_type="video"` and add `generator_type="animatediff"`. Update its docstring (it currently says "media_type='animatediff'") to "a Video record produced by AnimateDiff (.gif)".

3c. `new_animate(...)`: change `media_type="animate"` → `media_type="video"` and add `generator_type="animate"`. Update its docstring.

3d. `HistoryStore.append()`: change `generator_type=None` → `generator_type=record.generator_type`.

- [ ] **Step 4: Run to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_history_store.py -q`
Expected: PASS. Fix any existing test that asserted `new_animatediff().media_type == "animatediff"` / `new_animate().media_type == "animate"` — update the expectation to `"video"` (+ the generator_type), preserving the test's intent.

- [ ] **Step 5: Commit**

```bash
git add app/history_store.py tests/test_history_store.py
git commit -m "feat(history): new_animatediff/new_animate write media_type=video + generator_type provenance"
```

---

### Task 3: Attractor plays video-typed gifs by extension (required functional fix)

**Files:**
- Modify: `app/attractor.py`
- Test: `tests/test_attractor.py`

**Interfaces:**
- Consumes: records now `media_type="video"` with a `.gif` `video_path`.
- Produces: `_load_slot` (and the loop-handling at ~1782) route to `_load_animated_gif` when the record's file ends in `.gif`, regardless of `media_type` — so migrated/new AnimateDiff gifs animate in TT-TV instead of hitting the fragile `Gtk.Video` path.

- [ ] **Step 1: Write the failing test**

Read `tests/test_attractor.py` for how it exercises `_load_slot`/slot rendering with a fake record. Add a test that a **`media_type="video"` record whose `video_path` ends in `.gif`** is rendered via the animated-gif path, not `Gtk.Video`. If `_load_slot` is hard to drive directly, add/extend a small helper test that asserts the gif-vs-video DECISION (a pure predicate — see Step 3) returns "gif" for a `.gif` video record and "video" for an `.mp4` one. Prefer testing a small extracted predicate over driving the whole slot.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement — decide gif by extension**

Read `_load_slot` (`attractor.py:1599`) and the `media_type=="animatediff"` branches (1686/1699 and the loop-handling ~1782). Restructure so the gif decision is extension-based:
- Extract a tiny pure predicate (module-level, testable), e.g.:
  ```python
  def _is_gif_record(record) -> bool:
      p = getattr(record, "video_path", "") or getattr(record, "file_path", "") or ""
      return p.lower().endswith(".gif")
  ```
- In `_load_slot`: for the video path (media_type `"video"`, i.e. the non-artgen/non-image branch), if `_is_gif_record(record)` → `_load_animated_gif(slot._text_box, path)`, else the existing `Gtk.Video` load. Keep the existing `media_type=="artgen"` branch (1630) for genuine artgen kinds (ansi/palette/svg/etc.) — those are unchanged; animatediff no longer reaches it (it's video now).
- Replace the `elif media_type == "animatediff":` branch (1686) and the `media_type == "animatediff"` loop check (~1782) with the `_is_gif_record(record)` predicate.
- Leave `_model_source == "animatediff"` (1884) as-is (it's the Create model source, not a record media_type).
- Keep `_load_animated_gif`'s docstring accurate (drop the "native `media_type == "animatediff"` records" phrasing → "any record whose file is a `.gif`").

- [ ] **Step 4: Run to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_attractor.py -q`
Expected: PASS (new gif-by-extension test + existing attractor tests; the `.gif` predicate must still classify the previously-`animatediff` fixtures as gif).

- [ ] **Step 5: Commit**

```bash
git add app/attractor.py tests/test_attractor.py
git commit -m "fix(attractor): play video-typed .gif records via the animated-gif path (extension, not media_type)"
```

---

### Task 4: main_window gif cleanup, filter tidy, and finalize

**Files:**
- Modify: `app/main_window.py`, `app/history_store.py`, `VERSION`, `debian/changelog`, `CLAUDE.md`
- Test: `tests/test_possibilities_wall.py` (Video-tile bonus) + the main_window gif/gallery widget tests

**Interfaces:** none new (behavior-preserving cleanup + finalize).

- [ ] **Step 1: Bonus regression test — a video-typed gif is Video-tile-eligible**

In `tests/test_possibilities_wall.py`, add a test: a starred `media_type="video"` record with a `.gif` file (via the `_FakeStore` starred query for `("video", None)`) resolves as the Video tile art (`_resolve_tile_art(video_medium)` returns `("thumb", <that thumb>)`). This pins the knock-on benefit (starred AnimateDiff gifs can be the Video "Start Something" tile).

- [ ] **Step 2: main_window `is_gif` cleanup (behavior-preserving)**

At each `is_gif` site (`app/main_window.py` ~2272, 2386, 2881, 3226, 3612), the check is `record.media_type == "animatediff" or record.video_path.endswith(".gif")`. Drop the now-dead `media_type == "animatediff" or` clause, leaving `record.video_path.endswith(".gif")`. This is behavior-preserving (no record has `media_type=="animatediff"` after Task 1). Update the nearby comments/docstrings that describe "`media_type == "animatediff"` records" to "records whose file is a `.gif`".

- [ ] **Step 3: Filter tidy**

- `app/main_window.py:7978`: `[r for r in records if r.media_type in ("video", "animatediff")]` → `("video",)`.
- `app/history_store.py:335`: `if r.media_type in ("video", "animate", "animatediff"):` → `("video",)`.
Both are behavior-preserving after the migration (animate/animatediff rows are now `"video"`).

- [ ] **Step 4: Run the touched tests, then the full suite**

```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest \
  tests/test_possibilities_wall.py tests/test_main_window*.py tests/test_media_store.py \
  tests/test_history_store.py tests/test_attractor.py -q
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q \
  --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module \
  --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes \
  --deselect tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen
```
Expected: green. Fix any test that asserted the old `media_type=="animatediff"` gallery/routing behavior (update to the video-typed expectation, preserving intent).

- [ ] **Step 5: Version, changelog, CLAUDE.md**

- Bump `VERSION` (minor, e.g. `0.86.1` → `0.87.0`).
- Prepend a `debian/changelog` stanza: AnimateDiff + Wan2.2-Animate media is now `media_type="video"` (past via an idempotent migration, future via the record factories), keeping `model_id` + a `generator_type` provenance stamp; gif rendering is now driven by the file extension everywhere (main_window + the TT-TV attractor), so a video-typed `.gif` animates correctly; params/files untouched; a starred AnimateDiff gif can now be the Video "Start Something" tile.
- Add a CLAUDE.md "AnimateDiff is Video" section: the migration (`media_store._migrate_media_types`, `PRAGMA user_version`), the factory change (`new_animatediff`/`new_animate` → video + `generator_type`, `append` persists it), the artgen path being dead for animatediff (`discover_mediums` skips it), gif-detection-by-extension (main_window `is_gif` + `attractor._is_gif_record`), and that identity/stats are preserved (the 73 older artgen-path records keep their leaner params).

- [ ] **Step 6: Commit**

```bash
git add app/main_window.py app/history_store.py VERSION debian/changelog CLAUDE.md tests/test_possibilities_wall.py
git commit -m "chore: retire the bespoke animatediff media_type + finalize AnimateDiff-is-Video (version, changelog, docs)"
```

- [ ] **Step 7: Manual check (user, real display — NOT automated)**

Open the app: existing AnimateDiff gifs now appear in the **Video** gallery (not Artgen), still labeled AnimateDiff, animating in the detail pane; generate a new AnimateDiff clip from the Video medium and confirm it lands in Video; play TT-TV and confirm a `.gif` clip animates (not a frozen frame / blank); check a starred AnimateDiff gif can surface on the Video "Start Something" tile.

---

## Ordering & risk

1 → 2 → 3 → 4. **Highest risk: Task 3** (the attractor — the one place with no `.gif` fallback; get the extension predicate right so gifs don't hit `Gtk.Video`). Task 1 is the load-bearing data fix (idempotent, reversible-by-re-running). Tasks 2/4 are contained. The migration only ever *widens* what counts as video, and gif detection only moves from media_type to extension — no record loses its files, params, `model_id`, or star.

## Self-Review

**Spec coverage:** migration §A → T1; future path §B → T2; rendering/routing cleanup §C (attractor is the required part) → T3 + T4; bonus §D → T4 Step 1; the `animate` fold-in (resolved yes) → T1 migration second UPDATE + T2 `new_animate` + T4 filters; params-left-as-is → Global Constraints (no param writes anywhere). ✓

**Placeholder scan:** the "grep the existing test file / mirror the construction pattern" notes are concrete (they name the file and what to match), not vague TBDs; migration + factory code is complete; the attractor step names the exact branches (1599/1686/1782) and the predicate to extract. ✓

**Type/name consistency:** `_migrate_media_types`/`_MEDIA_TYPE_MIGRATION_VERSION` (T1); `GenerationRecord.generator_type` + `new_animatediff`/`new_animate` + `append` (T2); `_is_gif_record` (T3); the `is_gif` sites + filters (T4) all reference the same media_type/generator_type values written in T1/T2. ✓
