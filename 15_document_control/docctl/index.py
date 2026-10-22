"""The search index (s7, s8): the in-process stand-in for Azure AI Search.

Each row is a chunk with its vector, BM25 terms and the metadata the specification lists:
doc number, revision, status, effective date, type, site, reader groups, section path,
pages, chunk hash, the embedding model that made the vector, and a flag for text that reads
like instructions (s12: flagged at ingest).

- **Status and access are metadata, not content.** When a revision becomes effective, the
  status of every revision of that document is replaced in one write; when a group loses
  access, the document's reader groups are replaced in one write. Nothing is re-embedded,
  and a query can never see half of either change.
- **Every vector names its model.** A query vector from another model is refused, as 14
  refuses an index made for another model's dimensions.
- **Reindexing is a pointer flip.** A new index is built alongside, scored on the gold
  questions, and the alias points to it only if it does at least as well. The old index is
  kept for rollback.

What Azure AI Search does that this does not: HNSW approximate search with int8
quantization and full-precision rescoring (here search is exact over every row), semantic
ranking, partitions and replicas. The parameters the design chose for those are in its s8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

INJECTION = re.compile(r"ignore (all |any )?(previous|prior) instructions|disregard (the|your) (rules|instructions)"
                       r"|\bsystem note\b|call the \w+ tool|without review", re.I)
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def terms(text: str) -> list[str]:
    """BM25 terms: words, with identifiers such as SOP-QA-0012 and L26-0689 also kept whole."""
    out = []
    for t in _TOKEN.findall(text.lower()):
        parts = t.split("-")
        out += parts + ([t] if len(parts) > 1 else [])
    return out


@dataclass
class Row:
    chunk_id: str
    key: str                       # doc_number/revision
    doc_number: str
    revision: str
    doc_type: str | None
    site: str
    section_path: str
    pages: tuple[int, int]
    text: str
    chunk_hash: str
    effective_date: str | None
    embedding_model_id: str
    suspected_injection: bool
    is_table: bool = False
    context: str = ""              # the document this chunk belongs to, indexed with it (never shown as its text)

    @property
    def search_text(self) -> str:
        return f"{self.context}\n{self.text}" if self.context else self.text


@dataclass
class Index:
    name: str
    embedding_model_id: str
    rows: list[Row] = field(default_factory=list)
    vectors: np.ndarray | None = None
    status: dict[str, dict[str, str]] = field(default_factory=dict)   # doc_number -> revision -> status
    readers: dict[str, frozenset[str]] = field(default_factory=dict)  # key -> reader groups
    version: int = 0
    embedded: int = 0
    reused: int = 0
    _bm25: object = None

    def finish(self, vectors: list[list[float]]) -> "Index":
        from rank_bm25 import BM25Okapi

        v = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(v, axis=1, keepdims=True)
        self.vectors = v / np.where(norms == 0, 1, norms)
        self._bm25 = BM25Okapi([terms(r.search_text) for r in self.rows])
        self._df = {}
        for r in self.rows:
            for t in set(terms(r.search_text)):
                self._df[t] = self._df.get(t, 0) + 1
        return self

    # ---------------------------------------------------------------- one-write metadata changes

    def set_status(self, doc_number: str, revisions: dict[str, str]) -> None:
        """Every revision's status for one document, replaced in a single assignment."""
        self.status[doc_number] = dict(revisions)
        self.version += 1

    def set_readers(self, key: str, groups) -> None:
        self.readers[key] = frozenset(groups)
        self.version += 1

    def row_status(self, r: Row) -> str | None:
        return self.status.get(r.doc_number, {}).get(r.revision)

    # ---------------------------------------------------------------- scoring

    def visible(self, groups, statuses: set[str] | None, doc_numbers=None, revision=None) -> np.ndarray:
        """The mask applied before anything is ranked: reader groups, then status."""
        g = set(groups)
        out = np.zeros(len(self.rows), dtype=bool)
        for i, r in enumerate(self.rows):
            if not (g & self.readers.get(r.key, frozenset())):
                continue
            if doc_numbers is not None and r.doc_number not in doc_numbers:
                continue
            if revision is not None and r.revision != revision:
                continue
            if statuses is not None and self.row_status(r) not in statuses:
                continue
            out[i] = True
        return out

    def idf(self, term: str) -> float:
        import math

        return math.log((len(self.rows) + 1) / (self._df.get(term, 0) + 0.5))

    def bm25(self, query: str) -> np.ndarray:
        return np.asarray(self._bm25.get_scores(terms(query)), dtype=np.float64)

    def cosine(self, query_vector, model_id: str) -> np.ndarray:
        if model_id != self.embedding_model_id:
            raise ValueError(f"index {self.name} holds {self.embedding_model_id} vectors; the query was embedded "
                             f"with {model_id}")
        q = np.asarray(query_vector, dtype=np.float32)
        return self.vectors @ (q / (np.linalg.norm(q) or 1.0))


CONTEXT_FIELDS = ("material_id", "supplier_id", "lot")


def context(key: str, rec) -> str:
    """One line naming the document a chunk comes from: number, title and the identifiers a
    question may use (material, supplier, lot). Indexed with every chunk, so a table that never
    names its material is still found by the material's name. The revision is left out: which
    revision is current is decided by the status filter, not by text, and leaving it out keeps
    an unchanged section's indexed text identical across revisions, so it is embedded once."""
    number = key.split("/")[0]
    ids = " ".join(str(rec.fields[f].value) for f in CONTEXT_FIELDS if f in rec.fields)
    title = rec.fields["title"].value if "title" in rec.fields else ""
    return f"{number} {title} {ids}".strip()


def build(intake, embedder, name: str = "smartdms-v1") -> Index:
    """An index over every intake record, with status and readers from the lifecycle mirror."""
    model_id = getattr(embedder, "model", "unknown")
    idx = Index(name, model_id)
    for key, rec in intake.records().items():
        number, rev = key.split("/")
        idx.readers[key] = frozenset(rec.acl_groups)
        for c in rec.chunks:
            idx.rows.append(Row(c.chunk_id, key, number, rev, rec.doc_type, str(rec.fields.get("site").value)
                                if "site" in rec.fields else "", c.section_path, c.pages, c.text, c.chunk_hash,
                                str(rec.fields["effective_date"].value) if "effective_date" in rec.fields else None,
                                model_id, bool(INJECTION.search(c.text)), c.is_table, context(key, rec)))
    for key, rec in intake.records().items():
        number, rev = key.split("/")
        idx.status.setdefault(number, {})[rev] = intake.status(rec) or "unknown"
    unique = list(dict.fromkeys(r.search_text for r in idx.rows))     # an unchanged section is embedded once
    by_text = dict(zip(unique, embedder.embed_documents(unique)))
    idx.embedded, idx.reused = len(unique), len(idx.rows) - len(unique)
    vectors = [by_text[r.search_text] for r in idx.rows]
    return idx.finish(vectors)


class Alias:
    """The application's index pointer: queries go to whichever index it names."""

    def __init__(self, index: Index):
        self.active, self.previous = index, None
        self.history = [index.name]

    def flip(self, candidate: Index, score_old: float, score_new: float) -> bool:
        """Point to the candidate only if it scores at least as well on the gold questions."""
        if score_new < score_old:
            return False
        self.previous, self.active = self.active, candidate
        self.history.append(candidate.name)
        return True

    def rollback(self) -> None:
        if self.previous is None:
            raise RuntimeError("nothing to roll back to")
        self.active, self.previous = self.previous, self.active
        self.history.append(self.active.name)
