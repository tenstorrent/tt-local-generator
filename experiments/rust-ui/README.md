# tt-gen-rs — Rust/GTK4 UI experiment

Proof-of-concept reimplementation of tt-local-generator's main window in Rust
using [gtk-rs](https://gtk-rs.org) (GTK4 Rust bindings).

## Why

The Python app has unavoidable lag under load because:
- PyGObject's GIL means background threads contend with GTK's main loop
- Health polling, chip telemetry, and hover previews all fight for the same interpreter lock
- Widget construction (48 GenerationCard objects = 100ms on main thread) blocks scroll

In Rust:
- `!Send` on widget types makes threading discipline a compile-time guarantee
- HealthBus runs on a Tokio thread, sends typed `HealthSnapshot` via `glib::MainContext::channel` — zero unsafe, zero `idle_add` bookkeeping
- Card construction is faster (no interpreter, no GC pressure)
- `reqwest` async HTTP means health checks never block

## Build

```bash
sudo apt install libgtk-4-dev libglib2.0-dev
cargo build --release
./target/release/tt-gen-rs
```

## What it reuses from Python

- **History store** — reads `~/.local/share/tt-local-generator/history.json` directly (same schema as `app/history_store.py`)
- **Server scripts** — shells out to `./tt-ctl start|stop` for server lifecycle
- **Colour palette** — same hex values as `_CSS` in `app/main_window.py`

## Current scope (POC)

- [x] Window with correct default size (no force-maximize, WM-friendly)
- [x] Source tab bar (Video / Animate / Image / Artgen)
- [x] Prompt entry + Generate button stub
- [x] History cards loaded from JSON (prompt + placeholder thumbnail)
- [x] Server health status bar (ports 8000/8001/8002)
- [x] tt-ctl Start/Stop wired to buttons
- [ ] Real thumbnail rendering (next: `gdk4::Texture::from_filename`)
- [ ] Detail panel
- [ ] Actual generation API call (wire to `app/api_client.py` or re-implement)
- [ ] Hover preview (debounced, same 120ms logic)
- [ ] Artgen panel
