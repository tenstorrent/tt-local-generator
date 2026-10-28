"""ServerControl with a fake for every dependency.

No test runs the real tt, docker or gozer: tt, gozer, the boutique scripts,
docker inspect and docker stop are all in-memory fakes that record their calls
in one shared, ordered list (``Env.calls``) so tests can assert both what was
called and in which order.
"""
import logging
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import server_control as sc_mod
from gozer_client import AdoptRefused, Lease, Queued
from server_control import ServerControl
from server_registry import ModelEntry
from server_view import RunningServer, ServerRecords
from tt_backend import ServePlan, TtResult


# --------------------------------------------------------------------------
# Fixtures and fakes
# --------------------------------------------------------------------------

def entry(key, *, backend="tt", tt_name=None, sm_key=None, group="artgen",
          chips=None, port=None, served_id=None, name=None):
    return ModelEntry(key=key, name=name or key, capabilities=(), backend=backend,
                      tt_name=tt_name, sm_key=sm_key, hardware_group=group,
                      chips=chips, port=port, served_id=served_id, runner_key=None,
                      installed=True, benefit="")


REGISTRY = {
    # a tt-model bundle: pinnable (plan says tt-model, 2 chips)
    "ns/bundle": entry("ns/bundle", tt_name="ns/bundle"),
    # a catalog model: unpinnable, 2 chips from the override/catalog
    "Cat-7B": entry("Cat-7B", tt_name="Cat-7B", chips=2),
    # a catalog model the operator prefers through tt that our script also serves
    "wan2.2": entry("wan2.2", tt_name="Wan2.2-T2V", sm_key="wan2.2", group="media", chips=2),
    # boutique models (script path), media group
    "flux": entry("flux", backend="boutique", sm_key="flux", group="media", port=8000),
    "wan-old": entry("wan-old", backend="boutique", sm_key="wan-old", group="media", port=8000),
    "wan2.2-i2v": entry("wan2.2-i2v", backend="boutique", sm_key="wan2.2-i2v",
                        group="media", port=8000),
    # a tt model whose hardware group is unknown
    "Media-X": entry("Media-X", tt_name="Media-X", group=None, chips=2),
    # a catalog model with no chip count anywhere
    "Cat-unknown": entry("Cat-unknown", tt_name="Cat-unknown", chips=None),
}

PLANS = {
    "ns/bundle": ServePlan("ns/bundle", "tt-model", (), "p300", None, 2),
    "Cat-7B": ServePlan("Cat-7B", "inference-server", (), "p300x2", None, None),
    "Wan2.2-T2V": ServePlan("Wan2.2-T2V", "inference-server", (), "p300x2", None, None),
    "Media-X": ServePlan("Media-X", "inference-server", (), "p300x2", None, None),
    "Cat-unknown": ServePlan("Cat-unknown", "inference-server", (), "p300x2", None, None),
}

STATUS = {"chips": [
    {"bdf": "0000:01:00.0", "dev_index": 0, "board": "B1", "state": "FREE", "who": None},
    {"bdf": "0000:02:00.0", "dev_index": 1, "board": "B1", "state": "FREE", "who": None},
    {"bdf": "0000:03:00.0", "dev_index": 2, "board": "B2", "state": "FREE", "who": None},
    {"bdf": "0000:04:00.0", "dev_index": 3, "board": "B2", "state": "FREE", "who": None},
]}


def rs(key, *, name=None, source="tt", url=None, local=True, port=8011,
       health="healthy", uptime_s=None, container="c-" + "x", container_id="60e0b238c988",
       served_id=None, owner="external"):
    return RunningServer(key=key, name=name or key, source=source,
                         url=url or f"http://127.0.0.1:{port}/v1", host_is_local=local,
                         port=port, health=health, uptime_s=uptime_s, container=container,
                         container_id=container_id, served_id=served_id, owner=owner)


class CP:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class Env:
    """Holds the fake world: the running-server view, and every recorded call."""

    def __init__(self, tmp_path):
        self.calls = []
        self.view = []
        self.view_fail = False
        self.records = ServerRecords(tmp_path / "servers.json")
        self.confirm_answers = {}
        self.confirms = []
        self.t = 0.0
        self.sleep_hook = None
        self.status_map = {}
        self.progress = []
        # tt behaviour
        self.serve_result = TtResult(True, "", ("booting", "ready"))
        self.serve_adds = None          # RunningServer added to the view on serve
        self.stop_result = TtResult(True, "")
        # gozer behaviour
        self.gozer_available = True
        self.acquire_result = Lease("L1", ("0000:03:00.0", "0000:04:00.0"))
        self.wait_result = None
        self.cancel_ticket_result = True
        self.find_lease_result = None
        self.adopt_results = {}
        self.status = {"chips": [dict(r) for r in STATUS["chips"]]}
        self.free = ["0000:01:00.0", "0000:02:00.0", "0000:03:00.0", "0000:04:00.0"]
        # boutique behaviour
        self.boutique_start_rc = 0
        self.boutique_adds = None
        # docker
        # the bundle container (started_bundle) opened the leased chips 2,3
        self.devices = {"60e0b238c988": ["/dev/tenstorrent/2", "/dev/tenstorrent/3"]}
        self.view_fail_on = set()       # 1-based view_fn call numbers that raise
        self.view_calls = 0
        self.boutique_stop_rc = 0
        self.docker_stop_result = TtResult(True, "")
        self.port_containers = {}
        # hooks: run inside gozer.wait / tt.serve, to model a race
        self.wait_hook = None
        self.serve_hook = None
        self.serve_kwargs = []
        # endpoint probe (for rows whose health is empty / "running")
        self.probe = lambda url: False
        self.reset_result = True

        env = self

        class TT:
            @staticmethod
            def plan_serve(model, port=None, device=None):
                env.calls.append(("tt.plan_serve", model))
                return PLANS.get(model)

            @staticmethod
            def serve(model, *, port=None, extra_args=(), on_line=None, cancel=None, **kw):
                env.calls.append(("tt.serve", model, tuple(extra_args)))
                env.serve_kwargs.append(dict(kw, port=port))
                for ln in env.serve_result.lines:
                    if on_line:
                        on_line(ln)
                if env.serve_adds is not None:
                    env.view.append(env.serve_adds)
                if env.serve_hook:
                    env.serve_hook()
                return env.serve_result

            @staticmethod
            def stop(names):
                env.calls.append(("tt.stop", tuple(names)))
                if env.stop_result.ok:
                    env.view[:] = [s for s in env.view if s.name not in names]
                return env.stop_result

        class Gozer:
            @staticmethod
            def available():
                env.calls.append(("gozer.available",))
                return env.gozer_available

            @staticmethod
            def acquire(chips, who, reason, expect="2h", queue=True):
                env.calls.append(("gozer.acquire", chips, who))
                return env.acquire_result

            @staticmethod
            def wait(ticket, timeout, cancel=None):
                env.calls.append(("gozer.wait", ticket))
                if env.wait_hook:
                    env.wait_hook()
                return env.wait_result

            @staticmethod
            def cancel_ticket(ticket):
                env.calls.append(("gozer.cancel_ticket", ticket))
                return env.cancel_ticket_result

            @staticmethod
            def find_lease(who):
                env.calls.append(("gozer.find_lease", who))
                return env.find_lease_result

            @staticmethod
            def release(lease_id):
                env.calls.append(("gozer.release", lease_id))
                return True

            @staticmethod
            def adopt(target, who, reason):
                env.calls.append(("gozer.adopt", target, who))
                return env.adopt_results.get(target)

            @staticmethod
            def status():
                env.calls.append(("gozer.status",))
                return env.status

            @staticmethod
            def free_chips():
                env.calls.append(("gozer.free_chips",))
                return list(env.free)

        class Boutique:
            @staticmethod
            def start(key, gui=True):
                env.calls.append(("boutique.start", key))
                if env.boutique_adds is not None and env.boutique_start_rc == 0:
                    env.view.append(env.boutique_adds)
                return [CP(env.boutique_start_rc, "script out\nlast script line", "")]

            @staticmethod
            def stop(key):
                env.calls.append(("boutique.stop", key))
                if env.boutique_stop_rc == 0:
                    env.view[:] = [s for s in env.view if s.key != key]
                return [CP(env.boutique_stop_rc, "", "stop failed" if env.boutique_stop_rc else "")]

        self.tt, self.gozer, self.boutique = TT, Gozer, Boutique

    # injected callables -------------------------------------------------
    def view_fn(self):
        self.view_calls += 1
        if self.view_fail or self.view_calls in self.view_fail_on:
            raise RuntimeError("ps failed")
        return list(self.view)

    def confirm_fn(self, kind, text):
        self.confirms.append((kind, text))
        self.calls.append(("confirm", kind))
        return self.confirm_answers.get(kind, False)

    def docker_devices(self, cid):
        self.calls.append(("docker.devices", cid))
        return self.devices.get(cid)

    def docker_stop(self, cid):
        self.calls.append(("docker.stop", cid))
        self.view[:] = [s for s in self.view if s.container_id != cid]
        return self.docker_stop_result

    def docker_container_for_port(self, port):
        self.calls.append(("docker.container_for_port", port))
        return self.port_containers.get(port)

    def probe_fn(self, url):
        self.calls.append(("probe", url))
        return self.probe(url)

    def reset_fn(self):
        self.calls.append(("reset",))
        return self.reset_result

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.sleep_hook:
            self.sleep_hook()

    def control(self, **kw):
        return ServerControl(
            registry_fn=lambda: REGISTRY, view_fn=self.view_fn, tt=self.tt,
            gozer=self.gozer, records=self.records, boutique=self.boutique,
            status_of=lambda k: self.status_map.get(k, "off"),
            confirm_fn=self.confirm_fn, docker_devices=self.docker_devices,
            docker_stop=self.docker_stop, clock=self.clock, sleep=self.sleep,
            docker_container_for_port=self.docker_container_for_port,
            probe_fn=self.probe_fn, reset_fn=self.reset_fn, **kw)

    def names(self):
        return [c[0] for c in self.calls]

    def index(self, call):
        return self.calls.index(call)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def started_bundle(cid="60e0b238c988"):
    return rs("ns/bundle", name="ns/bundle", container_id=cid, port=20000)


