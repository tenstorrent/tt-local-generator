"""gozer_client against a fake `gozer` script. No test runs the real gozer."""
import json
import os
import time
from pathlib import Path

import pytest

import gozer_client as gc
from fake_gozer import calls, make_fake_gozer

FIXTURE = Path(__file__).parent / "fixtures" / "gozer" / "status.json"


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    # PATH holds only the fake dir: the real gozer is unreachable.
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    gc._find_cache = None
    yield
    gc._find_cache = None


def _install(tmp_path, commands, version="gozer 0.3.4"):
    d = tmp_path / "bin"
    make_fake_gozer(d, {"version": version, "commands": commands})
    return d


def _j(obj, exit=0):
    return {"stdout": json.dumps(obj), "exit": exit}


GRANT = {"granted": True, "lease_id": "L-1", "chips": ["0000:03:00.0", "0000:04:00.0"],
         "dev_indices": [2, 3], "env": {}}


def test_available_false_without_gozer(tmp_path):
    (tmp_path / "bin").mkdir()
    assert gc.available() is False


def test_available_false_for_a_different_gozer(tmp_path):
    _install(tmp_path, {}, version="something else 1.0")
    assert gc.available() is False


def test_available_true(tmp_path):
    _install(tmp_path, {})
    assert gc.available() is True


def test_acquire_returns_lease_with_chip_ids(tmp_path):
    d = _install(tmp_path, {"acquire": _j(GRANT)})
    r = gc.acquire(1, "tt-gen", "serve x")
    assert r == gc.Lease("L-1", ("0000:03:00.0", "0000:04:00.0"))
    argv = calls(d)[-1]
    assert argv[0] == "acquire" and "--json" in argv
    assert "--chips=1" in argv
    assert "--who=tt-gen" in argv
    assert "--expect=2h" in argv
    assert "--no-queue" not in argv


def test_acquire_no_queue_flag(tmp_path):
    d = _install(tmp_path, {"acquire": _j({"granted": False, "queued": False}, 12)})
    assert gc.acquire(1, "w", "r", queue=False) is None
    assert "--no-queue" in calls(d)[-1]


def test_acquire_queued_returns_ticket_and_wait_grants(tmp_path):
    d = _install(tmp_path, {
        "acquire": _j({"granted": False, "queued": True, "ticket": "T-9",
                       "position": 1, "ahead": []}, 10),
        "wait": _j({"granted": True, "lease_id": "L-2", "chips": ["0000:01:00.0"],
                    "env": {}})})
    q = gc.acquire(2, "w", "r")
    assert q == gc.Queued("T-9")
    lease = gc.wait(q.ticket, 30)
    assert lease == gc.Lease("L-2", ("0000:01:00.0",))
    argv = [c for c in calls(d) if c[0] == "wait"][0]
    assert argv[1] == "T-9" and "--json" in argv


def test_wait_returns_none_when_still_queued(tmp_path):
    _install(tmp_path, {"wait": _j({"granted": False, "ticket": "T", "position": 2}, 11)})
    assert gc.wait("T", 5) is None


def test_wait_cancel_stops_waiting(tmp_path):
    pidfile = tmp_path / "wait.pid"
    _install(tmp_path, {"wait": {"hang": True, "pidfile": str(pidfile)}})
    t0 = time.monotonic()
    assert gc.wait("T-1", 60, cancel=lambda: time.monotonic() - t0 > 0.5) is None
    assert time.monotonic() - t0 < 5
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # the child really is gone


def test_release_returns_true_and_false_on_error(tmp_path):
    _install(tmp_path, {"release L-1": _j({"released": True, "message": "ok"}),
                        "release L-bad": _j({"released": False, "message": "lease L-bad not found"}, 13)})
    assert gc.release("L-1") is True
    assert gc.release("L-bad") is False


def test_release_false_when_gozer_missing(tmp_path):
    (tmp_path / "bin").mkdir()
    assert gc.release("L-1") is False


def _fixture_text():
    return FIXTURE.read_text()


def test_holders_reports_other_agents_leases(tmp_path):
    _install(tmp_path, {"status": {"stdout": _fixture_text()}})
    assert gc.holders(["0000:01:00.0"]) == ["claude:orchard-kernel-ladder"]
    assert gc.holders(["1"]) == ["claude:orchard-kernel-ladder"]  # by dev index
    assert gc.holders(["0000:03:00.0"]) == []


def test_free_chips_from_fixture(tmp_path):
    _install(tmp_path, {"status": {"stdout": _fixture_text()}})
    assert gc.free_chips() == ["0000:03:00.0", "0000:04:00.0"]


