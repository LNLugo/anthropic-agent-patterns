"""Intake: source records in, checked and enriched document records out (s4, s5, s6).

    source record --contract--> Event --log--> broker --consumer--> handler
                        \\--> dead letters (quarantine, with the reason)

The handler for a document: fetch the file from landing storage, parse it, hash its text,
register its location, and, for the first time a key and content are seen, redact
personal data, chunk, embed (reusing unchanged chunks), classify, extract and validate.
Email attachments re-enter intake as documents of their own. Status events go to the
lifecycle mirror.

Delivery is at least once (12's broker). Exactly-once handling comes from 12's processed
store keyed on the event, and from keying the work on the document (number, revision and
content hash), so the same document from two systems is processed once.
"""

from __future__ import annotations

import hashlib
import json
import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from integration.adapters import Rejected
from integration.delivery import Broker, Clock, Consumer, DeadLetters, ProcessedStore
from integration.envelope import Event, InvalidEvent

from . import contracts
from .chunking import Chunk, EmbeddingCache, chunk
from .classify import Classification, classify, taxonomy
from .corpus import FIXTURES
from .eventlog import EventLog
from .extract import Field, extract
from .identity import Registry, content_hash
from .lifecycle import LifecycleMirror, StatusEvent
from .parse import ParseError, Parsed, parse
from .pii import Finding, redact
from .validate import Failure, validate


@dataclass
class Record:
    key: str
    system: str
    content_hash: str
    doc_type: str | None
    classification: Classification
    fields: dict[str, Field]
    failures: list[Failure]
    pii: list[Finding]
    chunks: list[Chunk]
    acl_groups: list[str]
    parent: str | None = None
    parsed: Parsed | None = field(default=None, repr=False)

    @property
    def review(self) -> bool:
        return self.classification.review or bool(self.failures)


class Landing:
    """Landing storage: source files by system and name, attachments by content hash."""

    def __init__(self, folder: Path = FIXTURES):
        self.folder = folder
        self.blobs: dict[str, bytes] = {}

    def read(self, system: str, name: str) -> bytes:
        if system == "attachment":
            return self.blobs[name]
        path = self.folder / "sources" / system / "files" / name
        if not path.exists():
            raise InvalidEvent(f"file not in landing storage: {system}/{name}")
        return path.read_bytes()

    def put(self, data: bytes) -> str:
        h = hashlib.sha256(data).hexdigest()
        self.blobs[h] = data
        return h


