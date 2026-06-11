//! tt-gen-rs — Rust/GTK4 proof-of-concept for tt-local-generator
//!
//! Architecture mirrors the Python app but gets the threading right at compile
//! time: GTK widget types are !Send so the borrow checker prevents any widget
//! touch from a background thread.  The HealthBus runs on a Tokio thread and
//! communicates back via glib::MainContext::channel() — the Rust equivalent of
//! GLib.idle_add, but typed and zero-overhead.
//!
//! Build:
//!   sudo apt install libgtk-4-dev libglib2.0-dev
//!   cargo build --release
//!   ./target/release/tt-gen-rs
//!
//! What this covers (proof-of-concept scope):
//!   - Main window with correct default size (respects WM tiling/panels)
//!   - Source tab bar: Video / Animate / Image / Artgen
//!   - Gallery placeholder (FlowBox — real thumbnail loading is next)
//!   - Server health status bar (polls port 8000/8001/8002 in background)
//!   - Prompt entry + Generate button
//!   - tt-ctl integration: "Start server" shells out to tt-ctl, non-blocking
//!
//! What re-uses Python work:
//!   - History store: reads ~/.local/share/tt-local-generator/history.json
//!     (same schema as app/history_store.py) and shows one card per record.
//!   - Server scripts: shells out to bin/start_*.sh via std::process::Command.
//!   - tt-ctl: shells out to ./tt-ctl for start/stop/status.

use gtk4::prelude::*;
use gtk4::{
    Application, ApplicationWindow, Box as GtkBox, Button, CssProvider, Entry,
    FlowBox, Label, Orientation, Paned, ScrolledWindow, SelectionMode, ToggleButton,
};
use glib::clone;
use gio::ApplicationFlags;

mod health;
mod history;

use health::HealthSnapshot;
use history::load_history;

const APP_ID: &str = "ai.tenstorrent.tt-video-gen-rs";

