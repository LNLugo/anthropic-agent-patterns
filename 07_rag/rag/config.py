"""Build a retriever from a YAML config, so each retrieval strategy is one file.

Example (configs/hybrid_rerank.yaml):

    name: hybrid_rerank
    chunking: {size: 1000, overlap: 200, parent_size: 4000}
    k: 5
    retriever:
      type: rerank
      candidate_k: 20
      reranker: {type: cross_encoder, model: cross-encoder/ms-marco-MiniLM-L-6-v2}
      base:
        type: hybrid
        rrf_k: 60
        candidate_k: 50
        members:
          - {type: bm25}
          - {type: dense, embedder: {type: sentence_transformer, model: BAAI/bge-small-en-v1.5}}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .retrievers import (
    BM25Retriever,
    CrossEncoderReranker,
    DenseRetriever,
    HybridRetriever,
    ParentRetriever,
    RerankRetriever,
    Retriever,
    SentenceTransformerEmbedder,
    TfidfSvdEmbedder,
)

DEFAULT_CHUNKING = {"size": 1000, "overlap": 200, "parent_size": 4000}


def load_config(path: str | Path) -> dict[str, Any]:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cfg.setdefault("name", Path(path).stem)
    cfg["chunking"] = {**DEFAULT_CHUNKING, **cfg.get("chunking", {})}
    cfg.setdefault("k", 5)
    return cfg


def _embedder(spec: dict[str, Any]):
    kind = spec.get("type", "sentence_transformer")
    if kind == "sentence_transformer":
        return SentenceTransformerEmbedder(spec.get("model", "BAAI/bge-small-en-v1.5"), spec.get("query_prefix", ""))
    if kind == "tfidf_svd":
        return TfidfSvdEmbedder(spec.get("n_components", 256))
    raise ValueError(f"unknown embedder type: {kind}")


def _reranker(spec: dict[str, Any]):
    kind = spec.get("type", "cross_encoder")
    if kind == "cross_encoder":
        return CrossEncoderReranker(spec.get("model", "cross-encoder/ms-marco-MiniLM-L-6-v2"))
    raise ValueError(f"unknown reranker type: {kind}")


def build_retriever(spec: dict[str, Any]) -> Retriever:
    kind = spec["type"]
    if kind == "bm25":
        return BM25Retriever(spec.get("k1", 1.5), spec.get("b", 0.75))
    if kind == "dense":
        return DenseRetriever(_embedder(spec.get("embedder", {})))
    if kind == "hybrid":
        return HybridRetriever(
            [build_retriever(m) for m in spec["members"]], spec.get("rrf_k", 60), spec.get("candidate_k", 50)
        )
    if kind == "rerank":
        return RerankRetriever(build_retriever(spec["base"]), _reranker(spec.get("reranker", {})), spec.get("candidate_k", 20))
    if kind == "parent":
        return ParentRetriever(build_retriever(spec["base"]), child_k=spec.get("child_k", 20))
    raise ValueError(f"unknown retriever type: {kind}")
