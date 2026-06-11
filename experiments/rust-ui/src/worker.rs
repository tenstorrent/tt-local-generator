//! worker.rs — Generation worker
//!
//! Rust port of app/worker.py GenerationWorker.
//! Runs on a dedicated std::thread; communicates back to the GTK main thread
//! via three mpsc senders (progress, finished, error).
//!
//! Architecture:
//!   1. POST /v1/videos/generations or /v1/images/generations
//!   2. Poll GET /v1/videos/generations/{id} until completed/failed
//!   3. Download the file, write to disk
//!   4. INSERT record into media.db
//!   5. Send CompletedRecord over `on_finished` channel

use crate::history::{self, Record};
use rusqlite::Connection;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Sender;
use std::sync::Arc;
use std::time::{Duration, Instant};

// ── Public types ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum WorkerMsg {
    Progress(String),
    Finished(Record),
    Error(String),
}

#[derive(Debug, Clone, PartialEq)]
pub enum ModelSource {
    Video,    // Wan2.2 / Mochi on port 8000
    Image,    // FLUX on port 8000
    Animate,  // Wan2.2-Animate on port 8000
    SkyReels, // SkyReels on port 8000
}

impl ModelSource {
    pub fn as_media_type(&self) -> &'static str {
        match self {
            ModelSource::Video    => "video",
            ModelSource::Image    => "image",
            ModelSource::Animate  => "animate",
            ModelSource::SkyReels => "video",
        }
    }
}

#[derive(Debug, Clone)]
pub struct GenerationRequest {
    pub prompt:          String,
    pub negative_prompt: String,
    pub steps:           u32,
    pub seed:            i64,
    pub model_source:    ModelSource,
    pub server_url:      String,
}

impl Default for GenerationRequest {
    fn default() -> Self {
        Self {
            prompt:          String::new(),
            negative_prompt: String::new(),
            steps:           20,
            seed:            -1,
            model_source:    ModelSource::Video,
            server_url:      "http://localhost:8000".into(),
        }
    }
}

// ── Worker ────────────────────────────────────────────────────────────────────

pub struct GenerationWorker {
    request:   GenerationRequest,
    tx:        Sender<WorkerMsg>,
    cancelled: Arc<AtomicBool>,
}

impl GenerationWorker {
    pub fn new(request: GenerationRequest, tx: Sender<WorkerMsg>) -> Self {
        Self { request, tx, cancelled: Arc::new(AtomicBool::new(false)) }
    }

    /// Cancel handle — call from any thread.
    pub fn cancel_handle(&self) -> Arc<AtomicBool> {
        self.cancelled.clone()
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    fn progress(&self, msg: &str) {
        let _ = self.tx.send(WorkerMsg::Progress(msg.to_owned()));
    }

    fn error(&self, msg: &str) {
        let _ = self.tx.send(WorkerMsg::Error(msg.to_owned()));
    }

    /// Spawn on a background thread and return immediately.
    pub fn spawn(self) {
        std::thread::Builder::new()
            .name("generation-worker".into())
            .spawn(move || self.run())
            .expect("generation worker thread");
    }

    fn run(self) {
        // Build the HTTP client with a generous timeout for slow TT hardware.
        let client = match reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(600))
            .build()
        {
            Ok(c)  => c,
            Err(e) => { self.error(&format!("HTTP client error: {e}")); return; }
        };

        self.progress("Submitting job…");

        match self.request.model_source {
            ModelSource::Image => self.run_image(&client),
            _                  => self.run_video(&client),
        }
    }

    fn run_video(&self, client: &reqwest::blocking::Client) {
        let req = &self.request;

        // 1. Submit
        let body = serde_json::json!({
            "prompt":               req.prompt,
            "negative_prompt":      req.negative_prompt,
            "num_inference_steps":  req.steps,
            "seed":                 req.seed,
        });

        let resp = match client
            .post(format!("{}/v1/videos/generations", req.server_url))
            .json(&body)
            .send()
        {
            Ok(r)  => r,
            Err(e) => { self.error(&format!("Submit failed: {e}")); return; }
        };

        if resp.status().as_u16() != 202 {
            self.error(&format!("Submit rejected: HTTP {}", resp.status()));
            return;
        }

        let submit_resp: serde_json::Value = match resp.json() {
            Ok(v)  => v,
            Err(e) => { self.error(&format!("Submit response parse error: {e}")); return; }
        };

        let job_id = match submit_resp["id"].as_str() {
            Some(id) => id.to_string(),
            None     => { self.error("Submit response missing 'id'"); return; }
        };

        self.progress(&format!("Job queued ({:.8}…)", job_id));

        // 2. Poll
        let t0 = Instant::now();
        let poll_url = format!("{}/v1/videos/generations/{}", req.server_url, job_id);

        loop {
            if self.cancelled.load(Ordering::Relaxed) {
                self.error("Cancelled by user");
                return;
            }
            std::thread::sleep(Duration::from_secs(3));

            let resp = match client.get(&poll_url).send() {
                Ok(r)  => r,
                Err(e) => { self.error(&format!("Poll error: {e}")); return; }
            };

            let val: serde_json::Value = match resp.json() {
                Ok(v)  => v,
                Err(e) => { self.error(&format!("Poll parse error: {e}")); return; }
            };

            let status = val["status"].as_str().unwrap_or("unknown").to_string();
            let elapsed = t0.elapsed().as_secs();

            match status.as_str() {
                "completed" | "succeeded" => {
                    self.progress(&format!("Generating… {elapsed}s — done, downloading…"));
                    self.download_and_finish(client, &job_id, elapsed);
                    return;
                }
                "failed" | "error" => {
                    let detail = val["error"].as_str().unwrap_or("no details");
                    self.error(&format!("Job {status}: {detail}"));
                    return;
                }
                _ => {
                    self.progress(&format!("Generating… {elapsed}s ({status})"));
                }
            }
        }
    }

