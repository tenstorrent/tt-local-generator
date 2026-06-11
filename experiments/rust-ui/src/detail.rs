//! detail.rs — DetailPanel widget
//!
//! Mirrors Python DetailPanel (main_window.py:2242).
//!
//! Shows a selected record in full detail:
//!   - Video player (gtk4::Video) or image viewer (gtk4::Picture)
//!   - Prompt + metadata sidebar
//!   - Copy Prompt / Export / Delete / Open externally buttons
//!   - Prev/Next navigation (calls back through DetailEvent)

use crate::history::Record;
use gtk4::prelude::*;
use gtk4::{
    Box as GtkBox, Button, Label, Orientation, Picture, ScrolledWindow, Video,
    Widget, Window,
};
use std::sync::mpsc::Sender;

// ── Events ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum DetailEvent {
    Back,
    Delete(String),       // record id
    ExportRequested(String), // record id
    OpenExternal(String), // file path
    NavigatePrev,
    NavigateNext,
    RemixRequested(String),
}

// ── Build function ────────────────────────────────────────────────────────────

/// Build the detail panel for a given record.
pub fn build_detail(
    rec:        &Record,
    tx:         Sender<DetailEvent>,
    has_prev:   bool,
    has_next:   bool,
) -> Widget {
    let outer = GtkBox::new(Orientation::Vertical, 0);
    outer.set_width_request(320);
    outer.set_hexpand(true);
    outer.set_vexpand(true);

    // ── Top bar: Back + Prev/Next nav ─────────────────────────────────────────
    let nav_bar = GtkBox::new(Orientation::Horizontal, 4);
    nav_bar.add_css_class("detail-nav-bar");
    nav_bar.set_margin_top(6);
    nav_bar.set_margin_start(8);
    nav_bar.set_margin_end(8);

    let back_btn = Button::with_label("← Gallery");
    back_btn.add_css_class("flat");
    {
        let tx2 = tx.clone();
        back_btn.connect_clicked(move |_| { let _ = tx2.send(DetailEvent::Back); });
    }
    nav_bar.append(&back_btn);

    let spacer = Label::new(None);
    spacer.set_hexpand(true);
    nav_bar.append(&spacer);

    if has_prev {
        let prev = Button::with_label("‹");
        prev.add_css_class("flat");
        let tx2 = tx.clone();
        prev.connect_clicked(move |_| { let _ = tx2.send(DetailEvent::NavigatePrev); });
        nav_bar.append(&prev);
    }
    if has_next {
        let next = Button::with_label("›");
        next.add_css_class("flat");
        let tx2 = tx.clone();
        next.connect_clicked(move |_| { let _ = tx2.send(DetailEvent::NavigateNext); });
        nav_bar.append(&next);
    }
    outer.append(&nav_bar);

    // ── Media area ────────────────────────────────────────────────────────────
    let is_video = matches!(rec.media_type.as_str(), "video" | "animate" | "animatediff")
                   && !rec.file_path.ends_with(".gif");
    let is_gif   = rec.file_path.ends_with(".gif");
    let is_image = rec.media_type == "image"
                   || rec.file_path.ends_with(".png")
                   || rec.file_path.ends_with(".jpg")
                   || rec.file_path.ends_with(".svg");

    if is_video && std::path::Path::new(&rec.file_path).exists() {
        let video = Video::for_filename(Some(&rec.file_path));
        video.set_autoplay(true);
        video.set_loop(true);
        video.set_hexpand(true);
        video.set_size_request(-1, 280);
        outer.append(&video);
    } else if (is_gif || is_image) && std::path::Path::new(&rec.file_path).exists() {
        let mf  = gtk4::MediaFile::for_filename(&rec.file_path);
        let pic = if is_gif {
            let p = Picture::new();
            p.set_paintable(Some(&mf));
            mf.play();
            p
        } else {
            Picture::for_filename(&rec.file_path)
        };
        pic.set_content_fit(gtk4::ContentFit::Contain);
        pic.set_can_shrink(true);
        pic.set_hexpand(true);
        pic.set_size_request(-1, 280);
        outer.append(&pic);
    } else if !rec.thumbnail_path.is_empty() && std::path::Path::new(&rec.thumbnail_path).exists() {
        let pic = Picture::for_filename(&rec.thumbnail_path);
        pic.set_content_fit(gtk4::ContentFit::Contain);
        pic.set_can_shrink(true);
        pic.set_hexpand(true);
        pic.set_size_request(-1, 280);
        outer.append(&pic);
    } else {
        let placeholder = Label::new(Some(match rec.media_type.as_str() {
            "video" | "animate" => "🎬",
            "image"             => "🖼",
            "artgen"            => "🎨",
            _                   => "▪",
        }));
        placeholder.add_css_class("detail-placeholder");
        placeholder.set_size_request(-1, 180);
        outer.append(&placeholder);
    }

    // ── Metadata scroll area ──────────────────────────────────────────────────
    let meta_scroll = ScrolledWindow::new();
    meta_scroll.set_vexpand(true);
    meta_scroll.set_propagate_natural_height(true);

    let meta_box = GtkBox::new(Orientation::Vertical, 6);
    meta_box.set_margin_start(12);
    meta_box.set_margin_end(12);
    meta_box.set_margin_top(8);
    meta_box.set_margin_bottom(8);

    // Prompt
    let prompt_lbl = Label::new(Some(&rec.prompt));
    prompt_lbl.set_wrap(true);
    prompt_lbl.set_wrap_mode(gtk4::pango::WrapMode::WordChar);
    prompt_lbl.set_xalign(0.0);
    prompt_lbl.add_css_class("detail-prompt");
    meta_box.append(&prompt_lbl);

    // Metadata grid
    let grid = gtk4::Grid::new();
    grid.set_row_spacing(4);
    grid.set_column_spacing(12);
    grid.set_margin_top(8);

    let meta_rows: &[(&str, &str)] = &[
        ("Type",    &rec.media_type),
        ("Created", &format_date(&rec.created_at)),
        ("Model",   &rec.model_id),
    ];

    for (i, (key, val)) in meta_rows.iter().enumerate() {
        let k = Label::new(Some(key));
        k.set_xalign(1.0);
        k.add_css_class("muted");
        let v = Label::new(Some(val));
        v.set_xalign(0.0);
        v.set_selectable(true);
        grid.attach(&k, 0, i as i32, 1, 1);
        grid.attach(&v, 1, i as i32, 1, 1);
    }
    meta_box.append(&grid);

    // File path (selectable, small)
    if !rec.file_path.is_empty() {
        let path_lbl = Label::new(Some(&rec.file_path));
        path_lbl.set_ellipsize(gtk4::pango::EllipsizeMode::Middle);
        path_lbl.set_xalign(0.0);
        path_lbl.add_css_class("muted");
        path_lbl.set_selectable(true);
        path_lbl.set_margin_top(4);
        meta_box.append(&path_lbl);
    }

    meta_scroll.set_child(Some(&meta_box));
    outer.append(&meta_scroll);

    // ── Action buttons ────────────────────────────────────────────────────────
    let btn_row = GtkBox::new(Orientation::Horizontal, 6);
    btn_row.set_margin_start(12);
    btn_row.set_margin_end(12);
    btn_row.set_margin_top(8);
    btn_row.set_margin_bottom(12);

    // Copy Prompt
    let copy_btn = Button::with_label("Copy Prompt");
    copy_btn.add_css_class("flat");
    {
        let prompt = rec.prompt.clone();
        copy_btn.connect_clicked(move |btn| {
            if let Some(display) = gtk4::gdk::Display::default() {
                display.clipboard().set_text(&prompt);
            }
            btn.set_label("Copied ✓");
            let btn2 = btn.clone();
            glib::timeout_add_local(std::time::Duration::from_secs(2), move || {
                btn2.set_label("Copy Prompt");
                glib::ControlFlow::Break
            });
        });
    }
    btn_row.append(&copy_btn);

    // Remix
    let remix_btn = Button::with_label("⟳ Remix");
    remix_btn.add_css_class("flat");
    {
        let tx2 = tx.clone();
        let id  = rec.id.clone();
        remix_btn.connect_clicked(move |_| { let _ = tx2.send(DetailEvent::RemixRequested(id.clone())); });
    }
    btn_row.append(&remix_btn);

    // Open externally
    let open_btn = Button::with_label("⤴ Open");
    open_btn.add_css_class("flat");
    {
        let tx2  = tx.clone();
        let path = rec.file_path.clone();
        open_btn.connect_clicked(move |_| { let _ = tx2.send(DetailEvent::OpenExternal(path.clone())); });
    }
    btn_row.append(&open_btn);

    // Delete
    let del_btn = Button::with_label("Delete");
    del_btn.add_css_class("destructive-action");
    {
        let tx2 = tx.clone();
        let id  = rec.id.clone();
        del_btn.connect_clicked(move |_| { let _ = tx2.send(DetailEvent::Delete(id.clone())); });
    }
    btn_row.append(&del_btn);

    outer.append(&btn_row);

    outer.upcast::<Widget>()
}