def started_cat(cid="abcdef123456", uptime_s=5):
    return rs("Cat-7B", name="Cat-7B", container_id=cid, port=20001, uptime_s=uptime_s)


# --------------------------------------------------------------------------
# Already running / booting elsewhere
# --------------------------------------------------------------------------

def test_already_running_anywhere_calls_no_serve(env):
    env.view = [rs("ns/bundle", name="ns/bundle", url="http://10.0.0.5:8011/v1", local=False,
                   owner="unknown")]
    out = env.control().start("ns/bundle")
    assert out.kind == "already_running"
    assert out.url == "http://10.0.0.5:8011/v1"
    assert "tt.serve" not in env.names() and "boutique.start" not in env.names()
    assert "gozer.acquire" not in env.names() and "confirm" not in env.names()


def test_still_booting_elsewhere_waits_then_reports_started(env):
    env.view = [rs("ns/bundle", name="ns/bundle", health="starting")]

    def becomes_healthy():
        env.view = [rs("ns/bundle", name="ns/bundle", health="healthy")]
    env.sleep_hook = becomes_healthy
    out = env.control().start("ns/bundle", on_progress=env.progress.append)
    assert out.kind == "already_running"
    assert out.url == "http://127.0.0.1:8011/v1"
    assert "tt.serve" not in env.names()
    assert any("started elsewhere" in p or "someone else" in p for p in env.progress)


def test_booting_elsewhere_that_never_gets_healthy_fails_after_the_timeout(env):
    env.view = [rs("ns/bundle", name="ns/bundle", health="starting")]
    out = env.control(elsewhere_timeout=30).start("ns/bundle")
    assert out.kind == "failed"
    assert "tt.serve" not in env.names()
    assert env.t >= 30


def test_booting_elsewhere_cancel_reports_starting_elsewhere(env):
    env.view = [rs("ns/bundle", name="ns/bundle", health="starting")]
    out = env.control().start("ns/bundle", cancel=lambda: True)
    assert out.kind == "starting_elsewhere"
    assert "tt.serve" not in env.names()


def test_unreadable_view_starts_nothing(env):
    env.view_fail = True
    out = env.control().start("ns/bundle")
    assert out.kind == "failed"
    assert "tt.serve" not in env.names()


# --------------------------------------------------------------------------
# Switch / unknown hardware / unknown chips
# --------------------------------------------------------------------------

def test_conflict_asks_and_decline_starts_nothing(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None)]
    out = env.control().start("flux")
    assert out.kind == "declined"
    assert ("confirm", "switch") in env.calls
    text = env.confirms[0][1]
    assert "wan-old" in text and "flux" in text
    assert "boutique.stop" not in env.names() and "boutique.start" not in env.names()
    assert "tt.stop" not in env.names() and "tt.serve" not in env.names()


def test_accepted_switch_stops_the_incumbent_then_starts(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None)]
    env.confirm_answers = {"switch": True}
    env.boutique_adds = rs("flux", source="script", port=8000, container_id=None)
    out = env.control().start("flux")
    assert out.kind == "started", out.message
    assert env.index(("boutique.stop", "wan-old")) < env.index(("boutique.start", "flux"))


def test_unknown_hardware_group_asks(env):
    env.gozer_available = False
    out = env.control().start("Media-X")
    assert out.kind == "declined"
    assert ("confirm", "unknown_hardware") in env.calls
    assert "Media-X" in env.confirms[0][1]
    assert "tt.serve" not in env.names()


def test_unknown_hardware_group_accepted_starts(env):
    env.gozer_available = False
    env.confirm_answers = {"unknown_hardware": True}
    env.serve_adds = rs("Media-X", name="Media-X", port=20002)
    out = env.control().start("Media-X")
    assert out.kind == "started", out.message
    assert ("tt.serve", "Media-X", ()) in env.calls


def test_unknown_chip_count_warns_and_asks_instead_of_blocking(env):
    out = env.control().start("Cat-unknown")
    assert out.kind == "declined"
    assert ("confirm", "unknown_chips") in env.calls
    assert "tt.serve" not in env.names()
    # Accepting goes ahead (unpinnable flow: adopt after start, no acquire).
    env.calls.clear()
    env.confirm_answers = {"unknown_chips": True}
    env.serve_adds = rs("Cat-unknown", name="Cat-unknown", container_id="abcabcabcabc",
                        port=20003)
    env.devices = {"abcabcabcabc": ["/dev/tenstorrent/0"]}
    env.adopt_results = {"0": Lease("A0", ("0000:01:00.0",))}
    out = env.control().start("Cat-unknown")
    assert out.kind == "started", out.message
    assert "gozer.acquire" not in env.names()


# --------------------------------------------------------------------------
# Pinnable flow
# --------------------------------------------------------------------------