    fn download_and_finish(
        &self,
        client: &reqwest::blocking::Client,
        job_id: &str,
        elapsed_s: u64,
    ) {
        let req = &self.request;

        let dl_url = format!("{}/v1/videos/generations/{}/download", req.server_url, job_id);
        let resp   = match client.get(&dl_url).send() {
            Ok(r)  => r,
            Err(e) => { self.error(&format!("Download failed: {e}")); return; }
        };

        let bytes = match resp.bytes() {
            Ok(b)  => b,
            Err(e) => { self.error(&format!("Download read error: {e}")); return; }
        };

        let record = match write_record(&bytes, req, elapsed_s, "video") {
            Ok(r)  => r,
            Err(e) => { self.error(&format!("Save failed: {e}")); return; }
        };

        let _ = self.tx.send(WorkerMsg::Finished(record));
    }

    fn run_image(&self, client: &reqwest::blocking::Client) {
        let req = &self.request;

        let body = serde_json::json!({
            "prompt":              req.prompt,
            "negative_prompt":     req.negative_prompt,
            "num_inference_steps": req.steps,
            "seed":                req.seed,
        });

        let resp = match client
            .post(format!("{}/v1/images/generations", req.server_url))
            .json(&body)
            .send()
        {
            Ok(r)  => r,
            Err(e) => { self.error(&format!("Image submit failed: {e}")); return; }
        };

        let val: serde_json::Value = match resp.json() {
            Ok(v)  => v,
            Err(e) => { self.error(&format!("Image response parse error: {e}")); return; }
        };

        let b64 = match val["images"].as_array().and_then(|a| a.first()).and_then(|v| v.as_str()) {
            Some(s) => s.to_string(),
            None    => { self.error("Image response missing 'images[0]'"); return; }
        };

        let bytes = match base64_decode(&b64) {
            Ok(b)  => b,
            Err(e) => { self.error(&format!("Base64 decode error: {e}")); return; }
        };

        let record = match write_record(&bytes, req, 0, "image") {
            Ok(r)  => r,
            Err(e) => { self.error(&format!("Save failed: {e}")); return; }
        };

        let _ = self.tx.send(WorkerMsg::Finished(record));
    }
}

// ── File + DB write ───────────────────────────────────────────────────────────

fn write_record(
    bytes:     &[u8],
    req:       &GenerationRequest,
    elapsed_s: u64,
    ext:       &str,
) -> Result<Record, String> {
    let store_dir = storage_dir();
    let media_dir = if ext == "image" {
        store_dir.join("images")
    } else {
        store_dir.join("videos")
    };
    std::fs::create_dir_all(&media_dir).map_err(|e| e.to_string())?;

    // Timestamp-based filename matching Python's pattern: 20260101_120000_{uuid8}.mp4
    let id        = uuid_v4();
    let now       = chrono::Utc::now();
    let ts        = now.format("%Y%m%d_%H%M%S").to_string();
    let created   = now.to_rfc3339();
    let short_id  = &id[..8];
    let filename  = format!("{ts}_{short_id}.{ext}");
    let file_path = media_dir.join(&filename);

    std::fs::write(&file_path, bytes).map_err(|e| e.to_string())?;

    // Thumbnail: extract first frame via ffmpeg if available, else empty.
    let thumb_dir  = store_dir.join("thumbnails");
    std::fs::create_dir_all(&thumb_dir).map_err(|e| e.to_string())?;
    let thumb_name = format!("{ts}_{short_id}.jpg");
    let thumb_path = thumb_dir.join(&thumb_name);
    extract_thumbnail(&file_path, &thumb_path);

    let rec = Record {
        id:             id.clone(),
        media_type:     req.model_source.as_media_type().into(),
        created_at:     created.clone(),
        file_path:      file_path.to_string_lossy().into(),
        thumbnail_path: thumb_path.to_string_lossy().into(),
        prompt:         req.prompt.clone(),
        model_id:       String::new(),
        starred:        false,
    };

    // Persist to media.db
    insert_record(&rec, elapsed_s).map_err(|e| e.to_string())?;

    Ok(rec)
}