def test_adopt_wraps_untracked_chip(tmp_path):
    d = _install(tmp_path, {"adopt": _j({"adopted": True, "lease_id": "L-7", "pids": [42]})})
    r = gc.adopt("0000:01:00.0", "tt-gen", "adopt served model")
    assert r == gc.Lease("L-7", ("0000:01:00.0",))
    argv = [c for c in calls(d) if c[0] == "adopt"][0]
    assert argv[:2] == ["adopt", "0000:01:00.0"]
    assert "--who=tt-gen" in argv


def test_adopt_refused_when_already_leased(tmp_path):
    _install(tmp_path, {
        "adopt": _j({"adopted": False}, 12),
        "status": {"stdout": _fixture_text()}})
    r = gc.adopt("0000:01:00.0", "tt-gen", "r")
    assert r == gc.AdoptRefused("claude:orchard-kernel-ladder")


def test_adopt_none_when_nothing_holds_the_chip(tmp_path):
    _install(tmp_path, {
        "adopt": _j({"adopted": False}, 12),
        "status": {"stdout": _fixture_text()}})
    assert gc.adopt("0000:03:00.0", "w", "r") is None


def test_hung_gozer_times_out_without_raising(tmp_path, monkeypatch):
    _install(tmp_path, {"status": {"hang": True}, "acquire": {"hang": True},
                        "release": {"hang": True}})
    monkeypatch.setattr(gc, "_CALL_TIMEOUT_S", 0.5)
    monkeypatch.setattr(gc, "_RELEASE_TIMEOUT_S", 0.5)
    t0 = time.monotonic()
    assert gc.status() == {}
    assert gc.holders(["x"]) == []
    assert gc.free_chips() == []
    assert gc.acquire(1, "w", "r") is None
    assert gc.release("L") is False
    assert time.monotonic() - t0 < 10


def test_garbage_output_never_raises(tmp_path):
    _install(tmp_path, {"status": {"stdout": "not json"}, "acquire": {"stdout": "[1]"},
                        "wait": {"stdout": ""}, "adopt": {"stdout": "{"}})
    assert gc.status() == {}
    assert gc.acquire(1, "w", "r") is None
    assert gc.wait("T", 2) is None
    assert gc.adopt("x", "w", "r") is None


def test_read_only_calls_never_mutate(tmp_path):
    d = _install(tmp_path, {"status": {"stdout": _fixture_text()}})
    gc.available()
    gc.status()
    gc.holders(["0000:01:00.0"])
    gc.free_chips()
    for argv in calls(d):
        assert argv == ["--version"] or argv[0] == "status", argv
    assert any(a[0] == "status" for a in calls(d))


def test_cancel_ticket_true(tmp_path):
    d = _install(tmp_path, {"cancel": _j({"cancelled": True})})
    assert gc.cancel_ticket("T-9") is True
    assert [c for c in calls(d) if c[0] == "cancel"] == [["cancel", "T-9", "--json"]]


def test_cancel_ticket_false(tmp_path):
    d = _install(tmp_path, {"cancel": _j({"cancelled": False}, 13)})
    assert gc.cancel_ticket("T-9") is False
    assert [c for c in calls(d) if c[0] == "cancel"] == [["cancel", "T-9", "--json"]]


HISTORY = {"history": [
    {"event": "granted", "lease_id": "old", "who": "claude:orchard-kernel-ladder",
     "chips": ["0000:01:00.0", "0000:02:00.0"]},
    {"event": "released", "lease_id": "old", "who": "claude:orchard-kernel-ladder"},
    {"event": "granted", "lease_id": "other", "who": "someone-else", "chips": ["0000:03:00.0"]},
    {"event": "granted", "lease_id": "cur", "who": "claude:orchard-kernel-ladder",
     "chips": ["0000:01:00.0", "0000:02:00.0"]},
]}


def test_find_lease_with_fixture(tmp_path):
    d = _install(tmp_path, {"status": {"stdout": _fixture_text()}, "history": _j(HISTORY)})
    assert gc.find_lease("claude:orchard-kernel-ladder") == gc.Lease(
        "cur", ("0000:01:00.0", "0000:02:00.0"))
    assert gc.find_lease("nobody") is None  # holds no chips
    assert gc.find_lease("someone-else") is None  # not holding per status
    assert all(c[0] in ("status", "history", "--version") for c in calls(d))


def test_find_lease_none_when_status_fails(tmp_path):
    _install(tmp_path, {"status": {"stdout": "x", "exit": 1}, "history": _j(HISTORY)})
    assert gc.find_lease("claude:orchard-kernel-ladder") is None


