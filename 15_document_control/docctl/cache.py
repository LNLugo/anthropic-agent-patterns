"""Caching and freshness (s14).

    answer           normalised question + the asker's access fingerprint + the index the
                     alias points to; purged when any document it cites changes status,
                     version or readers
    retrieval        query + filters + access fingerprint; 15 minutes, and cleared whenever
                     the index changes
    query embedding  text hash + embedding model id

Two people with different access never share an entry, because the fingerprint of their
groups is part of every key. A cached answer never outlives the revision it cites: the
index tells the cache which document changed, in the same write that changes it.
"""

from __future__ import annotations

import hashlib
import re
import time


def normalise(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower()).rstrip("?.! ")


def fingerprint(groups) -> str:
    return hashlib.sha256("|".join(sorted(set(groups))).encode()).hexdigest()[:16]


class AnswerCache:
    def __init__(self):
        self._entries: dict[tuple, object] = {}
        self.hits = self.misses = self.purged = 0

    def key(self, question, groups, index) -> tuple:
        return (normalise(question), fingerprint(groups), index.name)

    def get(self, question, groups, index):
        a = self._entries.get(self.key(question, groups, index))
        if a is None:
            self.misses += 1
        else:
            self.hits += 1
        return a

    def put(self, question, groups, index, answer) -> None:
        self._entries[self.key(question, groups, index)] = answer

    def purge_document(self, doc_number: str) -> int:
        """Drop every answer that cites this document, in any revision."""
        gone = [k for k, a in self._entries.items() if doc_number in a.cited]
        for k in gone:
            del self._entries[k]
        self.purged += len(gone)
        return len(gone)

    def __len__(self) -> int:
        return len(self._entries)


class RetrievalCache:
    TTL = 15 * 60

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._entries: dict[tuple, tuple[float, object]] = {}

    def get(self, key):
        e = self._entries.get(key)
        if e is None or self.clock() - e[0] > self.TTL:
            self._entries.pop(key, None)
            return None
        return e[1]

    def put(self, key, value) -> None:
        self._entries[key] = (self.clock(), value)

    def clear(self) -> None:
        self._entries.clear()


class QueryEmbeddings:
    """A query embedded once per model: a model change is a new key, never a stale vector."""

    def __init__(self, embedder):
        self.embedder = embedder
        self.model = getattr(embedder, "model", "unknown")
        self._v: dict[tuple[str, str], list[float]] = {}
        self.computed = 0

    def embed_query(self, text: str) -> list[float]:
        k = (hashlib.sha256(text.encode()).hexdigest(), self.model)
        if k not in self._v:
            self._v[k] = self.embedder.embed_query(text)
            self.computed += 1
        return self._v[k]


class Freshness:
    """Applies a document change to the index and every cache in one step."""

    def __init__(self, index, answers: AnswerCache, retrievals: RetrievalCache | None = None):
        self.index, self.answers, self.retrievals = index, answers, retrievals

    def status_changed(self, doc_number: str, revisions: dict[str, str]) -> int:
        self.index.set_status(doc_number, revisions)
        return self._purge(doc_number)

    def readers_changed(self, key: str, groups) -> int:
        self.index.set_readers(key, groups)
        return self._purge(key.split("/")[0])

    def _purge(self, doc_number: str) -> int:
        if self.retrievals is not None:
            self.retrievals.clear()
        return self.answers.purge_document(doc_number)
