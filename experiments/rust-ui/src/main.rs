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
    Application, ApplicationWindow, Box as GtkBox, CssProvider, Image, Orientation, Paned,
    PopoverMenuBar, Stack,
};
use gio::{ApplicationFlags, SimpleAction};

mod card;
mod control;
mod detail;
mod gallery;
mod health;
mod history;
mod prefs;
mod servers;
mod settings;
mod statusbar;
mod worker;

use card::CardEvent;
use control::{build_control_panel, ControlEvent};
use detail::{build_detail, build_detail_placeholder, DetailEvent};
use gallery::{build_gallery, GalleryTab};
use health::HealthSnapshot;
use servers::build_servers_button;
use statusbar::build_statusbar;
use worker::{GenerationWorker, WorkerMsg};

const APP_ID: &str = "ai.tenstorrent.tt-video-gen-rs";

/// Tenstorrent colour palette + Rust-UI specific widget styles.
const CSS: &str = r#"
window {
    background-color: #0F2A35;
    color: #E8F0F2;
}
/* ── Toolbar ── */
.tt-toolbar {
    background-color: #0d2330;
    padding: 4px 8px;
    border-bottom: 1px solid #1A3C47;
}
.tt-toolbar-title {
    font-weight: bold;
    font-size: 13px;
    color: #E8F0F2;
    margin-start: 4px;
}
/* ── Menu bar ── */
menubar {
    background-color: #112030;
    color: #B0C4DE;
    font-size: 12px;
    padding: 1px 4px;
    border-bottom: 1px solid #1A3C47;
}
menubar > item { padding: 2px 8px; border-radius: 4px; }
menubar > item:hover { background-color: #1A3C47; color: #E8F0F2; }
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
/* ── Servers menu button (toolbar) ── */
.servers-menu-btn {
    background-color: #1A3C47;
    color: #B0C4DE;
    border: 1px solid #2D4F5C;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 12px;
}
.servers-menu-btn:hover { background-color: #2D4F5C; }
/* ── Servers popover ── */
.servers-cap-header { font-weight: bold; font-size: 11px; color: #607D8B; margin-top: 4px; }
.servers-popover-key { font-size: 12px; color: #E8F0F2; }
.servers-popover-row { padding: 2px 0; }
.servers-popover-dot-on  { color: #4FD1C5; }
.servers-popover-dot-off { color: #607D8B; }
.servers-popover-btn {
    min-width: 28px; min-height: 24px;
    padding: 0 6px;
    font-size: 11px;
    background-color: #1A3C47;
    color: #B0C4DE;
    border: 1px solid #2D4F5C;
    border-radius: 4px;
}
.servers-popover-btn:hover { background-color: #2D4F5C; }
.servers-popover-btn-stop:hover { background-color: #3D1A1A; color: #FF6B6B; }
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
/* ── Inspire button + dot ── */
.inspire-btn {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D4F5C;
    border-radius: 6px;
    padding: 3px 12px;
    font-size: 12px;
}
.inspire-btn:hover { background-color: #2D4F5C; }
.inspire-btn-loading { color: #607D8B; font-size: 12px; }
.inspire-dot       { font-size: 10px; color: #607D8B; }
.inspire-dot-ready { font-size: 10px; color: #4FD1C5; }
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
/* ── Preferences dialog ── */
.prefs-section-header { font-weight: bold; color: #4FD1C5; font-size: 12px; }
/* ── Utility ── */
.muted   { color: #607D8B; }
.starred { color: #F4C471; font-size: 13px; }
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
    let settings = std::rc::Rc::new(std::cell::RefCell::new(settings::load()));

    // ── Health bus ────────────────────────────────────────────────────────────
    let (health_tx, health_rx)   = std::sync::mpsc::channel::<HealthSnapshot>();
    let (pg_health_tx, pg_health_rx) = std::sync::mpsc::channel::<bool>();
    health::start_bus(health_tx);
    start_prompt_gen_health_poll(pg_health_tx);

    // ── Shared health snapshot (read by servers popover) ─────────────────────
    let snap_rc: std::rc::Rc<std::cell::RefCell<HealthSnapshot>> =
        std::rc::Rc::new(std::cell::RefCell::new(HealthSnapshot::default()));

    // ── Channels — each Receiver has exactly one consumer (move into pump) ───
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

    // ── Root chrome: [toolbar | menubar | content_paned | statusbar] ─────────
    let root_vbox = GtkBox::new(Orientation::Vertical, 0);

    // ── Toolbar ───────────────────────────────────────────────────────────────
    let toolbar = build_toolbar(ctrl_tx.clone(), snap_rc.clone());
    root_vbox.append(&toolbar);

    // ── Menu bar (File / View / Debug) ────────────────────────────────────────
    let menu_bar = build_menubar(app, &window, ctrl_tx.clone(), settings.clone());
    root_vbox.append(&menu_bar);

    // ── Status bar ────────────────────────────────────────────────────────────
    let (statusbar_widget, statusbar_handle) = build_statusbar();

    // ── Control panel (left sidebar) ─────────────────────────────────────────
    let cur_settings = settings.borrow().clone();
    let (control_widget, control_handle) = build_control_panel(&cur_settings, ctrl_tx.clone());

    // ── Gallery tabs ──────────────────────────────────────────────────────────
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
    {
        let vr = video_ref.clone();
        let ar = animate_ref.clone();
        let ir = image_ref.clone();
        let xr = artgen_ref.clone();
        gallery_stack.connect_visible_child_notify(move |_| {
            vr.stop_all_playback();
            ar.stop_all_playback();
            ir.stop_all_playback();
            xr.stop_all_playback();
        });
    }

    // ── Detail panel ─────────────────────────────────────────────────────────
    let detail_stack = Stack::new();
    let placeholder  = build_detail_placeholder();
    detail_stack.add_named(&placeholder, Some("placeholder"));
    detail_stack.set_visible_child_name("placeholder");
    let current_records: std::rc::Rc<std::cell::RefCell<Vec<history::Record>>> =
        std::rc::Rc::new(std::cell::RefCell::new(history::load_history()));
    let current_index: std::rc::Rc<std::cell::Cell<usize>> =
        std::rc::Rc::new(std::cell::Cell::new(0));

    // ── Inner paned: gallery | detail ────────────────────────────────────────
    let inner_paned = Paned::new(Orientation::Horizontal);
    inner_paned.set_position(640);
    inner_paned.set_hexpand(true);
    inner_paned.set_vexpand(true);
    inner_paned.set_start_child(Some(&gallery_stack));
    inner_paned.set_end_child(Some(&detail_stack));

    // ── Root paned: controls | gallery+detail ────────────────────────────────
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
        let sh       = statusbar_handle.clone();
        let ch       = control_handle.clone();
        let snap_rc2 = snap_rc.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(1000), move || {
            let mut latest: Option<HealthSnapshot> = None;
            while let Ok(snap) = health_rx.try_recv() { latest = Some(snap); }
            if let Some(snap) = latest {
                sh.update(&snap);
                ch.update_health(&snap);
                *snap_rc2.borrow_mut() = snap;
            }
            glib::ControlFlow::Continue
        });
    }

    // ── Event pump: prompt-gen health ────────────────────────────────────────
    {
        let ch_pg = control_handle.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(500), move || {
            let mut latest: Option<bool> = None;
            while let Ok(ready) = pg_health_rx.try_recv() { latest = Some(ready); }
            if let Some(ready) = latest {
                ch_pg.set_prompt_gen_state(ready);
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
                    CardEvent::DeleteRequested(_) | CardEvent::StarToggled(_, _) => {}
                }
            }
            glib::ControlFlow::Continue
        });
    }

    // ── Event pump: ControlEvent ──────────────────────────────────────────────
    {
        let ch2         = control_handle.clone();
        let wtx         = worker_tx.clone();
        let vr2         = video_ref.clone();
        let ar2         = animate_ref.clone();
        let ir2         = image_ref.clone();
        let xr2         = artgen_ref.clone();
        let gs          = gallery_stack.clone();
        let ds          = detail_stack.clone();
        let settings_rc = settings.clone();
        let win2        = window.clone();
        let ctrl_tx2    = ctrl_tx.clone();
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
                        match tab_name {
                            "video"   => vr2.reload(),
                            "animate" => ar2.reload(),
                            "image"   => ir2.reload(),
                            _         => xr2.reload(),
                        }
                    }
                    ControlEvent::ServerStart(key)    => start_server(&key),
                    ControlEvent::ServerStop(key)     => stop_server(&key),
                    ControlEvent::ServerRestart(key)  => restart_server(&key),
                    ControlEvent::InspireRequested    => {}
                    ControlEvent::PromptGenHealthChanged(ready) => {
                        ch2.set_prompt_gen_state(ready);
                    }
                    ControlEvent::OpenMediaFolder => {
                        let data = history::data_dir();
                        let uri  = format!("file://{}", data.display());
                        let _ = gtk4::gio::AppInfo::launch_default_for_uri(
                            &uri, gtk4::gio::AppLaunchContext::NONE,
                        );
                    }
                    ControlEvent::OpenLogViewer => {
                        // Log viewer is a future feature; open /tmp for now
                        let _ = gtk4::gio::AppInfo::launch_default_for_uri(
                            "file:///tmp", gtk4::gio::AppLaunchContext::NONE,
                        );
                    }
                    ControlEvent::ToggleDetailPanel => {
                        // Toggle visibility of the detail pane
                        let visible = ds.is_visible();
                        ds.set_visible(!visible);
                    }
                    ControlEvent::SetGalleryDensity(density) => {
                        settings_rc.borrow_mut().gallery_density = density.clone();
                        let _ = settings::save(&settings_rc.borrow());
                        // Trigger gallery reload so cards respect new density
                        vr2.reload();
                        ar2.reload();
                        ir2.reload();
                        xr2.reload();
                    }
                    ControlEvent::PrefsChanged(new_settings) => {
                        *settings_rc.borrow_mut() = *new_settings;
                        let _ = settings::save(&settings_rc.borrow());
                        let _ = win2; // keep win2 alive in this closure
                        let _ = ctrl_tx2; // keep ctrl_tx2 alive
                    }
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
                        let recs    = crecs3.borrow();
                        let idx     = cidx3.get();
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
                            &uri, gtk4::gio::AppLaunchContext::NONE,
                        );
                    }
                    DetailEvent::ExportRequested(_) => {}
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
                        match rec.media_type.as_str() {
                            "video" | "skyreels" => vr4.reload(),
                            "animate"            => ar4.reload(),
                            "image"              => ir4.reload(),
                            _                    => {}
                        }
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

// ── Toolbar ───────────────────────────────────────────────────────────────────

fn build_toolbar(
    ctrl_tx: std::sync::mpsc::Sender<ControlEvent>,
    snap_rc: std::rc::Rc<std::cell::RefCell<HealthSnapshot>>,
) -> gtk4::Widget {
    let bar = GtkBox::new(Orientation::Horizontal, 6);
    bar.add_css_class("tt-toolbar");

    // Tenstorrent logo — look for it relative to the repo
    let logo_path = health::find_tt_ctl()
        .and_then(|p| p.parent().map(|d| d.join("app").join("assets").join("tenstorrent.png")));
    if let Some(path) = logo_path.filter(|p| p.exists()) {
        let img = Image::from_file(&path);
        img.set_pixel_size(24);
        bar.append(&img);
    }

    let title = gtk4::Label::new(Some("TT Local Generator"));
    title.add_css_class("tt-toolbar-title");
    bar.append(&title);

    // Flexible spacer
    let spacer = GtkBox::new(Orientation::Horizontal, 0);
    spacer.set_hexpand(true);
    bar.append(&spacer);

    // Servers ▾ button
    let srv_btn = build_servers_button(ctrl_tx, snap_rc);
    bar.append(&srv_btn);

    bar.upcast::<gtk4::Widget>()
}

// ── PopoverMenuBar (File / View / Debug) ──────────────────────────────────────

fn build_menubar(
    app:      &Application,
    window:   &ApplicationWindow,
    ctrl_tx:  std::sync::mpsc::Sender<ControlEvent>,
    settings: std::rc::Rc<std::cell::RefCell<crate::settings::Settings>>,
) -> gtk4::Widget {
    // ── Build the gio::Menu model ─────────────────────────────────────────────
    let menu_model = gio::Menu::new();

    // File menu
    let file_menu = gio::Menu::new();
    file_menu.append(Some("Open Media Folder"), Some("win.open-media-folder"));
    file_menu.append(Some("Preferences…"),      Some("win.preferences"));
    file_menu.append(Some("Quit"),              Some("app.quit"));
    menu_model.append_submenu(Some("File"), &file_menu);

    // View menu
    let view_menu = gio::Menu::new();
    view_menu.append(Some("Toggle Detail Panel"), Some("win.toggle-detail"));
    let density_section = gio::Menu::new();
    density_section.append(Some("Comfortable"),   Some("win.density-comfortable"));
    density_section.append(Some("Compact"),       Some("win.density-compact"));
    view_menu.append_section(Some("Gallery Density"), &density_section);
    menu_model.append_submenu(Some("View"), &view_menu);

    // Debug menu
    let debug_menu = gio::Menu::new();
    debug_menu.append(Some("Open Log Viewer"), Some("win.open-log-viewer"));
    menu_model.append_submenu(Some("Debug"), &debug_menu);

    // ── Register actions on the ApplicationWindow ─────────────────────────────
    {
        let tx = ctrl_tx.clone();
        let act = SimpleAction::new("open-media-folder", None);
        act.connect_activate(move |_, _| { let _ = tx.send(ControlEvent::OpenMediaFolder); });
        window.add_action(&act);
    }
    {
        let tx   = ctrl_tx.clone();
        let win2 = window.clone();
        let sett = settings.clone();
        let act  = SimpleAction::new("preferences", None);
        act.connect_activate(move |_, _| {
            prefs::show_preferences(&win2, &sett.borrow(), tx.clone());
        });
        window.add_action(&act);
    }
    {
        let tx = ctrl_tx.clone();
        let act = SimpleAction::new("toggle-detail", None);
        act.connect_activate(move |_, _| { let _ = tx.send(ControlEvent::ToggleDetailPanel); });
        window.add_action(&act);
    }
    {
        let tx = ctrl_tx.clone();
        let act = SimpleAction::new("density-comfortable", None);
        act.connect_activate(move |_, _| {
            let _ = tx.send(ControlEvent::SetGalleryDensity("comfortable".into()));
        });
        window.add_action(&act);
    }
    {
        let tx = ctrl_tx.clone();
        let act = SimpleAction::new("density-compact", None);
        act.connect_activate(move |_, _| {
            let _ = tx.send(ControlEvent::SetGalleryDensity("compact".into()));
        });
        window.add_action(&act);
    }
    {
        let tx = ctrl_tx.clone();
        let act = SimpleAction::new("open-log-viewer", None);
        act.connect_activate(move |_, _| { let _ = tx.send(ControlEvent::OpenLogViewer); });
        window.add_action(&act);
    }
    // app.quit — standard action on the Application
    {
        let app2 = app.clone();
        let act  = SimpleAction::new("quit", None);
        act.connect_activate(move |_, _| app2.quit());
        app.add_action(&act);
        app.set_accels_for_action("app.quit", &["<Primary>q"]);
    }

    // Keyboard shortcut for Preferences
    app.set_accels_for_action("win.preferences", &["<Primary>comma"]);
    // Toggle detail panel
    app.set_accels_for_action("win.toggle-detail", &["<Primary>d"]);

    PopoverMenuBar::from_model(Some(&menu_model)).upcast::<gtk4::Widget>()
}

// ── Prompt-gen health poll (port 8001) ────────────────────────────────────────

/// Polls `http://localhost:8001/health` every 5 s on a background thread,
/// then sends `true`/`false` over the channel so the main thread can update
/// the ⬤ dot in the control panel's inspire row.
fn start_prompt_gen_health_poll(tx: std::sync::mpsc::Sender<bool>) {
    std::thread::spawn(move || {
        loop {
            let ready = check_prompt_gen_health();
            let _ = tx.send(ready);
            std::thread::sleep(std::time::Duration::from_secs(5));
        }
    });
}

fn check_prompt_gen_health() -> bool {
    let Ok(mut stream) = std::net::TcpStream::connect_timeout(
        &"127.0.0.1:8001".parse().unwrap(),
        std::time::Duration::from_millis(800),
    ) else { return false; };
    use std::io::{Read, Write};
    let req = b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n";
    if stream.write_all(req).is_err() { return false; }
    let mut buf = vec![0u8; 512];
    let n = stream.read(&mut buf).unwrap_or(0);
    let resp = String::from_utf8_lossy(&buf[..n]);
    // Consider healthy if HTTP 200 and body contains `"model_ready":true`
    resp.contains("200 OK") && resp.contains("\"model_ready\":true")
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

fn restart_server(key: &str) {
    let key = key.to_string();
    if let Some(root) = find_repo_root() {
        std::thread::spawn(move || {
            let _ = std::process::Command::new(root.join("tt-ctl"))
                .args(["restart", &key])
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