fn insert_record(rec: &Record, duration_s: u64) -> rusqlite::Result<()> {
    let conn = Connection::open(history::media_db_path())?;
    conn.execute(
        "INSERT OR IGNORE INTO media
            (id, media_type, created_at, file_path, thumbnail_path, prompt,
             model_id, generator_type, params, starred)
         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
        rusqlite::params![
            rec.id, rec.media_type, rec.created_at,
            rec.file_path, rec.thumbnail_path, rec.prompt,
            rec.model_id, "", serde_json::json!({"duration_s": duration_s}).to_string(),
            0i64,
        ],
    )?;
    Ok(())
}

fn extract_thumbnail(src: &Path, dst: &Path) {
    // Best-effort — silently skip if ffmpeg is absent.
    let _ = std::process::Command::new("ffmpeg")
        .args([
            "-loglevel", "error",
            "-i", &src.to_string_lossy(),
            "-vframes", "1",
            "-update", "1",
            "-y",
            &dst.to_string_lossy(),
        ])
        .stdin(std::process::Stdio::null())
        .output();
}

fn storage_dir() -> PathBuf {
    history::data_dir()
}

fn uuid_v4() -> String {
    // Read 16 cryptographically random bytes from the OS. Propagate the error
    // rather than silently producing all-zero "UUIDs" (which would generate
    // duplicate filenames and corrupt the media DB).
    let mut buf = [0u8; 16];
    {
        use std::io::Read;
        std::fs::File::open("/dev/urandom")
            .and_then(|mut f| f.read_exact(&mut buf))
            .expect("/dev/urandom unavailable — cannot generate UUID");
    }
    buf[6] = (buf[6] & 0x0f) | 0x40; // version 4
    buf[8] = (buf[8] & 0x3f) | 0x80; // variant 1
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        buf[0],  buf[1],  buf[2],  buf[3],
        buf[4],  buf[5],
        buf[6],  buf[7],
        buf[8],  buf[9],
        buf[10], buf[11], buf[12], buf[13], buf[14], buf[15],
    )
}

fn base64_decode(s: &str) -> Result<Vec<u8>, String> {
    // Use openssl / base64 via std — avoids adding the base64 crate.
    // Simple decoder: strip whitespace, process 4-char groups.
    let clean: String = s.chars().filter(|c| !c.is_whitespace()).collect();
    let chars: Vec<u8> = clean.bytes().collect();
    let mut out = Vec::with_capacity(chars.len() * 3 / 4);
    let table: [i8; 256] = {
        let mut t = [-1i8; 256];
        for (i, &c) in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
            .iter().enumerate() { t[c as usize] = i as i8; }
        t['=' as usize] = 0;
        t
    };
    for chunk in chars.chunks(4) {
        if chunk.len() < 2 { break; }
        let a = table[chunk[0] as usize];
        let b = table[chunk[1] as usize];
        let c = if chunk.len() > 2 { table[chunk[2] as usize] } else { 0 };
        let d = if chunk.len() > 3 { table[chunk[3] as usize] } else { 0 };
        if a < 0 || b < 0 { return Err("invalid base64 character".into()); }
        out.push(((a << 2) | (b >> 4)) as u8);
        if chunk.len() > 2 && chunk[2] != b'=' { out.push(((b << 4) | (c >> 2)) as u8); }
        if chunk.len() > 3 && chunk[3] != b'=' { out.push(((c << 6) | d) as u8); }
    }
    Ok(out)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uuid_v4_format() {
        let id = uuid_v4();
        let parts: Vec<&str> = id.split('-').collect();
        assert_eq!(parts.len(), 5, "UUID should have 5 hyphen-separated groups");
        assert_eq!(parts[0].len(), 8);
        assert_eq!(parts[1].len(), 4);
        assert_eq!(parts[2].len(), 4);
        assert_eq!(parts[3].len(), 4);
        assert_eq!(parts[4].len(), 12);
        assert_eq!(&parts[2][..1], "4", "version nibble should be 4");
    }

    #[test]
    fn uuid_v4_uniqueness() {
        let ids: std::collections::HashSet<String> = (0..100).map(|_| uuid_v4()).collect();
        assert_eq!(ids.len(), 100, "all 100 UUIDs should be unique");
    }

    #[test]
    fn base64_decode_roundtrip() {
        // "Hello, world!" base64-encoded
        let encoded = "SGVsbG8sIHdvcmxkIQ==";
        let decoded = base64_decode(encoded).unwrap();
        assert_eq!(decoded, b"Hello, world!");
    }

    #[test]
    fn base64_decode_no_padding() {
        let encoded = "dGVzdA==";
        let decoded = base64_decode(encoded).unwrap();
        assert_eq!(decoded, b"test");
    }

    #[test]
    fn model_source_media_types() {
        assert_eq!(ModelSource::Video.as_media_type(),    "video");
        assert_eq!(ModelSource::Image.as_media_type(),    "image");
        assert_eq!(ModelSource::Animate.as_media_type(),  "animate");
        assert_eq!(ModelSource::SkyReels.as_media_type(), "video");
    }

    #[test]
    fn generation_request_default() {
        let r = GenerationRequest::default();
        assert_eq!(r.steps, 20);
        assert_eq!(r.seed, -1);
        assert_eq!(r.model_source, ModelSource::Video);
        assert_eq!(r.server_url, "http://localhost:8000");
    }
}