def test_pinnable_bundle_acquires_first_then_starts_pinned(env):
    env.serve_adds = started_bundle()
    env.status["chips"][2]["state"] = "HELD-FOREIGN"
    env.status["chips"][3]["state"] = "HELD-FOREIGN"
    ctl = env.control()
    out = ctl.start("ns/bundle", on_progress=env.progress.append)
    assert out.kind == "started", out.message
    assert out.lease_id == "L1"
    assert ("gozer.acquire", 2, ctl.who_for("ns/bundle")) in env.calls
    # leased BDFs 03:00.0/04:00.0 are device indices 2 and 3
    serve = ("tt.serve", "ns/bundle", ("--device-id", "2,3"))
    assert env.index(("gozer.acquire", 2, ctl.who_for("ns/bundle"))) < env.index(serve)
    assert "gozer.adopt" not in env.names()
    # serve's lines go straight to on_progress
    assert "booting" in env.progress and "ready" in env.progress
    rec = env.records.load()
    assert rec == [{"name": "ns/bundle", "port": 20000, "container_id": "60e0b238c988",
                    "lease_id": "L1", "started_at": rec[0]["started_at"]}]


def test_pinnable_flow_checks_lease_status_after_healthy(env, caplog):
    env.serve_adds = started_bundle()
    env.status["chips"][2]["state"] = "CLAIMED"
    with caplog.at_level(logging.WARNING, logger="server_control"):
        out = env.control().start("ns/bundle")
    assert out.kind == "started"
    status_calls = [i for i, c in enumerate(env.calls) if c == ("gozer.status",)]
    assert status_calls and status_calls[-1] > env.names().index("tt.serve")
    assert any("CLAIMED" in r.message and "reap" in r.message for r in caplog.records)

    # HELD-FOREIGN is normal for a container: no warning.
    caplog.clear()
    env2 = Env(env.records.path.parent / "two")
    env2.serve_adds = started_bundle()
    env2.status["chips"][2]["state"] = "HELD-FOREIGN"
    env2.status["chips"][3]["state"] = "HELD-FOREIGN"
    with caplog.at_level(logging.WARNING, logger="server_control"):
        assert env2.control().start("ns/bundle").kind == "started"
    assert not any("reap" in r.message for r in caplog.records)


def test_queued_lease_reports_waiting_and_cancel_cleans_up(env):
    env.acquire_result = Queued("T1")
    env.wait_result = None              # cancelled / timed out
    env.cancel_ticket_result = False    # the ticket was consumed in the race
    env.find_lease_result = Lease("L9", ("0000:03:00.0",))
    ctl = env.control()
    out = ctl.start("ns/bundle", on_progress=env.progress.append, cancel=lambda: True)
    assert out.kind == "cancelled"
    assert any("waiting for chips" in p.lower() for p in env.progress)
    assert env.index(("gozer.wait", "T1")) < env.index(("gozer.cancel_ticket", "T1"))
    assert env.index(("gozer.cancel_ticket", "T1")) < env.index(
        ("gozer.find_lease", ctl.who_for("ns/bundle")))
    assert ("gozer.release", "L9") in env.calls
    assert "tt.serve" not in env.names()
    assert env.records.load() == []


def test_queued_cancel_that_drops_the_ticket_does_not_look_for_a_lease(env):
    env.acquire_result = Queued("T1")
    env.cancel_ticket_result = True
    out = env.control().start("ns/bundle")
    assert out.kind == "waiting_for_chips"
    assert "gozer.find_lease" not in env.names() and "gozer.release" not in env.names()


def test_cancel_race_never_releases_a_lease_another_server_holds(env):
    env.records.add("other", 9000, "999999999999", "L9", 1.0)
    env.acquire_result = Queued("T1")
    env.cancel_ticket_result = False
    env.find_lease_result = Lease("L9", ("0000:03:00.0",))
    env.control().start("ns/bundle")
    assert "gozer.release" not in env.names()


def test_queued_lease_granted_after_wait_starts(env):
    env.acquire_result = Queued("T1")
    env.wait_result = Lease("L2", ("0000:01:00.0", "0000:02:00.0"))
    env.serve_adds = started_bundle()
    env.devices = {"60e0b238c988": ["/dev/tenstorrent/0", "/dev/tenstorrent/1"]}
    out = env.control().start("ns/bundle")
    assert out.kind == "started" and out.lease_id == "L2"
    assert ("tt.serve", "ns/bundle", ("--device-id", "0,1")) in env.calls


def test_failed_start_stops_and_releases_the_lease(env):
    env.serve_result = TtResult(False, "tt serve exited 1", ("pulling", "error: out of memory"))
    out = env.control().start("ns/bundle")
    assert out.kind == "failed"
    assert "error: out of memory" in out.message
    assert ("tt.stop", ("ns/bundle",)) in env.calls
    assert ("gozer.release", "L1") in env.calls
    assert env.index(("tt.stop", ("ns/bundle",))) < env.index(("gozer.release", "L1"))
    assert out.lease_id is None
    assert env.records.load() == []


def test_server_that_never_gets_healthy_is_stopped_and_its_lease_released(env):
    env.serve_adds = rs("ns/bundle", name="ns/bundle", health="starting", port=20000)
    out = env.control(start_timeout=60).start("ns/bundle")
    assert out.kind == "failed"
    assert "confirm" not in env.names()          # on the leased chips: proven ours
    assert ("tt.stop", ("ns/bundle",)) in env.calls
    assert ("gozer.release", "L1") in env.calls
    assert env.records.load() == []


def test_failed_stop_after_failed_start_keeps_the_lease_and_records_it(env):
    # Releasing resets the chips: never do that under a container still running.
    env.serve_result = TtResult(False, "boom", ("boom",))
    env.stop_result = TtResult(False, "tt model stop exited 1")
    out = env.control().start("ns/bundle")
    assert out.kind == "failed"
    assert "gozer.release" not in env.names()
    assert [r["lease_id"] for r in env.records.load()] == ["L1"]


# --------------------------------------------------------------------------
# Unpinnable flow
# --------------------------------------------------------------------------

def test_unpinnable_start_does_not_acquire_first(env):
    env.serve_adds = started_cat()
    env.devices = {"abcdef123456": ["/dev/tenstorrent/2", "/dev/tenstorrent/3"]}
    env.adopt_results = {"2": Lease("A2", ("0000:03:00.0",)),
                         "3": Lease("A3", ("0000:04:00.0",))}
    out = env.control().start("Cat-7B")
    assert out.kind == "started", out.message
    assert "gozer.acquire" not in env.names()
    assert env.index(("gozer.free_chips",)) < env.names().index("tt.serve")
    assert ("tt.serve", "Cat-7B", ()) in env.calls     # never pinned


def test_unpinnable_tt_start_adopts_the_chips_the_container_opened(env):
    env.serve_adds = started_cat()
    env.devices = {"abcdef123456": ["/dev/tenstorrent/2", "/dev/tenstorrent/3"]}
    ctl = env.control()
    who = ctl.who_for("Cat-7B")
    env.adopt_results = {"2": Lease("A2", ("0000:03:00.0", "0000:04:00.0")),
                         "3": AdoptRefused(who)}    # same board, already ours
    out = ctl.start("Cat-7B")
    assert out.kind == "started", out.message
    assert env.index(("docker.devices", "abcdef123456")) > env.names().index("tt.serve")
    assert ("gozer.adopt", "2", who) in env.calls
    assert out.lease_id == "A2"
    assert env.records.load()[0]["lease_id"] == "A2"
    assert env.records.load()[0]["container_id"] == "abcdef123456"


def test_whole_directory_mount_adopts_every_chip(env):
    env.serve_adds = started_cat()
    env.devices = {"abcdef123456": ["/dev/tenstorrent"]}
    env.adopt_results = {i: Lease("A" + i, ()) for i in "0123"}
    out = env.control().start("Cat-7B")
    assert out.kind == "started"
    assert [c[1] for c in env.calls if c[0] == "gozer.adopt"] == ["0", "1", "2", "3"]


