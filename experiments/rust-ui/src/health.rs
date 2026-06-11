//! health.rs — HealthBus for Rust/GTK4
//!
//! Mirrors health_bus.py. HealthSnapshot is Send (plain data), so it
//! crosses threads via std::sync::mpsc.  The GTK main thread polls
//! the receiver with glib::timeout_add_local — equivalent to Python's
//! GLib.idle_add but without the Send requirement.
//!
//! Poll cycle: 15 s when any server is up, 30 s otherwise.
//! Three unique URLs (ports 8000/8001/8002), fetched concurrently.

use std::sync::mpsc::Sender;
use std::time::Duration;
use tokio::time::sleep;

/// Snapshot of all server health at one point in time.
#[derive(Debug, Clone, Default)]
pub struct HealthSnapshot {
    pub port8000:      bool,
    pub port8001:      bool,
    pub port8002:      bool,
    pub any_up:        bool,
    pub running_model: Option<String>,
    pub artgen_model:  Option<String>,
}

/// Start the health-bus background task.
///
/// Spawns a Tokio runtime on a dedicated OS thread so it never touches the
/// GTK main thread.  Results are sent to `tx` which the GTK main loop drains
/// via glib::Receiver<HealthSnapshot>.
/// Opaque handle — keeps API symmetric with the Python HealthBus.
pub struct HealthBus;

pub fn start_bus(tx: Sender<HealthSnapshot>) -> HealthBus {
    std::thread::Builder::new()
        .name("health-bus".into())
        .spawn(move || {
            let rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .expect("tokio runtime");
            rt.block_on(bus_loop(tx));
        })
        .expect("health-bus thread");
    HealthBus
}

async fn bus_loop(tx: Sender<HealthSnapshot>) {
    loop {
        let snap = poll_once().await;
        let interval = if snap.any_up { 15 } else { 30 };
        // Send to GTK main thread; ignore if window is closed.
        if tx.send(snap).is_err() { break; }
        sleep(Duration::from_secs(interval)).await;
    }
}

async fn poll_once() -> HealthSnapshot {
    // Fire three URL checks concurrently.
    let (r8000, r8001, r8002) = tokio::join!(
        check_url("http://localhost:8000/tt-liveness", 2),
        check_url("http://localhost:8001/health",      2),
        check_url("http://localhost:8002/v1/models",   2),
    );

    // Parse running model from port 8000 liveness response.
    let running_model = r8000.as_ref().ok().and_then(|body| {
        serde_json::from_str::<serde_json::Value>(body).ok()
            .and_then(|v| v["runner_in_use"].as_str().map(str::to_string))
    });

    // Parse artgen model from port 8002 /v1/models response.
    let artgen_model = r8002.as_ref().ok().and_then(|body| {
        serde_json::from_str::<serde_json::Value>(body).ok()
            .and_then(|v| {
                v["data"].as_array()
                    .and_then(|arr| arr.first())
                    .and_then(|m| m["id"].as_str().map(str::to_string))
            })
    });

    let up8000 = r8000.is_ok();
    let up8001 = r8001.is_ok();
    let up8002 = r8002.is_ok();

    HealthSnapshot {
        port8000:      up8000,
        port8001:      up8001,
        port8002:      up8002,
        any_up:        up8000 || up8001 || up8002,
        running_model,
        artgen_model,
    }
}

/// Fetch a URL with a timeout.  Returns the response body on success.
async fn check_url(url: &str, timeout_s: u64) -> Result<String, ()> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_s))
        .build()
        .map_err(|_| ())?;
    let resp = client.get(url).send().await.map_err(|_| ())?;
    if resp.status().is_success() {
        resp.text().await.map_err(|_| ())
    } else {
        Err(())
    }
}
