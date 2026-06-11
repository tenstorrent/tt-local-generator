//! health.rs — HealthBus for tt-gen-rs
//!
//! Shells out to `tt-ctl status --json` on a background thread, parses the
//! result, and sends a HealthSnapshot to the GTK main thread via
//! std::sync::mpsc.  The main thread drains it with glib::timeout_add_local.
//!
//! Using tt-ctl means we get queue depth, per-service status, and model name
//! all in one call — and we automatically benefit from tt-ctl's port-scan
//! logic (compatible vLLM / llama.cpp servers are treated the same as ours).
//!
//! Poll cycle: 10 s when any service is running, 30 s otherwise.

use serde::Deserialize;
use std::path::PathBuf;
use std::sync::mpsc::Sender;
use std::time::Duration;

// ── Public types ─────────────────────────────────────────────────────────────

/// Snapshot of all server health at one point in time.
#[derive(Debug, Clone, Default)]
pub struct HealthSnapshot {
    pub server_alive:   bool,
    pub server_ready:   bool,
    pub model:          Option<String>,
    pub queue_depth:    usize,
    pub any_service_up: bool,
    /// Per-service running flags, e.g. "wan2.2" → true
    pub services:       Vec<(String, bool)>,
}

/// Opaque handle — keeps the API symmetric with Python's HealthBus.
pub struct HealthBus;

pub fn start_bus(tx: Sender<HealthSnapshot>) -> HealthBus {
    std::thread::Builder::new()
        .name("health-bus".into())
        .spawn(move || bus_loop(tx))
        .expect("health-bus thread");
    HealthBus
}

// ── Internal ──────────────────────────────────────────────────────────────────

fn bus_loop(tx: Sender<HealthSnapshot>) {
    loop {
        let snap = poll_once();
        let interval = if snap.any_service_up { 10 } else { 30 };
        if tx.send(snap).is_err() {
            break; // receiver dropped — window closed
        }
        std::thread::sleep(Duration::from_secs(interval));
    }
}

fn poll_once() -> HealthSnapshot {
    match run_tt_ctl_status() {
        Ok(raw) => parse_status_json(&raw),
        Err(_)  => HealthSnapshot::default(),
    }
}

/// Run `tt-ctl status --json` and return stdout.
/// Searches for tt-ctl starting from the binary's own directory, walking up.
pub fn run_tt_ctl_status() -> Result<String, String> {
    let tt_ctl = find_tt_ctl().ok_or_else(|| "tt-ctl not found".to_string())?;
    let output = std::process::Command::new(&tt_ctl)
        .args(["status", "--json"])
        .output()
        .map_err(|e| e.to_string())?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).into_owned())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).into_owned())
    }
}

/// Parse the JSON emitted by `tt-ctl status --json` into a HealthSnapshot.
pub fn parse_status_json(raw: &str) -> HealthSnapshot {
    let Ok(v) = serde_json::from_str::<TtCtlStatus>(raw) else {
        return HealthSnapshot::default();
    };

    let services: Vec<(String, bool)> = v.services
        .as_object()
        .map(|obj| obj.iter().map(|(k, val)| (k.clone(), val.as_bool().unwrap_or(false))).collect())
        .unwrap_or_default();

    let any_service_up = services.iter().any(|(_, up)| *up);

    HealthSnapshot {
        server_alive:   v.server.alive,
        server_ready:   v.server.ready,
        model:          v.server.model.filter(|s| !s.is_empty()),
        queue_depth:    v.queue.depth,
        any_service_up,
        services,
    }
}

// ── Serde shapes matching tt-ctl status --json output ─────────────────────

#[derive(Deserialize)]
struct TtCtlStatus {
    server:   TtCtlServer,
    services: serde_json::Value,
    queue:    TtCtlQueue,
}

#[derive(Deserialize)]
struct TtCtlServer {
    alive: bool,
    ready: bool,
    model: Option<String>,
}

#[derive(Deserialize)]
struct TtCtlQueue {
    depth: usize,
}

// ── Path resolution ───────────────────────────────────────────────────────────

pub fn find_tt_ctl() -> Option<PathBuf> {
    // Walk up from the binary's location to find tt-ctl at the repo root.
    let exe = std::env::current_exe().ok()?;
    let mut dir = exe.parent()?;
    for _ in 0..8 {
        let candidate = dir.join("tt-ctl");
        if candidate.exists() {
            return Some(candidate);
        }
        dir = dir.parent()?;
    }
    // Fallback: check the current working directory (useful in tests / dev)
    let cwd = std::env::current_dir().ok()?;
    let candidate = cwd.join("tt-ctl");
    if candidate.exists() {
        return Some(candidate);
    }
    None
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_json(alive: bool, model: Option<&str>, queue_depth: usize, wan_up: bool) -> String {
        let model_str = model.map(|m| format!("\"{}\"", m)).unwrap_or("null".into());
        format!(r#"{{
            "server": {{"url":"http://localhost:8000","alive":{alive},"ready":{alive},"model":{model_str}}},
            "services": {{"wan2.2":{wan_up},"mochi":false,"flux":false,"prompt-server":false}},
            "queue": {{"depth":{queue_depth},"items":[]}},
            "history": {{"total":10,"by_type":{{}},"newest_at":null,"oldest_at":null}}
        }}"#)
    }

    #[test]
    fn parse_server_alive() {
        let snap = parse_status_json(&sample_json(true, Some("Wan2.2"), 0, true));
        assert!(snap.server_alive);
        assert!(snap.server_ready);
        assert_eq!(snap.model.as_deref(), Some("Wan2.2"));
    }

    #[test]
    fn parse_server_offline() {
        let snap = parse_status_json(&sample_json(false, None, 0, false));
        assert!(!snap.server_alive);
        assert!(snap.model.is_none());
        assert!(!snap.any_service_up);
    }

    #[test]
    fn parse_queue_depth() {
        let snap = parse_status_json(&sample_json(true, None, 3, false));
        assert_eq!(snap.queue_depth, 3);
    }

    #[test]
    fn parse_service_flags() {
        let snap = parse_status_json(&sample_json(false, None, 0, true));
        let wan = snap.services.iter().find(|(k, _)| k == "wan2.2");
        assert_eq!(wan.map(|(_, v)| *v), Some(true));
        assert!(snap.any_service_up);
    }

    #[test]
    fn parse_invalid_json_returns_default() {
        let snap = parse_status_json("not json at all");
        assert!(!snap.server_alive);
        assert_eq!(snap.queue_depth, 0);
        assert!(snap.services.is_empty());
    }

    #[test]
    fn parse_empty_model_filtered() {
        let snap = parse_status_json(&sample_json(true, Some(""), 0, false));
        assert!(snap.model.is_none(), "empty string model should become None");
    }
}