class Intake:
    def __init__(self, folder: Path = FIXTURES, workdir: str | Path | None = None, embedder=None, dedup=True):
        from llm_provider import HashingEmbeddings

        self._tmp = tempfile.TemporaryDirectory() if workdir is None else None
        work = Path(workdir or self._tmp.name)
        self.folder = folder
        self.broker = Broker(Clock())
        self.log = EventLog()
        self.dead = DeadLetters(work / "dead.db")
        self.processed = ProcessedStore(work / "processed.db") if dedup else None
        self.consumer = Consumer(self.broker, {"document.upserted": self.on_document,
                                               "document.status_changed": self.on_status},
                                 self.processed, self.dead)
        self.landing = Landing(folder)
        self.registry = Registry()
        self.mirror = LifecycleMirror()
        self.embeddings = EmbeddingCache(embedder or HashingEmbeddings())
        self.outputs: dict[tuple[str, str], Record] = {}     # (key, content hash) -> record
        self.handler_runs = 0
        self.rejected: list[Rejected] = []

    # ---------------------------------------------------------------- arrival

    def submit(self, source: str, record: dict) -> None:
        out = contracts.to_event(source, record)
        if isinstance(out, Rejected):
            self.rejected.append(out)
            self.dead.add_rejected(out)
            return
        self.log.append(out)
        self.broker.publish(out)

    def run(self) -> None:
        self.consumer.run_until_idle(max_steps=100_000)

    # ---------------------------------------------------------------- handlers

    def on_status(self, e: Event) -> None:
        d = e.data
        self.mirror.apply(StatusEvent(e.id, d["doc_number"], d["revision"], d["to"], e.time))

    def on_document(self, e: Event) -> None:
        self.handler_runs += 1
        d = e.data
        system = d["system"]
        data = self.landing.read(system, d["file"])
        try:
            doc = parse(d["mime"], data)
        except ParseError as err:
            raise InvalidEvent(f"parse: {err}") from None
        chash = content_hash(doc.text())
        hint = d.get("type_hint", "")
        cls = classify(doc, hint)
        fields = extract(doc, cls.doc_type, d)
        key = e.subject
        if system == "attachment":                 # no metadata of its own: the key is read from content
            number = fields.get("doc_number")
            if number is None:
                raise InvalidEvent(f"attachment {d['file_name']}: no document number in its content")
            rev = fields["revision"].value if "revision" in fields else "01"
            key = f"{number.value}/{rev}"
        self.registry.register(key, system, d.get("file_name", d["file"]), chash)
        if (key, chash) in self.outputs:           # the same document from another system: done once
            return
        redacted, pii = redact(doc)
        chunks = chunk(key, redacted)
        self.embeddings.vectors(chunks)
        failures = validate(cls.doc_type, fields, {**d, "doc_number": key.split("/")[0],
                                                    "revision": key.split("/")[1]})
        self.outputs[(key, chash)] = Record(key, system, chash, cls.doc_type, cls, fields, failures, pii, chunks,
                                            list(d.get("acl_groups", [])), d.get("parent"), redacted)
        for att in doc.attachments:                # back through intake as documents of their own
            blob = self.landing.put(att.data)
            ev = Event(f"{e.key}/{att.name}", "dms/attachment", "document.upserted", f"{key}/{att.name}", e.time,
                       {"system": "attachment", "file": blob, "file_name": att.name, "mime": att.mime,
                        "acl_groups": d.get("acl_groups", []), "parent": key, "type_hint": ""})
            self.log.append(ev)
            self.broker.publish(ev)

    # ---------------------------------------------------------------- views

    def records(self) -> dict[str, Record]:
        """One record per document: the system of record's content where copies conflict."""
        canon = self.registry.records()
        return {k: self.outputs[(k, c.content_hash)] for k, c in sorted(canon.items())
                if (k, c.content_hash) in self.outputs}

    def status(self, rec: Record) -> str | None:
        t = taxonomy()["types"].get(rec.doc_type or "", {})
        if not t.get("controlled"):
            return "effective" if rec.doc_type else None      # records are current from arrival
        number, rev = rec.key.split("/")
        s = self.mirror.revision(number, rev)
        return s.status if s else None

    def fingerprint(self) -> str:
        """Everything intake concluded, as one hash: equal hashes mean the same outcome."""
        recs = {k: {"type": r.doc_type, "status": self.status(r),
                    "fields": {n: str(f.value) for n, f in sorted(r.fields.items())},
                    "failures": sorted((x.rule, x.field) for x in r.failures),
                    "chunks": [c.chunk_hash for c in r.chunks]} for k, r in self.records().items()}
        reg = {k: (c.content_hash, sorted(x.uri for x in c.locations), sorted(x.uri for x in c.conflicts),
                   c.same_content_as) for k, c in self.registry.records().items()}
        life = {n: {r: s.status for r, s in sorted(revs.items())} for n, revs in sorted(self.mirror.documents().items())}
        dead = sorted({x["reason"] for x in self.dead.items()})   # a record quarantined twice is one quarantine
        blob = json.dumps([recs, reg, life, dead], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()


# ---------------------------------------------------------------- the fixtures as arrivals

def arrivals(folder: Path = FIXTURES) -> list[tuple[str, dict]]:
    """Every source record, in the order the sources produced them (by their own timestamps)."""
    out = []
    src = folder / "sources"
    for source, path, ts in (("qdms", src / "qdms" / "metadata.jsonl", lambda r: r.get("Modified", "")),
                             ("sharepoint", src / "sharepoint" / "items.jsonl", lambda r: r["lastModifiedDateTime"]),
                             ("sap", src / "sap" / "documents.jsonl", lambda r: r["AEDAT"] + "T00:00:00Z"),
                             ("status", src / "qdms" / "status_events.jsonl", lambda r: r["at"])):
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            out.append((ts(r), source, r))
    out.sort(key=lambda x: (x[0], x[1], json.dumps(x[2], sort_keys=True)))
    return [(s, r) for _, s, r in out]


def run_intake(folder: Path = FIXTURES, shuffle_seed: int | None = None, redeliver: float = 0.0,
               crash_after: int | None = None, dedup: bool = True, workdir=None) -> Intake:
    """Intake over every fixture. shuffle_seed reorders arrival; redeliver sends that share of
    events twice; crash_after drops every in-flight message back to the queue after that many
    handled, as a consumer restart would."""
    intake = Intake(folder, workdir=workdir, dedup=dedup)
    items = arrivals(folder)
    rng = random.Random(shuffle_seed if shuffle_seed is not None else 0)
    if shuffle_seed is not None:
        rng.shuffle(items)
    for source, record in items:
        intake.submit(source, record)
        if redeliver and rng.random() < redeliver:
            intake.submit(source, record)
    if crash_after is not None:
        for _ in range(crash_after):
            intake.broker.receive()                 # taken, never acknowledged
        intake.broker.crash()
    intake.run()
    return intake
