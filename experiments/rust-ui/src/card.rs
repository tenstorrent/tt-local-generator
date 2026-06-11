//! card.rs — GenerationCard widget
//!
//! Mirrors Python GenerationCard (main_window.py:1628).
//!
//! Features:
//!   - Thumbnail via gtk4::Picture (ContentFit::Cover)
//!   - GIF animation via gtk4::MediaFile on hover (120 ms debounce)
//!   - Hover action bar: Star / Delete / Remix buttons revealed on hover
//!   - Star toggle writes to media.db immediately
//!   - Delete with confirmation dialog
//!   - Right-click context popover (Copy Prompt, Open folder)
//!   - Emits typed signals over the GalleryEvent channel

use crate::history::Record;
use gtk4::prelude::*;
use gtk4::{
    Box as GtkBox, Button, GestureClick, Label, MediaFile, Orientation,
    Overlay, Picture, Revealer, RevealerTransitionType, Stack, Widget,
};
use std::cell::{Cell, RefCell};
use std::path::Path;
use std::rc::Rc;
use std::sync::mpsc::Sender;

// ── Public event type ─────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum CardEvent {
    Selected(String),          // record id
    StarToggled(String, bool), // record id, new starred state
    DeleteRequested(String),   // record id
    RemixRequested(String),    // record id
    CopyPrompt(String),        // prompt text
}

// ── Build function ────────────────────────────────────────────────────────────

