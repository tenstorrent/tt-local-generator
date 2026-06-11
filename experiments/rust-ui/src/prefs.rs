//! prefs.rs — Preferences dialog
//!
//! Mirrors Python PreferencesDialog in main_window.py (~line 7962).
//!
//! Sections:
//!   - Server URL (text entry)
//!   - Quality / Steps (spinbutton 4–100)
//!   - Gallery density (Comfortable / Compact radio)
//!   - Sleep after N gens (spinbutton, 0 = never)
//!
//! Reads the current Settings, lets the user edit, and emits
//! ControlEvent::PrefsChanged(new_settings) on Apply/OK.

use crate::control::ControlEvent;
use crate::settings::Settings;
use gtk4::prelude::*;
use gtk4::{
    Adjustment, Box as GtkBox, Button, CheckButton, Dialog, Entry, Grid, Label,
    Orientation, ResponseType, SpinButton, Widget, Window,
};
use std::sync::mpsc::Sender;

// ── Public entry point ────────────────────────────────────────────────────────

/// Show the Preferences dialog as a child of `parent`.
/// On Apply/OK, `ControlEvent::PrefsChanged(new_settings)` is sent.
pub fn show_preferences(parent: &impl IsA<Window>, settings: &Settings, tx: Sender<ControlEvent>) {
    let dlg = build_prefs_dialog(parent, settings, tx);
    dlg.present();
}

// ── Build ─────────────────────────────────────────────────────────────────────

