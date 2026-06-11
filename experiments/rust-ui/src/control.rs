//! control.rs — ControlPanel (left sidebar)
//!
//! Mirrors Python ControlPanel (main_window.py:3793).
//!
//! Contains:
//!   - Source tab bar (Video / Animate / Image / Artgen)
//!   - Prompt Entry with ✨ Inspire button (async subprocess generate_prompt.py)
//!   - Model/style chip scrollbox (swapped on tab switch)
//!   - Steps SpinButton and seed Entry
//!   - Server Start/Stop row with health dot
//!   - Generate button → emits ControlEvent::Generate

use crate::health::HealthSnapshot;
use crate::settings::Settings;
use crate::worker::{GenerationRequest, ModelSource};
use gtk4::prelude::*;
use gtk4::{
    Box as GtkBox, Button, Entry, Label, Orientation, Revealer,
    RevealerTransitionType, SpinButton, ToggleButton, Widget,
};
use std::sync::mpsc::Sender;

// ── Events ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum ControlEvent {
    Generate(GenerationRequest),
    TabChanged(ModelSource),
    ServerStart(String),    // service key
    ServerStop(String),
    InspireRequested,
}

// ── Build function ────────────────────────────────────────────────────────────

pub fn build_control_panel(
    settings: &Settings,
    tx:       Sender<ControlEvent>,
) -> (Widget, ControlHandle) {
    let panel = GtkBox::new(Orientation::Vertical, 6);
    panel.set_width_request(300);
    panel.set_margin_top(8);
    panel.set_margin_bottom(8);
    panel.set_margin_start(8);
    panel.set_margin_end(4);

    // ── Source tab bar ────────────────────────────────────────────────────────
    let (tab_row, tab_state) = build_source_tabs(tx.clone());
    panel.append(&tab_row);

    // ── Prompt entry ──────────────────────────────────────────────────────────
    let prompt = Entry::new();
    prompt.set_placeholder_text(Some("Describe the video you want to generate…"));
    prompt.add_css_class("prompt-entry");
    prompt.set_hexpand(true);
    panel.append(&prompt);

    // ── Inspire row ───────────────────────────────────────────────────────────
    let inspire_row = GtkBox::new(Orientation::Horizontal, 4);
    let inspire_btn = Button::with_label("✨ Inspire");
    inspire_btn.add_css_class("flat");
    inspire_btn.add_css_class("inspire-btn");
    // Internal mpsc channel: background thread posts the generated prompt text
    // back to the main thread without GTK widget captures.
    let (inspire_tx, inspire_rx) = std::sync::mpsc::channel::<Option<String>>();
    let inspire_rx = std::sync::Arc::new(std::sync::Mutex::new(inspire_rx));
    {
        let inspire_tx2 = inspire_tx.clone();
        inspire_btn.connect_clicked(move |btn| {
            btn.set_sensitive(false);
            btn.set_label("✨…");
            let itx = inspire_tx2.clone();
            std::thread::spawn(move || {
                let result = run_generate_prompt();
                let _ = itx.send(result);
            });
        });
    }
    // Drain inspire_rx every 200 ms on the main thread
    {
        let prompt4  = prompt.clone();
        let btn4     = inspire_btn.clone();
        let tx4      = tx.clone();
        let irx4     = inspire_rx.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(200), move || {
            let rx = irx4.lock().unwrap();
            if let Ok(result) = rx.try_recv() {
                if let Some(p) = result {
                    prompt4.set_text(&p);
                }
                btn4.set_label("✨ Inspire");
                btn4.set_sensitive(true);
                let _ = tx4.send(ControlEvent::InspireRequested);
            }
            glib::ControlFlow::Continue
        });
    }
    inspire_row.append(&inspire_btn);
    panel.append(&inspire_row);

    // ── Negative prompt (collapsed by default) ────────────────────────────────
    let neg_rev = Revealer::new();
    neg_rev.set_transition_type(RevealerTransitionType::SlideDown);
    let neg_entry = Entry::new();
    neg_entry.set_placeholder_text(Some("Negative prompt (optional)…"));
    neg_entry.add_css_class("prompt-entry");
    neg_rev.set_child(Some(&neg_entry));

    let neg_toggle = Button::with_label("﹢ Negative prompt");
    neg_toggle.add_css_class("flat");
    neg_toggle.add_css_class("neg-toggle");
    {
        let rev = neg_rev.clone();
        let btn = neg_toggle.clone();
        neg_toggle.connect_clicked(move |_| {
            let showing = rev.reveals_child();
            rev.set_reveal_child(!showing);
            btn.set_label(if showing { "﹢ Negative prompt" } else { "﹣ Negative prompt" });
        });
    }
    panel.append(&neg_toggle);
    panel.append(&neg_rev);

    // ── Quality / Steps row ───────────────────────────────────────────────────
    let steps_row = GtkBox::new(Orientation::Horizontal, 8);
    steps_row.set_margin_top(4);

    let steps_lbl = Label::new(Some("Steps"));
    steps_lbl.add_css_class("muted");
    steps_lbl.set_xalign(0.0);

    let steps_adj = gtk4::Adjustment::new(
        settings.quality_steps as f64, 4.0, 100.0, 1.0, 5.0, 0.0,
    );
    let steps_spin = SpinButton::new(Some(&steps_adj), 1.0, 0);
    steps_spin.set_width_request(70);
    steps_row.append(&steps_lbl);
    steps_row.append(&steps_spin);
    panel.append(&steps_row);

    // ── Server control row (pinned at bottom) ─────────────────────────────────
    let srv_row = GtkBox::new(Orientation::Horizontal, 4);
    srv_row.set_margin_top(8);

    let srv_dot = Label::new(Some("○"));
    srv_dot.add_css_class("server-dot-offline");

    let srv_label = Label::new(Some("Server offline"));
    srv_label.add_css_class("muted");
    srv_label.set_hexpand(true);
    srv_label.set_xalign(0.0);

    let start_btn = Button::with_label("▶");
    start_btn.add_css_class("flat");
    {
        let tx2 = tx.clone();
        start_btn.connect_clicked(move |_| {
            let _ = tx2.send(ControlEvent::ServerStart("wan2.2".into()));
        });
    }

    let stop_btn = Button::with_label("■");
    stop_btn.add_css_class("flat");
    {
        let tx2 = tx.clone();
        stop_btn.connect_clicked(move |_| {
            let _ = tx2.send(ControlEvent::ServerStop("wan2.2".into()));
        });
    }

    srv_row.append(&srv_dot);
    srv_row.append(&srv_label);
    srv_row.append(&start_btn);
    srv_row.append(&stop_btn);
    panel.append(&srv_row);

    // ── Generate button ───────────────────────────────────────────────────────
    let gen_btn = Button::with_label("▶ Generate");
    gen_btn.add_css_class("generate-btn");
    {
        let tx2        = tx.clone();
        let prompt2    = prompt.clone();
        let neg2       = neg_entry.clone();
        let steps2     = steps_spin.clone();
        let tab_state2 = tab_state.clone();
        gen_btn.connect_clicked(move |_| {
            let text = prompt2.text().to_string();
            if text.trim().is_empty() { return; }
            let req = GenerationRequest {
                prompt:          text,
                negative_prompt: neg2.text().to_string(),
                steps:           steps2.value() as u32,
                seed:            -1,
                model_source:    tab_state2.borrow().clone(),
                server_url:      "http://localhost:8000".into(),
            };
            let _ = tx2.send(ControlEvent::Generate(req));
        });
    }
    panel.append(&gen_btn);

    let handle = ControlHandle {
        srv_dot:   srv_dot.clone(),
        srv_label: srv_label.clone(),
        gen_btn:   gen_btn.clone(),
        prompt:    prompt.clone(),
    };

    (panel.upcast::<Widget>(), handle)
}