def test_adopt_status_failure_after_failed_adopt_is_none(tmp_path):
    _install(tmp_path, {"adopt": _j({"adopted": False}, 12),
                        "status": {"stdout": "", "exit": 1}})
    assert gc.adopt("0000:01:00.0", "w", "r") is None


def test_adopt_refused_holder_is_who_string(tmp_path):
    st = {"chips": [{"bdf": "0000:09:00.0", "dev_index": 0, "board": "B", "state": "HELD", "who": "x"}]}
    _install(tmp_path, {"adopt": _j({"adopted": False}, 12), "status": _j(st)})
    assert gc.adopt("0000:09:00.0", "w", "r") == gc.AdoptRefused("x")


def test_adopt_reads_back_board_chips_from_status(tmp_path):
    # target is a board unit; the lease covers both of its chips.
    _install(tmp_path, {"adopt": _j({"adopted": True, "lease_id": "L-b", "pids": [1]}),
                        "status": {"stdout": _fixture_text()}})
    r = gc.adopt("0000046131924062", "w", "r")
    assert r == gc.Lease("L-b", ("0000:01:00.0", "0000:02:00.0"))


def test_values_starting_with_dash_use_equals_form(tmp_path):
    d = _install(tmp_path, {"acquire": _j(GRANT), "adopt": _j({"adopted": True, "lease_id": "L"})})
    gc.acquire(1, "--who-evil", "--reason-evil", expect="-1h")
    argv = [c for c in calls(d) if c[0] == "acquire"][0]
    assert "--who=--who-evil" in argv and "--reason=--reason-evil" in argv
    assert "--expect=-1h" in argv and "--chips=1" in argv
    gc.adopt("x", "--w", "--r")
    argv = [c for c in calls(d) if c[0] == "adopt"][0]
    assert "--who=--w" in argv and "--reason=--r" in argv


def test_bad_chip_count_returns_none(tmp_path):
    _install(tmp_path, {"acquire": _j(GRANT)})
    assert gc.acquire("lots", "w", "r") is None
    assert gc.acquire(None, "w", "r") is None


def test_gozer_path_is_cached_per_path(tmp_path):
    d = _install(tmp_path, {"status": {"stdout": _fixture_text()}})
    gc.status(); gc.status(); gc.status()
    assert sum(1 for c in calls(d) if c == ["--version"]) == 1


def _held_by(who, bdfs):
    return {"chips": [{"bdf": b, "dev_index": i, "board": "B", "state": "HELD", "who": who}
                      for i, b in enumerate(bdfs)]}


def test_find_lease_skips_reaped_lease(tmp_path):
    hist = {"history": [
        {"event": "granted", "lease_id": "A", "who": "w", "chips": ["c1", "c2"]},
        {"event": "granted", "lease_id": "B", "who": "w", "chips": ["c2"]},
        {"event": "reaped", "lease_id": "B", "who": "w", "chips": ["c2"]}]}
    _install(tmp_path, {"status": _j(_held_by("w", ["c1", "c2"])), "history": _j(hist)})
    assert gc.find_lease("w") == gc.Lease("A", ("c1", "c2"))


def test_find_lease_none_when_only_lease_was_reaped(tmp_path):
    hist = {"history": [
        {"event": "granted", "lease_id": "B", "who": "w", "chips": ["c2"]},
        {"event": "reaped", "lease_id": "B", "who": "w", "chips": ["c2"]}]}
    _install(tmp_path, {"status": _j(_held_by("w", ["c2"])), "history": _j(hist)})
    assert gc.find_lease("w") is None


def test_cached_path_revalidated_when_binary_disappears(tmp_path):
    d = _install(tmp_path, {"status": {"stdout": _fixture_text()}})
    assert gc.available() is True
    (d / "gozer").unlink()
    assert gc.available() is False  # same PATH, binary gone
    make_fake_gozer(d, {"commands": {}})
    assert gc.available() is True   # and it comes back


def test_release_passes_no_reset_only_when_asked(tmp_path):
    d = _install(tmp_path, {"release L-1": _j({"released": True, "message": "ok"})})
    assert gc.release("L-1") is True
    assert gc.release("L-1", reset=False) is True
    rel = [c for c in calls(d) if c and c[0] != "--version" and "release" in c]
    assert len(rel) == 2
    assert "--no-reset" not in rel[0]
    assert "--no-reset" in rel[1] and rel[1][rel[1].index("release") + 1] == "L-1"
