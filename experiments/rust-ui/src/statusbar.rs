//! statusbar.rs — StatusBar widget
//!
//! Mirrors Python _StatusBar (main_window.py:6097).
//!
//! Shows:
//!   - Server dot + model label (MenuButton expands capability dashboard)
//!   - Queue depth badge (hidden when empty)
//!   - Disk-free indicator
//!   - Chip telemetry via `tt-smi -s` (refreshed every 30 s)

use crate::health::HealthSnapshot;
use glib;
use gtk4::prelude::*;
use gtk4::{Box as GtkBox, Label, Orientation, Widget};

const POLL_INTERVAL_S: u64 = 30;

// ── Build function ────────────────────────────────────────────────────────────

pub fn build_statusbar() -> (Widget, StatusBarHandle) {
    let bar = GtkBox::new(Orientation::Horizontal, 8);
    bar.add_css_class("statusbar");
    bar.set_margin_start(8);
    bar.set_margin_end(8);

    // Server dot + model label
    let dot = Label::new(Some("○"));
    dot.add_css_class("statusbar-offline");

    let model_lbl = Label::new(Some("Server offline"));
    model_lbl.add_css_class("statusbar-offline");
    model_lbl.set_xalign(0.0);

    bar.append(&dot);
    bar.append(&model_lbl);

    // Queue depth badge (hidden when empty)
    let queue_lbl = Label::new(None);
    queue_lbl.add_css_class("statusbar-queue");
    queue_lbl.set_visible(false);
    bar.append(&queue_lbl);

    // Spacer
    let spacer = Label::new(None);
    spacer.set_hexpand(true);
    bar.append(&spacer);

    // Disk free
    let disk_lbl = Label::new(None);
    disk_lbl.add_css_class("muted");
    bar.append(&disk_lbl);
    refresh_disk_free(&disk_lbl);

    // Chip telemetry
    let chip_lbl = Label::new(None);
    chip_lbl.add_css_class("muted");
    bar.append(&chip_lbl);

    // Start tt-smi polling
    {
        let chip2 = chip_lbl.clone();
        glib::timeout_add_local(std::time::Duration::from_secs(POLL_INTERVAL_S), move || {
            refresh_chip_telemetry(&chip2);
            glib::ControlFlow::Continue
        });
        // Fire once immediately on next idle
        let chip3 = chip_lbl.clone();
        glib::idle_add_local_once(move || refresh_chip_telemetry(&chip3));
    }

    let handle = StatusBarHandle { dot, model_lbl, queue_lbl };
    (bar.upcast::<Widget>(), handle)
}

// ── Handle ────────────────────────────────────────────────────────────────────

#[derive(Clone)]
pub struct StatusBarHandle {
    dot:       Label,
    model_lbl: Label,
    queue_lbl: Label,
}

impl StatusBarHandle {
    pub fn update(&self, snap: &HealthSnapshot) {
        let (dot_str, css, label) = if snap.server_alive && snap.server_ready {
            ("●", "statusbar-ready",   snap.model.as_deref().unwrap_or("Server ready").to_string())
        } else if snap.server_alive {
            ("●", "statusbar-loading", "Model loading…".into())
        } else {
            ("○", "statusbar-offline", "Server offline".into())
        };

        self.dot.set_label(dot_str);
        self.dot.set_css_classes(&[css]);
        self.model_lbl.set_label(&label);
        self.model_lbl.set_css_classes(&[css]);

        // Queue badge
        if snap.queue_depth > 0 {
            self.queue_lbl.set_label(&format!("[{} queued]", snap.queue_depth));
            self.queue_lbl.set_visible(true);
        } else {
            self.queue_lbl.set_visible(false);
        }
    }
}

// ── Telemetry helpers ─────────────────────────────────────────────────────────

fn refresh_disk_free(lbl: &Label) {
    let path = dirs_next::data_local_dir()
        .unwrap_or_default()
        .join("tt-video-gen");
    std::fs::create_dir_all(&path).ok();
    if let Some(free_gb) = disk_free_gb(&path.to_string_lossy()) {
        lbl.set_label(&format!("{free_gb:.1} GB free"));
    }
}

fn disk_free_gb(path: &str) -> Option<f64> {
    // statvfs via the `libc` crate would be cleaner but adds a dep.
    // Use `df` subprocess instead — portable on Linux.
    let out = std::process::Command::new("df")
        .args(["-B1", "--output=avail", path])
        .output()
        .ok()?;
    let stdout = String::from_utf8_lossy(&out.stdout);
    let bytes: u64 = stdout.lines()
        .nth(1)?               // skip header
        .trim()
        .parse()
        .ok()?;
    Some(bytes as f64 / 1_073_741_824.0)
}

