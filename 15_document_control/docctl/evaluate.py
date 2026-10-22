"""Every number retrieval reports, computed against fixtures/questions.yaml.

A unit of evidence is a section of a revision ("SOP-QA-0012/03#4 Procedure"). Each
question lists the units that answer it, as its asker may read them, and the units it must
never return: superseded revisions for a question about the current one, and documents the
asker may not open.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .assistant import route
from .corpus import FIXTURES, USERS
from .index import Index, build
from .retrieve import ANSWERABLE, retrieve


def questions(folder: Path = FIXTURES) -> list[dict]:
    return yaml.safe_load((folder / "questions.yaml").read_text(encoding="utf-8"))


def unit(chunk_id: str) -> str:
    return chunk_id.rsplit("#", 1)[0]


RETRIEVAL_KINDS = ("lookup", "effective_content", "history", "permission", "lot", "injection", "unanswerable")


def run_questions(index: Index, embedder, qs, groups_for=lambda q: USERS[q["as"]], **kw) -> list[dict]:
    rows = []
    for q in qs:
        if q["kind"] not in RETRIEVAL_KINDS:
            continue
        r = retrieve(index, q["question"], groups_for(q), embedder, **kw)
        units = list(dict.fromkeys(unit(h.row.chunk_id) for h in r.hits))
        rows.append({"id": q["id"], "kind": q["kind"], "route": r.route, "returned": len(r.hits),
                     "found": len(set(q["expected"]) & set(units)), "expected": len(q["expected"]),
                     "forbidden": sorted(set(q["forbidden"]) & set(units)),
                     "not_effective": sum(1 for h in r.hits if h.status != "effective"),
                     "unlabelled_history": sum(1 for h in r.hits if h.status != "effective" and
                                               not h.label.startswith(h.status)),
                     "unreadable": sum(1 for h in r.hits if not set(groups_for(q)) & index.readers[h.row.key]),
                     "coverage": r.coverage, "best_bm25": round(r.best_bm25, 2), "answerable": r.answerable,
                     "should_answer": q["answerable"], "flagged_in_hits": sum(h.row.suspected_injection for h in r.hits)})
    return rows


def _sum(rows, kinds=None):
    rows = [r for r in rows if kinds is None or r["kind"] in kinds]
    return {"questions": len(rows), "found": sum(r["found"] for r in rows), "expected": sum(r["expected"] for r in rows)}


def calibrate(rows) -> dict:
    """The answerability threshold, from development questions that went to search."""
    search = [r for r in rows if r["route"] == "search"]
    ans = [r["coverage"] for r in search if r["should_answer"]]
    una = [r["coverage"] for r in search if not r["should_answer"]]
    bm_a = [r["best_bm25"] for r in search if r["should_answer"]]
    bm_u = [r["best_bm25"] for r in search if not r["should_answer"]]
    return {"coverage": {"lowest_answerable": min(ans), "highest_unanswerable": max(una),
                         "separates": min(ans) > max(una), "midpoint": round((min(ans) + max(una)) / 2, 2)},
            "bm25": {"lowest_answerable": min(bm_a), "highest_unanswerable": max(bm_u),
                     "separates": min(bm_a) > max(bm_u)},
            "threshold_in_use": ANSWERABLE}


class _Model:
    def __init__(self, base, model, dims=None):
        self.base, self.model = base, model

    def embed_documents(self, texts):
        return self.base.embed_documents(texts)

    def embed_query(self, text):
        return self.base.embed_query(text)


class Scrambled:
    """A deliberately bad embedding model, for the control: vectors from a hash, no meaning."""

    model = "scrambled"

    def _v(self, t):
        import hashlib

        import numpy as np

        seed = int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)
        return list(np.random.default_rng(seed).standard_normal(256))

    def embed_documents(self, texts):
        return [self._v(t) for t in texts]

    def embed_query(self, text):
        return self._v(text)


def migration(intake, embedder, qs) -> dict:
    """s7: a new embedding model builds a shadow index; vector-only recall on the development
    questions decides whether the alias flips. A deliberately bad model is the control."""
    from llm_provider import HashingEmbeddings

    from .index import Alias

    old = build(intake, embedder, "smartdms-v1")
    alias = Alias(old)

    def score(idx, emb):
        return _sum(run_questions(idx, emb, qs, lexical=False), ("effective_content", "lot", "history"))

    base = score(old, embedder)
    cand = _Model(HashingEmbeddings(dimensions=2048), "hashing-2048")
    new = build(intake, cand, "smartdms-v2")
    s_new = score(new, cand)
    flipped = alias.flip(new, base["found"], s_new["found"])
    bad = build(intake, Scrambled(), "smartdms-v3")
    s_bad = score(bad, Scrambled())
    refused = not alias.flip(bad, s_new["found"], s_bad["found"])
    refused_query = False
    try:
        new.cosine(embedder.embed_query("x"), embedder.model)
    except ValueError:
        refused_query = True
    alias.rollback()
    return {"vector_only_found": {"hashing-4096 (in use)": base, "hashing-2048 (candidate)": s_new,
                                  "scrambled (control)": s_bad},
            "candidate_flipped": flipped, "bad_model_refused": refused,
            "query_from_another_model_refused": refused_query, "rolled_back_to": alias.active.name,
            "alias_history": alias.history}


def summary(intake, embedder) -> dict:
    qs = questions()
    index = build(intake, embedder)
    rows = run_questions(index, embedder, qs)
    default = [r for r in rows if r["kind"] != "history"]
    control_status = run_questions(index, embedder, qs, status_filter=False)
    control_groups = run_questions(index, embedder, qs,
                                   groups_for=lambda q: sorted({g for gs in USERS.values() for g in gs}))
    contractor = run_questions(index, embedder, qs, groups_for=lambda q: USERS["contractor"])
    routed = [(q["id"], q["intent"], route(q["question"])) for q in qs]
    by_kind = {k: _sum(rows, (k,)) for k in RETRIEVAL_KINDS}
    return {
        "index": {"chunks": len(index.rows), "embedded": index.embedded, "reused_unchanged": index.reused,
                  "embedding_model_id": index.embedding_model_id,
                  "flagged_as_instruction_like": sum(r.suspected_injection for r in index.rows)},
        "questions": {"development": len(qs), "retrieval": len(rows)},
        "recall": {"all": _sum(rows), "by_kind": by_kind},
        "short_circuit": sum(1 for r in rows if r["route"] == "document"),
        "stale_versions": {"returned_by_default": sum(r["not_effective"] for r in default),
                           "control_without_the_status_filter": sum(
                               r["not_effective"] for r in control_status if r["kind"] != "history"),
                           "superseded_units_forbidden_and_returned": sum(len(r["forbidden"]) for r in default
                                                                         if r["kind"] != "permission"),
                           "history_hits_unlabelled": sum(r["unlabelled_history"] for r in rows)},
        "access": {"unreadable_returned": sum(r["unreadable"] for r in rows),
                   "forbidden_returned": sum(len(r["forbidden"]) for r in rows if r["kind"] == "permission"),
                   "control_with_every_group": sum(len(r["forbidden"]) for r in control_groups
                                                   if r["kind"] == "permission"),
                   "returned_to_someone_in_no_group": sum(r["returned"] for r in contractor)},
        "answerability": {**calibrate(rows),
                          "correct": sum(1 for r in rows if r["answerable"] == r["should_answer"]),
                          "of": len(rows)},
        "routing": {"correct": sum(1 for _, want, got in routed if want == got), "of": len(routed),
                    "wrong": [x for x in routed if x[1] != x[2]]},
        "injection": {"questions_retrieving_it": sum(1 for r in rows if r["flagged_in_hits"])},
        "migration": migration(intake, embedder, qs),
        "per_question": rows,
    }
