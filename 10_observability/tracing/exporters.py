"""Where finished spans go. An exporter receives plain dicts, already redacted.

    JsonlExporter     the default: one JSON line per record, in a local folder
    InMemoryExporter  for tests and for showing a trace in the app
    FailingExporter   raises on every export, to prove a broken exporter never breaks a run

Anything with export(record) and shutdown() is an exporter, so an OpenTelemetry
or vendor exporter plugs in without changing the instrumented code. None is
included: the default sends nothing off the machine.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Protocol


class SpanExporter(Protocol):
    def export(self, record: dict) -> None: ...
    def shutdown(self) -> None: ...


def _default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return repr(o)


class JsonlExporter:
    """Appends each record as it arrives, so a crash keeps every span that had already
    ended. One file per day: traces-YYYY-MM-DD.jsonl."""

    def __init__(self, folder: str | Path = "traces"):
        self.folder = Path(folder)
        self._lock = threading.Lock()

    def path(self, day: date | None = None) -> Path:
        return self.folder / f"traces-{(day or date.today()).isoformat()}.jsonl"

    def export(self, record: dict) -> None:
        line = json.dumps(record, default=_default)
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            with self.path().open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def shutdown(self) -> None:
        pass


class InMemoryExporter:
    def __init__(self):
        self.records: list[dict] = []

    def export(self, record: dict) -> None:
        # A JSON round trip, so tests see exactly what a file would hold.
        self.records.append(json.loads(json.dumps(record, default=_default)))

    def shutdown(self) -> None:
        pass

    @property
    def spans(self) -> list[dict]:
        return [r for r in self.records if r["type"] == "span"]

    @property
    def traces(self) -> list[dict]:
        return [r for r in self.records if r["type"] == "trace"]

    def of_kind(self, kind: str) -> list[dict]:
        return [s for s in self.spans if s["kind"] == kind]

    def clear(self) -> None:
        self.records.clear()


class FailingExporter:
    def export(self, record: dict) -> None:
        raise OSError("exporter unavailable")

    def shutdown(self) -> None:
        pass
