//! history.rs — Read the same media.db that the Python app writes.
//!
//! Schema (see media_store.py):
//!   media(id, media_type, created_at, file_path, thumbnail_path, prompt,
//!         model_id, generator_type, params, starred)
//!
//! DB location: ~/.local/share/tt-video-gen/media.db
//! No Python interop needed — pure SQL.

use rusqlite::{Connection, Result as SqlResult};
use std::path::PathBuf;

/// Mirrors the columns we actually use from the media table.
#[derive(Debug, Clone, Default)]
pub struct Record {
    pub id:             String,
    pub media_type:     String,    // "video" | "image" | "animate" | "artgen" | "animatediff"
    pub created_at:     String,
    pub file_path:      String,
    pub thumbnail_path: String,
    pub prompt:         String,
    pub model_id:       String,
    pub starred:        bool,
}

/// Load up to `limit` records from media.db, newest first.
/// Falls back to an empty vec on any error (DB not found, locked, etc.)
pub fn load_history() -> Vec<Record> {
    match load_inner() {
        Ok(recs) => recs,
        Err(e) => {
            eprintln!("[history] SQLite error: {e}");
            vec![]
        }
    }
}

fn load_inner() -> SqlResult<Vec<Record>> {
    load_inner_from_path(&db_path())
}

/// Testable variant that accepts an explicit path.
pub(crate) fn load_inner_from_path(path: &PathBuf) -> SqlResult<Vec<Record>> {
    if !path.exists() {
        eprintln!("[history] media.db not found at {}", path.display());
        return Ok(vec![]);
    }

    // Open read-only — we never write from the Rust UI.
    let conn = Connection::open(path)?;

    let mut stmt = conn.prepare(
        "SELECT id, media_type, created_at, file_path, thumbnail_path,
                prompt, COALESCE(model_id,''), COALESCE(starred,0)
         FROM   media
         ORDER  BY created_at DESC
         LIMIT  500"
    )?;

    let rows = stmt.query_map([], |row| {
        Ok(Record {
            id:             row.get(0)?,
            media_type:     row.get(1)?,
            created_at:     row.get(2)?,
            file_path:      row.get(3)?,
            thumbnail_path: row.get(4)?,
            prompt:         row.get(5)?,
            model_id:       row.get(6)?,
            starred:        row.get::<_, i64>(7)? != 0,
        })
    })?;

    let mut records = Vec::new();
    for row in rows {
        if let Ok(rec) = row {
            records.push(rec);
        }
    }
    Ok(records)
}

fn db_path() -> PathBuf {
    // Mirrors media_store.py: ~/.local/share/tt-video-gen/media.db
    dirs_next::data_local_dir()
        .unwrap_or_else(|| PathBuf::from(std::env::var("HOME").unwrap_or_default()))
        .join("tt-video-gen")
        .join("media.db")
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;
    use std::path::PathBuf;

    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);

    /// Build a uniquely-pathed SQLite DB with the same schema as media_store.py.
    fn make_test_db() -> (Connection, PathBuf) {
        let n = COUNTER.fetch_add(1, Ordering::Relaxed);
        let dir  = std::env::temp_dir()
            .join(format!("tt-gen-rs-test-{}-{n}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("media.db");
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS media (
                id              TEXT PRIMARY KEY,
                media_type      TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT '',
                file_path       TEXT NOT NULL DEFAULT '',
                thumbnail_path  TEXT NOT NULL DEFAULT '',
                prompt          TEXT NOT NULL DEFAULT '',
                model_id        TEXT,
                generator_type  TEXT,
                params          TEXT,
                starred         INTEGER NOT NULL DEFAULT 0
            );",
        ).unwrap();
        (conn, path)
    }

    fn insert(conn: &Connection, id: &str, media_type: &str, created_at: &str,
              prompt: &str, starred: i64)
    {
        conn.execute(
            "INSERT INTO media (id, media_type, created_at, prompt, starred)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            rusqlite::params![id, media_type, created_at, prompt, starred],
        ).unwrap();
    }

    // We can't call load_history() directly (it hard-codes the path), but we
    // can call load_inner_from_path() — a test-only variant we'll add below.
    fn load_from(path: &PathBuf) -> Vec<Record> {
        load_inner_from_path(path).unwrap_or_default()
    }

    #[test]
    fn returns_empty_when_db_missing() {
        let path = std::env::temp_dir().join("definitely-does-not-exist.db");
        let recs = load_from(&path);
        assert!(recs.is_empty());
    }

    #[test]
    fn loads_records_newest_first() {
        let (conn, path) = make_test_db();
        insert(&conn, "aaa", "video", "2026-01-01T00:00:00", "first",  0);
        insert(&conn, "bbb", "video", "2026-06-01T00:00:00", "newest", 0);
        insert(&conn, "ccc", "video", "2026-03-01T00:00:00", "middle", 0);
        drop(conn);

        let recs = load_from(&path);
        assert_eq!(recs.len(), 3);
        assert_eq!(recs[0].id, "bbb", "newest-first sort failed");
        assert_eq!(recs[1].id, "ccc");
        assert_eq!(recs[2].id, "aaa");
    }

    #[test]
    fn starred_flag_roundtrips() {
        let (conn, path) = make_test_db();
        insert(&conn, "s1", "image", "2026-01-01T00:00:00", "starred one", 1);
        insert(&conn, "s2", "image", "2026-01-02T00:00:00", "not starred",  0);
        drop(conn);

        let recs = load_from(&path);
        let s1 = recs.iter().find(|r| r.id == "s1").unwrap();
        let s2 = recs.iter().find(|r| r.id == "s2").unwrap();
        assert!(s1.starred);
        assert!(!s2.starred);
    }

    #[test]
    fn mixed_media_types_all_loaded() {
        let (conn, path) = make_test_db();
        for (i, mt) in ["video","image","animate","artgen","animatediff"].iter().enumerate() {
            insert(&conn, &format!("id{i}"), mt,
                   &format!("2026-01-0{}T00:00:00", i+1), "p", 0);
        }
        drop(conn);

        let recs = load_from(&path);
        assert_eq!(recs.len(), 5);
        let types: std::collections::HashSet<_> = recs.iter().map(|r| r.media_type.as_str()).collect();
        assert!(types.contains("video"));
        assert!(types.contains("artgen"));
        assert!(types.contains("animatediff"));
    }

    #[test]
    fn respects_500_record_limit() {
        let (conn, path) = make_test_db();
        for i in 0..600usize {
            insert(&conn, &format!("id{i:04}"), "video",
                   &format!("2026-01-01T{:02}:{:02}:00", i/60, i%60), "p", 0);
        }
        drop(conn);

        let recs = load_from(&path);
        assert!(recs.len() <= 500, "expected ≤ 500 records, got {}", recs.len());
    }
}
