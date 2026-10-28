"""Music generation runtime: server profiles and a job client (standard library only, no GTK).

Two families of server are known, both with a job API (submit, poll, fetch audio, cancel):
  * MiniMax-Music3 on `/v1/music` (lyrics and a caption);
  * Stable Audio 3 small-music on `/v1/audio` (a prompt).
A `MusicProfile` says what one server accepts. It is read from the server's `/v1/health` reply (limits, defaults, model), from its
OpenAPI document when that has `paths`, and from a built-in table for what a server cannot say (the lyrics field, trigger phrases,
a credit line). `MusicClient` maps the app's request onto the wire names of the family through `WIRE`: when the wire notes
(docs/superpowers/specs/2026-10-28-music-wire-notes.md) are corrected by a live check, `WIRE` and `PROFILE_TABLE` are the places to edit.

The bearer token, when there is one, is sent in the Authorization header and is removed from every error text.
"""
from __future__ import annotations

import dataclasses
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

INSTRUMENTAL_PLACEHOLDER = "[Instrumental]"     # UNCONFIRMED: what MiniMax accepts for a song with no words (see the wire notes)
MAX_PROMPT_CHARS = 2000
SEED_MAX = 2**31 - 1
MUSIC_PREFIX, AUDIO_PREFIX = "/v1/music", "/v1/audio"

# Wire names per job prefix: request fields, and where the job status carries what the client reads back.
WIRE = {
    MUSIC_PREFIX: {
        "req": {"prompt": "instructions", "lyrics": "input", "seconds": "audio_duration", "seed": "seed", "steps": "num_inference_steps",
                "cfg": None},
        "extra": {"response_format": "flac"},
        "seconds_got": ("stats", "audio_s"), "ended": ("stats", "ended"),
        "seed": (("stats", "seed"), ("settings", "seed")),
    },
    AUDIO_PREFIX: {
        "req": {"prompt": "prompt", "lyrics": None, "seconds": "duration", "seed": "seed", "steps": "steps", "cfg": "cfg_scale"},
        "extra": {},
        "seconds_got": ("stats", "audio_s"), "ended": None,
        "seed": (("settings", "seed"), ("stats", "seed")),
    },
}


@dataclass(frozen=True)
class MusicProfile:
    model_id: str
    label: str
    jobs_prefix: str
    text_fields: tuple
    has_lyrics: bool
    min_seconds: float
    max_seconds: float
    steps_default: Optional[int]
    steps_max: Optional[int]
    cfg_default: Optional[float]
    cfg_min: Optional[float]
    cfg_max: Optional[float]
    formats: tuple
    audio_in: str                       # "none" | "reference" | "edit" | "both"
    triggers: tuple
    credit: str
    blurb: str


# What a server cannot say about itself, matched on a lowercase substring of the health `model` string.
PROFILE_TABLE = [
    (("fiona", "minimax", "crapple"), dict(
        label="MiniMax-Music3", jobs_prefix=MUSIC_PREFIX, text_fields=("caption", "lyrics"), has_lyrics=True,
        min_seconds=5, max_seconds=60, steps_default=None, steps_max=100, cfg_default=None, cfg_min=None, cfg_max=None,
        formats=("flac",), audio_in="none", triggers=("fiona crapple",), credit="MiniMax-Music3",
        blurb="Writes a song from lyrics and a description of the music. A song ends when its lyrics do.")),
    (("stable-audio", "stable audio"), dict(
        label="Stable Audio 3 small-music", jobs_prefix=AUDIO_PREFIX, text_fields=("prompt",), has_lyrics=False,
        min_seconds=1, max_seconds=120, steps_default=8, steps_max=100, cfg_default=1.0, cfg_min=1.0, cfg_max=20.0,
        formats=("flac", "wav"), audio_in="none", triggers=(), credit="Stable Audio 3 small-music",
        blurb="Makes an instrumental clip from a text prompt.")),
]
GENERIC = dict(
    label="Music server", jobs_prefix=AUDIO_PREFIX, text_fields=("prompt",), has_lyrics=False,
    min_seconds=1, max_seconds=60, steps_default=None, steps_max=None, cfg_default=None, cfg_min=None, cfg_max=None,
    formats=("flac", "wav"), audio_in="none", triggers=(), credit="", blurb="A music server.")


