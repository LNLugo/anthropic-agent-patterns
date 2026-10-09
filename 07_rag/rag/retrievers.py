"""Retrievers, all behind one interface so they can be swapped from YAML.

Retrieval is scoped to one contract per query (the metadata filter). That is
how contract review actually runs: a reviewer asks about the agreement in
front of them, not the whole corpus. It also mirrors a production design where
every vector query carries a filter on business unit, vendor and contract type.

Available building blocks:
  bm25    lexical scoring (Okapi BM25)
  dense   embedding similarity (sentence-transformers, or an offline TF-IDF/SVD
          stand-in for environments that cannot download models)
  hybrid  Reciprocal Rank Fusion over any member retrievers
  rerank  cross-encoder second stage over a candidate pool
  parent  return the larger parent window of each matched child
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .chunking import Chunk

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class Scored:
    chunk: Chunk
    score: float


class Retriever(Protocol):
    name: str

    def fit(self, chunks: list[Chunk]) -> None: ...

    def retrieve(self, query: str, contract_id: str, k: int) -> list[Scored]: ...


def _by_contract(chunks: list[Chunk]) -> dict[str, list[Chunk]]:
    groups: dict[str, list[Chunk]] = defaultdict(list)
    for c in chunks:
        groups[c.contract_id].append(c)
    return groups


# ---------------------------------------------------------------- lexical

class BM25Retriever:
    name = "bm25"

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self._idx: dict[str, tuple[object, list[Chunk]]] = {}

    def fit(self, chunks: list[Chunk]) -> None:
        from rank_bm25 import BM25Okapi

        for cid, group in _by_contract(chunks).items():
            self._idx[cid] = (BM25Okapi([tokenize(c.text) for c in group], k1=self.k1, b=self.b), group)

    def retrieve(self, query: str, contract_id: str, k: int) -> list[Scored]:
        bm25, group = self._idx[contract_id]
        scores = bm25.get_scores(tokenize(query))
        order = np.argsort(-scores, kind="stable")[:k]
        return [Scored(group[i], float(scores[i])) for i in order]


# ---------------------------------------------------------------- dense

class Embedder(Protocol):
    name: str

    def fit(self, texts: list[str]) -> None: ...

    def encode_docs(self, texts: list[str]) -> np.ndarray: ...

    def encode_query(self, text: str) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    """Any sentence-transformers model. BGE models expect a query instruction."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", query_prefix: str = "", batch_size: int = 32):
        self.name = f"st:{model}"
        self.model_name, self.query_prefix, self.batch_size = model, query_prefix, batch_size
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def fit(self, texts: list[str]) -> None:
        self._load()

    def encode_docs(self, texts: list[str]) -> np.ndarray:
        return self._load().encode(texts, batch_size=self.batch_size, normalize_embeddings=True)

    def encode_query(self, text: str) -> np.ndarray:
        return self._load().encode([self.query_prefix + text], normalize_embeddings=True)[0]


class TfidfSvdEmbedder:
    """Offline stand-in: TF-IDF projected to a dense space with truncated SVD.

    This is a lexical method in dense clothing. It exists so the pipeline and
    tests run without downloading a model. Never report it as a semantic
    embedding result.
    """

    def __init__(self, n_components: int = 256, seed: int = 7):
        self.name = f"tfidf-svd{n_components}"
        self.n_components, self.seed = n_components, seed
        self._vec = self._svd = None

    def fit(self, texts: list[str]) -> None:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vec = TfidfVectorizer(sublinear_tf=True, ngram_range=(1, 2), min_df=1)
        x = self._vec.fit_transform(texts)
        n = max(2, min(self.n_components, x.shape[1] - 1, x.shape[0] - 1))
        self._svd = TruncatedSVD(n_components=n, random_state=self.seed).fit(x)

    def _project(self, texts: list[str]) -> np.ndarray:
        v = self._svd.transform(self._vec.transform(texts))
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)

    def encode_docs(self, texts: list[str]) -> np.ndarray:
        return self._project(texts)

    def encode_query(self, text: str) -> np.ndarray:
        return self._project([text])[0]


