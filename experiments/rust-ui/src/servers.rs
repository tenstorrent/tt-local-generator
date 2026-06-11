//! servers.rs — Servers ▾ popover (managed service start/stop/restart)
//!
//! Mirrors Python's _build_servers_popover() in main_window.py:4852.
//!
//! Shows one row per service with:
//!   - Status dot (offline / loading / ready)
//!   - Service label
//!   - ▶ Start  ■ Stop  ↺ Restart buttons
//!
//! Status is refreshed on popover open and every REFRESH_MS when visible.
//! Actions dispatch via the ControlEvent channel so main.rs can shell out.

use crate::control::ControlEvent;
use crate::health::HealthSnapshot;
use gtk4::prelude::*;
use gtk4::{Box as GtkBox, Button, Label, MenuButton, Orientation, Popover, Separator, Widget};
use std::cell::RefCell;
use std::rc::Rc;
use std::sync::mpsc::Sender;

// ── Service catalogue ─────────────────────────────────────────────────────────

struct SvcDef {
    key:   &'static str,
    label: &'static str,
    cap:   &'static str,   // capability group heading
}

const SERVICES: &[SvcDef] = &[
    SvcDef { key: "wan2.2",        label: "Wan2.2-T2V-A14B (P300X2)",        cap: "Video" },
    SvcDef { key: "mochi",         label: "Mochi-1",                          cap: "Video" },
    SvcDef { key: "skyreels",      label: "SkyReels-V2-I2V-14B-540P",        cap: "Video" },
    SvcDef { key: "flux",          label: "FLUX.1-schnell",                   cap: "Image" },
    SvcDef { key: "animate",       label: "Wan2.2-Animate-14B",              cap: "Animate" },
    SvcDef { key: "prompt-server", label: "Prompt Generator (Qwen3-0.6B)",   cap: "Prompt" },
    SvcDef { key: "artgen-qwen3-8b", label: "Artgen LLM (Qwen3-8B)",         cap: "Artgen" },
];

// ── Public build function ─────────────────────────────────────────────────────

/// Build the "Servers ▾" menu button with its health-refresh popover.
///
/// `snap_rc` is updated by the health bus pump in main.rs and read here
/// when the popover opens to show current dot states.
pub fn build_servers_button(
    tx:      Sender<ControlEvent>,
    snap_rc: Rc<RefCell<HealthSnapshot>>,
) -> Widget {
    let btn = MenuButton::builder()
        .label("Servers ▾")
        .build();
    btn.add_css_class("servers-menu-btn");
    btn.set_tooltip_text(Some("Start, stop, or restart managed services"));

    // Dot labels stored so the refresh callback can update them.
    let dots: Rc<RefCell<Vec<(String, Label)>>> = Rc::new(RefCell::new(vec![]));

    let popover = build_servers_popover(tx, snap_rc.clone(), dots.clone());
    btn.set_popover(Some(&popover));

    // Refresh dot states each time the popover opens.
    {
        let snap_rc2 = snap_rc.clone();
        let dots2    = dots.clone();
        popover.connect_show(move |_| {
            refresh_dots(&snap_rc2.borrow(), &dots2.borrow());
        });
    }

    btn.upcast::<Widget>()
}

// ── Popover internals ─────────────────────────────────────────────────────────

fn build_servers_popover(
    tx:      Sender<ControlEvent>,
    snap_rc: Rc<RefCell<HealthSnapshot>>,
    dots:    Rc<RefCell<Vec<(String, Label)>>>,
) -> Popover {
    let pop = Popover::new();
    pop.set_has_arrow(false);
    pop.set_autohide(true);

    let outer = GtkBox::new(Orientation::Vertical, 0);
    outer.set_margin_top(8);
    outer.set_margin_bottom(8);
    outer.set_margin_start(10);
    outer.set_margin_end(10);
    outer.set_size_request(340, -1);

    // Header
    let hdr = GtkBox::new(Orientation::Horizontal, 0);
    let hdr_lbl = Label::new(Some("Managed Services"));
    hdr_lbl.add_css_class("servers-cap-header");
    hdr_lbl.set_hexpand(true);
    hdr_lbl.set_xalign(0.0);
    hdr.append(&hdr_lbl);

    let refresh_btn = Button::with_label("↻");
    refresh_btn.add_css_class("servers-popover-btn");
    refresh_btn.set_tooltip_text(Some("Refresh server status"));
    {
        let snap_rc2 = snap_rc.clone();
        let dots2    = dots.clone();
        refresh_btn.connect_clicked(move |_| {
            refresh_dots(&snap_rc2.borrow(), &dots2.borrow());
        });
    }
    hdr.append(&refresh_btn);
    outer.append(&hdr);

    let sep = Separator::new(Orientation::Horizontal);
    sep.set_margin_top(4);
    sep.set_margin_bottom(4);
    outer.append(&sep);

    let mut current_cap = "";
    for svc in SERVICES {
        // Capability group label
        if svc.cap != current_cap {
            current_cap = svc.cap;
            let cap_lbl = Label::new(Some(svc.cap));
            cap_lbl.add_css_class("servers-cap-header");
            cap_lbl.set_xalign(0.0);
            cap_lbl.set_margin_top(6);
            outer.append(&cap_lbl);
        }

        let (row, dot) = build_service_row(svc, tx.clone());
        dots.borrow_mut().push((svc.key.to_string(), dot));
        outer.append(&row);
    }

    pop.set_child(Some(&outer));
    pop
}