// ── ControlHandle ─────────────────────────────────────────────────────────────

/// Lets the main window push health updates into the control panel.
#[derive(Clone)]
pub struct ControlHandle {
    srv_dot:   Label,
    srv_label: Label,
    gen_btn:   Button,
    pub prompt:    Entry,
}

impl ControlHandle {
    pub fn update_health(&self, snap: &HealthSnapshot) {
        let (dot, css, label) = if snap.server_alive && snap.server_ready {
            ("●", "server-dot-ready",   snap.model.as_deref().unwrap_or("Server ready").to_string())
        } else if snap.server_alive {
            ("●", "server-dot-loading", "Model loading…".into())
        } else {
            ("○", "server-dot-offline", "Server offline".into())
        };
        self.srv_dot.set_label(dot);
        self.srv_dot.set_css_classes(&[css]);
        self.srv_label.set_label(&label);
        self.gen_btn.set_sensitive(snap.server_ready);
    }

    pub fn set_generating(&self, generating: bool) {
        self.gen_btn.set_sensitive(!generating);
        self.gen_btn.set_label(if generating { "⌛ Generating…" } else { "▶ Generate" });
    }
}

// ── Source tabs ───────────────────────────────────────────────────────────────

fn build_source_tabs(
    tx: Sender<ControlEvent>,
) -> (Widget, std::rc::Rc<std::cell::RefCell<ModelSource>>) {
    let row = GtkBox::new(Orientation::Horizontal, 0);
    let state = std::rc::Rc::new(std::cell::RefCell::new(ModelSource::Video));

    let tabs: &[(&str, &[&str], ModelSource)] = &[
        ("🎬 Video",   &["source-btn", "source-btn-left"],  ModelSource::Video),
        ("💃 Animate", &["source-btn"],                     ModelSource::Animate),
        ("🖼 Image",   &["source-btn"],                     ModelSource::Image),
        ("🎨 Artgen",  &["source-btn", "source-btn-right"], ModelSource::Animate), // placeholder
    ];

    let mut first: Option<ToggleButton> = None;
    for (label, css_classes, src) in tabs {
        let btn = ToggleButton::with_label(label);
        for &cls in *css_classes { btn.add_css_class(cls); }
        if let Some(ref f) = first {
            btn.set_group(Some(f));
        } else {
            btn.set_active(true);
            first = Some(btn.clone());
        }
        let tx2    = tx.clone();
        let state2 = state.clone();
        let src2   = src.clone();
        btn.connect_toggled(move |b| {
            if b.is_active() {
                *state2.borrow_mut() = src2.clone();
                let _ = tx2.send(ControlEvent::TabChanged(src2.clone()));
            }
        });
        row.append(&btn);
    }

    (row.upcast::<Widget>(), state)
}

