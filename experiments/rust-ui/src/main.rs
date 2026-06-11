//! tt-gen-rs — Rust/GTK4 UI for tt-local-generator
//!
//! Architecture:
//!   - GTK widget types are !Send; the borrow checker prevents widget
//!     access from background threads at compile time.
//!   - Health bus runs on a dedicated std::thread, posts HealthSnapshot
//!     over std::sync::mpsc; the GTK main loop drains the Receiver with
//!     a 1 s glib::timeout_add_local.
//!   - All module events (CardEvent, ControlEvent, DetailEvent, WorkerMsg)
//!     travel over mpsc channels and are drained on the main thread.
//!
//! Build:
//!   sudo apt install libgtk-4-dev libglib2.0-dev
//!   cargo build --release
//!   ./target/release/tt-gen-rs

use gtk4::prelude::*;
use gtk4::{
    Application, ApplicationWindow, Box as GtkBox, CssProvider, Orientation, Paned, Stack,
};
use gio::ApplicationFlags;

mod card;
mod control;
mod detail;
mod gallery;
mod health;
mod history;
mod settings;
mod statusbar;
mod worker;

use card::CardEvent;
use control::{build_control_panel, ControlEvent};
use detail::{build_detail, build_detail_placeholder, DetailEvent};
use gallery::{build_gallery, GalleryTab};
use health::HealthSnapshot;
use statusbar::build_statusbar;
use worker::{GenerationWorker, WorkerMsg};

const APP_ID: &str = "ai.tenstorrent.tt-video-gen-rs";

