# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""Connect dialog: remember a model server on another machine by its URL.

Task 8 of the server-control plan. Opened from the Servers menu's
"Connect..." button. The user gives a URL, an optional label and an optional
bearer token; "Test" probes the server on a background thread and "Save"
stores ``{url, label, token}`` in the ``remote_servers`` app setting. The
Servers menu lists saved servers in its Remote section on the next refresh.
Remote servers are use-only: the app never starts or stops them.

The token rules:

* It is stored as plain text in the settings file; the dialog says so.
* It never appears in a label, tooltip, log line or error text. Every message
  that could carry it (an HTTP error, an exception string) goes through
  ``scrub``.

The helpers above the GTK class (``normalise_url``, ``save_remote_server``,
``probe_server``, ``scrub``, ``plain_http_warning``) have no GTK dependency so
they are tested directly.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse, urlunparse

log = logging.getLogger(__name__)

SETTINGS_KEY = "remote_servers"
TOKEN_NOTICE = "The token is stored as plain text in the app settings file."
_PROBE_TIMEOUT_S = 3.0
_PROBE_PATHS = ("/v1/models", "/v1/health")
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain"}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def normalise_url(raw: str) -> str:
    """``http://host:port[/path]`` with a lowercase scheme and host, no
    user/password part, no query or fragment and no trailing slash. A trailing
    ``/v1`` is dropped (the probe adds it). Raises ValueError when the text is
    not an http(s) URL with a host."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Enter the server's URL, for example http://10.0.0.5:8000.")
    if "://" not in text:
        text = "http://" + text
    try:
        u = urlparse(text)
        port = u.port
    except ValueError as exc:
        raise ValueError(f"That URL could not be read ({exc}).") from None
    scheme = (u.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError("The URL must start with http:// or https://.")
    host = (u.hostname or "").lower()
    if not host:
        raise ValueError("The URL has no host name.")
    if ":" in host:                      # IPv6 literal
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port else host
    path = (u.path or "").rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    return urlunparse((scheme, netloc, path, "", "", ""))


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip("[]").lower()
    if h in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def plain_http_warning(url: str) -> str:
    """A warning line for plain http to another machine, else ""."""
    try:
        u = urlparse(url)
    except ValueError:
        return ""
    if u.scheme == "http" and u.hostname and not _is_loopback_host(u.hostname):
        return ("Plain http to another machine: prompts and the token travel "
                "unencrypted on the network.")
    return ""


def scrub(text, token: str | None) -> str:
    """``text`` with every occurrence of ``token`` replaced by ``***``."""
    s = str(text if text is not None else "")
    if token:
        s = s.replace(token, "***")
    return s


def save_remote_server(settings, url: str, label: str = "", token: str = "") -> list[dict]:
    """Add (or update) one remote server in ``settings[SETTINGS_KEY]``.

    The URL is normalised first; an entry with the same normalised URL is
    replaced, so there are no duplicates. Returns the new list. Raises
    ValueError for a bad URL.
    """
    norm = normalise_url(url)
    current = settings.get(SETTINGS_KEY) or []
    out: list[dict] = []
    for e in current:
        if not isinstance(e, dict):
            continue
        try:
            same = normalise_url(e.get("url", "")) == norm
        except ValueError:
            same = False
        if not same:
            out.append(dict(e))
    out.append({"url": norm, "label": str(label or "").strip(),
                "token": str(token or "").strip()})
    settings.set(SETTINGS_KEY, out)
    return out


def saved_rows(settings) -> list[dict]:
    """Saved remote servers as ``server_view.merge`` ``saved`` entries:
    name and url only. The token is left out on purpose."""
    rows = []
    for e in settings.get(SETTINGS_KEY) or []:
        if not isinstance(e, dict) or not e.get("url"):
            continue
        try:
            url = normalise_url(e["url"])
        except ValueError:
            continue
        rows.append({"name": str(e.get("label") or "").strip() or url, "url": url})
    return rows


def urls_with_token(settings) -> set[str]:
    """Normalised URLs of saved servers that carry a token (not the tokens)."""
    out = set()
    for e in settings.get(SETTINGS_KEY) or []:
        if isinstance(e, dict) and e.get("url") and str(e.get("token") or "").strip():
            try:
                out.add(normalise_url(e["url"]))
            except ValueError:
                pass
    return out


def probe_server(url: str, token: str = "", timeout: float = _PROBE_TIMEOUT_S,
                 opener=None) -> tuple[bool, str]:
    """GET ``/v1/models`` then ``/v1/health``. ``(ok, message)``; never raises.

    Blocking: call it off the GTK thread. The message never contains the
    token. ``opener`` (default ``urllib.request.urlopen``) lets tests fake
    the network.
    """
    token = str(token or "").strip()
    try:
        base = normalise_url(url)
    except ValueError as exc:
        return False, scrub(exc, token)
    open_fn = opener or urllib.request.urlopen
    last = ""
    for path in _PROBE_PATHS:
        req = urllib.request.Request(base + path, method="GET")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with open_fn(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                if 200 <= status < 300:
                    return True, f"The server answered {path} (HTTP {status})."
                last = f"{path} answered HTTP {status}."
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                last = (f"{path} refused the request (HTTP {exc.code}): check the token.")
            else:
                last = f"{path} answered HTTP {exc.code}."
        except Exception as exc:
            reason = getattr(exc, "reason", exc)
            last = f"Could not reach {base}: {reason}."
    return False, scrub(last, token)


# ---------------------------------------------------------------------------
# GTK dialog
# ---------------------------------------------------------------------------

def _gtk():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    return GLib, Gtk


class ConnectServerDialog:
    """A small modal window: URL, label, token, Test, Save, Cancel.

    ``settings``: an ``app_settings.AppSettings``-like object (get/set).
    ``on_saved(entries)``: called on the main thread after a save.
    ``probe``: ``probe_server`` by default (tests inject a fake); it always
    runs on a background thread, and its answer reaches the widgets through
    ``GLib.idle_add`` with a guard so a closed dialog is never touched.
    """

    def __init__(self, parent=None, *, settings, on_saved=None, probe=probe_server):
        GLib, Gtk = _gtk()
        self._GLib = GLib
        self._settings = settings
        self._on_saved = on_saved
        self._probe = probe
        self._alive = True
        self._probe_seq = 0

        win = Gtk.Window(title="Connect to a model server")
        win.set_modal(True)
        if parent is not None:
            win.set_transient_for(parent)
        win.set_default_size(460, -1)
        win.connect("close-request", self._on_close_request)
        self.window = win

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(14)

        def label(text, css=None):
            lbl = Gtk.Label(label=text)
            lbl.set_xalign(0)
            lbl.set_wrap(True)
            if css:
                lbl.add_css_class(css)
            return lbl

        box.append(label("URL"))
        self.url_entry = Gtk.Entry()
        self.url_entry.set_placeholder_text("http://10.0.0.5:8000")
        self.url_entry.connect("changed", self._on_url_changed)
        box.append(self.url_entry)
        self.http_warning = label("", "servers-tt-note")
        self.http_warning.set_visible(False)
        box.append(self.http_warning)

        box.append(label("Label (optional)"))
        self.label_entry = Gtk.Entry()
        self.label_entry.set_placeholder_text("e.g. Llama on the lab box")
        box.append(self.label_entry)

        box.append(label("Bearer token (optional)"))
        self.token_entry = Gtk.PasswordEntry()
        self.token_entry.set_show_peek_icon(True)
        box.append(self.token_entry)
        box.append(label(TOKEN_NOTICE, "servers-row-detail"))

        self.result_label = label("")
        self.result_label.set_selectable(True)
        box.append(self.result_label)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btns.set_halign(Gtk.Align.END)
        self.test_btn = Gtk.Button(label="Test")
        self.test_btn.connect("clicked", lambda _b: self.test())
        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.connect("clicked", lambda _b: self.close())
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", lambda _b: self.save())
        for b in (self.test_btn, self.cancel_btn, self.save_btn):
            btns.append(b)
        box.append(btns)
        win.set_child(box)

    # -- helpers ---------------------------------------------------------
    def _token(self) -> str:
        return self.token_entry.get_text().strip()

    def _show(self, text: str) -> None:
        self.result_label.set_label(scrub(text, self._token()))

    def _on_url_changed(self, _entry) -> None:
        try:
            warn = plain_http_warning(normalise_url(self.url_entry.get_text()))
        except ValueError:
            warn = ""
        self.http_warning.set_label(warn)
        self.http_warning.set_visible(bool(warn))

    # -- actions -----------------------------------------------------------
    def present(self) -> None:
        self.window.present()

    def test(self) -> None:
        """Probe on a background thread; the answer comes back via idle_add."""
        url, token = self.url_entry.get_text(), self._token()
        self._probe_seq += 1
        seq = self._probe_seq
        self.test_btn.set_sensitive(False)
        self._show("Testing...")

        def work():
            try:
                ok, msg = self._probe(url, token)
            except Exception as exc:          # a probe should not raise
                ok, msg = False, f"Test failed: {exc}"
            self._GLib.idle_add(self._probe_done, seq, ok, scrub(msg, token))

        threading.Thread(target=work, daemon=True, name="connect-probe").start()

    def _probe_done(self, seq: int, ok: bool, msg: str) -> bool:
        if not self._alive or seq != self._probe_seq:
            return False
        self.test_btn.set_sensitive(True)
        self._show(("Connected. " if ok else "Not reachable. ") + msg)
        return False

    def save(self) -> bool:
        try:
            entries = save_remote_server(self._settings, self.url_entry.get_text(),
                                         self.label_entry.get_text(), self._token())
        except ValueError as exc:
            self._show(str(exc))
            return False
        if self._on_saved is not None:
            try:
                self._on_saved(entries)
            except Exception:
                log.exception("on_saved failed")
        self.close()
        return True

    def close(self) -> None:
        if self._alive:
            self._alive = False
            self.window.destroy()

    def _on_close_request(self, _win) -> bool:
        self._alive = False
        return False
