//! gallery.rs — GalleryWidget
//!
//! Mirrors Python GalleryWidget (main_window.py:3381).
//!
//! A scrolled FlowBox of GenerationCards, with:
//!   - Tab filtering by media_type
//!   - 48-card paging with prev/next nav bar
//!   - stop_all_playback() — called when this tab becomes hidden
//!   - reload_from_db() — called after a generation completes

use crate::card::{build_card, CardEvent};
use crate::history::{load_history, Record};
use gtk4::prelude::*;
use gtk4::{
    Box as GtkBox, Button, FlowBox, Label, Orientation, ScrolledWindow,
    SelectionMode, Widget,
};
use std::sync::mpsc::Sender;

const PAGE_SIZE: usize = 48;

// ── Public type ───────────────────────────────────────────────────────────────

/// Active tab / media_type filter.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum GalleryTab {
    #[default]
    Video,
    Animate,
    Image,
    Artgen,
}

impl GalleryTab {
    pub fn media_types(&self) -> &[&'static str] {
        match self {
            GalleryTab::Video   => &["video"],
            GalleryTab::Animate => &["animate", "animatediff"],
            GalleryTab::Image   => &["image"],
            GalleryTab::Artgen  => &["artgen"],
        }
    }
}

// ── Build function ────────────────────────────────────────────────────────────

/// Build the gallery area: scrolled FlowBox + pager bar.
///
/// Returns the container widget and a `Refresher` handle that the caller
/// can use to reload cards or switch tabs.
pub fn build_gallery(
    tab:    GalleryTab,
    tx:     Sender<CardEvent>,
) -> (Widget, Refresher) {
    let outer = GtkBox::new(Orientation::Vertical, 0);
    outer.set_hexpand(true);
    outer.set_vexpand(true);

    // Scrolled flow box
    let scroll = ScrolledWindow::new();
    scroll.set_hexpand(true);
    scroll.set_vexpand(true);

    let flow = FlowBox::new();
    flow.set_selection_mode(SelectionMode::None);
    flow.set_homogeneous(false);
    flow.set_min_children_per_line(2);
    flow.set_max_children_per_line(12);
    flow.set_column_spacing(8);
    flow.set_row_spacing(8);
    flow.set_margin_top(8);
    flow.set_margin_bottom(8);
    flow.set_margin_start(8);
    flow.set_margin_end(8);
    scroll.set_child(Some(&flow));
    outer.append(&scroll);

    // Pager bar (hidden until needed)
    let pager = GtkBox::new(Orientation::Horizontal, 8);
    pager.add_css_class("pager-bar");
    pager.set_halign(gtk4::Align::Center);
    pager.set_margin_top(4);
    pager.set_margin_bottom(4);
    outer.append(&pager);

    let page_lbl = Label::new(Some(""));
    page_lbl.add_css_class("muted");

    let refresher = Refresher {
        flow:     flow.clone(),
        pager:    pager.clone(),
        page_lbl: page_lbl.clone(),
        tab:      std::rc::Rc::new(std::cell::RefCell::new(tab)),
        page:     std::rc::Rc::new(std::cell::Cell::new(0usize)),
        tx,
    };

    // Populate initial cards
    refresher.reload();

    (outer.upcast::<Widget>(), refresher)
}

// ── Refresher handle ──────────────────────────────────────────────────────────

#[derive(Clone)]
pub struct Refresher {
    flow:     FlowBox,
    pager:    GtkBox,
    page_lbl: Label,
    tab:      std::rc::Rc<std::cell::RefCell<GalleryTab>>,
    page:     std::rc::Rc<std::cell::Cell<usize>>,
    tx:       Sender<CardEvent>,
}

impl Refresher {
    pub fn set_tab(&self, tab: GalleryTab) {
        *self.tab.borrow_mut() = tab;
        self.page.set(0);
        self.reload();
    }