class DenseRetriever:
    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.name = f"dense[{embedder.name}]"
        self._idx: dict[str, tuple[np.ndarray, list[Chunk]]] = {}

    def fit(self, chunks: list[Chunk]) -> None:
        self.embedder.fit([c.text for c in chunks])
        for cid, group in _by_contract(chunks).items():
            self._idx[cid] = (np.asarray(self.embedder.encode_docs([c.text for c in group])), group)

    def retrieve(self, query: str, contract_id: str, k: int) -> list[Scored]:
        mat, group = self._idx[contract_id]
        sims = mat @ self.embedder.encode_query(query)
        order = np.argsort(-sims, kind="stable")[:k]
        return [Scored(group[i], float(sims[i])) for i in order]


# ---------------------------------------------------------------- fusion

def rrf_fuse(rankings: list[list[Scored]], rrf_k: int = 60) -> list[Scored]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of 1 / (rrf_k + rank).

    Rank based, so BM25 scores and cosine similarities never need to be put
    on a common scale.
    """
    fused: dict[str, float] = defaultdict(float)
    keep: dict[str, Chunk] = {}
    for ranking in rankings:
        for rank, s in enumerate(ranking, start=1):
            fused[s.chunk.id] += 1.0 / (rrf_k + rank)
            keep[s.chunk.id] = s.chunk
    order = sorted(fused, key=lambda cid: (-fused[cid], cid))
    return [Scored(keep[cid], fused[cid]) for cid in order]


class HybridRetriever:
    def __init__(self, members: list[Retriever], rrf_k: int = 60, candidate_k: int = 50):
        self.members, self.rrf_k, self.candidate_k = members, rrf_k, candidate_k
        self.name = "hybrid(" + "+".join(m.name for m in members) + f",rrf{rrf_k})"

    def fit(self, chunks: list[Chunk]) -> None:
        for m in self.members:
            m.fit(chunks)

    def retrieve(self, query: str, contract_id: str, k: int) -> list[Scored]:
        lists = [m.retrieve(query, contract_id, self.candidate_k) for m in self.members]
        return rrf_fuse(lists, self.rrf_k)[:k]


# ---------------------------------------------------------------- rerank

class Reranker(Protocol):
    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(self, model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2", batch_size: int = 32):
        self.name = f"ce:{model}"
        self.model_name, self.batch_size = model, batch_size
        self._model = None

    def score(self, query: str, texts: list[str]) -> list[float]:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        return [float(x) for x in self._model.predict([(query, t) for t in texts], batch_size=self.batch_size)]


class RerankRetriever:
    """Recall stage pulls candidate_k, precision stage reorders and keeps k."""

    def __init__(self, base: Retriever, reranker: Reranker, candidate_k: int = 20):
        self.base, self.reranker, self.candidate_k = base, reranker, candidate_k
        self.name = f"rerank[{reranker.name}]({base.name},top{candidate_k})"

    def fit(self, chunks: list[Chunk]) -> None:
        self.base.fit(chunks)

    def retrieve(self, query: str, contract_id: str, k: int) -> list[Scored]:
        cands = self.base.retrieve(query, contract_id, self.candidate_k)
        if not cands:
            return []
        scores = self.reranker.score(query, [s.chunk.text for s in cands])
        order = sorted(range(len(cands)), key=lambda i: (-scores[i], i))
        return [Scored(cands[i].chunk, scores[i]) for i in order[:k]]


# ---------------------------------------------------------------- parent

class ParentRetriever:
    """Match on small child chunks, return their parent windows, deduplicated.

    Trade-off this makes visible: higher evidence coverage per returned item,
    paid for in context tokens. The eval reports both.
    """

    def __init__(self, base: Retriever, parents: dict[str, Chunk] | None = None, child_k: int = 20):
        self.base, self.child_k = base, child_k
        self.parents = parents or {}
        self.name = f"parent({base.name})"

    def set_parents(self, parents: dict[str, Chunk]) -> None:
        self.parents = parents

    def fit(self, chunks: list[Chunk]) -> None:
        self.base.fit(chunks)

    def retrieve(self, query: str, contract_id: str, k: int) -> list[Scored]:
        out: list[Scored] = []
        seen: set[str] = set()
        for s in self.base.retrieve(query, contract_id, self.child_k):
            pid = s.chunk.parent_id
            if pid is None or pid not in self.parents:
                raise ValueError("ParentRetriever needs chunks built with parent_size and set_parents()")
            if pid in seen:
                continue
            seen.add(pid)
            out.append(Scored(self.parents[pid], s.score))
            if len(out) == k:
                break
        return out