/// Tenstorrent colour palette + Rust-UI specific widget styles.
const CSS: &str = r#"
window {
    background-color: #0F2A35;
    color: #E8F0F2;
}
/* ── Source tab bar ── */
.source-btn {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D4F5C;
    border-radius: 0;
    padding: 4px 10px;
    font-size: 12px;
}
.source-btn:checked {
    background-color: #4FD1C5;
    color: #080f14;
    font-weight: bold;
}
.source-btn-left  { border-radius: 6px 0 0 6px; }
.source-btn-right { border-radius: 0 6px 6px 0; }
/* ── Prompt / entries ── */
.prompt-entry {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D4F5C;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
}
/* ── Generate button ── */
.generate-btn {
    background-color: #4FD1C5;
    color: #080f14;
    font-weight: bold;
    border-radius: 6px;
    padding: 6px 18px;
    font-size: 13px;
}
.generate-btn:hover   { background-color: #81E6D9; }
.generate-btn:disabled { opacity: 0.4; }
/* ── Status bar ── */
.statusbar {
    background-color: #0a1e26;
    padding: 2px 8px;
    font-size: 11px;
    color: #607D8B;
}
.statusbar-ready   { color: #4FD1C5; }
.statusbar-offline { color: #607D8B; }
.statusbar-loading { color: #F4C471; }
.statusbar-queue   { color: #EC96B8; font-size: 11px; }
/* ── Server dot in control panel ── */
.server-dot-ready   { color: #4FD1C5; }
.server-dot-offline { color: #607D8B; }
.server-dot-loading { color: #F4C471; }
/* ── Cards ── */
.card {
    background-color: #1A3C47;
    border: 1px solid #2D4F5C;
    border-radius: 8px;
    margin: 4px;
}
.card-prompt {
    font-size: 11px;
    color: #B0C4DE;
}
.thumb-placeholder {
    font-size: 32px;
    background-color: #0F2A35;
}
.hover-action-bar {
    background-color: rgba(15,42,53,0.85);
    padding: 2px 4px;
    border-radius: 0 0 8px 8px;
}
.hover-action-btn {
    padding: 2px 6px;
    font-size: 13px;
    color: #E8F0F2;
    min-width: 0;
    min-height: 0;
}
.hover-action-btn-delete { color: #FF6B6B; }
/* ── Detail panel ── */
.detail-nav-bar   { background-color: #1A3C47; }
.detail-prompt    { font-size: 13px; color: #E8F0F2; }
.detail-placeholder { font-size: 48px; color: #2D4F5C; }
/* ── Utility ── */
.muted   { color: #607D8B; }
.starred { color: #F4C471; font-size: 13px; }
.inspire-btn { font-size: 12px; padding: 2px 8px; }
.neg-toggle  { font-size: 11px; color: #607D8B; padding: 0 4px; }
"#;

// ── Entry point ───────────────────────────────────────────────────────────────

fn main() -> glib::ExitCode {
    let app = Application::builder()
        .application_id(APP_ID)
        .flags(ApplicationFlags::NON_UNIQUE)
        .build();

    app.connect_activate(build_ui);
    app.run()
}

// ── UI build ──────────────────────────────────────────────────────────────────

fn build_ui(app: &Application) {
    apply_css();

    // ── Load settings ────────────────────────────────────────────────────────
    let settings = settings::load();

    // ── Health bus ────────────────────────────────────────────────────────────
    // Background thread → mpsc → main thread polls every 1 s.
    let (health_tx, health_rx) = std::sync::mpsc::channel::<HealthSnapshot>();
    health::start_bus(health_tx);

    // ── Card / gallery event channel ──────────────────────────────────────────
    // Each Receiver has exactly one consumer — move directly into the pump
    // closure via `move ||`. No Arc<Mutex> needed; that pattern is only
    // appropriate when a Receiver must be shared across multiple consumers.
    let (card_tx, card_rx)     = std::sync::mpsc::channel::<CardEvent>();
    let (ctrl_tx, ctrl_rx)     = std::sync::mpsc::channel::<ControlEvent>();
    let (detail_tx, detail_rx) = std::sync::mpsc::channel::<DetailEvent>();
    let (worker_tx, worker_rx) = std::sync::mpsc::channel::<WorkerMsg>();

    // ── Window ────────────────────────────────────────────────────────────────
    let window = ApplicationWindow::builder()
        .application(app)
        .title("TT Local Generator")
        .default_width(1280)
        .default_height(800)
        .build();

    // ── Root chrome: vertical box → [content_paned | statusbar] ──────────────
    let root_vbox = GtkBox::new(Orientation::Vertical, 0);

    // ── Status bar ────────────────────────────────────────────────────────────
    let (statusbar_widget, statusbar_handle) = build_statusbar();

    // ── Control panel (left sidebar) ─────────────────────────────────────────
    let (control_widget, control_handle) = build_control_panel(&settings, ctrl_tx.clone());

    // ── Gallery tabs (Stack of one gallery per media type) ────────────────────
    // We keep four GalleryTab variants and switch them on source tab change.
    let gallery_stack = Stack::new();

    let (video_widget,   video_ref)   = build_gallery(GalleryTab::Video,   card_tx.clone());
    let (animate_widget, animate_ref) = build_gallery(GalleryTab::Animate, card_tx.clone());
    let (image_widget,   image_ref)   = build_gallery(GalleryTab::Image,   card_tx.clone());
    let (artgen_widget,  artgen_ref)  = build_gallery(GalleryTab::Artgen,  card_tx.clone());

    gallery_stack.add_named(&video_widget,   Some("video"));
    gallery_stack.add_named(&animate_widget, Some("animate"));
    gallery_stack.add_named(&image_widget,   Some("image"));
    gallery_stack.add_named(&artgen_widget,  Some("artgen"));
    gallery_stack.set_visible_child_name("video");

    // Stop playback when the gallery tab changes
    {
        let vr = video_ref.clone();
        let ar = animate_ref.clone();
        let ir = image_ref.clone();
        let xr = artgen_ref.clone();
        gallery_stack.connect_visible_child_notify(move |stack| {
            // Pause all, then let the new one auto-play on next hover
            vr.stop_all_playback();
            ar.stop_all_playback();
            ir.stop_all_playback();
            xr.stop_all_playback();
            let _ = stack; // just to avoid "unused variable" without the let binding
        });
    }

    // ── Detail panel (right pane) ─────────────────────────────────────────────
    let detail_stack = Stack::new();
    let placeholder  = build_detail_placeholder();
    detail_stack.add_named(&placeholder, Some("placeholder"));
    detail_stack.set_visible_child_name("placeholder");

    // Keep track of current record list for Prev/Next navigation
    let current_records: std::rc::Rc<std::cell::RefCell<Vec<history::Record>>> =
        std::rc::Rc::new(std::cell::RefCell::new(history::load_history()));
    let current_index: std::rc::Rc<std::cell::Cell<usize>> =
        std::rc::Rc::new(std::cell::Cell::new(0));

    // ── Inner paned: gallery | detail ─────────────────────────────────────────
    let inner_paned = Paned::new(Orientation::Horizontal);
    inner_paned.set_position(640);
    inner_paned.set_hexpand(true);
    inner_paned.set_vexpand(true);
    inner_paned.set_start_child(Some(&gallery_stack));
    inner_paned.set_end_child(Some(&detail_stack));

    // ── Root horizontal paned: controls | gallery+detail ─────────────────────
    let root_paned = Paned::new(Orientation::Horizontal);
    root_paned.set_position(310);
    root_paned.set_shrink_start_child(false);
    root_paned.set_resize_start_child(false);
    root_paned.set_hexpand(true);
    root_paned.set_vexpand(true);
    root_paned.set_start_child(Some(&control_widget));
    root_paned.set_end_child(Some(&inner_paned));

    root_vbox.append(&root_paned);
    root_vbox.append(&statusbar_widget);
    window.set_child(Some(&root_vbox));
    window.present();

    // ── Event pump: health ────────────────────────────────────────────────────
    {
        let sh = statusbar_handle.clone();
        let ch = control_handle.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(1000), move || {
            let mut latest: Option<HealthSnapshot> = None;
            while let Ok(snap) = health_rx.try_recv() { latest = Some(snap); }
            if let Some(snap) = latest {
                sh.update(&snap);
                ch.update_health(&snap);
            }
            glib::ControlFlow::Continue
        });
    }

    // ── Event pump: CardEvent ─────────────────────────────────────────────────
    {
        let detail_stack2  = detail_stack.clone();
        let detail_tx2     = detail_tx.clone();
        let ctrl_prompt    = control_handle.prompt.clone();
        let crecs          = current_records.clone();
        let cidx           = current_index.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
            while let Ok(ev) = card_rx.try_recv() {
                match ev {
                    CardEvent::Selected(id) => {
                        let recs = crecs.borrow();
                        if let Some((idx, rec)) = recs.iter().enumerate().find(|(_, r)| r.id == id) {
                            cidx.set(idx);
                            // Remove old detail child and add new one
                            if let Some(old) = detail_stack2.child_by_name("detail") {
                                detail_stack2.remove(&old);
                            }
                            let has_prev = idx > 0;
                            let has_next = idx + 1 < recs.len();
                            let detail = build_detail(rec, detail_tx2.clone(), has_prev, has_next);
                            detail_stack2.add_named(&detail, Some("detail"));
                            detail_stack2.set_visible_child_name("detail");
                        }
                    }
                    CardEvent::RemixRequested(id) => {
                        let recs = crecs.borrow();
                        if let Some(rec) = recs.iter().find(|r| r.id == id) {
                            ctrl_prompt.set_text(&rec.prompt);
                        }
                    }
                    CardEvent::CopyPrompt(text) => {
                        if let Some(display) = gtk4::gdk::Display::default() {
                            display.clipboard().set_text(&text);
                        }
                    }
                    CardEvent::DeleteRequested(_) | CardEvent::StarToggled(_, _) => {
                        // DB write already done in card.rs; no extra action needed here
                    }
                }
            }
            glib::ControlFlow::Continue
        });
    }

    // ── Event pump: ControlEvent ──────────────────────────────────────────────
    {
        let ch2        = control_handle.clone();
        let wtx        = worker_tx.clone();
        let vr2        = video_ref.clone();
        let ar2        = animate_ref.clone();
        let ir2        = image_ref.clone();
        let xr2        = artgen_ref.clone();
        let gs         = gallery_stack.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
            while let Ok(ev) = ctrl_rx.try_recv() {
                match ev {
                    ControlEvent::Generate(req) => {
                        ch2.set_generating(true);
                        let w = GenerationWorker::new(req, wtx.clone());
                        w.spawn();
                    }
                    ControlEvent::TabChanged(src) => {
                        let tab_name = src.as_media_type();
                        gs.set_visible_child_name(tab_name);
                        // Reload the relevant gallery
                        match tab_name {
                            "video"   => vr2.reload(),
                            "animate" => ar2.reload(),
                            "image"   => ir2.reload(),
                            _         => xr2.reload(),
                        }
                    }
                    ControlEvent::ServerStart(key) => {
                        start_server(&key);
                    }
                    ControlEvent::ServerStop(key) => {
                        stop_server(&key);
                    }
                    ControlEvent::InspireRequested => { /* button already reset */ }
                }
            }
            glib::ControlFlow::Continue
        });
    }

    // ── Event pump: DetailEvent ───────────────────────────────────────────────
    {
        let detail_stack3 = detail_stack.clone();
        let detail_tx3    = detail_tx.clone();
        let crecs3        = current_records.clone();
        let cidx3         = current_index.clone();
        let ctrl_prompt2  = control_handle.prompt.clone();
        let vr3           = video_ref.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
            while let Ok(ev) = detail_rx.try_recv() {
                match ev {
                    DetailEvent::Back => {
                        detail_stack3.set_visible_child_name("placeholder");
                    }
                    DetailEvent::Delete(id) => {
                        delete_record_from_db(&id);
                        detail_stack3.set_visible_child_name("placeholder");
                        vr3.reload();
                    }
                    DetailEvent::NavigatePrev | DetailEvent::NavigateNext => {
                        let recs = crecs3.borrow();
                        let idx  = cidx3.get();
                        let new_idx = match ev {
                            DetailEvent::NavigatePrev => idx.saturating_sub(1),
                            _ => (idx + 1).min(recs.len().saturating_sub(1)),
                        };
                        if new_idx != idx {
                            cidx3.set(new_idx);
                            if let Some(rec) = recs.get(new_idx) {
                                if let Some(old) = detail_stack3.child_by_name("detail") {
                                    detail_stack3.remove(&old);
                                }
                                let has_prev = new_idx > 0;
                                let has_next = new_idx + 1 < recs.len();
                                let d = build_detail(rec, detail_tx3.clone(), has_prev, has_next);
                                detail_stack3.add_named(&d, Some("detail"));
                                detail_stack3.set_visible_child_name("detail");
                            }
                        }
                    }
                    DetailEvent::RemixRequested(id) => {
                        let recs = crecs3.borrow();
                        if let Some(rec) = recs.iter().find(|r| r.id == id) {
                            ctrl_prompt2.set_text(&rec.prompt);
                        }
                    }
                    DetailEvent::OpenExternal(path) => {
                        let uri = format!("file://{path}");
                        let _ = gtk4::gio::AppInfo::launch_default_for_uri(
                            &uri,
                            gtk4::gio::AppLaunchContext::NONE,
                        );
                    }
                    DetailEvent::ExportRequested(_) => { /* TODO: file save dialog */ }
                }
            }
            glib::ControlFlow::Continue
        });
    }

    // ── Event pump: WorkerMsg ─────────────────────────────────────────────────
    {
        let ch3    = control_handle.clone();
        let vr4    = video_ref.clone();
        let ar4    = animate_ref.clone();
        let ir4    = image_ref.clone();
        let crecs4 = current_records.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(200), move || {
            while let Ok(msg) = worker_rx.try_recv() {
                match msg {
                    WorkerMsg::Progress(text) => {
                        eprintln!("[worker] {text}");
                    }
                    WorkerMsg::Finished(rec) => {
                        ch3.set_generating(false);
                        // Reload the gallery for the finished media type
                        match rec.media_type.as_str() {
                            "video" | "skyreels" => vr4.reload(),
                            "animate"            => ar4.reload(),
                            "image"              => ir4.reload(),
                            _                    => {}
                        }
                        // Refresh the in-memory record list
                        *crecs4.borrow_mut() = history::load_history();
                    }
                    WorkerMsg::Error(msg) => {
                        ch3.set_generating(false);
                        eprintln!("[worker error] {msg}");
                    }
                }
            }
            glib::ControlFlow::Continue
        });
    }
}

// ── CSS ───────────────────────────────────────────────────────────────────────

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

// ── Server start/stop helpers ─────────────────────────────────────────────────

fn find_repo_root() -> Option<std::path::PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let mut dir = exe.parent()?;
    for _ in 0..8 {
        if dir.join("tt-ctl").exists() {
            return Some(dir.to_path_buf());
        }
        dir = dir.parent()?;
    }
    // CWD fallback
    let cwd = std::env::current_dir().ok()?;
    let mut d = cwd.as_path();
    for _ in 0..8 {
        if d.join("tt-ctl").exists() {
            return Some(d.to_path_buf());
        }
        d = d.parent()?;
    }
    None
}

fn start_server(key: &str) {
    let key = key.to_string();
    if let Some(root) = find_repo_root() {
        std::thread::spawn(move || {
            let _ = std::process::Command::new(root.join("tt-ctl"))
                .args(["start", &key])
                .stdin(std::process::Stdio::null())
                .spawn();
        });
    }
}

fn stop_server(key: &str) {
    let key = key.to_string();
    if let Some(root) = find_repo_root() {
        std::thread::spawn(move || {
            let _ = std::process::Command::new(root.join("tt-ctl"))
                .args(["stop", &key])
                .stdin(std::process::Stdio::null())
                .spawn();
        });
    }
}

// ── DB delete ─────────────────────────────────────────────────────────────────

fn delete_record_from_db(id: &str) {
    let path = history::media_db_path();
    if let Ok(conn) = rusqlite::Connection::open(&path) {
        let _ = conn.execute("DELETE FROM media WHERE id = ?1", rusqlite::params![id]);
    }
}