    pub fn reload(&self) {
        let records = load_history();
        let tab     = self.tab.borrow();
        let types   = tab.media_types();

        let filtered: Vec<&Record> = records.iter()
            .filter(|r| types.contains(&r.media_type.as_str()))
            .collect();

        let total_pages = if filtered.is_empty() { 0 } else { (filtered.len() + PAGE_SIZE - 1) / PAGE_SIZE };
        let page        = self.page.get().min(total_pages.saturating_sub(1));
        self.page.set(page);

        let start = page * PAGE_SIZE;
        let slice = &filtered[start..(start + PAGE_SIZE).min(filtered.len())];

        // Clear existing cards
        while let Some(child) = self.flow.first_child() {
            self.flow.remove(&child);
        }

        if slice.is_empty() {
            let lbl = Label::new(Some("No generations yet — enter a prompt and click ▶ Generate"));
            lbl.add_css_class("muted");
            lbl.set_margin_top(32);
            self.flow.append(&lbl);
        } else {
            for rec in slice {
                let card = build_card(rec, self.tx.clone());
                self.flow.append(&card);
            }
        }

        // Update pager
        while let Some(c) = self.pager.first_child() { self.pager.remove(&c); }

        if total_pages > 1 {
            let prev = Button::with_label("‹");
            prev.add_css_class("flat");
            {
                let r = self.clone();
                prev.connect_clicked(move |_| {
                    let p = r.page.get();
                    if p > 0 { r.page.set(p - 1); r.reload(); }
                });
            }
            self.pager.append(&prev);

            self.page_lbl.set_label(&format!("{} / {}", page + 1, total_pages));
            self.pager.append(&self.page_lbl);

            let next = Button::with_label("›");
            next.add_css_class("flat");
            {
                let r = self.clone();
                next.connect_clicked(move |_| {
                    let p = r.page.get();
                    if p + 1 < total_pages { r.page.set(p + 1); r.reload(); }
                });
            }
            self.pager.append(&next);
            self.pager.set_visible(true);
        } else {
            self.pager.set_visible(false);
        }
    }

    /// Stop all GIF / video animations in this gallery.
    /// Called when the tab becomes hidden (notify::visible-child).
    pub fn stop_all_playback(&self) {
        let mut child = self.flow.first_child();
        while let Some(w) = child {
            // Reset any card that has an "anim" stack child playing
            if let Some(card_frame) = w.downcast_ref::<gtk4::Frame>() {
                stop_card_playback(card_frame);
            }
            child = w.next_sibling();
        }
    }
}

fn stop_card_playback(frame: &gtk4::Frame) {
    // Walk: Frame > Box > Overlay > Stack — find the Stack and switch to "thumb"
    let Some(vbox) = frame.child().and_then(|w| w.downcast::<GtkBox>().ok()) else { return };
    let Some(overlay) = vbox.first_child().and_then(|w| w.downcast::<gtk4::Overlay>().ok()) else { return };
    let Some(stack)   = overlay.child().and_then(|w| w.downcast::<gtk4::Stack>().ok()) else { return };
    stack.set_visible_child_name("thumb");
    // Pause any MediaFile
    if let Some(pic)  = stack.child_by_name("anim").and_then(|w| w.downcast::<gtk4::Picture>().ok()) {
        if let Some(mf) = pic.paintable().and_then(|p| p.downcast::<gtk4::MediaFile>().ok()) {
            mf.pause();
        }
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gallery_tab_media_types() {
        assert!(GalleryTab::Video.media_types().contains(&"video"));
        assert!(GalleryTab::Animate.media_types().contains(&"animate"));
        assert!(GalleryTab::Animate.media_types().contains(&"animatediff"));
        assert!(GalleryTab::Image.media_types().contains(&"image"));
        assert!(GalleryTab::Artgen.media_types().contains(&"artgen"));
    }

    #[test]
    fn gallery_tab_no_cross_contamination() {
        // Video tab should not include artgen records
        assert!(!GalleryTab::Video.media_types().contains(&"artgen"));
        assert!(!GalleryTab::Image.media_types().contains(&"video"));
    }

    #[test]
    fn page_size_constant() {
        assert_eq!(PAGE_SIZE, 48);
    }

    #[test]
    fn pager_logic() {
        // Simulate the total_pages calculation
        let count = 100usize;
        let total_pages = (count + PAGE_SIZE - 1) / PAGE_SIZE;
        assert_eq!(total_pages, 3); // 48 + 48 + 4

        let count2 = 48usize;
        let total_pages2 = (count2 + PAGE_SIZE - 1) / PAGE_SIZE;
        assert_eq!(total_pages2, 1); // exactly one page → no pager

        // Empty: (0 + 48 - 1).max(1) / 48 = 47 / 48 = 0 pages in integer div,
        // but the gallery handles this with an empty-state label (pager hidden).
        let count3 = 0usize;
        let total_pages3 = (count3 + PAGE_SIZE - 1).max(1) / PAGE_SIZE;
        assert_eq!(total_pages3, 0); // 0 → pager hidden, empty-state label shown
    }
}
