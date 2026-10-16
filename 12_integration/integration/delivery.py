"""At-least-once delivery, handled exactly once.

Every broker worth using delivers at least once: a consumer that crashes before
acknowledging gets the message again, and a publisher that retries after a
timeout can send it twice. So the consumer, not the broker, makes handling
exactly once:

    receive --seen before?--yes--> ack, skip                 (idempotency store)
              |no
              v
           handle --ok--> mark seen, ack
              |
              +--InvalidEvent, no handler--> dead-letter now  (retrying cannot fix it)
              +--any other error--> retry with exponential backoff
                                    --> dead-letter after max_attempts

A dead letter keeps the event (or the raw input an adapter rejected) and the
reason, and can be replayed once the cause is fixed. The broker here is in
process, with a clock the tests control; Service Bus, SQS or Kafka sit behind
the same three calls: receive, ack, nack.
"""

from __future__ import annotations

import heapq
import itertools
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .adapters import Rejected
from .envelope import Event, InvalidEvent


class Clock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@dataclass(order=True)
class Delivery:
    available_at: float
    seq: int
    event: Event = field(compare=False)
    attempts: int = field(default=0, compare=False)   # failed attempts so far


class Broker:
    """In-process, at least once. nack() redelivers after a delay; crash() returns every
    message in flight to the queue, as a lapsed visibility timeout would."""

    def __init__(self, clock: Clock | None = None):
        self.clock = clock or Clock()
        self._ready: list[Delivery] = []
        self._in_flight: dict[int, Delivery] = {}
        self._seq = itertools.count()
        self.published = 0

    def publish(self, event: Event) -> None:
        self.published += 1
        heapq.heappush(self._ready, Delivery(self.clock(), next(self._seq), event))

    def receive(self) -> Delivery | None:
        if self._ready and self._ready[0].available_at <= self.clock():
            d = heapq.heappop(self._ready)
            self._in_flight[d.seq] = d
            return d
        return None

    def ack(self, d: Delivery) -> None:
        self._in_flight.pop(d.seq, None)

    def nack(self, d: Delivery, delay: float) -> None:
        self._in_flight.pop(d.seq, None)
        heapq.heappush(self._ready, Delivery(self.clock() + delay, next(self._seq), d.event, d.attempts + 1))

    def crash(self) -> None:
        for d in self._in_flight.values():
            heapq.heappush(self._ready, Delivery(self.clock(), next(self._seq), d.event, d.attempts))
        self._in_flight.clear()

    def next_available(self) -> float | None:
        return self._ready[0].available_at if self._ready else None

    def __len__(self) -> int:
        return len(self._ready) + len(self._in_flight)


class _Db:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")


class ProcessedStore(_Db):
    """Keys of events already handled. Durable, so a restarted consumer still skips them."""

    def __init__(self, path):
        super().__init__(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS processed (key TEXT PRIMARY KEY, at REAL)")

    def seen(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM processed WHERE key = ?", (key,)).fetchone() is not None

    def mark(self, key: str, at: float) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO processed VALUES (?, ?)", (key, at))


class DeadLetters(_Db):
    def __init__(self, path):
        super().__init__(path)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS dead (id INTEGER PRIMARY KEY, source TEXT, key TEXT,
                             event TEXT, raw TEXT, reason TEXT, attempts INTEGER, replayed INTEGER DEFAULT 0)""")

    def add_event(self, event: Event, reason: str, attempts: int) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO dead (source, key, event, reason, attempts) VALUES (?, ?, ?, ?, ?)",
                              (event.source, event.key, json.dumps(event.to_dict()), reason, attempts))

    def add_rejected(self, r: Rejected) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO dead (source, key, raw, reason, attempts) VALUES (?, ?, ?, ?, 0)",
                              (r.source, f"{r.source}:{r.where}", r.raw, f"{r.where}: {r.reason}"))

    def items(self, include_replayed: bool = False) -> list[dict]:
        rows = self.conn.execute("SELECT id, source, key, event, raw, reason, attempts, replayed FROM dead"
                                 + ("" if include_replayed else " WHERE replayed = 0") + " ORDER BY id").fetchall()
        return [dict(zip(("id", "source", "key", "event", "raw", "reason", "attempts", "replayed"), r)) for r in rows]

    def replay(self, dead_id: int, broker: Broker) -> Event:
        """Publish a dead-lettered event again, with its original id, once its cause is fixed.
        Raw inputs an adapter rejected cannot be replayed: they need correcting at the source."""
        row = self.conn.execute("SELECT event FROM dead WHERE id = ? AND replayed = 0", (dead_id,)).fetchone()
        if row is None or row[0] is None:
            raise KeyError(f"dead letter {dead_id} is not a replayable event")
        event = Event.from_dict(json.loads(row[0]))
        broker.publish(event)
        with self.conn:
            self.conn.execute("UPDATE dead SET replayed = 1 WHERE id = ?", (dead_id,))
        return event


@dataclass
class Stats:
    handled: int = 0
    duplicates_skipped: int = 0
    retries: int = 0
    dead_lettered: int = 0


Handler = Callable[[Event], None]


class Consumer:
    def __init__(self, broker: Broker, handlers: dict[str, Handler], processed: ProcessedStore | None,
                 dead: DeadLetters, max_attempts: int = 4, base_delay: float = 1.0, max_delay: float = 30.0):
        """processed=None turns deduplication off: the control in the measurements."""
        self.broker, self.handlers, self.processed, self.dead = broker, handlers, processed, dead
        self.max_attempts, self.base_delay, self.max_delay = max_attempts, base_delay, max_delay
        self.stats = Stats()
        self.delays: list[float] = []

    def backoff(self, failures: int) -> float:
        return min(self.base_delay * 2 ** (failures - 1), self.max_delay)

    def step(self) -> bool:
        d = self.broker.receive()
        if d is None:
            return False
        e = d.event
        if self.processed is not None and self.processed.seen(e.key):
            self.stats.duplicates_skipped += 1
            self.broker.ack(d)
            return True
        handler = self.handlers.get(e.type)
        try:
            if handler is None:
                raise InvalidEvent(f"no handler for {e.type!r}")
            handler(e)
        except InvalidEvent as err:
            self._dead(d, str(err), d.attempts + 1)
        except Exception as err:  # transient until proven otherwise: retry, then dead-letter
            failures = d.attempts + 1
            if failures >= self.max_attempts:
                self._dead(d, f"gave up after {failures} attempts: {type(err).__name__}: {err}", failures)
            else:
                self.stats.retries += 1
                delay = self.backoff(failures)
                self.delays.append(delay)
                self.broker.nack(d, delay)
        else:
            if self.processed is not None:
                self.processed.mark(e.key, self.broker.clock())
            self.stats.handled += 1
            self.broker.ack(d)
        return True

    def _dead(self, d: Delivery, reason: str, attempts: int) -> None:
        self.dead.add_event(d.event, reason, attempts)
        self.stats.dead_lettered += 1
        self.broker.ack(d)

    def run_until_idle(self, max_steps: int = 10_000) -> Stats:
        """Handle everything, moving the clock forward to the next retry when nothing is ready."""
        for _ in range(max_steps):
            if self.step():
                continue
            nxt = self.broker.next_available()
            if nxt is None:
                return self.stats
            self.broker.clock.t = max(self.broker.clock.t, nxt)
        raise RuntimeError("consumer did not go idle")