def test_adopt_finds_another_lease_on_those_chips_stops_the_new_server_and_reports_chips_held(env):
    env.serve_adds = rs("wan2.2", name="Wan2.2-T2V", container_id="abcdef123456", port=20001)
    env.devices = {"abcdef123456": ["/dev/tenstorrent/0", "/dev/tenstorrent/2"]}
    env.adopt_results = {"0": Lease("A0", ("0000:01:00.0",)),
                         "2": AdoptRefused("claude:someone-else")}
    env.confirm_answers = {"stop_external": True}
    out = env.control().start("wan2.2")
    assert out.kind == "chips_held"
    assert ("confirm", "stop_external") in env.calls
    assert "claude:someone-else" in env.confirms[-1][1]
    assert "claude:someone-else" in out.message
    assert ("tt.stop", ("Wan2.2-T2V",)) in env.calls
    # the lease adopted before the refusal is released, after the stop
    assert env.index(("tt.stop", ("Wan2.2-T2V",))) < env.index(("gozer.release", "A0"))
    # a boutique alternative is offered
    assert "script" in out.message.lower()
    assert env.records.load() == []
    assert out.lease_id is None


def test_adopt_none_is_a_failure_and_stops_the_server(env):
    env.serve_adds = started_cat()
    env.devices = {"abcdef123456": ["/dev/tenstorrent/2"]}
    env.adopt_results = {}               # adopt returns None
    env.confirm_answers = {"stop_external": True}
    out = env.control().start("Cat-7B")
    assert out.kind == "failed"
    assert ("tt.stop", ("Cat-7B",)) in env.calls
    assert env.records.load() == []


def test_adopt_refused_for_our_own_lease_is_not_a_conflict(env):
    env.serve_adds = started_cat()
    env.devices = {"abcdef123456": ["/dev/tenstorrent/2"]}
    ctl = env.control()
    who = ctl.who_for("Cat-7B")
    env.adopt_results = {"2": AdoptRefused(who)}
    env.find_lease_result = Lease("OWN", ("0000:03:00.0",))
    out = ctl.start("Cat-7B")
    assert out.kind == "started", out.message
    assert "tt.stop" not in env.names()
    assert out.lease_id == "OWN"


def test_container_devices_unreadable_stops_the_server(env):
    env.serve_adds = started_cat()
    env.devices = {}                     # docker inspect failed -> None
    env.confirm_answers = {"stop_external": True}
    out = env.control().start("Cat-7B")
    assert out.kind == "failed"
    assert ("tt.stop", ("Cat-7B",)) in env.calls
    assert "gozer.adopt" not in env.names()


def test_too_few_free_chips_reports_chips_held_without_starting(env):
    env.free = ["0000:01:00.0"]
    out = env.control().start("Cat-7B")
    assert out.kind == "chips_held"
    assert "tt.serve" not in env.names()


def test_no_gozer_means_no_lease_logic(env):
    env.gozer_available = False
    env.serve_adds = started_bundle()
    out = env.control().start("ns/bundle")
    assert out.kind == "started"
    gozer_calls = [n for n in env.names() if n.startswith("gozer.") and n != "gozer.available"]
    assert gozer_calls == []
    assert "docker.devices" not in env.names()
    assert ("tt.serve", "ns/bundle", ()) in env.calls      # no pin without a lease
    assert env.records.load()[0]["lease_id"] is None


def test_tt_that_takes_its_own_lease_means_no_lease_logic(env):
    env.serve_adds = started_bundle()
    out = env.control(tt_takes_lease=True).start("ns/bundle")
    assert out.kind == "started"
    assert not [n for n in env.names() if n.startswith("gozer.")]


def test_boutique_entry_uses_the_script_path(env):
    env.gozer_available = False
    env.boutique_adds = rs("flux", source="script", port=8000, container_id=None)
    out = env.control().start("flux")
    assert out.kind == "started", out.message
    assert ("boutique.start", "flux") in env.calls
    assert "tt.serve" not in env.names()
    assert env.records.load()[0]["name"] == "flux"


def test_boutique_script_failure_shows_its_last_lines(env):
    env.gozer_available = False
    env.boutique_start_rc = 1
    out = env.control().start("flux")
    assert out.kind == "failed"
    assert "last script line" in out.message
    assert ("boutique.stop", "flux") in env.calls


# --------------------------------------------------------------------------
# Stop
# --------------------------------------------------------------------------

def test_stop_app_owned_does_not_ask_and_releases_lease(env):
    env.records.add("ns/bundle", 20000, "60e0b238c988", "L1", 1.0)
    env.records.add("other", 9000, "999999999999", "L-other", 1.0)
    srv = rs("ns/bundle", name="ns/bundle", port=20000, owner="app")
    env.view = [srv]
    out = env.control().stop(srv)
    assert out.kind == "stopped"
    assert "confirm" not in env.names()
    assert ("tt.stop", ("ns/bundle",)) in env.calls
    assert [c for c in env.calls if c[0] == "gozer.release"] == [("gozer.release", "L1")]
    assert env.index(("tt.stop", ("ns/bundle",))) < env.index(("gozer.release", "L1"))
    assert [r["name"] for r in env.records.load()] == ["other"]


def test_stop_external_local_asks_and_decline_leaves_it_running(env):
    srv = rs("ns/bundle", name="ns/bundle", container="tt-model-qwen", uptime_s=3720,
             owner="external")
    out = env.control().stop(srv)
    assert out.kind == "declined"
    assert ("confirm", "stop_external") in env.calls
    text = env.confirms[0][1]
    assert "ns/bundle" in text and "tt-model-qwen" in text and "1h 2m" in text
    assert "tt" in text            # its backend/source
    assert not [n for n in env.names() if n in ("tt.stop", "boutique.stop", "docker.stop")]
    assert "gozer.release" not in env.names()


def test_stop_external_accepted_never_releases_a_lease(env):
    env.records.add("ns/bundle", 20000, "000000000000", "L-stale", 1.0)
    env.confirm_answers = {"stop_external": True}
    srv = rs("ns/bundle", name="ns/bundle", owner="external")
    out = env.control().stop(srv)
    assert out.kind == "stopped"
    assert "gozer.release" not in env.names()


def test_stop_remote_is_not_allowed(env):
    srv = rs("ns/bundle", name="ns/bundle", url="http://10.0.0.5:8011/v1", local=False,
             owner="unknown")
    out = env.control().stop(srv)
    assert out.kind == "not_allowed"
    assert env.calls == []


def test_stop_unknown_name_uses_boutique_or_docker_stop_not_tt_stop(env):
    env.confirm_answers = {"stop_external": True}
    ctl = env.control()
    srv = rs("wan2.2-i2v", name="Wan-AI/Wan2.2-I2V-A14B-Diffusers", container_id="111111111111")
    assert ctl.stop(srv).kind == "stopped"
    assert ("boutique.stop", "wan2.2-i2v") in env.calls
    srv2 = rs("Some/Repo", name="Some/Repo", container_id="c0ffeec0ffee")
    assert ctl.stop(srv2).kind == "stopped"
    assert ("docker.stop", "c0ffeec0ffee") in env.calls
    assert "tt.stop" not in env.names()


def test_failed_stop_of_app_server_keeps_its_lease(env):
    env.records.add("ns/bundle", 20000, "60e0b238c988", "L1", 1.0)
    env.stop_result = TtResult(False, "tt model stop exited 1", ("still busy",))
    srv = rs("ns/bundle", name="ns/bundle", port=20000, owner="app")
    out = env.control().stop(srv)
    assert out.kind == "failed"
    assert "gozer.release" not in env.names()
    assert env.records.load()[0]["lease_id"] == "L1"


# --------------------------------------------------------------------------
# Restart
# --------------------------------------------------------------------------

