"""A stdlib fake of a music job server, for tests. No chip, no network beyond loopback.

It imitates either family of server the app talks to ("music" = MiniMax-Music3 style, "audio" = Stable Audio style).
Everything about the wire lives in `STYLES` below, so the live spike can change a field name in one place.
Behaviour switches (set before a request): `busy` (submit answers 429), `down_after_submit` (the server stops answering once a job
is accepted), `ended_early` (done with fewer seconds than asked, and the ended flag where the family has one),
`require_token` (401 without that exact bearer), `error_500_echoes_headers` (a GET to the job answers 500 with the request headers in the body).
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# request field names, and where the job status reports what the client reads back
STYLES = {
    "music": {
        "prefix": "/v1/music",
        "req": {"prompt": "instructions", "lyrics": "input", "seconds": "audio_duration", "seed": "seed", "steps": "num_inference_steps",
                "cfg": None},
        "required": ("input", "instructions"),
        "seconds_got_in": "stats", "seconds_got": "audio_s", "ended": "ended", "seed_in": "stats", "seed": "seed",
        "health": {"status": "ok", "model": "fiona-crapple-merged", "mesh": "1x1", "device_name": "P150", "busy": False,
                   "queue_depth": 0, "error": None},
    },
    "audio": {
        "prefix": "/v1/audio",
        "req": {"prompt": "prompt", "lyrics": None, "seconds": "duration", "seed": "seed", "steps": "steps", "cfg": "cfg_scale"},
        "required": ("prompt",),
        "seconds_got_in": "stats", "seconds_got": "audio_s", "ended": None, "seed_in": "settings", "seed": "seed",
        "health": {"status": "ok", "model": "stable-audio-3 small-music, diffusion transformer on one Blackhole chip",
                   "device_name": "one Blackhole chip", "queue_depth": 0, "queue_max": 2, "busy": False,
                   "limits": {"min_seconds": 1, "max_seconds": 120, "max_steps": 100, "min_cfg_scale": 1.0, "max_cfg_scale": 20.0},
                   "defaults": {"duration": 10, "steps": 8, "cfg_scale": 1.0}},
    },
}


class FakeMusicServer:
    def __init__(self, style="audio", health=None, steps_before_done=1, busy=False, down_after_submit=False, ended_early=False,
                 require_token=None, error_500_echoes_headers=False, echo_token_in_stage=False):
        self.style = STYLES[style]
        self.prefix = self.style["prefix"]
        self.health_body = dict(health if health is not None else self.style["health"])
        self.steps_before_done = steps_before_done
        self.busy, self.down_after_submit, self.ended_early = busy, down_after_submit, ended_early
        self.require_token, self.error_500_echoes_headers = require_token, error_500_echoes_headers
        self.echo_token_in_stage = echo_token_in_stage      # a running job's `stage` carries the Authorization header
        self.audio_format_header = None                     # when set, the audio reply starts with these bytes instead of fLaC
        self.jobs = {}              # id -> {"body", "polls"}
        self.submitted = []         # request bodies in order
        self.requests = []          # (method, path) in order
        self.cancelled = []         # ids that got a DELETE
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, raw=None, ctype="application/json"):
                data = raw if raw is not None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _gate(self):
                outer.requests.append((self.command, self.path))
                if outer.require_token and self.headers.get("Authorization") != f"Bearer {outer.require_token}":
                    self._send(401, {"detail": "missing or wrong bearer token"})
                    return False
                return True

            def do_GET(self):
                if not self._gate():
                    return
                s, p = outer.style, self.path
                if p == "/v1/health":
                    return self._send(200, outer.health_body)
                if outer.down_after_submit and outer.submitted:
                    self.close_connection = True
                    self.connection.close()
                    return
                if p.startswith(outer.prefix + "/jobs/") and p.endswith("/audio"):
                    return self._send(200, raw=(outer.audio_format_header or b"fLaC") + b"-fake-" + p.split("/")[4].encode(), ctype="audio/flac")
                if p.startswith(outer.prefix + "/jobs/"):
                    jid = p.split("/")[4]
                    job = outer.jobs.get(jid)
                    if job is None:
                        return self._send(404, {"detail": "no such job"})
                    if outer.error_500_echoes_headers:
                        return self._send(500, {"detail": "boom; request headers were " + str(dict(self.headers))})
                    job["polls"] += 1
                    asked = job["body"].get(s["req"]["seconds"], 10)
                    if job["polls"] <= outer.steps_before_done:
                        return self._send(200, {"id": jid, "status": "running", "stage": "sampling" + (" " + str(self.headers.get("Authorization")) if outer.echo_token_in_stage else ""), "queue_position": 0})
                    got = 5.2 if outer.ended_early else float(asked)
                    view = {"id": jid, "status": "done", "stage": "done", "settings": {}, "stats": {"total_s": 3.0}}
                    view[s["seconds_got_in"]][s["seconds_got"]] = got
                    if s["ended"]:
                        view["stats"][s["ended"]] = bool(outer.ended_early)
                    seed = job["body"].get(s["req"]["seed"])
                    view[s["seed_in"]][s["seed"]] = seed
                    return self._send(200, view)
                return self._send(404, {"detail": "not found"})

            def do_POST(self):
                if not self._gate():
                    return
                if self.path != outer.prefix + "/jobs":
                    return self._send(404, {"detail": "not found"})
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if outer.busy:
                    return self._send(429, {"detail": "the queue is full"})
                if any(not body.get(k) for k in outer.style["required"]):
                    return self._send(422, {"detail": "missing " + ", ".join(outer.style["required"])})
                jid = f"job{len(outer.jobs) + 1:03d}"
                outer.jobs[jid] = {"body": body, "polls": 0}
                outer.submitted.append(body)
                return self._send(202, {"id": jid, "status": "queued", "queue_position": 0})

            def do_DELETE(self):
                if not self._gate():
                    return
                jid = self.path.split("/")[4] if self.path.startswith(outer.prefix + "/jobs/") else None
                outer.cancelled.append(jid)
                return self._send(200, {"id": jid, "status": "cancelled"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