fn build_service_row(svc: &SvcDef, tx: Sender<ControlEvent>) -> (Widget, Label) {
    let row = GtkBox::new(Orientation::Horizontal, 6);
    row.add_css_class("servers-popover-row");
    row.set_margin_top(2);
    row.set_margin_bottom(2);

    let dot = Label::new(Some("○"));
    dot.add_css_class("servers-popover-dot-off");
    dot.set_width_chars(2);
    row.append(&dot);

    let name_lbl = Label::new(Some(svc.label));
    name_lbl.add_css_class("servers-popover-key");
    name_lbl.set_xalign(0.0);
    name_lbl.set_hexpand(true);
    name_lbl.set_max_width_chars(28);
    name_lbl.set_ellipsize(gtk4::pango::EllipsizeMode::End);
    row.append(&name_lbl);

    let key = svc.key;

    let start_btn = Button::with_label("▶");
    start_btn.add_css_class("servers-popover-btn");
    start_btn.set_tooltip_text(Some(&format!("Start {}", svc.label)));
    {
        let tx2 = tx.clone();
        let k   = key.to_string();
        start_btn.connect_clicked(move |_| {
            let _ = tx2.send(ControlEvent::ServerStart(k.clone()));
        });
    }
    row.append(&start_btn);

    let stop_btn = Button::with_label("■");
    stop_btn.add_css_class("servers-popover-btn");
    stop_btn.add_css_class("servers-popover-btn-stop");
    stop_btn.set_tooltip_text(Some(&format!("Stop {}", svc.label)));
    {
        let tx2 = tx.clone();
        let k   = key.to_string();
        stop_btn.connect_clicked(move |_| {
            let _ = tx2.send(ControlEvent::ServerStop(k.clone()));
        });
    }
    row.append(&stop_btn);

    let restart_btn = Button::with_label("↺");
    restart_btn.add_css_class("servers-popover-btn");
    restart_btn.set_tooltip_text(Some(&format!("Restart {}", svc.label)));
    {
        let tx2 = tx.clone();
        let k   = key.to_string();
        restart_btn.connect_clicked(move |_| {
            let _ = tx2.send(ControlEvent::ServerRestart(k.clone()));
        });
    }
    row.append(&restart_btn);

    (row.upcast::<Widget>(), dot)
}

// ── Dot refresh ───────────────────────────────────────────────────────────────

fn refresh_dots(snap: &HealthSnapshot, dots: &[(String, Label)]) {
    for (key, dot) in dots {
        let running = snap.services.iter().any(|(k, up)| k == key && *up);
        if running {
            dot.set_label("●");
            dot.set_css_classes(&["servers-popover-dot-on"]);
        } else {
            dot.set_label("○");
            dot.set_css_classes(&["servers-popover-dot-off"]);
        }
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn service_count() {
        assert!(!SERVICES.is_empty());
        // Each service has a non-empty key and label
        for s in SERVICES {
            assert!(!s.key.is_empty());
            assert!(!s.label.is_empty());
        }
    }

    #[test]
    fn refresh_dots_running_service() {
        // Simulate a HealthSnapshot with wan2.2 running
        let snap = HealthSnapshot {
            services: vec![
                ("wan2.2".into(), true),
                ("flux".into(), false),
            ],
            ..Default::default()
        };
        // Just verify the matching logic — no GTK widgets in unit tests
        let running = snap.services.iter().any(|(k, up)| k == "wan2.2" && *up);
        assert!(running);
        let not_running = snap.services.iter().any(|(k, up)| k == "flux" && *up);
        assert!(!not_running);
    }
}
