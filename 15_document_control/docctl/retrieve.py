"""Retrieval (s9): mandatory filters first, then hybrid ranking.

1. **Query understanding, by rules.** Document numbers, revisions, material and supplier
   codes, lots and sites are read from the question with patterns. Words such as
   "previous", "superseded" or a named revision ask for history.
2. **Mandatory filters, before anything is ranked.** The asker's groups must intersect the
   document's readers, and status must be effective unless the question asks for history.
   History returns superseded and obsolete revisions as well, each labelled. A row whose
   status is unknown is never returned (fail closed).
3. **A named document short-circuits the search.** If the question names a document number
   the asker can read, its chunks are returned and nothing else is ranked. A number the
   asker cannot read behaves like one that does not exist.
4. **Hybrid candidates.** BM25 top 50 and vector top 50 over what passed the filters, fused
   with 07's reciprocal rank fusion (k 60). Several hits from one section collapse to the
   best one.
5. **Answerability.** The share of the question's distinctive vocabulary (its terms
   weighted by IDF, common words dropped) that the best chunk contains. Below a threshold
   calibrated on the development questions, there is nothing to answer from, and the caller
   abstains or asks for clarification instead of generating. A raw BM25 score was tried
   first and did not separate answerable from unanswerable questions (run_retrieval.py
   reports both).

Not built: a cross-encoder or semantic reranker (07 has one; it needs a model download,
so offline runs would not exercise it), multi-turn query rewriting, and language detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from rag.chunking import Chunk as RagChunk
from rag.retrievers import Scored, rrf_fuse

from .index import Index, Row

DOC_NUMBER = re.compile(r"\b(SOP-[A-Z]{2}-\d{4}|SPEC-RM-\d{4}|CC-\d{4}-\d{3}|DEV-\d{4}-\d{3}|CMP-\d{4}-\d{3}"
                        r"|COA-\d{4}-L\d{2}-\d{4}|COR-\d{4}-\d{3})\b")
REVISION = re.compile(r"\brev(?:ision)?\.?\s*(\d{1,2})\b", re.I)
MATERIAL = re.compile(r"\bRM-\d{5}\b")
SUPPLIER = re.compile(r"\bSUP-\d{4}\b")
LOT = re.compile(r"\bL\d{2}-\d{4}\b")
HISTORY = re.compile(r"\b(previous|prior|earlier|superseded|obsolete|old|history|used to|before (it|the) (was )?"
                     r"(changed|revised))\b", re.I)
SITES = ("Northfield", "Brookvale", "Lakemoor")

CANDIDATES = 50
RRF_K = 60
TOP = 8
ANSWERABLE = 0.5          # coverage needed to answer: the midpoint between answerable and unanswerable
                          # development questions (run_retrieval.py recomputes it; a test checks it)
STOPWORDS = frozenset("a an and are as at be by did do does for from how i in is it of on or the to was were what "
                      "when where which who why will with show me there".split())


@dataclass(frozen=True)
class Understood:
    doc_numbers: tuple[str, ...]
    revision: str | None
    materials: tuple[str, ...]
    suppliers: tuple[str, ...]
    lots: tuple[str, ...]
    sites: tuple[str, ...]
    history: bool


def understand(question: str) -> Understood:
    rev = REVISION.search(question)
    return Understood(tuple(dict.fromkeys(DOC_NUMBER.findall(question))),
                      f"{int(rev.group(1)):02d}" if rev else None,
                      tuple(MATERIAL.findall(question)), tuple(SUPPLIER.findall(question)),
                      tuple(LOT.findall(question)), tuple(s for s in SITES if s.lower() in question.lower()),
                      bool(HISTORY.search(question)) or bool(rev))


@dataclass(frozen=True)
class Hit:
    row: Row
    status: str
    score: float           # fused score, or BM25 within a named document
    bm25: float
    label: str             # "effective", or "superseded: not the current version"


@dataclass(frozen=True)
class Retrieval:
    hits: list[Hit]
    understood: Understood
    route: str             # "document" (short circuit), "search", or "nothing visible"
    answerable: bool
    best_bm25: float
    coverage: float = 0.0


EFFECTIVE = {"effective"}
HISTORY_STATUSES = {"effective", "superseded", "obsolete"}


def _label(status: str) -> str:
    return "effective" if status == "effective" else f"{status}: not the current version"


def retrieve(index: Index, question: str, groups, embedder=None, k: int = TOP,
             status_filter: bool = True, lexical: bool = True) -> Retrieval:
    """status_filter=False is the control for the stale-version leakage test, never a user option.
    lexical=False ranks by vectors alone: how two embedding models are compared (s7)."""
    u = understand(question)
    statuses = (HISTORY_STATUSES if u.history else EFFECTIVE) if status_filter else None
    bm = index.bm25(question)
    if u.doc_numbers:
        mask = index.visible(groups, statuses, doc_numbers=set(u.doc_numbers), revision=u.revision)
        if mask.any():
            order = sorted(np.flatnonzero(mask), key=lambda i: (-bm[i], index.rows[i].chunk_id))
            hits = [Hit(index.rows[i], index.row_status(index.rows[i]), float(bm[i]), float(bm[i]),
                        _label(index.row_status(index.rows[i]))) for i in order[:k]]
            return Retrieval(hits, u, "document", True, float(bm[order[0]]), 1.0)
    mask = index.visible(groups, statuses)
    if not mask.any():
        return Retrieval([], u, "nothing visible", False, 0.0)
    allowed = np.flatnonzero(mask)
    lists = []
    if lexical:
        lex = sorted(allowed, key=lambda i: (-bm[i], index.rows[i].chunk_id))[:CANDIDATES]
        lists.append([Scored(_rag(index.rows[i]), float(bm[i])) for i in lex])
    if embedder is not None:
        cos = index.cosine(embedder.embed_query(question), getattr(embedder, "model", "unknown"))
        vec = sorted(allowed, key=lambda i: (-cos[i], index.rows[i].chunk_id))[:CANDIDATES]
        lists.append([Scored(_rag(index.rows[i]), float(cos[i])) for i in vec])
    fused = rrf_fuse(lists, RRF_K)
    by_id = {r.chunk_id: (i, r) for i, r in enumerate(index.rows)}
    hits, seen = [], set()
    for s in fused:
        i, r = by_id[s.chunk.id]
        section = (r.key, r.section_path)
        if section in seen:                    # several hits from one section count once
            continue
        seen.add(section)
        st = index.row_status(r)
        hits.append(Hit(r, st, s.score, float(bm[i]), _label(st)))
        if len(hits) == k:
            break
    best = max((h.bm25 for h in hits), default=0.0)
    cov = max((coverage(index, question, h.row) for h in hits), default=0.0)
    return Retrieval(hits, u, "search", cov >= ANSWERABLE, best, round(cov, 3))


def coverage(index: Index, question: str, row: Row) -> float:
    """IDF-weighted share of the question's content terms found in one chunk (with its context)."""
    from .index import terms

    from .index import _TOKEN

    q = [t for t in dict.fromkeys(_TOKEN.findall(question.lower())) if t not in STOPWORDS]   # identifiers whole
    if not q:
        return 0.0
    have = set(terms(row.search_text))
    total = sum(index.idf(t) for t in q)
    return sum(index.idf(t) for t in q if t in have) / total if total else 0.0


def _rag(r: Row) -> RagChunk:
    """A row as 07's chunk, so 07's fusion and citation checks run on it unchanged."""
    return RagChunk(r.chunk_id, r.key, r.text, 0, len(r.text))