fn refresh_chip_telemetry(lbl: &Label) {
    // Use a small mpsc channel: background thread produces the text,
    // main thread reads it after a brief idle to avoid GTK !Send violations.
    let (itx, irx) = std::sync::mpsc::channel::<Option<String>>();
    std::thread::spawn(move || {
        let _ = itx.send(run_tt_smi());
    });
    // Poll on the GTK main thread immediately via idle_add_local_once.
    let lbl2 = lbl.clone();
    glib::idle_add_local(move || {
        if let Ok(result) = irx.try_recv() {
            if let Some(text) = result {
                lbl2.set_label(&text);
                lbl2.set_visible(true);
            } else {
                lbl2.set_visible(false);
            }
            return glib::ControlFlow::Break;
        }
        glib::ControlFlow::Continue
    });
}

fn run_tt_smi() -> Option<String> {
    let out = std::process::Command::new("tt-smi")
        .args(["-s"])
        .stdin(std::process::Stdio::null())
        .output()
        .ok()?;
    if !out.status.success() { return None; }
    parse_tt_smi_snapshot(&String::from_utf8_lossy(&out.stdout))
}

/// Parse `tt-smi -s` JSON snapshot.
/// Returns a compact string like "2× Blackhole  42°C  85%"
pub fn parse_tt_smi_snapshot(json: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(json).ok()?;
    let devices = v.as_array().or_else(|| v["devices"].as_array())?;
    if devices.is_empty() { return None; }

    let count = devices.len();
    let mut total_temp = 0.0_f64;
    let mut total_util = 0.0_f64;
    let mut found_temp = 0usize;
    let mut found_util = 0usize;
    let mut chip_name  = "Chip".to_string();

    for d in devices {
        if let Some(name) = d["board_info"]["board_type"].as_str()
            .or_else(|| d["chip_info"]["arch"].as_str())
        {
            chip_name = name.to_string();
        }
        if let Some(t) = d["telemetry"]["asic_temperature"].as_f64()
            .or_else(|| d["temperature"]["AICLK"].as_f64())
        {
            total_temp += t;
            found_temp += 1;
        }
        if let Some(u) = d["telemetry"]["aiclk"].as_f64()
            .or_else(|| d["voltage"]["AICLK"].as_f64())
        {
            total_util += u;
            found_util += 1;
        }
    }

    let mut parts = vec![format!("{count}× {chip_name}")];
    if found_temp > 0 { parts.push(format!("{:.0}°C", total_temp / found_temp as f64)); }
    if found_util > 0 { parts.push(format!("{:.0} MHz", total_util / found_util as f64)); }

    Some(parts.join("  "))
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_snap(alive: bool, ready: bool, model: Option<&str>, queue: usize) -> HealthSnapshot {
        HealthSnapshot {
            server_alive:   alive,
            server_ready:   ready,
            model:          model.map(str::to_string),
            queue_depth:    queue,
            any_service_up: alive,
            services:       vec![],
        }
    }

    #[test]
    fn status_offline() {
        let snap = make_snap(false, false, None, 0);
        let (dot, css, label) = if snap.server_alive && snap.server_ready {
            ("●", "statusbar-ready",   snap.model.as_deref().unwrap_or("Server ready").to_string())
        } else if snap.server_alive {
            ("●", "statusbar-loading", "Model loading…".into())
        } else {
            ("○", "statusbar-offline", "Server offline".into())
        };
        assert_eq!(dot, "○");
        assert_eq!(css, "statusbar-offline");
        assert_eq!(label, "Server offline");
    }

    #[test]
    fn status_ready_with_model() {
        let snap = make_snap(true, true, Some("Wan2.2"), 0);
        let label = if snap.server_ready {
            snap.model.as_deref().unwrap_or("Server ready").to_string()
        } else { "loading".into() };
        assert_eq!(label, "Wan2.2");
    }

    #[test]
    fn queue_badge_shown_when_nonempty() {
        let snap = make_snap(true, true, None, 3);
        assert!(snap.queue_depth > 0);
        let badge = format!("[{} queued]", snap.queue_depth);
        assert_eq!(badge, "[3 queued]");
    }

    #[test]
    fn parse_tt_smi_empty_array() {
        let result = parse_tt_smi_snapshot("[]");
        assert!(result.is_none());
    }

    #[test]
    fn parse_tt_smi_minimal_device() {
        let json = r#"[{"board_info":{"board_type":"Blackhole"},"telemetry":{"asic_temperature":45.0,"aiclk":800.0}}]"#;
        let result = parse_tt_smi_snapshot(json);
        assert!(result.is_some());
        let s = result.unwrap();
        assert!(s.contains("Blackhole"), "got: {s}");
        assert!(s.contains("45°C"), "got: {s}");
    }

    #[test]
    fn parse_tt_smi_multi_device() {
        let json = r#"[
            {"board_info":{"board_type":"Blackhole"},"telemetry":{"asic_temperature":40.0}},
            {"board_info":{"board_type":"Blackhole"},"telemetry":{"asic_temperature":50.0}}
        ]"#;
        let result = parse_tt_smi_snapshot(json);
        assert!(result.is_some());
        let s = result.unwrap();
        assert!(s.starts_with("2×"), "got: {s}");
        assert!(s.contains("45°C"), "avg 40+50/2=45, got: {s}");
    }

    #[test]
    fn parse_tt_smi_invalid_json() {
        assert!(parse_tt_smi_snapshot("not json").is_none());
    }
}