def test_restart_external_asks_then_stops_then_starts(env):
    env.gozer_available = False
    env.confirm_answers = {"stop_external": True}
    srv = rs("ns/bundle", name="ns/bundle", owner="external")
    env.view = [srv]
    env.serve_adds = started_bundle()
    out = env.control().restart(srv)
    assert out.kind == "started", out.message
    i_confirm = env.index(("confirm", "stop_external"))
    i_stop = env.index(("tt.stop", ("ns/bundle",)))
    i_serve = env.names().index("tt.serve")
    assert i_confirm < i_stop < i_serve


def test_restart_declined_starts_nothing(env):
    srv = rs("ns/bundle", name="ns/bundle", owner="external")
    env.view = [srv]
    out = env.control().restart(srv)
    assert out.kind == "declined"
    assert "tt.stop" not in env.names() and "tt.serve" not in env.names()


# --------------------------------------------------------------------------
# Lease reaping
# --------------------------------------------------------------------------

def test_reap_prunes_only_on_the_second_miss(env):
    env.records.add("gone", 9000, "aaaaaaaaaaaa", "L-gone", 1.0)
    env.records.add("live", 9001, "bbbbbbbbbbbb", "L-live", 1.0)
    ctl = env.control()
    assert ctl.reap_stale_leases({"bbbbbbbbbbbb"}, set()) == []
    assert "gozer.release" not in env.names()
    assert {r["name"] for r in env.records.load()} == {"gone", "live"}
    assert ctl.reap_stale_leases({"bbbbbbbbbbbb"}, set()) == ["L-gone"]
    assert [c for c in env.calls if c[0] == "gozer.release"] == [("gozer.release", "L-gone")]
    assert {r["name"] for r in env.records.load()} == {"live"}


def test_reap_forgets_a_suspect_that_comes_back(env):
    env.records.add("flaky", 9000, "aaaaaaaaaaaa", "L-flaky", 1.0)
    ctl = env.control()
    assert ctl.reap_stale_leases(set(), set()) == []
    assert ctl.reap_stale_leases({"aaaaaaaaaaaa"}, set()) == []   # seen again
    assert ctl.reap_stale_leases(set(), set()) == []               # first miss again
    assert "gozer.release" not in env.names()


def test_reap_with_ps_failure_none_prunes_nothing(env):
    env.records.add("gone", 9000, "aaaaaaaaaaaa", "L-gone", 1.0)
    ctl = env.control()
    assert ctl.reap_stale_leases(None, None) == []
    assert ctl.reap_stale_leases(None, None) == []
    assert ctl.reap_stale_leases(None, None) == []
    assert "gozer.release" not in env.names()
    assert len(env.records.load()) == 1


def test_reap_script_record_by_endpoint(env):
    env.records.add("flux", 8000, None, "L-flux", 1.0)
    ctl = env.control()
    assert ctl.reap_stale_leases(set(), {("flux", 8000)}) == []
    assert ctl.reap_stale_leases(set(), set()) == []
    assert ctl.reap_stale_leases(set(), set()) == ["L-flux"]


# --------------------------------------------------------------------------
# Default docker helpers (argv lists, timeouts, read-only inspect)
# --------------------------------------------------------------------------

def test_default_docker_devices_reads_inspect_json(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, stdout=(
            '[{"HostConfig": {"Devices": [{"PathOnHost": "/dev/tenstorrent/0"},'
            '{"PathOnHost": "/dev/tenstorrent/1"}], "Binds": ["/x:/y"]},'
            '"Mounts": [{"Source": "/dev/tenstorrent", "Destination": "/dev/tenstorrent"}]}]'),
            stderr="")
    monkeypatch.setattr(sc_mod.subprocess, "run", fake_run)
    assert sc_mod.docker_devices("abc123") == [
        "/dev/tenstorrent/0", "/dev/tenstorrent/1", "/dev/tenstorrent"]
    assert seen["argv"] == ["docker", "inspect", "abc123"]
    assert seen["kw"]["timeout"] and seen["kw"]["stdin"] == subprocess.DEVNULL
    assert sc_mod.docker_devices("-rf") is None


def test_default_docker_stop_uses_a_long_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, stdout="abc123\n", stderr="")
    monkeypatch.setattr(sc_mod.subprocess, "run", fake_run)
    assert sc_mod.docker_stop("abc123").ok
    assert seen["argv"][:2] == ["docker", "stop"] and seen["argv"][-1] == "abc123"
    assert int(seen["argv"][seen["argv"].index("--time") + 1]) >= 60
    assert seen["kw"]["timeout"] > 60


# --------------------------------------------------------------------------
# Fix round 1: races, unreapable records, fail-closed pin, reset, port lookup
# --------------------------------------------------------------------------

def saved_row(health="unknown", source="saved", cid="cafecafecafe"):
    return rs("ns/bundle", name="ns/bundle", source=source, health=health,
              container_id=cid, port=20000)


def test_healthy_server_started_elsewhere_during_chip_wait_is_used_not_adopted(env):
    env.acquire_result = Queued("T1")
    env.wait_result = Lease("L1", ("0000:03:00.0", "0000:04:00.0"))
    other = rs("ns/bundle", name="ns/bundle", container_id="ffffffffffff", port=20000)
    env.wait_hook = lambda: env.view.append(other)
    out = env.control().start("ns/bundle")
    assert out.kind == "already_running"
    assert out.url == other.url
    assert "tt.serve" not in env.names() and "tt.stop" not in env.names()
    assert "confirm" not in env.names()
    assert ("gozer.release", "L1") in env.calls      # our unused lease is given back
    assert env.records.load() == []                  # never recorded as app-owned


def test_booting_server_started_elsewhere_during_chip_wait_is_never_stopped(env):
    # The reviewer's race: another party boots the model while we queue.
    env.acquire_result = Queued("T1")
    env.wait_result = Lease("L1", ("0000:03:00.0", "0000:04:00.0"))
    env.serve_result = TtResult(False, "tt serve exited 1", ("port in use",))
    other = rs("ns/bundle", name="ns/bundle", health="starting",
               container_id="ffffffffffff", port=20000)
    env.wait_hook = lambda: env.view.append(other)
    out = env.control(elsewhere_timeout=30).start("ns/bundle")
    assert out.kind == "failed"
    assert "elsewhere" in out.message
    assert "tt.serve" not in env.names()
    assert not [n for n in env.names() if n in ("tt.stop", "boutique.stop", "docker.stop")]
    assert "confirm" not in env.names()
    assert ("gozer.release", "L1") in env.calls
    assert env.records.load() == []


def test_failed_start_leaves_a_server_listed_before_launch_alone(env):
    # A row of this model existed before launch (a saved local entry): the
    # by-name fallback stop would hit it, so nothing is stopped.
    env.view = [saved_row()]
    env.serve_result = TtResult(False, "tt serve exited 1", ("boom",))
    out = env.control().start("ns/bundle")
    assert out.kind == "failed"
    assert "left alone" in out.message
    assert not [n for n in env.names() if n in ("tt.stop", "boutique.stop", "docker.stop")]
    # Our lease may sit under our own half-booted container: kept, never reaped.
    assert "gozer.release" not in env.names()
    assert "gozer release L1" in out.message
    rec = env.records.load()
    assert [(r["lease_id"], r.get("unreapable")) for r in rec] == [("L1", True)]


def test_health_wait_ignores_a_row_that_existed_before_launch(env):
    env.view = [saved_row()]

    def other_party_brings_it_up():
        env.view[:] = [saved_row(health="healthy", source="tt")]
    env.serve_hook = other_party_brings_it_up
    out = env.control(start_timeout=30).start("ns/bundle")
    assert out.kind == "failed"
    # the other party's container is never recorded as ours
    assert not [r for r in env.records.load() if r.get("container_id") == "cafecafecafe"]
    assert not [n for n in env.names() if n in ("tt.stop", "boutique.stop", "docker.stop")]