/// Empty placeholder shown before any card is selected.
pub fn build_detail_placeholder() -> Widget {
    let b = GtkBox::new(Orientation::Vertical, 0);
    b.set_width_request(300);
    let lbl = Label::new(Some("Select a card to see details"));
    lbl.add_css_class("muted");
    lbl.set_vexpand(true);
    lbl.set_valign(gtk4::Align::Center);
    b.append(&lbl);
    b.upcast::<Widget>()
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn format_date(iso: &str) -> String {
    // "2026-06-09T19:35:14.532541+00:00" → "2026-06-09 19:35"
    iso.get(..16)
       .map(|s| s.replace('T', " "))
       .unwrap_or_else(|| iso.to_string())
}

/// Open a file with the system default application.
pub fn open_externally(path: &str, window: &impl IsA<Window>) {
    let uri = format!("file://{path}");
    let _ = gtk4::gio::AppInfo::launch_default_for_uri(
        &uri,
        Some(&gtk4::gio::AppLaunchContext::new()),
    );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn format_date_full_iso() {
        assert_eq!(
            format_date("2026-06-09T19:35:14.532541+00:00"),
            "2026-06-09 19:35"
        );
    }

    #[test]
    fn format_date_short() {
        assert_eq!(format_date("2026-06-09"), "2026-06-09");
    }

    #[test]
    fn format_date_empty() {
        assert_eq!(format_date(""), "");
    }

    #[test]
    fn is_video_logic() {
        let video_types = &["video", "animate", "animatediff"];
        for mt in video_types {
            let is_video = matches!(*mt, "video" | "animate" | "animatediff");
            assert!(is_video, "{mt} should be video type");
        }
        assert!(!matches!("image", "video" | "animate" | "animatediff"));
    }
}
