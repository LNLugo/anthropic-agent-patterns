from pathlib import Path

import pytest

from rag.chunking import Chunk, chunk_contract
from rag.config import build_retriever, load_config
from rag.metrics import aggregate, score_query
from rag.retrievers import (
    BM25Retriever,
    DenseRetriever,
    HybridRetriever,
    ParentRetriever,
    RerankRetriever,
    Scored,
    TfidfSvdEmbedder,
    rrf_fuse,
)

CONFIGS = sorted((Path(__file__).parents[1] / "configs").rglob("*.yaml"))


def _c(i, start=0):
    return Chunk(f"x:{i}", "x", f"text {i}", start, start + 10)


def test_rrf_rewards_agreement_across_lists():
    a, b, c = _c("a"), _c("b"), _c("c")
    fused = rrf_fuse([[Scored(a, 9), Scored(b, 8)], [Scored(b, 0.9), Scored(c, 0.8)]], rrf_k=60)
    assert fused[0].chunk.id == b.id  # b is in both lists
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)


@pytest.fixture
def indexed(contract):
    kids, parents = chunk_contract(contract.text, contract.id, size=300, overlap=60, parent_size=1200)
    return kids, parents


def _gold_hit(retrieved, query):
    return any(c.overlaps(g) for c in retrieved for g in query.gold_spans)


def test_bm25_finds_governing_law(indexed, queries):
    kids, _ = indexed
    r = BM25Retriever()
    r.fit(kids)
    q = queries["Governing Law"]
    assert _gold_hit([s.chunk for s in r.retrieve(q.text, q.contract_id, 3)], q)


def test_hybrid_with_offline_embedder_runs(indexed, queries):
    kids, _ = indexed
    r = HybridRetriever([BM25Retriever(), DenseRetriever(TfidfSvdEmbedder(16))], candidate_k=10)
    r.fit(kids)
    q = queries["Governing Law"]
    out = r.retrieve(q.text, q.contract_id, 3)
    assert len(out) == 3 and _gold_hit([s.chunk for s in out], q)


class ReverseReranker:
    name = "reverse"

    def score(self, query, texts):
        return list(range(len(texts)))  # last candidate scores highest


def test_rerank_reorders_candidates(indexed, queries):
    kids, _ = indexed
    base = BM25Retriever()
    rr = RerankRetriever(base, ReverseReranker(), candidate_k=4)
    rr.fit(kids)
    q = queries["Governing Law"]
    cands = base.retrieve(q.text, q.contract_id, 4)
    out = rr.retrieve(q.text, q.contract_id, 2)
    assert [s.chunk.id for s in out] == [cands[3].chunk.id, cands[2].chunk.id]


def test_parent_retriever_returns_unique_parents(indexed, queries):
    kids, parents = indexed
    r = ParentRetriever(BM25Retriever(), parents, child_k=10)
    r.fit(kids)
    q = queries["Expiration Date"]
    out = r.retrieve(q.text, q.contract_id, 3)
    ids = [s.chunk.id for s in out]
    assert len(ids) == len(set(ids)) and all(i in parents for i in ids)


def test_metrics_hit_recall_mrr():
    gold = ((100, 120), (500, 520))
    miss = Chunk("m", "x", "a" * 50, 0, 50)
    hit1 = Chunk("h", "x", "b" * 50, 90, 140)
    r = score_query("q", gold, [miss, hit1], latency_ms=1.0)
    assert r.hit and r.reciprocal_rank == 0.5 and r.span_recall == 0.5
    agg = aggregate([r, score_query("q2", gold, [miss], 2.0)])
    assert agg["hit"] == 0.5 and agg["mrr"] == 0.25


def test_unanswerable_query_is_rejected_by_scorer():
    with pytest.raises(ValueError):
        score_query("q", (), [], 0.0)


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_every_config_builds(path):
    cfg = load_config(path)
    assert cfg["k"] > 0
    build_retriever(cfg["retriever"])  # model downloads are lazy, so this is offline-safe
