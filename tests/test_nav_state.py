import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from nav_state import NavState, Crumb, Context


def test_crumbs_set_get_and_change_only_notify():
    fired = []
    ns = NavState(notify=lambda s: fired.append(len(s.crumbs())))
    ns.set_crumbs([Crumb("Library", "library"), Crumb("Lighthouse")])
    assert [c.label for c in ns.crumbs()] == ["Library", "Lighthouse"]
    assert fired == [2]
    # setting the identical trail again does NOT notify
    ns.set_crumbs([Crumb("Library", "library"), Crumb("Lighthouse")])
    assert fired == [2]
    ns.set_crumbs([Crumb("Create")])
    assert fired == [2, 1]


def test_open_update_close_context():
    fired = []
    ns = NavState(notify=lambda s: fired.append([c.id for c in s.contexts()]))
    ns.open_context(Context("pipeline", "Pipeline 2/5", kind="pipeline", running=True))
    ns.open_context(Context("remix", "Remix: lighthouse", kind="remix"))
    assert [c.id for c in ns.contexts()] == ["pipeline", "remix"]
    assert ns.has_context("pipeline") and ns.has_context("remix")
    # open with an existing id REPLACES in place (order preserved), notifies on change
    ns.open_context(Context("pipeline", "Pipeline 3/5", kind="pipeline", running=True))
    ctxs = ns.contexts()
    assert [c.id for c in ctxs] == ["pipeline", "remix"]
    assert ctxs[0].label == "Pipeline 3/5"
    # update mutates named fields
    ns.update_context("pipeline", running=False, label="Pipeline done")
    assert ns.contexts()[0].running is False and ns.contexts()[0].label == "Pipeline done"
    # update of an absent id is a no-op (no notify)
    before = len(fired)
    ns.update_context("nope", running=True)
    assert len(fired) == before
    # close removes
    ns.close_context("pipeline")
    assert [c.id for c in ns.contexts()] == ["remix"]
    # closing an absent id is a no-op (no notify)
    before = len(fired)
    ns.close_context("nope")
    assert len(fired) == before


def test_open_identical_context_does_not_notify():
    fired = []
    ns = NavState(notify=lambda s: fired.append(1))
    c = Context("watch", "Watch", kind="watch", running=True)
    ns.open_context(c)
    ns.open_context(Context("watch", "Watch", kind="watch", running=True))  # identical
    assert fired == [1]


def test_subscribe_unsubscribe_and_raising_subscriber_is_isolated():
    seen_a, seen_b = [], []
    ns = NavState()
    def bad(_s):
        raise RuntimeError("boom")
    un_bad = ns.subscribe(bad)
    ns.subscribe(lambda s: seen_a.append(1))
    ns.set_crumbs([Crumb("X")])          # bad raises but a still fires
    assert seen_a == [1]
    un_bad()
    ns.subscribe(lambda s: seen_b.append(1))
    ns.set_crumbs([Crumb("Y")])
    assert seen_b == [1]


def test_ctor_notify_and_subscribers_both_fire():
    ctor_hits, sub_hits = [], []
    ns = NavState(notify=lambda s: ctor_hits.append(1))
    ns.subscribe(lambda s: sub_hits.append(1))
    ns.open_context(Context("p", "P"))
    assert ctor_hits == [1] and sub_hits == [1]