def _num(d, key):
    v = d.get(key) if isinstance(d, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def profile_for(health: dict, schema: Optional[dict] = None) -> MusicProfile:
    """The profile of the server that sent `health` (and, when it has `paths`, `schema`): the table first, the server's own figures over it."""
    health = health if isinstance(health, dict) else {}
    model = str(health.get("model") or "")
    low = model.lower()
    base = dict(GENERIC)
    for needles, row in PROFILE_TABLE:
        if any(n in low for n in needles):
            base = dict(row)
            break
    paths = schema.get("paths") if isinstance(schema, dict) else None
    if isinstance(paths, dict):
        for prefix in (MUSIC_PREFIX, AUDIO_PREFIX):
            if prefix + "/jobs" in paths:
                base["jobs_prefix"] = prefix
                break
    lim, dfl = health.get("limits") or {}, health.get("defaults") or {}
    for key, src, name in (("min_seconds", lim, "min_seconds"), ("max_seconds", lim, "max_seconds"), ("steps_max", lim, "max_steps"),
                           ("cfg_min", lim, "min_cfg_scale"), ("cfg_max", lim, "max_cfg_scale"),
                           ("steps_default", dfl, "steps"), ("cfg_default", dfl, "cfg_scale")):
        v = _num(src, name)
        if v is not None:
            base[key] = v
    return MusicProfile(model_id=model, **base)


@dataclass
class MusicRequest:
    prompt: str
    lyrics: Optional[str]
    seconds: float
    seed: Optional[int]
    steps: Optional[int]
    cfg_scale: Optional[float]
    instrumental: bool = False


@dataclass
class MusicResult:
    audio: bytes
    audio_format: str
    job_id: str
    stats: dict
    seconds_got: Optional[float]
    ended: bool
    seed: int


class MusicServerError(Exception):
    """The server refused a request, or a job ended in error."""


class ServerBusy(MusicServerError):
    """HTTP 429: the queue is full."""


class ServerDown(MusicServerError):
    """The server did not answer."""


def _opener():
    """No proxy handler: a music server is a local one, and a proxy named in the environment would send the request elsewhere."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class MusicClient:
    def __init__(self, base_url: str, profile: MusicProfile, token: Optional[str] = None, timeout: float = 30.0):
        self.base, self.profile, self.token, self.timeout = base_url.rstrip("/"), profile, token, timeout
        self.wire = WIRE.get(profile.jobs_prefix) or WIRE[AUDIO_PREFIX]

    def _scrub(self, text) -> str:
        """The text with the token removed, wherever a server echoed it back."""
        text = str(text)
        return text.replace(self.token, "***") if self.token else text

    def _call(self, method: str, path: str, body: Optional[dict] = None, raw: bool = False):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with _opener().open(request, timeout=self.timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode()).get("detail", "")
            except Exception:
                pass                                    # a body that is missing, cut short or not JSON: the status still says what happened
            detail = self._scrub(detail or e.reason)
            if e.code == 429:
                raise ServerBusy("The music server is busy: its queue is full. Try again in a minute.") from None
            if e.code == 503:
                raise MusicServerError(f"The music server is not ready: {detail}.") from None
            if e.code in (401, 403):
                raise MusicServerError("The music server refused the request: it needs a valid token.") from None
            raise MusicServerError(f"The music server answered {e.code}: {detail}") from None
        except Exception as e:                          # URLError, timeouts, a reply that stops halfway
            raise ServerDown(f"The music server at {self.base} is not reachable ({self._scrub(getattr(e, 'reason', None) or type(e).__name__)}).") from None
        if raw:
            return payload
        try:
            doc = json.loads(payload)
        except ValueError:
            raise MusicServerError(f"The music server at {self.base} sent a reply that is not JSON.") from None
        if not isinstance(doc, dict):
            raise MusicServerError(f"The music server at {self.base} sent an unexpected reply.")
        return doc

    # ---- the API -----------------------------------------------------------------------------------------

    def health(self) -> dict:
        return self._call("GET", "/v1/health")

    def _check(self, req: MusicRequest) -> None:
        p = self.profile
        if not isinstance(req.prompt, str) or not req.prompt.strip():
            raise ValueError("The prompt must not be empty.")
        if len(req.prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"The prompt must be at most {MAX_PROMPT_CHARS} characters.")
        if p.has_lyrics and not req.instrumental and not (isinstance(req.lyrics, str) and req.lyrics.strip()):
            raise ValueError("The lyrics must not be empty (or choose instrumental).")
        s = req.seconds
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not p.min_seconds <= s <= p.max_seconds:
            raise ValueError(f"The length must be between {p.min_seconds:g} and {p.max_seconds:g} seconds, got {s!r}.")
        if req.seed is not None and (isinstance(req.seed, bool) or not isinstance(req.seed, int) or not 0 <= req.seed <= 2**32 - 1):
            raise ValueError(f"The seed must be a whole number of 0 or more, got {req.seed!r}.")
        if req.steps is not None and (isinstance(req.steps, bool) or not isinstance(req.steps, int) or req.steps < 1
                                      or (p.steps_max is not None and req.steps > p.steps_max)):
            raise ValueError(f"Steps must be a whole number from 1 to {p.steps_max}, got {req.steps!r}.")
        if req.cfg_scale is not None and p.cfg_min is not None and p.cfg_max is not None and not (
                isinstance(req.cfg_scale, (int, float)) and not isinstance(req.cfg_scale, bool) and p.cfg_min <= req.cfg_scale <= p.cfg_max):
            raise ValueError(f"The guidance scale must be between {p.cfg_min:g} and {p.cfg_max:g}, got {req.cfg_scale!r}.")

    def submit(self, req: MusicRequest) -> str:
        """Queue a job and return its id. Raises ValueError for a request the server would refuse, before any request is made."""
        self._check(req)
        names = self.wire["req"]
        body = dict(self.wire["extra"])
        body[names["prompt"]] = req.prompt
        if names["lyrics"] and self.profile.has_lyrics:
            body[names["lyrics"]] = INSTRUMENTAL_PLACEHOLDER if req.instrumental else req.lyrics
        body[names["seconds"]] = req.seconds
        for value, key in ((req.seed, "seed"), (req.steps, "steps"), (req.cfg_scale, "cfg")):
            if value is not None and names.get(key):
                body[names[key]] = value
        job_id = self._call("POST", self.profile.jobs_prefix + "/jobs", body).get("id")
        if not isinstance(job_id, str):
            raise MusicServerError("The music server accepted the job but sent no job id.")
        return job_id

    def poll(self, job_id: str) -> dict:
        return self._call("GET", f"{self.profile.jobs_prefix}/jobs/{job_id}")

    def fetch(self, job_id: str) -> bytes:
        return self._call("GET", f"{self.profile.jobs_prefix}/jobs/{job_id}/audio", raw=True)

    def cancel(self, job_id: str) -> None:
        try:
            self._call("DELETE", f"{self.profile.jobs_prefix}/jobs/{job_id}")
        except MusicServerError:
            pass                                        # finished already, or the server is gone: nothing left to cancel

    def _read(self, view: dict, where):
        """A value from a job view at (section, key), or None."""
        sec = view.get(where[0]) if where else None
        return sec.get(where[1]) if isinstance(sec, dict) else None

    def generate(self, req: MusicRequest, on_progress: Optional[Callable[[str], None]] = None, poll_s: float = 2.0,
                 should_cancel: Optional[Callable[[], bool]] = None, timeout_s: float = 900.0) -> MusicResult:
        """Submit, wait, and return the clip. An abandoned job (cancel asked for, timeout, an error, Ctrl-C) is cancelled on the server."""
        if req.seed is None:
            req = dataclasses.replace(req, seed=random.randint(0, SEED_MAX))      # always known, so a clip can be asked for again
        job_id = self.submit(req)
        deadline = time.monotonic() + timeout_s
        try:
            while True:
                if should_cancel is not None and should_cancel():
                    raise MusicServerError("The job was cancelled.")
                view = self.poll(job_id)
                state = view.get("status")
                if state == "done":
                    return self._result(job_id, view, req)
                if state in ("error", "cancelled"):
                    raise MusicServerError(f"The job {state}: {self._scrub(view.get('error') or 'no reason given')}")
                if on_progress:
                    on_progress(self._progress_text(view))
                if time.monotonic() > deadline:
                    raise MusicServerError(f"Timed out after {timeout_s:.0f} s waiting for job {job_id}.")
                time.sleep(poll_s)
        except BaseException:
            self.cancel(job_id)
            raise

    @staticmethod
    def _progress_text(view: dict) -> str:
        state, stage = str(view.get("status") or ""), str(view.get("stage") or "")
        done, mx = view.get("frames_done"), view.get("frames_max")
        if isinstance(done, int) and isinstance(mx, int) and mx > 0:
            return f"{stage or state}: {100 * done // mx}%"
        if state == "queued" and view.get("queue_position"):
            return f"waiting in the queue (position {view['queue_position']})"
        return stage or state

    def _result(self, job_id: str, view: dict, req: MusicRequest) -> MusicResult:
        audio = self.fetch(job_id)
        stats = view.get("stats") if isinstance(view.get("stats"), dict) else {}
        got = self._read(view, self.wire["seconds_got"])
        ended = bool(self._read(view, self.wire["ended"])) if self.wire["ended"] else False
        seed = next((v for v in (self._read(view, w) for w in self.wire["seed"]) if isinstance(v, int) and not isinstance(v, bool)), req.seed)
        if audio[:4] == b"fLaC":
            fmt = "flac"
        elif audio[:4] == b"RIFF":
            fmt = "wav"
        else:
            fmt = str(stats.get("audio_format") or self.profile.formats[0])
        return MusicResult(audio, fmt, job_id, stats, float(got) if isinstance(got, (int, float)) else None, ended, seed)