// Inline CSS — same colour palette as the Python app's _CSS constant.
// Only a subset needed at this scale; expand as widgets are added.
const CSS: &str = r#"
window {
    background-color: #0F2A35;
    color: #E8F0F2;
}
.source-btn {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D4F5C;
    border-radius: 0;
    padding: 4px 12px;
    font-size: 13px;
}
.source-btn:checked {
    background-color: #4FD1C5;
    color: #080f14;
    font-weight: bold;
}
.source-btn-left  { border-radius: 6px 0 0 6px; }
.source-btn-right { border-radius: 0 6px 6px 0; }
.prompt-entry {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D4F5C;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
}
.generate-btn {
    background-color: #4FD1C5;
    color: #080f14;
    font-weight: bold;
    border-radius: 6px;
    padding: 6px 18px;
}
.generate-btn:hover { background-color: #81E6D9; }
.statusbar {
    background-color: #0a1e26;
    padding: 2px 8px;
    font-size: 11px;
    color: #607D8B;
}
.statusbar-ready  { color: #4FD1C5; }
.statusbar-offline { color: #607D8B; }
.card {
    background-color: #1A3C47;
    border: 1px solid #2D4F5C;
    border-radius: 8px;
    padding: 8px;
    margin: 4px;
}
.muted { color: #607D8B; }
.thumb-placeholder {
    font-size: 32px;
    background-color: #0F2A35;
}
.starred { color: #F4C471; font-size: 13px; }
"#;

fn main() -> glib::ExitCode {
    let app = Application::builder()
        .application_id(APP_ID)
        .flags(ApplicationFlags::NON_UNIQUE)
        .build();

    app.connect_activate(build_ui);
    app.run()
}

fn build_ui(app: &Application) {
    apply_css();

    // ── Health bus ──────────────────────────────────────────────────────────
    // Polls ports 8000/8001/8002 on a Tokio thread.  Results cross via
    // std::sync::mpsc; the GTK main loop drains the Receiver with a
    // 1 s glib::timeout_add_local — zero unsafe, no glib::Sender needed.
    let (health_tx, health_rx) = std::sync::mpsc::channel::<HealthSnapshot>();
    let health_rx = std::sync::Arc::new(std::sync::Mutex::new(health_rx));
    health::start_bus(health_tx);

    // ── Window ──────────────────────────────────────────────────────────────
    let window = ApplicationWindow::builder()
        .application(app)
        .title("TT Local Generator")
        // set_default_size is respected because we never force-maximize.
        // GTK honours the WM's available area; panels on any edge are fine.
        .default_width(1280)
        .default_height(800)
        .build();

    // ── Root layout: horizontal Paned (controls | gallery+detail) ──────────
    let root_paned = Paned::new(Orientation::Horizontal);
    root_paned.set_position(310);
    root_paned.set_shrink_start_child(false);
    root_paned.set_resize_start_child(false);

    // ── Left panel: source tabs + prompt + generate ─────────────────────
    let left = build_left_panel(&window);
    root_paned.set_start_child(Some(&left));

    // ── Right: inner Paned (gallery | detail) ───────────────────────────
    let inner_paned = Paned::new(Orientation::Horizontal);
    inner_paned.set_position(480);

    let gallery_area = build_gallery();
    let detail_area  = build_detail_placeholder();
    inner_paned.set_start_child(Some(&gallery_area));
    inner_paned.set_end_child(Some(&detail_area));

    root_paned.set_end_child(Some(&inner_paned));

    // ── Window chrome ───────────────────────────────────────────────────
    let vbox = GtkBox::new(Orientation::Vertical, 0);
    vbox.append(&root_paned);
    vbox.append(&build_statusbar());

    window.set_child(Some(&vbox));
    window.present();

    // ── Wire health bus to statusbar ────────────────────────────────────
    // Drain the mpsc Receiver every second on the GTK main thread.
    // timeout_add_local runs on the main thread so widget access is safe.
    let statusbar_lbl = vbox
        .last_child()
        .and_then(|w| w.downcast::<Label>().ok())
        .unwrap_or_else(|| Label::new(None));
    glib::timeout_add_local(std::time::Duration::from_millis(1000), move || {
        // Drain all pending snapshots; keep only the latest.
        let rx = health_rx.lock().unwrap();
        let mut latest: Option<HealthSnapshot> = None;
        while let Ok(snap) = rx.try_recv() {
            latest = Some(snap);
        }
        if let Some(snap) = latest {
            statusbar_lbl.set_label(&health_summary(&snap));
            statusbar_lbl.set_css_classes(if snap.any_service_up || snap.server_alive {
                &["statusbar", "statusbar-ready"]
            } else {
                &["statusbar", "statusbar-offline"]
            });
        }
        glib::ControlFlow::Continue
    });
}

// ── Left panel ──────────────────────────────────────────────────────────────

fn build_left_panel(window: &ApplicationWindow) -> GtkBox {
    let panel = GtkBox::new(Orientation::Vertical, 8);
    panel.set_width_request(310);
    panel.set_margin_top(8);
    panel.set_margin_bottom(8);
    panel.set_margin_start(8);
    panel.set_margin_end(4);

    // Source tab bar
    panel.append(&build_source_tabs());

    // Prompt entry
    let prompt = Entry::new();
    prompt.set_placeholder_text(Some("Describe the video you want to generate…"));
    prompt.add_css_class("prompt-entry");
    prompt.set_hexpand(true);
    panel.append(&prompt);

    // Generate button
    let gen_btn = Button::with_label("▶ Generate");
    gen_btn.add_css_class("generate-btn");
    gen_btn.connect_clicked(clone!(
        #[weak] prompt,
        #[weak] window,
        move |_| {
            let _ = window.is_active();
            let text = prompt.text().to_string();
            if text.trim().is_empty() { return; }
            // TODO: wire to GenerationWorker (same pattern as Python worker.py)
            eprintln!("[tt-gen-rs] generate: {text}");
        }
    ));
    panel.append(&gen_btn);

    // tt-ctl server control
    panel.append(&build_server_control());

    panel
}

fn build_source_tabs() -> GtkBox {
    let row = GtkBox::new(Orientation::Horizontal, 0);

    let tabs = [
        ("🎬 Video",         "source-btn source-btn-left",  "video"),
        ("💃 Animate",       "source-btn",                  "animate"),
        ("🖼 Image",         "source-btn",                  "image"),
        ("🎨 Generative Art","source-btn source-btn-right", "artgen"),
    ];

    let mut first: Option<ToggleButton> = None;
    for (label, css, _key) in &tabs {
        let btn = ToggleButton::with_label(label);
        for cls in css.split_whitespace() {
            btn.add_css_class(cls);
        }
        if let Some(ref f) = first {
            btn.set_group(Some(f));
        } else {
            btn.set_active(true);
            first = Some(btn.clone());
        }
        // TODO: connect to gallery stack switch
        row.append(&btn);
    }
    row
}

fn build_server_control() -> GtkBox {
    let row = GtkBox::new(Orientation::Horizontal, 6);
    row.set_margin_top(4);

    let start_btn = Button::with_label("▶ Start server");
    start_btn.connect_clicked(|_| {
        // Shell out to tt-ctl non-blocking — same as Python server_manager.start()
        let repo = find_repo_root();
        if let Some(root) = repo {
            std::thread::spawn(move || {
                let _ = std::process::Command::new(root.join("tt-ctl"))
                    .arg("start")
                    .arg("wan2.2")
                    .spawn();
            });
        }
    });

    let stop_btn = Button::with_label("■ Stop");
    stop_btn.connect_clicked(|_| {
        let repo = find_repo_root();
        if let Some(root) = repo {
            std::thread::spawn(move || {
                let _ = std::process::Command::new(root.join("tt-ctl"))
                    .arg("stop")
                    .arg("wan2.2")
                    .spawn();
            });
        }
    });

    row.append(&start_btn);
    row.append(&stop_btn);
    row
}

// ── Gallery ──────────────────────────────────────────────────────────────────

fn build_gallery() -> ScrolledWindow {
    let scroll = ScrolledWindow::new();
    scroll.set_hexpand(true);
    scroll.set_vexpand(true);

    let flow = FlowBox::new();
    flow.set_selection_mode(SelectionMode::None);
    flow.set_homogeneous(false);
    flow.set_min_children_per_line(2);
    flow.set_max_children_per_line(8);
    flow.set_column_spacing(12);
    flow.set_row_spacing(12);
    flow.set_margin_top(8);
    flow.set_margin_bottom(8);
    flow.set_margin_start(8);
    flow.set_margin_end(8);

    // Load from the same history.json the Python app writes.
    let records = load_history();
    for rec in records.iter().take(48) {
        let card = build_card(rec);
        flow.append(&card);
    }

    if records.is_empty() {
        let lbl = Label::new(Some("No generations yet — enter a prompt and click Generate"));
        lbl.add_css_class("muted");
        flow.append(&lbl);
    }

    scroll.set_child(Some(&flow));
    scroll
}

fn build_card(rec: &history::Record) -> GtkBox {
    let card = GtkBox::new(Orientation::Vertical, 4);
    card.add_css_class("card");
    card.set_width_request(200);

    // Thumbnail — use gtk4::Picture if a thumbnail file exists, else emoji fallback.
    let thumb_path = std::path::Path::new(&rec.thumbnail_path);
    if !rec.thumbnail_path.is_empty() && thumb_path.exists() {
        let pic = gtk4::Picture::for_filename(&rec.thumbnail_path);
        pic.set_size_request(200, 112);
        pic.set_content_fit(gtk4::ContentFit::Cover);
        pic.set_can_shrink(true);
        card.append(&pic);
    } else {
        // Artgen / missing thumbnail — show media type icon
        let icon = match rec.media_type.as_str() {
            "video" | "animate" => "🎬",
            "image"             => "🖼",
            "artgen"            => "🎨",
            "animatediff"       => "✨",
            _                   => "▪",
        };
        let lbl = Label::new(Some(icon));
        lbl.set_size_request(200, 112);
        lbl.add_css_class("thumb-placeholder");
        card.append(&lbl);
    }

    // Prompt snippet
    let prompt = Label::new(Some(&rec.prompt));
    prompt.set_max_width_chars(24);
    prompt.set_ellipsize(gtk4::pango::EllipsizeMode::End);
    prompt.set_xalign(0.0);
    prompt.add_css_class("muted");
    card.append(&prompt);

    // Starred indicator
    if rec.starred {
        let star = Label::new(Some("★"));
        star.set_xalign(1.0);
        star.add_css_class("starred");
        card.append(&star);
    }

    card
}

// ── Detail placeholder ───────────────────────────────────────────────────────

fn build_detail_placeholder() -> GtkBox {
    let b = GtkBox::new(Orientation::Vertical, 0);
    b.set_width_request(300);
    let lbl = Label::new(Some("Select a card to see details"));
    lbl.add_css_class("muted");
    lbl.set_vexpand(true);
    lbl.set_valign(gtk4::Align::Center);
    b.append(&lbl);
    b
}

// ── Status bar ───────────────────────────────────────────────────────────────

fn build_statusbar() -> Label {
    let lbl = Label::new(Some("● Server offline"));
    lbl.add_css_class("statusbar");
    lbl.add_css_class("statusbar-offline");
    lbl.set_xalign(0.0);
    lbl.set_margin_start(8);
    lbl
}

fn health_summary(snap: &HealthSnapshot) -> String {
    let server = if snap.server_alive {
        format!("● {}", snap.model.as_deref().unwrap_or("server ready"))
    } else {
        "○ Server offline".to_string()
    };
    // Show any service that's up from the services vec
    let extras: String = snap.services.iter()
        .filter(|(k, up)| *up && k != "wan2.2")
        .map(|(k, _)| format!("  ● {k}"))
        .collect::<Vec<_>>()
        .join("");
    let queue = if snap.queue_depth > 0 {
        format!("  [{} queued]", snap.queue_depth)
    } else {
        String::new()
    };
    format!("{server}{extras}{queue}")
}

// ── CSS ──────────────────────────────────────────────────────────────────────

fn apply_css() {
    let provider = CssProvider::new();
    provider.load_from_string(CSS);
    if let Some(display) = gtk4::gdk::Display::default() {
        gtk4::style_context_add_provider_for_display(
            &display,
            &provider,
            gtk4::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
    }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

fn find_repo_root() -> Option<std::path::PathBuf> {
    // Walk up from the binary's location to find tt-ctl.
    let exe = std::env::current_exe().ok()?;
    let mut dir = exe.parent()?;
    for _ in 0..6 {
        if dir.join("tt-ctl").exists() {
            return Some(dir.to_path_buf());
        }
        dir = dir.parent()?;
    }
    None
}
