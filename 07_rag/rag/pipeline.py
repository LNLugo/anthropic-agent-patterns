"""Index construction shared by the eval runners."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .chunking import Chunk, chunk_contract
from .config import build_retriever
from .data import Contract
from .retrievers import HybridRetriever, ParentRetriever, RerankRetriever, Retriever


@dataclass
class Index:
    retriever: Retriever
    chunks: list[Chunk]
    parents: dict[str, Chunk]
    build_seconds: float


def _wire_parents(r: Retriever, parents: dict[str, Chunk]) -> None:
    if isinstance(r, ParentRetriever):
        r.set_parents(parents)
        _wire_parents(r.base, parents)
    elif isinstance(r, RerankRetriever):
        _wire_parents(r.base, parents)
    elif isinstance(r, HybridRetriever):
        for m in r.members:
            _wire_parents(m, parents)


def build_index(contracts: list[Contract], cfg: dict) -> Index:
    ch = cfg["chunking"]
    chunks: list[Chunk] = []
    parents: dict[str, Chunk] = {}
    for c in contracts:
        kids, pars = chunk_contract(c.text, c.id, ch["size"], ch["overlap"], ch.get("parent_size"))
        chunks.extend(kids)
        parents.update(pars)
    retriever = build_retriever(cfg["retriever"])
    _wire_parents(retriever, parents)
    t0 = time.perf_counter()
    retriever.fit(chunks)
    return Index(retriever, chunks, parents, time.perf_counter() - t0)
