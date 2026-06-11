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
    let path = db_path();
    if !path.exists() {
        eprintln!("[history] media.db not found at {}", path.display());
        return Ok(vec![]);
    }

    // Open read-only — we never write from the Rust UI.
    let conn = Connection::open(&path)?;

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
