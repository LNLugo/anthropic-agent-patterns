"""At least once in, exactly once handled: duplicates skipped, failures retried with
backoff, poison dead-lettered without blocking the rest, and replayable."""

import pytest

from integration.delivery import Broker, Clock, Consumer, DeadLetters, ProcessedStore
from integration.envelope import Event, InvalidEvent


def ev(i, type="alert.raised"):
    return Event(str(i), "bus/service-alerts", type, f"asset-{i}", "2026-10-12T07:00:00Z", {"n": i})


@pytest.fixture
def stores(tmp_path):
    return ProcessedStore(tmp_path / "processed.db"), DeadLetters(tmp_path / "dead.db")


def consumer(broker, handlers, stores, dedupe=True, **kw):
    processed, dead = stores
    return Consumer(broker, handlers, processed if dedupe else None, dead, **kw)


def test_duplicates_are_handled_once(stores):
    broker, effects = Broker(), []
    for i in range(10):
        broker.publish(ev(i))
        broker.publish(ev(i))          # the publisher retried after a timeout
    c = consumer(broker, {"alert.raised": lambda e: effects.append(e.id)}, stores)
    s = c.run_until_idle()
    assert len(effects) == 10 and s.duplicates_skipped == 10


def test_without_the_store_every_duplicate_is_an_effect(stores):
    broker, effects = Broker(), []
    for i in range(10):
        broker.publish(ev(i))
        broker.publish(ev(i))
    consumer(broker, {"alert.raised": lambda e: effects.append(e.id)}, stores, dedupe=False).run_until_idle()
    assert len(effects) == 20


def test_the_store_survives_a_restart(tmp_path):
    broker, effects = Broker(), []
    broker.publish(ev(1))
    Consumer(broker, {"alert.raised": lambda e: effects.append(1)}, ProcessedStore(tmp_path / "p.db"),
             DeadLetters(tmp_path / "d.db")).run_until_idle()
    broker.publish(ev(1))  # redelivered after the consumer restarted
    Consumer(broker, {"alert.raised": lambda e: effects.append(1)}, ProcessedStore(tmp_path / "p.db"),
             DeadLetters(tmp_path / "d.db")).run_until_idle()
    assert effects == [1]


def test_a_transient_failure_is_retried_with_exponential_backoff(stores):
    broker, calls = Broker(Clock()), []

    def flaky(e):
        calls.append(broker.clock())
        if len(calls) < 3:
            raise ConnectionError("Jira timed out")

    broker.publish(ev(1))
    c = consumer(broker, {"alert.raised": flaky}, stores, base_delay=2.0)
    s = c.run_until_idle()
    assert s.handled == 1 and s.retries == 2 and calls == [0.0, 2.0, 6.0]   # waits of 2s, then 4s
    assert stores[1].items() == []


def test_a_message_that_keeps_failing_is_dead_lettered_and_the_rest_still_flow(stores):
    broker, done = Broker(Clock()), []

    def handler(e):
        if e.id == "poison":
            raise RuntimeError("bad asset reference")
        done.append(e.id)

    broker.publish(Event("poison", "bus/service-alerts", "alert.raised", "x", "2026-10-12T07:00:00Z"))
    for i in range(5):
        broker.publish(ev(i))
    s = consumer(broker, {"alert.raised": handler}, stores, max_attempts=4).run_until_idle()
    assert done == ["0", "1", "2", "3", "4"]                      # no head-of-line blocking
    assert s.dead_lettered == 1 and s.retries == 3
    [dead] = stores[1].items()
    assert dead["attempts"] == 4 and "gave up after 4 attempts: RuntimeError" in dead["reason"]


def test_an_invalid_event_is_dead_lettered_at_once(stores):
    broker = Broker()

    def handler(e):
        raise InvalidEvent("alert names no asset")

    broker.publish(ev(1))
    broker.publish(ev(2, type="alert.unknown_type"))
    s = consumer(broker, {"alert.raised": handler}, stores).run_until_idle()
    assert s.retries == 0 and s.dead_lettered == 2
    assert [d["reason"] for d in stores[1].items()] == ["alert names no asset", "no handler for 'alert.unknown_type'"]


def test_a_dead_letter_is_replayed_once_its_cause_is_fixed(stores):
    broker, fixed, effects = Broker(Clock()), {"yes": False}, []

    def handler(e):
        if not fixed["yes"]:
            raise RuntimeError("asset registry down")
        effects.append(e.id)

    broker.publish(ev(1))
    c = consumer(broker, {"alert.raised": handler}, stores, max_attempts=2)
    c.run_until_idle()
    [dead] = stores[1].items()
    fixed["yes"] = True
    stores[1].replay(dead["id"], broker)
    with pytest.raises(KeyError):
        stores[1].replay(dead["id"], broker)        # a dead letter replays once
    broker.publish(ev(1))                            # and a late duplicate of it still counts once
    c.run_until_idle()
    assert effects == ["1"] and stores[1].items() == []


def test_a_crash_before_the_ack_redelivers(stores):
    broker, effects = Broker(), []
    broker.publish(ev(1))
    d = broker.receive()
    effects.append(d.event.id)        # handled, then the process died before acknowledging
    broker.crash()
    consumer(broker, {"alert.raised": lambda e: effects.append(e.id)}, stores).run_until_idle()
    assert effects == ["1", "1"]      # the broker cannot know: the handler must be idempotent too (see triage)
