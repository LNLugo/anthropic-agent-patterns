"""An append-only event log: the stand-in for Event Hubs' retention (s5, s16).

12's broker is a queue: an acknowledged message is gone. The specification needs a log,
so that after an outage or a parser fix every event can be read again from an offset and
the index rebuilt from it. Every event that passes its contract is appended here before
it is published to the broker; the consumer works from the broker, and a rebuild works
from the log.
"""

from __future__ import annotations

import json
from pathlib import Path

from integration.envelope import Event


class EventLog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._events: list[Event] = []
        if self.path and self.path.exists():
            self._events = [Event.from_dict(json.loads(x)) for x in self.path.read_text("utf-8").splitlines() if x]

    def append(self, event: Event) -> int:
        """The event's offset. A redelivered event is appended again: the log records what
        arrived, and the consumer's processed store is what makes handling exactly once."""
        self._events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
        return len(self._events) - 1

    def read(self, offset: int = 0):
        yield from self._events[offset:]

    def __len__(self) -> int:
        return len(self._events)
