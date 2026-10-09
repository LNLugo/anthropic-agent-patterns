"""Retrieval metrics scored against lawyer-annotated CUAD spans.

hit@k          share of queries where any top-k item overlaps any gold span
span_recall@k  share of gold spans touched by at least one top-k item
mrr            mean reciprocal rank of the first item that overlaps gold
context_chars  characters handed to the model per query, the cost side of
               every recall gain (parent retrieval and larger k both buy
               recall with tokens)
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from .chunking import Chunk


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    hit: bool
    span_recall: float
    reciprocal_rank: float
    context_chars: int
    latency_ms: float


def score_query(
    query_id: str, gold: tuple[tuple[int, int], ...], retrieved: list[Chunk], latency_ms: float
) -> QueryResult:
    if not gold:
        raise ValueError("score_query needs at least one gold span; filter unanswerable queries first")
    first = next((i for i, c in enumerate(retrieved, start=1) if any(c.overlaps(g) for g in gold)), None)
    touched = sum(1 for g in gold if any(c.overlaps(g) for c in retrieved))
    return QueryResult(
        query_id=query_id,
        hit=first is not None,
        span_recall=touched / len(gold),
        reciprocal_rank=0.0 if first is None else 1.0 / first,
        context_chars=sum(len(c.text) for c in retrieved),
        latency_ms=latency_ms,
    )


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[idx]


def aggregate(results: list[QueryResult]) -> dict[str, float]:
    if not results:
        return {}
    return {
        "queries": len(results),
        "hit": mean(r.hit for r in results),
        "span_recall": mean(r.span_recall for r in results),
        "mrr": mean(r.reciprocal_rank for r in results),
        "context_chars_mean": mean(r.context_chars for r in results),
        "est_context_tokens_mean": mean(r.context_chars for r in results) / 4.0,
        "latency_ms_p50": _pct([r.latency_ms for r in results], 0.50),
        "latency_ms_p95": _pct([r.latency_ms for r in results], 0.95),
    }