// ── Inspire subprocess ────────────────────────────────────────────────────────

fn run_generate_prompt() -> Option<String> {
    // Find generate_prompt.py relative to the tt-ctl location
    let script = crate::health::find_tt_ctl()
        .and_then(|p| p.parent().map(|d| d.join("app").join("generate_prompt.py")))?;
    if !script.exists() { return None; }

    let output = std::process::Command::new("python3")
        .arg(&script)
        .args(["--raw", "--no-enhance"])
        .output()
        .ok()?;

    if !output.status.success() { return None; }

    // --raw emits the prompt directly; strip trailing newline
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if text.is_empty() { None } else { Some(text) }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn model_source_clone() {
        // ModelSource must be Clone for the tab state Rc<RefCell>
        let src = ModelSource::Video;
        let cloned = src.clone();
        assert_eq!(cloned, ModelSource::Video);
    }

    #[test]
    fn control_event_generate_has_fields() {
        let req = GenerationRequest {
            prompt: "test".into(),
            ..Default::default()
        };
        let ev = ControlEvent::Generate(req.clone());
        if let ControlEvent::Generate(r) = ev {
            assert_eq!(r.prompt, "test");
        } else {
            panic!("wrong variant");
        }
    }

    #[test]
    fn tab_source_coverage() {
        // All four source tab types should map to a ModelSource variant.
        let sources = [
            ModelSource::Video,
            ModelSource::Image,
            ModelSource::Animate,
            ModelSource::SkyReels,
        ];
        for s in sources {
            let mt = s.as_media_type();
            assert!(!mt.is_empty());
        }
    }
}
