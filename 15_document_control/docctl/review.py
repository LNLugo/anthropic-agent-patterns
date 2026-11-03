"""Human review and the audit trail (s13), and writing decisions back to the system of record (s4).

- **Routing by risk and confidence.** Quality's taxonomy gives each type a risk class. High
  risk always goes to a person; medium when anything failed or confidence is short; low is
  approved automatically when clean.
- **Blind review keeps reviewers and automatic approval honest.** A fixed share of automatic
  approvals is sent to a person without the AI's output, and override rates are tracked per
  type and per reviewer, so rubber-stamping shows up as a number.
- **A decision waits for a person, and survives a restart.** The review step is a LangGraph
  interrupt on a SQLite checkpoint: the mechanism of ADR 0005. 08 builds that into its Jira
  assistant, so it cannot be imported here; this is the same mechanism in a three-step graph
  of its own. The identity that asked, or the service itself, cannot approve.
- **Every AI output has an audit record**, appended to a hash-chained log: changing or
  deleting any record breaks the chain at that point.
- **Edits become examples.** A reviewer's edit or rejection is added to the gold set with the
  reviewer's correction as its label.
- **Decisions go back to the system of record**, through its API, with a request id, so a
  retry after a lost response writes once (ADR 0012).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TypedDict

from .classify import taxonomy

SERVICE = "svc-smartdms"


def _now() -> str:
    return datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc).isoformat()   # fixed: results must reproduce


# ---------------------------------------------------------------- routing

@dataclass(frozen=True)
class Route:
    to: str            # "review" or "auto"
    risk: str
    reasons: tuple[str, ...]
    blind: bool = False


def route(record) -> Route:
    pol = taxonomy()["review"]
    risk = pol["risk_class"].get(record.doc_type or "", "high")      # unknown type: treated as high risk
    conf = record.classification.confidence
    failed = [f.rule for f in record.failures] + (["classification"] if record.classification.review else [])
    if risk == "high":
        return Route("review", risk, ("high-risk type",) + tuple(failed))
    need = pol["medium_needs"] if risk == "medium" else pol["low_needs"]
    if failed or conf < need:
        return Route("review", risk, tuple(failed) + ((f"confidence {conf} below {need}",) if conf < need else ()))
    blind = int(hashlib.sha256(record.key.encode()).hexdigest(), 16) % 1000 < pol["blind_sample"] * 1000
    return Route("auto", risk, ("clean",), blind)


# ---------------------------------------------------------------- the audit trail

@dataclass
class AuditRecord:
    output_id: str
    kind: str                       # classification, extraction, comparison, answer
    doc_ids: list[str]
    model_version: str
    prompt_version: str
    input_chunk_ids: list[str]
    output: dict
    confidence: float | None
    reviewer: str | None = None
    decision: str | None = None     # accept, edit, reject, auto
    edits: dict = field(default_factory=dict)
    reason_code: str | None = None
    created_at: str = field(default_factory=_now)
    decided_at: str | None = None
    prev_hash: str = ""
    hash: str = ""


class AuditLog:
    """Append-only, hash-chained: each record carries the hash of the one before it."""

    def __init__(self):
        self.records: list[AuditRecord] = []

    @staticmethod
    def _digest(r: AuditRecord) -> str:
        body = {k: v for k, v in asdict(r).items() if k != "hash"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

    def append(self, r: AuditRecord) -> AuditRecord:
        r.prev_hash = self.records[-1].hash if self.records else ""
        r.hash = self._digest(r)
        self.records.append(r)
        return r

    def verify(self) -> int | None:
        """The position of the first record that does not check out, or None if the chain holds."""
        prev = ""
        for i, r in enumerate(self.records):
            if r.prev_hash != prev or self._digest(r) != r.hash:
                return i
            prev = r.hash
        return None


def output_id(kind: str, key: str, output: dict) -> str:
    return hashlib.sha256(json.dumps([kind, key, output], sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- the system of record

class LostResponse(Exception):
    """The write was committed but the response never reached us."""


class MockDMS:
    """The source DMS's decision API. It stores records of assistance against the document;
    a request id seen before returns the stored record instead of writing again."""

    def __init__(self, lose_first_response: bool = False):
        self.written: list[dict] = []
        self._by_request: dict[str, dict] = {}
        self._lose = lose_first_response

    def post_decision(self, doc_key: str, decision: dict, request_id: str | None) -> dict:
        if request_id is not None and request_id in self._by_request:
            return self._by_request[request_id]
        rec = {"doc": doc_key, **decision, "request_id": request_id}
        self.written.append(rec)
        if request_id is not None:
            self._by_request[request_id] = rec
        if self._lose:
            self._lose = False
            raise LostResponse(doc_key)
        return rec


def write_back(dms: MockDMS, doc_key: str, decision: dict, request_id: str | None, attempts: int = 3) -> dict:
    for _ in range(attempts):
        try:
            return dms.post_decision(doc_key, decision, request_id)
        except LostResponse:
            continue
    raise RuntimeError(f"decision for {doc_key} not confirmed after {attempts} attempts")


# ---------------------------------------------------------------- the review graph

class State(TypedDict, total=False):
    output: dict
    requested_by: str
    decision: dict
    outcome: str
    written: dict


@dataclass
class Reviews:
    """What the graph writes to as it runs: the audit log, the gold set and the DMS."""

    audit: AuditLog
    dms: MockDMS
    gold: list[dict] = field(default_factory=list)


def build_review(reviews: Reviews, checkpointer):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    if checkpointer is None:
        raise ValueError("review needs a checkpointer: a decision waits for a person, and must survive the wait")

    def wait(s: State) -> State:
        return {"decision": interrupt(s["output"])}

    def decide(s: State) -> State:
        d, o = s["decision"], s["output"]
        who = d.get("reviewer")
        if not who or who in (s["requested_by"], SERVICE):
            return {"outcome": "declined: the requester or the service cannot approve its own output"}
        rec = AuditRecord(o["output_id"], o["kind"], o["doc_ids"], o.get("model_version", "rules"),
                          o.get("prompt_version", "n/a"), o.get("input_chunk_ids", []), o["output"],
                          o.get("confidence"), who, d["decision"], d.get("edits", {}), d.get("reason_code"),
                          decided_at=_now())
        reviews.audit.append(rec)
        if d["decision"] in ("edit", "reject"):
            reviews.gold.append({"output_id": o["output_id"], "kind": o["kind"], "doc_ids": o["doc_ids"],
                                 "ai": o["output"], "label": d.get("edits") or {"rejected": True},
                                 "reviewer": who, "reason_code": d.get("reason_code")})
        return {"outcome": d["decision"]}

    def write(s: State) -> State:
        o, d = s["output"], s["decision"]
        rid = f"{o['output_id']}:{d['reviewer']}:{d['decision']}"
        return {"written": write_back(reviews.dms, o["doc_ids"][0],
                                      {"output_id": o["output_id"], "decision": d["decision"],
                                       "reviewer": d["reviewer"], "edits": d.get("edits", {})}, rid)}

    g = StateGraph(State)
    g.add_node("wait", wait)
    g.add_node("decide", decide)
    g.add_node("write", write)
    g.add_edge(START, "wait")
    g.add_edge("wait", "decide")
    g.add_conditional_edges("decide", lambda s: "write" if not s["outcome"].startswith("declined") else END,
                            {"write": "write", END: END})
    g.add_edge("write", END)
    return g.compile(checkpointer=checkpointer)


def submit(app, thread: str, output: dict, requested_by: str) -> dict:
    """Start a review; it stops at the interrupt and waits for resume()."""
    return app.invoke({"output": output, "requested_by": requested_by}, {"configurable": {"thread_id": thread}})


def pending(app, thread: str) -> dict | None:
    snap = app.get_state({"configurable": {"thread_id": thread}})
    for task in snap.tasks:
        for i in task.interrupts:
            return i.value
    return None


def resume(app, thread: str, reviewer: str, decision: str, edits: dict | None = None,
           reason_code: str | None = None) -> dict:
    from langgraph.types import Command

    return app.invoke(Command(resume={"reviewer": reviewer, "decision": decision, "edits": edits or {},
                                      "reason_code": reason_code}), {"configurable": {"thread_id": thread}})


def classification_output(record) -> dict:
    out = {"doc_type": record.doc_type, "fields": {k: str(f.value) for k, f in sorted(record.fields.items())},
           "failures": sorted(f.rule for f in record.failures)}
    return {"output_id": output_id("classification", record.key, out), "kind": "classification",
            "doc_ids": [record.key], "model_version": "rules", "prompt_version": "taxonomy " + taxonomy()["version"],
            "input_chunk_ids": [c.chunk_id for c in record.chunks[:1]], "output": out,
            "confidence": record.classification.confidence}


def override_rates(audit: AuditLog) -> dict:
    """Share of decisions that edited or rejected the AI's output, by type and by reviewer."""
    by_type, by_rev = {}, {}
    for r in audit.records:
        if r.decision in (None, "auto"):
            continue
        t = r.output.get("doc_type") or "unclassified"
        for d, k in ((by_type, t), (by_rev, r.reviewer)):
            n, o = d.get(k, (0, 0))
            d[k] = (n + 1, o + (r.decision in ("edit", "reject")))
    fmt = lambda d: {k: {"decisions": n, "overridden": o} for k, (n, o) in sorted(d.items())}  # noqa: E731
    return {"by_type": fmt(by_type), "by_reviewer": fmt(by_rev)}