def test_failed_stop_without_identity_marks_record_unreapable(env):
    env.serve_result = TtResult(False, "boom", ("boom",))
    env.stop_result = TtResult(False, "tt model stop exited 1")
    out = env.control().start("ns/bundle")
    assert out.kind == "failed"
    assert "L1" in out.message
    rec = env.records.load()
    assert len(rec) == 1 and rec[0]["lease_id"] == "L1" and rec[0]["unreapable"] is True
    ctl = env.control()
    assert ctl.reap_stale_leases(set(), set()) == []
    assert ctl.reap_stale_leases(set(), set()) == []
    assert "gozer.release" not in env.names()
    assert len(env.records.load()) == 1


def test_failed_stop_record_takes_identity_from_the_view(env):
    env.serve_result = TtResult(False, "boom", ("boom",))
    env.serve_adds = rs("ns/bundle", name="ns/bundle", health="starting",
                        container_id="dddddddddddd", port=20000)
    env.devices = {"dddddddddddd": ["/dev/tenstorrent/2", "/dev/tenstorrent/3"]}
    env.stop_result = TtResult(False, "tt model stop exited 1")
    env.control().start("ns/bundle")
    rec = env.records.load()[0]
    assert (rec["container_id"], rec["port"], rec["lease_id"]) == ("dddddddddddd", 20000, "L1")
    assert not rec.get("unreapable")


def test_pinned_serve_passes_its_plan_so_tt_backend_needs_no_second_dry_run(env):
    env.serve_adds = started_bundle()
    env.control().start("ns/bundle")
    assert env.serve_kwargs[0]["plan"] is PLANS["ns/bundle"]


def test_accepted_switch_resets_once_without_gozer(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None)]
    env.confirm_answers = {"switch": True}
    env.boutique_adds = rs("flux", source="script", port=8000, container_id=None)
    out = env.control().start("flux")
    assert out.kind == "started", out.message
    assert env.names().count("reset") == 1
    assert (env.index(("boutique.stop", "wan-old")) < env.index(("reset",))
            < env.index(("boutique.start", "flux")))


def test_accepted_switch_never_resets_on_a_gozer_box(env, caplog):
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None)]
    env.confirm_answers = {"switch": True, "unknown_chips": True}
    with caplog.at_level(logging.INFO, logger="server_control"):
        env.control().start("flux")
    assert "reset" not in env.names()
    assert any("reset" in r.message.lower() and "gozer" in r.message.lower()
               for r in caplog.records)


def test_declined_switch_never_resets(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None)]
    assert env.control().start("flux").kind == "declined"
    assert "reset" not in env.names()


def test_failed_reset_starts_nothing(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None)]
    env.confirm_answers = {"switch": True}
    env.reset_result = False
    out = env.control().start("flux")
    assert out.kind == "failed"
    assert "boutique.start" not in env.names()


def test_script_server_container_found_by_port_is_adopted(env):
    env.confirm_answers = {"unknown_chips": True}
    env.boutique_adds = rs("flux", source="script", port=8000, container_id=None)
    env.port_containers = {8000: "aaaa1111bbbb"}
    env.devices = {"aaaa1111bbbb": ["/dev/tenstorrent/0"]}
    env.adopt_results = {"0": Lease("A0", ("0000:01:00.0",))}
    out = env.control().start("flux")
    assert out.kind == "started", out.message
    assert out.lease_id == "A0"
    assert ("docker.devices", "aaaa1111bbbb") in env.calls
    # ownership of a script row is by endpoint, so its record keeps no container id
    rec = env.records.load()[0]
    assert (rec["name"], rec["port"], rec["container_id"]) == ("flux", 8000, None)


def test_script_server_whose_container_is_unknown_is_stopped_with_the_port_named(env):
    env.confirm_answers = {"unknown_chips": True, "stop_external": True}
    env.boutique_adds = rs("flux", source="script", port=8000, container_id=None)
    out = env.control().start("flux")
    assert out.kind == "failed"
    assert "8000" in out.message and "stopped" in out.message
    assert ("boutique.stop", "flux") in env.calls
    assert env.records.load() == []


def test_switch_prompt_names_an_external_incumbent(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None, uptime_s=120)]
    env.control().start("flux")
    text = env.confirms[0][1]
    assert "not started by tt-local-generator" in text and "2m" in text


def test_switch_prompt_for_an_app_owned_incumbent_does_not_call_it_external(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None, owner="app")]
    env.control().start("flux")
    assert "not started by tt-local-generator" not in env.confirms[0][1]


def test_remote_only_incumbent_is_not_stopped_locally(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="discovered", url="http://10.0.0.9:8000", local=False,
                   port=8000, container_id=None, owner="unknown")]
    env.confirm_answers = {"switch": True}
    out = env.control().start("flux")
    assert out.kind == "failed"
    assert "another machine" in out.message
    assert "boutique.stop" not in env.names() and "boutique.start" not in env.names()
    assert "confirm" not in env.names()


def test_own_adopt_refusal_merges_older_leases_of_the_model(env):
    env.records.add("Cat-7B", 20001, "000000000000", "PREV", 1.0)
    env.serve_adds = started_cat()
    env.devices = {"abcdef123456": ["/dev/tenstorrent/2", "/dev/tenstorrent/3"]}
    ctl = env.control()
    who = ctl.who_for("Cat-7B")
    env.adopt_results = {"2": Lease("A2", ("0000:03:00.0",)), "3": AdoptRefused(who)}
    env.find_lease_result = Lease("OLD", ("0000:04:00.0",))
    out = ctl.start("Cat-7B")
    assert out.kind == "started", out.message
    assert set(out.lease_id.split(",")) == {"A2", "OLD", "PREV"}
    assert set(env.records.load()[0]["lease_id"].split(",")) == {"A2", "OLD", "PREV"}


def test_stop_survives_an_oserror_from_the_script(env):
    def boom(key):
        raise OSError("bash missing")
    env.boutique.stop = staticmethod(boom)
    srv = rs("flux", source="script", port=8000, container_id=None, owner="app")
    out = env.control().stop(srv)
    assert out.kind == "failed" and "bash missing" in out.message


def test_restart_survives_an_oserror(env):
    def boom(key):
        raise OSError("bash missing")
    env.boutique.stop = staticmethod(boom)
    srv = rs("flux", source="script", port=8000, container_id=None, owner="app")
    out = env.control().restart(srv)
    assert out.kind == "failed"
    assert "boutique.start" not in env.names()