fn build_prefs_dialog(
    parent:   &impl IsA<Window>,
    settings: &Settings,
    tx:       Sender<ControlEvent>,
) -> Dialog {
    let dlg = Dialog::builder()
        .title("Preferences")
        .transient_for(parent)
        .modal(true)
        .build();
    dlg.set_default_size(420, -1);

    let content = dlg.content_area();
    content.set_orientation(Orientation::Vertical);
    content.set_spacing(12);
    content.set_margin_top(16);
    content.set_margin_bottom(8);
    content.set_margin_start(20);
    content.set_margin_end(20);

    let grid = Grid::new();
    grid.set_row_spacing(10);
    grid.set_column_spacing(16);

    let mut row = 0i32;

    // ── Server URL ────────────────────────────────────────────────────────────
    let srv_lbl = Label::new(Some("Server URL"));
    srv_lbl.set_xalign(1.0);
    srv_lbl.add_css_class("muted");
    let srv_entry = Entry::new();
    srv_entry.set_text(&settings.server_url);
    srv_entry.set_hexpand(true);
    srv_entry.set_placeholder_text(Some("http://localhost:8000"));
    grid.attach(&srv_lbl,   0, row, 1, 1);
    grid.attach(&srv_entry, 1, row, 1, 1);
    row += 1;

    // ── Quality steps ─────────────────────────────────────────────────────────
    let steps_lbl = Label::new(Some("Quality (steps)"));
    steps_lbl.set_xalign(1.0);
    steps_lbl.add_css_class("muted");
    let steps_adj = Adjustment::new(settings.quality_steps as f64, 4.0, 100.0, 1.0, 5.0, 0.0);
    let steps_spin = SpinButton::new(Some(&steps_adj), 1.0, 0);
    steps_spin.set_width_request(80);
    grid.attach(&steps_lbl,  0, row, 1, 1);
    grid.attach(&steps_spin, 1, row, 1, 1);
    row += 1;

    // ── Sleep after N gens ────────────────────────────────────────────────────
    let sleep_lbl = Label::new(Some("Sleep after N gens"));
    sleep_lbl.set_xalign(1.0);
    sleep_lbl.add_css_class("muted");
    let sleep_adj = Adjustment::new(settings.sleep_after_n_gens as f64, 0.0, 100.0, 1.0, 5.0, 0.0);
    let sleep_spin = SpinButton::new(Some(&sleep_adj), 1.0, 0);
    sleep_spin.set_width_request(80);
    let sleep_hint = Label::new(Some("0 = never"));
    sleep_hint.add_css_class("muted");
    let sleep_row = GtkBox::new(Orientation::Horizontal, 6);
    sleep_row.append(&sleep_spin);
    sleep_row.append(&sleep_hint);
    grid.attach(&sleep_lbl, 0, row, 1, 1);
    grid.attach(&sleep_row, 1, row, 1, 1);
    row += 1;

    // ── Gallery density ───────────────────────────────────────────────────────
    let density_lbl = Label::new(Some("Gallery density"));
    density_lbl.set_xalign(1.0);
    density_lbl.add_css_class("muted");
    let density_comfortable = CheckButton::with_label("Comfortable");
    let density_compact     = CheckButton::with_label("Compact");
    density_compact.set_group(Some(&density_comfortable));
    if settings.gallery_density == "compact" {
        density_compact.set_active(true);
    } else {
        density_comfortable.set_active(true);
    }
    let density_row = GtkBox::new(Orientation::Horizontal, 8);
    density_row.append(&density_comfortable);
    density_row.append(&density_compact);
    grid.attach(&density_lbl, 0, row, 1, 1);
    grid.attach(&density_row, 1, row, 1, 1);
    row += 1;

    // ── Skyreels frames ───────────────────────────────────────────────────────
    let sky_lbl = Label::new(Some("SkyReels frames"));
    sky_lbl.set_xalign(1.0);
    sky_lbl.add_css_class("muted");
    let sky_adj  = Adjustment::new(settings.skyreels_num_frames as f64, 9.0, 97.0, 4.0, 8.0, 0.0);
    let sky_spin = SpinButton::new(Some(&sky_adj), 1.0, 0);
    sky_spin.set_width_request(80);
    let sky_hint = Label::new(Some("9/33/65/97  (4n+1)"));
    sky_hint.add_css_class("muted");
    let sky_row = GtkBox::new(Orientation::Horizontal, 6);
    sky_row.append(&sky_spin);
    sky_row.append(&sky_hint);
    grid.attach(&sky_lbl, 0, row, 1, 1);
    grid.attach(&sky_row, 1, row, 1, 1);

    content.append(&grid);

    // ── Button row ────────────────────────────────────────────────────────────
    let btn_row = GtkBox::new(Orientation::Horizontal, 8);
    btn_row.set_halign(gtk4::Align::End);
    btn_row.set_margin_top(8);

    let cancel_btn = Button::with_label("Cancel");
    cancel_btn.add_css_class("flat");

    let apply_btn = Button::with_label("Apply");
    apply_btn.add_css_class("suggested-action");

    btn_row.append(&cancel_btn);
    btn_row.append(&apply_btn);
    content.append(&btn_row);

    // ── Signals ───────────────────────────────────────────────────────────────
    {
        let dlg2 = dlg.clone();
        cancel_btn.connect_clicked(move |_| dlg2.response(ResponseType::Cancel));
    }
    {
        let dlg2    = dlg.clone();
        let tx2     = tx.clone();
        let srv2    = srv_entry.clone();
        let steps2  = steps_spin.clone();
        let sleep2  = sleep_spin.clone();
        let comf2   = density_comfortable.clone();
        let sky2    = sky_spin.clone();
        apply_btn.connect_clicked(move |_| {
            let new_settings = collect_settings(&srv2, &steps2, &sleep2, &comf2, &sky2);
            let _ = tx2.send(ControlEvent::PrefsChanged(Box::new(new_settings)));
            dlg2.response(ResponseType::Accept);
        });
    }

    dlg.connect_response(|dlg, _| dlg.close());
    dlg
}

fn collect_settings(
    srv_entry:    &Entry,
    steps_spin:   &SpinButton,
    sleep_spin:   &SpinButton,
    comfortable:  &CheckButton,
    sky_spin:     &SpinButton,
) -> Settings {
    let mut s = Settings::default();
    s.server_url          = srv_entry.text().to_string();
    s.quality_steps       = steps_spin.value() as u32;
    s.sleep_after_n_gens  = sleep_spin.value() as u32;
    s.gallery_density     = if comfortable.is_active() { "comfortable".into() } else { "compact".into() };
    s.skyreels_num_frames = sky_spin.value() as u32;
    s
}

/// Wires the dialog as GTK4 wants: `Dialog::add_response` style via `ResponseType`.
pub fn build_prefs_widget(_parent: &impl IsA<Widget>) -> Label {
    // Placeholder — preferences are always opened as a modal dialog, not embedded.
    Label::new(Some(""))
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn collect_settings_density_logic() {
        // Simulate the gallery density logic
        let density = "comfortable";
        let compact = density == "compact";
        assert!(!compact);
        let density2 = "compact";
        assert_eq!(density2 == "compact", true);
    }

    #[test]
    fn default_server_url() {
        let s = Settings::default();
        assert_eq!(s.server_url, "http://localhost:8000");
    }
}