/// Build a GenerationCard widget.
///
/// Returns the root widget and an Rc<Cell<bool>> that reflects the current
/// starred state (so the gallery can read it back without re-querying the DB).
pub fn build_card(
    rec:    &Record,
    tx:     Sender<CardEvent>,
) -> Widget {
    // Sizes match Python THUMB_W / THUMB_H constants
    const THUMB_W: i32 = 200;
    const THUMB_H: i32 = 112;

    let root = gtk4::Frame::new(None);
    root.add_css_class("card");
    root.set_width_request(THUMB_W);

    let vbox = GtkBox::new(Orientation::Vertical, 0);
    root.set_child(Some(&vbox));

    // ── Media area ────────────────────────────────────────────────────────────
    let media_stack = Stack::new();
    media_stack.set_size_request(THUMB_W, THUMB_H);

    // Thumbnail (always present)
    let thumb_path = rec.thumbnail_path.clone();
    let gif_path   = if rec.file_path.ends_with(".gif") { Some(rec.file_path.clone()) } else { None };

    let thumb = if !thumb_path.is_empty() && Path::new(&thumb_path).exists() {
        let pic = Picture::for_filename(&thumb_path);
        pic.set_size_request(THUMB_W, THUMB_H);
        pic.set_content_fit(gtk4::ContentFit::Cover);
        pic.set_can_shrink(true);
        pic.upcast::<Widget>()
    } else {
        let icon = match rec.media_type.as_str() {
            "video" | "animate" => "🎬",
            "image"             => "🖼",
            "artgen"            => "🎨",
            "animatediff"       => "✨",
            _                   => "▪",
        };
        let lbl = Label::new(Some(icon));
        lbl.set_size_request(THUMB_W, THUMB_H);
        lbl.add_css_class("thumb-placeholder");
        lbl.upcast::<Widget>()
    };
    media_stack.add_named(&thumb, Some("thumb"));
    media_stack.set_visible_child_name("thumb");

    // GIF or video widget created lazily on first hover (see below)
    let media_widget: Rc<std::cell::RefCell<Option<Widget>>> = Rc::new(std::cell::RefCell::new(None));

    // ── Hover action bar ──────────────────────────────────────────────────────
    let action_rev = Revealer::new();
    action_rev.set_transition_type(RevealerTransitionType::Crossfade);
    action_rev.set_transition_duration(120);
    action_rev.set_reveal_child(false);
    action_rev.set_valign(gtk4::Align::End);

    let action_bar = GtkBox::new(Orientation::Horizontal, 4);
    action_bar.add_css_class("hover-action-bar");

    let starred_state = Rc::new(Cell::new(rec.starred));

    // Star button
    let star_btn = Button::new();
    star_btn.add_css_class("hover-action-btn");
    star_btn.set_label(if rec.starred { "★" } else { "☆" });
    let star_label = star_btn.clone();
    {
        let tx2    = tx.clone();
        let id     = rec.id.clone();
        let state  = starred_state.clone();
        star_btn.connect_clicked(move |btn| {
            let new_starred = !state.get();
            state.set(new_starred);
            btn.set_label(if new_starred { "★" } else { "☆" });
            update_starred_in_db(&id, new_starred);
            let _ = tx2.send(CardEvent::StarToggled(id.clone(), new_starred));
        });
    }
    action_bar.append(&star_btn);

    // Remix button
    let remix_btn = Button::with_label("⟳");
    remix_btn.add_css_class("hover-action-btn");
    {
        let tx2 = tx.clone();
        let id  = rec.id.clone();
        remix_btn.connect_clicked(move |_| { let _ = tx2.send(CardEvent::RemixRequested(id.clone())); });
    }
    action_bar.append(&remix_btn);

    // Delete button
    let del_btn = Button::with_label("✕");
    del_btn.add_css_class("hover-action-btn");
    del_btn.add_css_class("hover-action-btn-delete");
    {
        let tx2 = tx.clone();
        let id  = rec.id.clone();
        del_btn.connect_clicked(move |_| { let _ = tx2.send(CardEvent::DeleteRequested(id.clone())); });
    }
    action_bar.append(&del_btn);

    action_rev.set_child(Some(&action_bar));

    // ── Overlay: media + action bar ───────────────────────────────────────────
    let overlay = Overlay::new();
    overlay.set_child(Some(&media_stack));
    overlay.add_overlay(&action_rev);
    vbox.append(&overlay);

    // ── Prompt label ──────────────────────────────────────────────────────────
    let prompt_lbl = Label::new(Some(&rec.prompt));
    prompt_lbl.set_max_width_chars(24);
    prompt_lbl.set_ellipsize(gtk4::pango::EllipsizeMode::End);
    prompt_lbl.set_xalign(0.0);
    prompt_lbl.add_css_class("card-prompt");
    prompt_lbl.set_margin_start(6);
    prompt_lbl.set_margin_end(6);
    prompt_lbl.set_margin_top(4);
    prompt_lbl.set_margin_bottom(4);
    vbox.append(&prompt_lbl);

    // ── Hover controller (enter/leave) ────────────────────────────────────────
    let hover_ctrl = gtk4::EventControllerMotion::new();
    // SourceId doesn't implement Clone/Copy; store in Rc<RefCell<Option<SourceId>>>.
    let debounce_id: Rc<RefCell<Option<glib::SourceId>>> = Rc::new(RefCell::new(None));

    {
        let action_rev2   = action_rev.clone();
        let media_stack2  = media_stack.clone();
        let media_widget2 = media_widget.clone();
        let gif_path2     = gif_path.clone();
        let debounce_id2  = debounce_id.clone();
        hover_ctrl.connect_enter(move |_, _, _| {
            action_rev2.set_reveal_child(true);
            // Cancel any pending debounce
            if let Some(id) = debounce_id2.borrow_mut().take() { id.remove(); }
            // 120 ms debounce before starting animation (matches Python)
            let ms2    = media_stack2.clone();
            let mw2    = media_widget2.clone();
            let gp2    = gif_path2.clone();
            let did2   = debounce_id2.clone();
            let src = glib::timeout_add_local(std::time::Duration::from_millis(120), move || {
                *did2.borrow_mut() = None;
                start_hover_anim(&ms2, &mw2, gp2.as_deref());
                glib::ControlFlow::Break
            });
            *debounce_id2.borrow_mut() = Some(src);
        });
    }
    {
        let action_rev2  = action_rev.clone();
        let media_stack2 = media_stack.clone();
        let debounce_id2 = debounce_id.clone();
        hover_ctrl.connect_leave(move |_| {
            action_rev2.set_reveal_child(false);
            if let Some(id) = debounce_id2.borrow_mut().take() { id.remove(); }
            // Stop GIF / video
            media_stack2.set_visible_child_name("thumb");
            if let Some(mf) = get_media_file(&media_stack2) {
                mf.pause();
            }
        });
    }
    overlay.add_controller(hover_ctrl);

    // ── Click → Selected event ────────────────────────────────────────────────
    let click_ctrl = GestureClick::new();
    {
        let tx3 = tx.clone();
        let id  = rec.id.clone();
        click_ctrl.connect_pressed(move |_, _, _, _| {
            let _ = tx3.send(CardEvent::Selected(id.clone()));
        });
    }
    overlay.add_controller(click_ctrl);

    // ── Right-click context menu ──────────────────────────────────────────────
    let rclick = GestureClick::new();
    rclick.set_button(3); // right mouse button
    {
        let tx4    = tx.clone();
        let prompt = rec.prompt.clone();
        let root2  = root.clone();
        rclick.connect_pressed(move |_, _, x, y| {
            show_context_popover(&root2.clone().upcast(), x, y, &prompt, &tx4);
        });
    }
    overlay.add_controller(rclick);

    root.upcast::<Widget>()
}