def test_reap_survives_a_records_error(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(env.records, "prune", boom)
    env.records.add("gone", 9000, "aaaaaaaaaaaa", "L-gone", 1.0)
    ctl = env.control()
    assert ctl.reap_stale_leases(set(), set()) == []
    assert ctl.reap_stale_leases(set(), set()) == []


def test_two_concurrent_starts_of_one_key_serve_once(env):
    import threading
    env.gozer_available = False
    entered, go = threading.Event(), threading.Event()
    calls = []

    def serve_hook():
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            go.wait(5)
            env.view.append(started_bundle())
    env.serve_hook = serve_hook
    ctl = env.control()
    outs = []
    a = threading.Thread(target=lambda: outs.append(ctl.start("ns/bundle")))
    a.start()
    assert entered.wait(5)
    b = threading.Thread(target=lambda: outs.append(ctl.start("ns/bundle")))
    b.start()
    b.join(0.3)
    go.set()
    a.join(5)
    b.join(5)
    assert len(calls) == 1
    assert sorted(o.kind for o in outs) == ["already_running", "started"]


def test_cancel_during_boot_returns_cancelled_and_cleans_up(env):
    env.serve_adds = rs("ns/bundle", name="ns/bundle", health="starting", port=20000)
    flag = []
    env.serve_hook = lambda: flag.append(1)
    out = env.control().start("ns/bundle", cancel=lambda: bool(flag))
    assert out.kind == "cancelled"
    assert ("tt.stop", ("ns/bundle",)) in env.calls
    assert ("gozer.release", "L1") in env.calls


def test_running_row_with_empty_health_waits_for_the_endpoint(env):
    # Real `tt model ps` may report status "running" with no health; merge
    # then shows health "running".
    env.view = [rs("ns/bundle", name="ns/bundle", health="running")]
    answers = iter([False, True])
    env.probe = lambda url: next(answers, True)
    out = env.control().start("ns/bundle")
    assert out.kind == "already_running"
    assert out.url == "http://127.0.0.1:8011/v1"
    assert "tt.serve" not in env.names()


def test_running_row_that_never_answers_fails_without_stopping_it(env):
    env.view = [rs("ns/bundle", name="ns/bundle", health="")]
    out = env.control(elsewhere_timeout=30).start("ns/bundle")
    assert out.kind == "failed"
    assert "tt.serve" not in env.names()
    assert not [n for n in env.names() if n in ("tt.stop", "boutique.stop", "docker.stop")]


def test_default_docker_container_for_port(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, stdout=seen.get("out", "abc123def456\n"),
                                           stderr="")
    monkeypatch.setattr(sc_mod.subprocess, "run", fake_run)
    assert sc_mod.docker_container_for_port(8000) == "abc123def456"
    assert seen["argv"] == ["docker", "ps", "--filter", "publish=8000", "--format", "{{.ID}}"]
    assert seen["kw"]["timeout"] and seen["kw"]["stdin"] == subprocess.DEVNULL
    seen["out"] = "aaa\nbbb\n"
    assert sc_mod.docker_container_for_port(8000) is None      # ambiguous
    seen.clear()
    for bad in (0, 70000, "8000; rm", None, True):
        assert sc_mod.docker_container_for_port(bad) is None
    assert "argv" not in seen


# --------------------------------------------------------------------------
# Fix round 2: adoption window, unreapable merge, left-alone leases, reset
# --------------------------------------------------------------------------

def theirs(key="Cat-7B", name="Cat-7B", cid="eeeeeeeeeeee", port=20009, health="healthy"):
    return rs(key, name=name, container_id=cid, port=port, health=health)


def unreapable(env):
    return [r for r in env.records.load() if r.get("unreapable")]


def test_two_new_rows_during_an_unpinnable_boot_adopt_none_and_stop_nothing(env):
    env.serve_hook = lambda: env.view.extend([started_cat(), theirs()])
    out = env.control().start("Cat-7B")
    assert out.kind == "failed"
    assert "gozer.adopt" not in env.names() and "docker.devices" not in env.names()
    assert not [n for n in env.names() if n in ("tt.stop", "boutique.stop", "docker.stop")]
    assert env.records.load() == []                  # nothing app-owned


def test_two_new_rows_during_a_pinned_boot_keep_the_lease_unreapable(env):
    env.serve_hook = lambda: env.view.extend(
        [started_bundle(), theirs("ns/bundle", "ns/bundle", port=20009)])
    out = env.control().start("ns/bundle")
    assert out.kind == "failed"
    assert not [n for n in env.names() if n in ("tt.stop", "gozer.release")]
    assert "gozer release L1" in out.message
    assert [r["lease_id"] for r in unreapable(env)] == ["L1"]
    assert not [r for r in env.records.load() if not r.get("unreapable")]


def test_pinned_boot_ignores_a_container_not_on_the_leased_chips(env):
    # Their bundle container appears during our boot, on chips 0 and 1.
    env.serve_adds = theirs("ns/bundle", "ns/bundle", port=20009)
    env.devices = {"eeeeeeeeeeee": ["/dev/tenstorrent/0", "/dev/tenstorrent/1"]}
    out = env.control().start("ns/bundle")
    assert out.kind == "failed"
    assert ("docker.devices", "eeeeeeeeeeee") in env.calls
    assert not [n for n in env.names() if n in ("tt.stop", "gozer.release")]
    assert not [r for r in env.records.load() if r.get("container_id") == "eeeeeeeeeeee"]
    assert [r["lease_id"] for r in unreapable(env)] == ["L1"]


def test_chips_held_asks_before_stopping_and_a_no_leaves_it_running(env):
    # Their container (with their own lease) appears during our unpinnable boot.
    env.serve_adds = started_cat()
    env.devices = {"abcdef123456": ["/dev/tenstorrent/0", "/dev/tenstorrent/2"]}
    env.adopt_results = {"0": Lease("A0", ("0000:01:00.0",)),
                         "2": AdoptRefused("claude:someone-else")}
    out = env.control().start("Cat-7B")
    assert out.kind == "chips_held"
    assert ("confirm", "stop_external") in env.calls
    text = env.confirms[-1][1]
    assert "claude:someone-else" in text and "abcdef123456" in text
    assert not [n for n in env.names() if n in ("tt.stop", "gozer.release")]
    assert not [r for r in env.records.load() if not r.get("unreapable")]
    assert [r["lease_id"] for r in unreapable(env)] == ["A0"]
    assert "gozer release A0" in out.message


def test_a_start_leaves_an_unreapable_record_and_its_lease_alone(env):
    # Legacy shape (name == key) and the new shape (distinct name) both survive.
    env.records.add("ns/bundle", None, None, "ZOMBIE", 1.0, unreapable=True)
    env.records.add("ns/bundle#unreapable-1", None, None, "ZOMBIE2", 1.0, unreapable=True)
    env.serve_adds = started_bundle()
    out = env.control().start("ns/bundle")
    assert out.kind == "started" and out.lease_id == "L1"
    recs = env.records.load()
    assert sorted(r["lease_id"] for r in recs if r.get("unreapable")) == ["ZOMBIE", "ZOMBIE2"]
    normal = [r for r in recs if not r.get("unreapable")]
    assert [(r["name"], r["lease_id"]) for r in normal] == [("ns/bundle", "L1")]
    ctl = env.control()
    ctl.stop(rs("ns/bundle", name="ns/bundle", port=20000, owner="app"))
    assert [c for c in env.calls if c[0] == "gozer.release"] == [("gozer.release", "L1")]


def test_failed_stop_record_does_not_replace_an_older_record_of_the_key(env):
    env.records.add("ns/bundle", 20000, "aaaaaaaaaaaa", "OLD", 1.0)
    env.serve_result = TtResult(False, "boom", ("boom",))
    env.serve_adds = rs("ns/bundle", name="ns/bundle", health="starting",
                        container_id="dddddddddddd", port=20000)
    env.devices = {"dddddddddddd": ["/dev/tenstorrent/2", "/dev/tenstorrent/3"]}
    env.stop_result = TtResult(False, "tt model stop exited 1")
    env.control().start("ns/bundle")
    leases = sorted(r["lease_id"] for r in env.records.load())
    assert leases == ["L1", "OLD"]


def test_reset_skipped_when_another_local_server_runs(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None),
                rs("Other-LLM", name="Other-LLM", port=8002, container_id="777777777777")]
    env.confirm_answers = {"switch": True}
    env.boutique_adds = rs("flux", source="script", port=8000, container_id=None)
    out = env.control().start("flux")
    assert out.kind == "started", out.message
    assert "reset" not in env.names()
    assert "not reset because Other-LLM is running on this machine" in out.message


def test_reset_still_runs_when_only_remote_servers_are_up(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None),
                rs("Other-LLM", name="Other-LLM", url="http://10.0.0.9:8002/v1", local=False,
                   owner="unknown")]
    env.confirm_answers = {"switch": True}
    env.boutique_adds = rs("flux", source="script", port=8000, container_id=None)
    env.control().start("flux")
    assert env.names().count("reset") == 1


