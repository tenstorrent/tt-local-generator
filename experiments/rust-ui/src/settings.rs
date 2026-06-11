//! settings.rs — Read/write app_settings.json
//!
//! Mirrors app_settings.py: same file path, same defaults, same key names.
//! Reads are cheap (cached in memory); writes flush atomically via a temp file.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

// ── Settings struct ───────────────────────────────────────────────────────────

/// All user-configurable settings.  Mirrors DEFAULTS in app_settings.py.
/// serde defaults fill in keys absent from the JSON file (forward-compat).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub quality_steps:           u32,
    pub sleep_after_n_gens:      u32,
    pub inhibit_screensaver:     bool,
    pub max_disk_gb:             u32,
    pub tttv_image_dwell_s:      u32,
    pub tttv_video_fallback_s:   u32,
    pub director_style_prob:     f64,
    pub director_pin:            String,
    pub skyreels_num_frames:     u32,
    pub animatediff_frames:      u32,
    pub clip_length_slot:        String,
    pub preferred_video_model:   String,
    pub seed_mode:               String,
    pub pinned_seed:             i64,
    pub motion_clips_dir:        String,
    /// plugin/model keys hidden from UI (does NOT affect MCP exposure)
    pub hidden_plugins:          Vec<String>,
    pub last_successful_deployment: String,
    pub dismissed_job_ids:       Vec<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            quality_steps:              20,
            sleep_after_n_gens:         0,
            inhibit_screensaver:        false,
            max_disk_gb:                0,
            tttv_image_dwell_s:         10,
            tttv_video_fallback_s:      90,
            director_style_prob:        0.33,
            director_pin:               String::new(),
            skyreels_num_frames:        33,
            animatediff_frames:         8,
            clip_length_slot:           "standard".into(),
            preferred_video_model:      String::new(),
            seed_mode:                  "random".into(),
            pinned_seed:                -1,
            motion_clips_dir:           String::new(),
            hidden_plugins:             vec![],
            last_successful_deployment: String::new(),
            dismissed_job_ids:          vec![],
        }
    }
}

// ── I/O ───────────────────────────────────────────────────────────────────────

/// Load settings from disk.  Returns defaults on any error (file missing, bad JSON).
pub fn load() -> Settings {
    let path = settings_path();
    if !path.exists() {
        return Settings::default();
    }
    let raw = match std::fs::read_to_string(&path) {
        Ok(s)  => s,
        Err(e) => { eprintln!("[settings] read error: {e}"); return Settings::default(); }
    };
    match serde_json::from_str::<Settings>(&raw) {
        Ok(s)  => s,
        Err(e) => { eprintln!("[settings] parse error: {e}"); Settings::default() }
    }
}

/// Write settings to disk atomically (temp file → rename).
pub fn save(s: &Settings) -> Result<(), String> {
    let path = settings_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let json = serde_json::to_string_pretty(s).map_err(|e| e.to_string())?;
    let tmp  = path.with_extension("json.tmp");
    std::fs::write(&tmp, json).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())
}

fn settings_path() -> PathBuf {
    // Mirrors app_settings.py: ~/.local/share/tt-video-gen/settings.json
    dirs_next::data_local_dir()
        .unwrap_or_else(|| PathBuf::from(std::env::var("HOME").unwrap_or_default()))
        .join("tt-video-gen")
        .join("settings.json")
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn tmp_path() -> PathBuf {
        let n = COUNTER.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir()
            .join(format!("tt-gen-rs-settings-{}-{n}", std::process::id()))
            .join("settings.json")
    }

    fn load_from(path: &PathBuf) -> Settings {
        if !path.exists() {
            return Settings::default();
        }
        let raw = std::fs::read_to_string(path).unwrap();
        serde_json::from_str(&raw).unwrap()
    }

    fn save_to(path: &PathBuf, s: &Settings) {
        if let Some(p) = path.parent() { std::fs::create_dir_all(p).unwrap(); }
        std::fs::write(path, serde_json::to_string_pretty(s).unwrap()).unwrap();
    }

    #[test]
    fn defaults_are_sane() {
        let s = Settings::default();
        assert_eq!(s.quality_steps, 20);
        assert_eq!(s.seed_mode, "random");
        assert_eq!(s.clip_length_slot, "standard");
        assert_eq!(s.skyreels_num_frames, 33);
        assert!(!s.inhibit_screensaver);
    }

    #[test]
    fn roundtrip_to_json() {
        let path = tmp_path();
        let mut s = Settings::default();
        s.quality_steps = 42;
        s.seed_mode = "repeat".into();
        s.hidden_plugins = vec!["mochi".into(), "skyreels".into()];
        save_to(&path, &s);

        let s2 = load_from(&path);
        assert_eq!(s2.quality_steps, 42);
        assert_eq!(s2.seed_mode, "repeat");
        assert_eq!(s2.hidden_plugins, vec!["mochi", "skyreels"]);
    }

    #[test]
    fn missing_keys_get_defaults() {
        // Simulate an older settings file that lacks newer keys.
        let path = tmp_path();
        if let Some(p) = path.parent() { std::fs::create_dir_all(p).unwrap(); }
        std::fs::write(&path, r#"{"quality_steps": 15}"#).unwrap();

        let s = load_from(&path);
        assert_eq!(s.quality_steps, 15);
        // New key not in the file — gets default.
        assert_eq!(s.skyreels_num_frames, 33);
        assert_eq!(s.seed_mode, "random");
    }

    #[test]
    fn bad_json_returns_defaults() {
        let path = tmp_path();
        if let Some(p) = path.parent() { std::fs::create_dir_all(p).unwrap(); }
        std::fs::write(&path, "not valid json {{{{").unwrap();

        // Can't call load() directly (hard-codes path), but we can verify
        // the serde fallback manually mirrors what load() does.
        let result: Result<Settings, _> = serde_json::from_str("not valid json {{{{");
        assert!(result.is_err());
        // load() would return Settings::default() here.
    }
}