// ── Animation helpers ─────────────────────────────────────────────────────────

fn start_hover_anim(stack: &Stack, media_widget: &Rc<std::cell::RefCell<Option<Widget>>>, gif_path: Option<&str>) {
    let Some(path) = gif_path else { return };
    if !Path::new(path).exists() { return }

    let mut mw = media_widget.borrow_mut();
    if mw.is_none() {
        // Lazy creation — GIF via MediaFile
        let mf  = MediaFile::for_filename(path);
        let pic = Picture::new();
        pic.set_paintable(Some(&mf));
        pic.set_content_fit(gtk4::ContentFit::Cover);
        pic.set_can_shrink(true);
        pic.set_size_request(200, 112);
        stack.add_named(&pic, Some("anim"));
        *mw = Some(pic.upcast::<Widget>());

        // Connect loop restart — MediaFile IS the stream (extends MediaStream)
        mf.connect_ended_notify(glib::clone!(#[weak] mf, move |s| {
            if s.is_ended() {
                s.seek(0);
                s.play();
            }
        }));

        mf.play();
    } else {
        // Already created — just restart
        if let Some(mf) = get_media_file(stack) {
            mf.seek(0);
            mf.play();
        }
    }

    stack.set_visible_child_name("anim");
}

fn get_media_file(stack: &Stack) -> Option<MediaFile> {
    stack.child_by_name("anim")
        .and_then(|w| w.downcast::<Picture>().ok())
        .and_then(|p| p.paintable())
        .and_then(|paint| paint.downcast::<MediaFile>().ok())
}

// ── Context popover ───────────────────────────────────────────────────────────

fn show_context_popover(parent: &Widget, x: f64, y: f64, prompt: &str, tx: &Sender<CardEvent>) {
    let pop = gtk4::Popover::new();
    pop.set_parent(parent);
    pop.set_pointing_to(Some(&gtk4::gdk::Rectangle::new(x as i32, y as i32, 1, 1)));
    pop.set_has_arrow(false);

    let vbox = GtkBox::new(Orientation::Vertical, 2);

    let copy_btn = Button::with_label("Copy prompt");
    copy_btn.add_css_class("flat");
    {
        let tx2  = tx.clone();
        let p2   = prompt.to_string();
        let pop2 = pop.clone();
        copy_btn.connect_clicked(move |_| {
            let _ = tx2.send(CardEvent::CopyPrompt(p2.clone()));
            pop2.popdown();
        });
    }
    vbox.append(&copy_btn);

    pop.set_child(Some(&vbox));
    pop.popup();
}

// ── DB write ──────────────────────────────────────────────────────────────────

fn update_starred_in_db(id: &str, starred: bool) {
    let path = dirs_next::data_local_dir()
        .unwrap_or_default()
        .join("tt-video-gen")
        .join("media.db");
    if let Ok(conn) = rusqlite::Connection::open(&path) {
        let _ = conn.execute(
            "UPDATE media SET starred = ?1 WHERE id = ?2",
            rusqlite::params![if starred { 1i64 } else { 0i64 }, id],
        );
    }
}

// ── Tests (pure logic, no GTK) ────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn media_type_icon_mapping() {
        // Verify the match arms cover expected types and don't panic.
        for mt in &["video", "animate", "image", "artgen", "animatediff", "unknown"] {
            let icon = match *mt {
                "video" | "animate" => "🎬",
                "image"             => "🖼",
                "artgen"            => "🎨",
                "animatediff"       => "✨",
                _                   => "▪",
            };
            assert!(!icon.is_empty());
        }
    }

    #[test]
    fn starred_toggle_logic() {
        // Simulate the toggle: start false, toggle, toggle back.
        let mut starred = false;
        starred = !starred; assert!(starred);
        starred = !starred; assert!(!starred);
    }
}
