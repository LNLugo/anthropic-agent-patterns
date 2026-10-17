"""Score retrievers against the questions' expected evidence.

recall@k     expected units returned / expected units
precision@k  expected units returned / units returned
forbidden    units returned that the asker may not read (must be 0; questions name them)

A unit is a log line, or a page (any of its sections). Scored separately for the
development questions (used while the retriever was built) and the held-out ones
(written after it was frozen), and for multi-hop and single-hop questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .corpus import USERS
from .retrieve import Retriever, unit


@dataclass
class Score:
    qid: str
    kind: str
    held_out: bool
    found: int
    expected: int
    returned: int
    forbidden: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.found / self.expected


def configs(r: Retriever, k: int) -> dict:
    """Each retriever under test, by name. Every one returns k units for the same question."""
    return {
        "vector": lambda q, g: r.vector_only(q, g, k),
        "vector + rarity": lambda q, g: r.rerank_by_rarity(r.vector_only(q, g, 50), k),
        "graph (default)": lambda q, g: r.graph_expanded(q, g, k),
        "graph, no rarity": lambda q, g: r.graph_expanded(q, g, k, rarity=False),
        "graph + idf": lambda q, g: r.graph_expanded(q, g, k, idf=True),
    }


def score(run, questions: list[dict], groups_for=lambda q: USERS[q["as"]]) -> list[Score]:
    out = []
    for q in questions:
        hits = run(q["question"], groups_for(q))
        units = list(dict.fromkeys(unit(h.key) for h in hits))
        expected = set(q["expected"])
        out.append(Score(q["id"], q["kind"], bool(q.get("held_out")), len(expected & set(units)), len(expected),
                         len(units), sorted(set(q.get("forbidden", ())) & set(units)),
                         sorted(expected - set(units))))
    return out


def summarise(scores: list[Score]) -> dict:
    def agg(rows):
        found, exp, ret = (sum(getattr(s, a) for s in rows) for a in ("found", "expected", "returned"))
        return {"questions": len(rows), "found": found, "expected": exp,
                "recall": round(found / exp, 3) if exp else None,
                "precision": round(found / ret, 3) if ret else None,
                "forbidden": sum(len(s.forbidden) for s in rows)}

    out = {}
    for split, rows in (("development", [s for s in scores if not s.held_out]),
                        ("held_out", [s for s in scores if s.held_out])):
        out[split] = {"all": agg(rows), "multi_hop": agg([s for s in rows if s.kind == "multi_hop"]),
                      "single_hop": agg([s for s in rows if s.kind == "single_hop"])}
    return out
