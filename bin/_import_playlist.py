#!/usr/bin/env python3
"""Import world's fair workflow artifacts into a tt-local-generator playlist."""
import sys, json, shutil, uuid
from pathlib import Path
from datetime import datetime, timezone

pname, img1, img2, video, depth, poem, rj, fair_key = sys.argv[1:]

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path.home() / ".local" / "share" / "tt-local-generator"
IMAGES_DIR = APP_DIR / "images"
VIDEOS_DIR = APP_DIR / "videos"
THUMBS_DIR = APP_DIR / "thumbnails"
for d in (IMAGES_DIR, VIDEOS_DIR, THUMBS_DIR):
    d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO_ROOT / "app"))
try:
    from media_store import media_store as _ms, MediaRecord
    from playlist_store import PlaylistStore

    _ps = PlaylistStore()
    pl = _ps.get_or_create(pname)
    record_ids = []

    def _import(src, media_type, prompt_text):
        src = Path(src) if src else None
        if not src or not src.exists():
            return None
        ts = datetime.now(timezone.utc)
        rid = str(uuid.uuid4())
        ts_str = ts.strftime("%Y%m%d_%H%M%S")
        dest_dir = VIDEOS_DIR if media_type == "video" else IMAGES_DIR
        dest = dest_dir / f"{ts_str}_{rid[:8]}{src.suffix}"
        shutil.copy2(src, dest)
        thumb = THUMBS_DIR / f"{ts_str}_{rid[:8]}.jpg"
        try:
            import subprocess
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(dest),
                 "-vf", "scale=200:112:force_original_aspect_ratio=decrease,"
                        "pad=200:112:(ow-iw)/2:(oh-ih)/2",
                 "-frames:v", "1", "-update", "1", "-q:v", "3", str(thumb)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
            )
        except Exception:
            try:
                shutil.copy2(dest, thumb)
            except Exception:
                pass
        params = {"workflow": f"worlds-fair-{fair_key}"}
        params["video_path" if media_type == "video" else "image_path"] = str(dest)
        rec = MediaRecord(
            id=rid, file_path=str(dest), thumbnail_path=str(thumb),
            prompt=prompt_text, media_type=media_type,
            created_at=ts.isoformat(), model_id="workflow",
            generator_type=None, starred=0, params=json.dumps(params),
        )
        _ms.add(rec)
        return rid

    for path, mtype, lbl in [
        (img1,  "image", f"{pname}: seed image"),
        (depth, "image", f"{pname}: depth map"),
        (video, "video", f"{pname}: SkyReels I2V"),
        (img2,  "image", f"{pname}: poem image"),
    ]:
        rid = _import(path, mtype, lbl)
        if rid:
            record_ids.append(rid)

    if record_ids:
        _ps.add_records(pl.id, record_ids)
    print(f"PLAYLIST:{len(record_ids)}:{pname}")
    print(f"✅ {pname}: {len(record_ids)} artifacts")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"⚠️  playlist import failed: {e}")
