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
use std::cell::Cell;
use std::rc::Rc;
use std::sync::mpsc::Sender;

// ── Events ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum ControlEvent {
    Generate(GenerationRequest),
    TabChanged(ModelSource),
    ServerStart(String),    // service key
    ServerStop(String),
    ServerRestart(String),
    InspireRequested,
    /// User clicked Apply in Preferences dialog — persist + apply new settings.
    PrefsChanged(Box<crate::settings::Settings>),
    /// Open the media storage folder in the system file manager.
    OpenMediaFolder,
    /// Open the log viewer window.
    OpenLogViewer,
    /// Toggle detail panel visibility.
    ToggleDetailPanel,
    /// Change gallery density ("comfortable" | "compact").
    SetGalleryDensity(String),
    /// Prompt-gen server health changed (true = LLM ready, false = algo-only).
    PromptGenHealthChanged(bool),
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
    let inspire_btn = Button::with_label("✨ Inspire me");
    inspire_btn.add_css_class("inspire-btn");

    // Dot: ⬤ algo only  (yellow) | ⬤ starting… (orange) | ⬤ ready (teal)
    let inspire_dot = Label::new(Some("⬤ algo only"));
    inspire_dot.add_css_class("inspire-dot");
    inspire_dot.add_css_class("muted");

    // Track whether LLM prompt server is ready — updated by main.rs health pump.
    let pg_ready: Rc<Cell<bool>> = Rc::new(Cell::new(false));

    // Background thread → mpsc → main-thread pump.
    let (inspire_tx, inspire_rx) = std::sync::mpsc::channel::<Option<String>>();
    {
        let inspire_tx2 = inspire_tx.clone();
        let pg_ready2   = pg_ready.clone();
        inspire_btn.connect_clicked(move |btn| {
            btn.set_sensitive(false);
            btn.set_label("⏳ Generating…");
            btn.remove_css_class("inspire-btn");
            btn.add_css_class("inspire-btn-loading");
            let itx     = inspire_tx2.clone();
            let use_llm = pg_ready2.get();
            std::thread::spawn(move || {
                let result = run_generate_prompt(use_llm);
                let _ = itx.send(result);
            });
        });
    }
    // Drain inspire_rx every 200 ms on the main thread
    {
        let prompt4 = prompt.clone();
        let btn4    = inspire_btn.clone();
        let tx4     = tx.clone();
        glib::timeout_add_local(std::time::Duration::from_millis(200), move || {
            if let Ok(result) = inspire_rx.try_recv() {
                if let Some(p) = result {
                    prompt4.set_text(&p);
                }
                btn4.set_label("✨ Inspire me");
                btn4.remove_css_class("inspire-btn-loading");
                btn4.add_css_class("inspire-btn");
                btn4.set_sensitive(true);
                let _ = tx4.send(ControlEvent::InspireRequested);
            }
            glib::ControlFlow::Continue
        });
    }
    inspire_row.append(&inspire_btn);

    // Spacer
    let sp = gtk4::Box::new(Orientation::Horizontal, 0);
    sp.set_hexpand(true);
    inspire_row.append(&sp);
    inspire_row.append(&inspire_dot);
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
        let tx2         = tx.clone();
        let prompt2     = prompt.clone();
        let neg2        = neg_entry.clone();
        let steps2      = steps_spin.clone();
        let tab_state2  = tab_state.clone();
        let server_url2 = settings.server_url.clone();
        gen_btn.connect_clicked(move |_| {
            let text = prompt2.text().to_string();
            if text.trim().is_empty() { return; }
            let req = GenerationRequest {
                prompt:          text,
                negative_prompt: neg2.text().to_string(),
                steps:           steps2.value() as u32,
                seed:            -1,
                model_source:    tab_state2.borrow().clone(),
                server_url:      server_url2.clone(),
            };
            let _ = tx2.send(ControlEvent::Generate(req));
        });
    }
    panel.append(&gen_btn);

    let handle = ControlHandle {
        srv_dot:    srv_dot.clone(),
        srv_label:  srv_label.clone(),
        gen_btn:    gen_btn.clone(),
        prompt:     prompt.clone(),
        inspire_dot: inspire_dot.clone(),
        pg_ready,
    };

    (panel.upcast::<Widget>(), handle)
}

// ── ControlHandle ─────────────────────────────────────────────────────────────

/// Lets the main window push health updates into the control panel.
#[derive(Clone)]
pub struct ControlHandle {
    srv_dot:     Label,
    srv_label:   Label,
    gen_btn:     Button,
    pub prompt:  Entry,
    inspire_dot: Label,
    pg_ready:    Rc<Cell<bool>>,
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

    /// Update the ⬤ dot in the inspire row to reflect prompt-server health.
    pub fn set_prompt_gen_state(&self, ready: bool) {
        self.pg_ready.set(ready);
        if ready {
            self.inspire_dot.set_label("⬤ LLM ready");
            self.inspire_dot.set_css_classes(&["inspire-dot-ready"]);
        } else {
            self.inspire_dot.set_label("⬤ algo only");
            self.inspire_dot.set_css_classes(&["inspire-dot", "muted"]);
        }
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
        // Artgen uses SkyReels as a placeholder until a dedicated ModelSource::Artgen
        // variant is added. At least it doesn't collide with the Animate gallery.
        ("🎨 Artgen",  &["source-btn", "source-btn-right"], ModelSource::SkyReels),
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

/// Run `generate_prompt.py` to produce a prompt.
///
/// When `use_llm` is true (prompt-gen server is ready on port 8001), the
/// script hits Qwen3-0.6B for LLM polish.  Otherwise it runs in algo-only
/// mode (`--no-enhance`), which works without any server.
fn run_generate_prompt(use_llm: bool) -> Option<String> {
    let script = crate::health::find_tt_ctl()
        .and_then(|p| p.parent().map(|d| d.join("app").join("generate_prompt.py")))?;
    if !script.exists() { return None; }

    let mut cmd = std::process::Command::new("python3");
    cmd.arg(&script).arg("--raw");
    if !use_llm {
        cmd.arg("--no-enhance");
    }

    let output = cmd.output().ok()?;
    if !output.status.success() { return None; }

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
