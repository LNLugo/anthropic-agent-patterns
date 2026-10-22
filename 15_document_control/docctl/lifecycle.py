"""The lifecycle mirror (s4): document status as the system of record reports it, checked.

SmartDMS never moves a document through its lifecycle. Approval, signature and status
change stay in the source. This mirrors the status events and checks them:

- **At most one effective revision per document number, at every moment.** When a
  revision becomes effective, every earlier effective revision of that number reads as
  superseded in the same update, whether or not the source's own "superseded" event has
  arrived yet.
- **Delivery order does not matter.** State is computed from every status event received,
  in the order they happened (their timestamps), not the order they arrived. Duplicated,
  delayed and shuffled deliveries converge to the same state.
- **Illegal transitions are quarantined, not applied.** A step the state machine does not
  allow (obsolete to effective, draft straight to effective) is skipped and reported. It
  is decided on the full ordered history, so an event that only looks illegal because the
  one before it has not arrived yet is applied once that one does.
"""

from __future__ import annotations

from dataclasses import dataclass

STATES = ("draft", "in_review", "approved", "effective", "superseded", "obsolete")
ALLOWED = {
    None: {"draft"},
    "draft": {"in_review", "obsolete"},
    "in_review": {"draft", "approved", "obsolete"},
    "approved": {"effective", "in_review", "obsolete"},
    "effective": {"superseded", "obsolete"},
    "superseded": {"obsolete"},
    "obsolete": set(),
}


@dataclass(frozen=True)
class StatusEvent:
    id: str
    doc_number: str
    revision: str
    to: str
    at: str                       # RFC 3339, sortable as text


@dataclass(frozen=True)
class RevisionState:
    doc_number: str
    revision: str
    status: str | None
    effective_since: str | None   # when it became effective, if it ever did
    superseded_by: str | None


class LifecycleMirror:
    def __init__(self):
        self._events: dict[str, StatusEvent] = {}           # by id: a redelivery adds nothing
        self._cache: dict[str, dict[str, RevisionState]] | None = None

    def apply(self, e: StatusEvent) -> None:
        if e.to not in STATES:
            raise ValueError(f"unknown status {e.to!r}")
        if e.id not in self._events:
            self._events[e.id] = e
            self._cache = None

    # ---------------------------------------------------------------- derived state

    def _compute(self) -> tuple[dict[str, dict[str, RevisionState]], list[StatusEvent]]:
        by_rev: dict[tuple[str, str], list[StatusEvent]] = {}
        for e in self._events.values():
            by_rev.setdefault((e.doc_number, e.revision), []).append(e)
        walked, illegal = {}, []
        for (number, rev), events in by_rev.items():
            status, since = None, None
            for e in sorted(events, key=lambda x: (x.at, x.id)):
                if e.to in ALLOWED[status]:
                    status = e.to
                    if status == "effective":
                        since = e.at
                else:
                    illegal.append(e)
            walked.setdefault(number, {})[rev] = (status, since)
        out = {}
        for number, revs in walked.items():
            effective = [(since, rev) for rev, (st, since) in revs.items() if st == "effective"]
            winner = max(effective)[1] if effective else None
            ever = sorted((since, rev) for rev, (_, since) in revs.items() if since)
            out[number] = {}
            for rev, (st, since) in revs.items():
                if st == "effective" and rev != winner:        # flipped in the same update
                    st = "superseded"
                # superseded by the next revision to become effective after this one did
                nxt = next((r for s, r in ever if since and s > since), None) if st in ("superseded",) else None
                out[number][rev] = RevisionState(number, rev, st, since, nxt)
        return out, sorted(illegal, key=lambda x: x.id)

    def _state(self):
        if self._cache is None:
            self._cache, self._illegal = self._compute()
        return self._cache

    def revision(self, doc_number: str, revision: str) -> RevisionState | None:
        return self._state().get(doc_number, {}).get(revision)

    def effective(self, doc_number: str) -> str | None:
        revs = self._state().get(doc_number, {})
        live = [r for r, s in revs.items() if s.status == "effective"]
        return live[0] if live else None

    def documents(self) -> dict[str, dict[str, RevisionState]]:
        return self._state()

    def illegal(self) -> list[StatusEvent]:
        self._state()
        return list(self._illegal)

    def effective_counts(self) -> dict[str, int]:
        """Effective revisions per document number: the invariant is that none exceeds 1."""
        return {n: sum(1 for s in revs.values() if s.status == "effective") for n, revs in self._state().items()}