def test_serve_that_returns_cancelled_is_reported_as_cancelled(env):
    flag = []
    env.serve_hook = lambda: flag.append(1)
    env.serve_result = TtResult(False, "cancelled; a half-booted container may still be running")
    out = env.control().start("ns/bundle", cancel=lambda: bool(flag))
    assert out.kind == "cancelled"
    out2 = Env(env.records.path.parent / "u")
    out2.serve_hook = lambda: flag.append(1)
    out2.serve_result = TtResult(False, "cancelled")
    assert out2.control().start("Cat-7B", cancel=lambda: bool(flag)).kind == "cancelled"


def test_unreadable_view_at_launch_fails_closed(env):
    env.gozer_available = False
    env.view_fail_on = {3}        # 1: running check, 2: recheck, 3: pre-launch snapshot
    out = env.control().start("Cat-7B")
    assert out.kind == "failed"
    assert "tt.serve" not in env.names()


def test_port_lookup_failure_message_follows_the_stop_result(env):
    env.confirm_answers = {"unknown_chips": True, "stop_external": True}
    env.boutique_adds = rs("flux", source="script", port=8000, container_id=None)
    env.boutique_stop_rc = 1
    out = env.control().start("flux")
    assert out.kind == "failed"
    assert "8000" in out.message
    assert "was stopped" not in out.message and "has been stopped" not in out.message
    assert "failed too" in out.message


def test_stop_that_raises_during_cleanup_still_records_the_kept_lease(env):
    env.serve_result = TtResult(False, "boom", ("boom",))

    def boom(names):
        env.calls.append(("tt.stop", tuple(names)))
        raise OSError("docker gone")
    env.tt.stop = staticmethod(boom)
    out = env.control().start("ns/bundle")
    assert out.kind == "failed"
    assert "gozer.release" not in env.names()
    assert [r["lease_id"] for r in unreapable(env)] == ["L1"]


# --------------------------------------------------------------------------
# Fix round 3: failure cleanup never stops a foreign row unasked
# --------------------------------------------------------------------------

def test_pinned_failed_serve_with_a_foreign_container_on_other_chips_stops_nothing(env):
    # Reviewer's scenario: our serve fails while their container appears on
    # chips 0-1; we leased 2-3.
    env.serve_result = TtResult(False, "tt serve exited 1", ("boom",))
    env.serve_adds = theirs("ns/bundle", "ns/bundle", port=20009, health="starting")
    env.devices = {"eeeeeeeeeeee": ["/dev/tenstorrent/0", "/dev/tenstorrent/1"]}
    out = env.control().start("ns/bundle")
    assert out.kind == "failed"
    assert ("confirm", "stop_external") in env.calls
    assert "eeeeeeeeeeee" in env.confirms[-1][1]
    assert not [n for n in env.names() if n in ("tt.stop", "docker.stop", "gozer.release")]
    assert "has been stopped" not in out.message
    assert "L1" in out.message and "eeeeeeeeeeee" in out.message
    assert [r["lease_id"] for r in unreapable(env)] == ["L1"]


def test_pinned_failed_serve_with_our_own_container_stops_without_asking(env):
    env.serve_result = TtResult(False, "tt serve exited 1", ("boom",))
    env.serve_adds = rs("ns/bundle", name="ns/bundle", health="starting", port=20000)
    out = env.control().start("ns/bundle")
    assert out.kind == "failed"
    assert "confirm" not in env.names()
    assert ("tt.stop", ("ns/bundle",)) in env.calls
    assert ("gozer.release", "L1") in env.calls


def test_unpinnable_timeout_with_a_single_foreign_row_asks_and_a_no_stops_nothing(env):
    env.serve_adds = theirs(health="starting")
    out = env.control(start_timeout=30).start("Cat-7B")
    assert out.kind == "failed"
    assert ("confirm", "stop_external") in env.calls
    assert "may not be ours" in env.confirms[-1][1]
    assert not [n for n in env.names() if n in ("tt.stop", "docker.stop")]
    assert env.records.load() == []


def test_unpinnable_timeout_with_a_single_foreign_row_stops_it_on_yes(env):
    env.serve_adds = theirs(health="starting")
    env.confirm_answers = {"stop_external": True}
    out = env.control(start_timeout=30).start("Cat-7B")
    assert out.kind == "failed"
    assert ("tt.stop", ("Cat-7B",)) in env.calls


def test_adopt_failure_asks_before_stopping_and_a_no_keeps_it(env):
    env.serve_adds = started_cat()
    env.devices = {}                     # devices unknown
    out = env.control().start("Cat-7B")
    assert out.kind == "failed"
    assert ("confirm", "stop_external") in env.calls
    assert "tt.stop" not in env.names()


def test_a_row_older_than_our_launch_asks_before_adopting(env):
    env.serve_adds = started_cat(uptime_s=3600)
    env.devices = {"abcdef123456": ["/dev/tenstorrent/2"]}
    env.adopt_results = {"2": Lease("A2", ("0000:03:00.0",))}
    out = env.control().start("Cat-7B")
    assert ("confirm", "adopt_unknown") in env.calls
    assert out.kind == "failed"
    assert "gozer.adopt" not in env.names()
    assert not [n for n in env.names() if n in ("tt.stop", "docker.stop")]
    assert env.records.load() == []
    # On yes it is adopted as ours.
    env2 = Env(env.records.path.parent / "y")
    env2.serve_adds = started_cat(uptime_s=3600)
    env2.devices = {"abcdef123456": ["/dev/tenstorrent/2"]}
    env2.adopt_results = {"2": Lease("A2", ("0000:03:00.0",))}
    env2.confirm_answers = {"adopt_unknown": True}
    assert env2.control().start("Cat-7B").kind == "started"


def test_a_young_row_is_adopted_without_asking(env):
    env.serve_adds = started_cat(uptime_s=10)
    env.devices = {"abcdef123456": ["/dev/tenstorrent/2"]}
    env.adopt_results = {"2": Lease("A2", ("0000:03:00.0",))}
    assert env.control().start("Cat-7B").kind == "started"
    assert "confirm" not in env.names()


def test_chips_held_no_message_names_the_container_and_warns_about_release(env):
    env.serve_adds = started_cat()
    env.devices = {"abcdef123456": ["/dev/tenstorrent/0", "/dev/tenstorrent/2"]}
    env.adopt_results = {"0": Lease("A0", ("0000:01:00.0",)),
                         "2": AdoptRefused("claude:someone-else")}
    out = env.control().start("Cat-7B")
    assert "abcdef123456" in out.message
    assert "Do not release A0 while container abcdef123456" in out.message


def test_switch_never_releases_a_legacy_unreapable_record_of_the_incumbent(env):
    env.gozer_available = False
    env.records.add("wan-old", None, None, "ZOMBIE", 1.0, unreapable=True)
    env.status_map = {"wan-old": "ready"}
    env.confirm_answers = {"switch": True}
    env.boutique_adds = rs("flux", source="script", port=8000, container_id=None)
    env.control().start("flux")
    assert ("boutique.stop", "wan-old") in env.calls
    assert ("gozer.release", "ZOMBIE") not in env.calls
    assert [r["lease_id"] for r in unreapable(env)] == ["ZOMBIE"]


def test_reset_skipped_while_another_local_server_boots(env):
    env.gozer_available = False
    env.status_map = {"wan-old": "ready"}
    env.view = [rs("wan-old", source="script", port=8000, container_id=None),
                rs("Other-LLM", name="Other-LLM", port=8002, container_id="777777777777",
                   health="starting")]
    env.confirm_answers = {"switch": True}
    env.boutique_adds = rs("flux", source="script", port=8000, container_id=None)
    out = env.control().start("flux")
    assert "reset" not in env.names()
    assert "not reset because Other-LLM" in out.message
