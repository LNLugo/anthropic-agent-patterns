"""Sessions: conversation history behind four methods.

    get_items(limit=None)   history, oldest first; with limit, the most recent `limit` items
    add_items(items)        append items
    pop_item()              remove and return the most recent item (undo a turn)
    clear_session()         delete everything in the session

The four methods follow the Session protocol in openai/openai-agents-python
v0.24.0; only the contract is borrowed. Three deliberate differences:

  * A session belongs to one identity. It is opened for (owner, session_id), and
    opening someone else's session raises PermissionError: a session id is not a
    credential, so knowing one must not be enough to read it. (Theirs has no owner.)
  * History expires. With retention_days, items older than that are not returned
    and are deleted on the next write, so memory does not outlive its purpose.
  * The interface is synchronous, like the rest of this repository.

An item is a dict: {"role": "user" | "assistant", "content": str, "pinned": bool},
plus "at" (when it was added) and "seq" (its position), which the session sets.
"pinned" marks a standing instruction from the user ("from now on, only Payments"),
which context strategies keep regardless of relevance (strategies.py).

Every call is a span in the run's trace (10_observability) with counts only: what
was said is content, and stays out of the trace.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

try:
    from tracing import get_tracer
except ImportError:  # the path setup goes away with the single pyproject
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "10_observability"))
    from tracing import get_tracer

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@runtime_checkable
class Session(Protocol):
    session_id: str

    def get_items(self, limit: int | None = None) -> list[dict]: ...
    def add_items(self, items: list[dict]) -> None: ...
    def pop_item(self) -> dict | None: ...
    def clear_session(self) -> None: ...


def _validate(item: dict) -> dict:
    if item.get("role") not in ("user", "assistant") or not isinstance(item.get("content"), str):
        raise ValueError("an item needs role user or assistant, and string content")
    return {"role": item["role"], "content": item["content"], "pinned": bool(item.get("pinned", False))}


class InMemorySession:
    """For tests and single-process demos. Same contract, owner and retention as SQLiteSession."""

    def __init__(self, session_id: str, owner: str, retention_days: int | None = None, clock: Clock = _utcnow):
        self.session_id, self.owner, self.retention_days, self.clock = session_id, owner, retention_days, clock
        self._items: list[dict] = []
        self._seq = 0

    def _live(self) -> list[dict]:
        if self.retention_days is None:
            return self._items
        cutoff = self.clock() - timedelta(days=self.retention_days)
        return [i for i in self._items if datetime.fromisoformat(i["at"]) >= cutoff]

    def get_items(self, limit: int | None = None) -> list[dict]:
        with get_tracer().span("tool", "memory.get_items", session=self.session_id, owner=self.owner) as span:
            items = self._live()
            out = [dict(i) for i in (items[-limit:] if limit else items)] if limit != 0 else []
            span.set(returned=len(out), stored=len(self._items))
            return out

    def add_items(self, items: list[dict]) -> None:
        with get_tracer().span("tool", "memory.add_items", session=self.session_id, owner=self.owner) as span:
            self._items = self._live()
            for item in items:
                self._seq += 1
                self._items.append(_validate(item) | {"seq": self._seq, "at": self.clock().isoformat()})
            span.set(added=len(items), pinned=sum(bool(i.get("pinned")) for i in items))

    def pop_item(self) -> dict | None:
        with get_tracer().span("tool", "memory.pop_item", session=self.session_id, owner=self.owner):
            self._items = self._live()
            return self._items.pop() if self._items else None

    def clear_session(self) -> None:
        with get_tracer().span("tool", "memory.clear_session", session=self.session_id, owner=self.owner) as span:
            span.set(deleted=len(self._items))
            self._items.clear()


class SQLiteSession:
    """Durable sessions in one SQLite file. A session is bound to the identity that created it."""

    def __init__(self, path: str | Path, session_id: str, owner: str, retention_days: int | None = None,
                 clock: Clock = _utcnow):
        self.session_id, self.owner, self.retention_days, self.clock = session_id, owner, retention_days, clock
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        with self.conn:
            self.conn.execute("CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, owner TEXT NOT NULL)")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS items (session_id TEXT NOT NULL, seq INTEGER NOT NULL,
                                 at TEXT NOT NULL, item TEXT NOT NULL, PRIMARY KEY (session_id, seq))""")
            row = self.conn.execute("SELECT owner FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                self.conn.execute("INSERT INTO sessions VALUES (?, ?)", (session_id, owner))
            elif row[0] != owner:
                # Same answer whatever the session holds: knowing an id reveals nothing.
                raise PermissionError(f"session {session_id!r} is not yours")

    def _cutoff(self) -> str | None:
        if self.retention_days is None:
            return None
        return (self.clock() - timedelta(days=self.retention_days)).isoformat()

    def _purge(self) -> None:
        cutoff = self._cutoff()
        if cutoff is not None:
            self.conn.execute("DELETE FROM items WHERE session_id = ? AND at < ?", (self.session_id, cutoff))

    def get_items(self, limit: int | None = None) -> list[dict]:
        with get_tracer().span("tool", "memory.get_items", session=self.session_id, owner=self.owner) as span:
            cutoff = self._cutoff() or ""
            rows = self.conn.execute("SELECT seq, at, item FROM items WHERE session_id = ? AND at >= ? ORDER BY seq",
                                     (self.session_id, cutoff)).fetchall()
            if limit is not None:
                rows = rows[-limit:] if limit else []
            span.set(returned=len(rows))
            return [json.loads(item) | {"seq": seq, "at": at} for seq, at, item in rows]

    def add_items(self, items: list[dict]) -> None:
        with get_tracer().span("tool", "memory.add_items", session=self.session_id, owner=self.owner) as span:
            with self._lock, self.conn:
                self._purge()
                (top,) = self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM items WHERE session_id = ?",
                                           (self.session_id,)).fetchone()
                for n, item in enumerate(items, start=1):
                    self.conn.execute("INSERT INTO items VALUES (?, ?, ?, ?)",
                                      (self.session_id, top + n, self.clock().isoformat(), json.dumps(_validate(item))))
            span.set(added=len(items), pinned=sum(bool(i.get("pinned")) for i in items))

    def pop_item(self) -> dict | None:
        with get_tracer().span("tool", "memory.pop_item", session=self.session_id, owner=self.owner):
            with self._lock, self.conn:
                self._purge()
                row = self.conn.execute("SELECT seq, at, item FROM items WHERE session_id = ? ORDER BY seq DESC LIMIT 1",
                                        (self.session_id,)).fetchone()
                if row is None:
                    return None
                self.conn.execute("DELETE FROM items WHERE session_id = ? AND seq = ?", (self.session_id, row[0]))
                return json.loads(row[2]) | {"seq": row[0], "at": row[1]}

    def clear_session(self) -> None:
        with get_tracer().span("tool", "memory.clear_session", session=self.session_id, owner=self.owner) as span:
            with self._lock, self.conn:
                n = self.conn.execute("DELETE FROM items WHERE session_id = ?", (self.session_id,)).rowcount
            span.set(deleted=n)
