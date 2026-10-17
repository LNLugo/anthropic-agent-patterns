"""Two retrievers over the same items, same store, same k.

    vector_only      the article's pipeline: embed the question, return the k nearest items
    graph_expanded   start from the nearest few (the seeds), then walk the graph from them
                     with personalised PageRank, over what the asker may read, and return
                     the k items the walk reaches most

Both filter by the asker's groups before anything is ranked: the vector store filters
inside the query, and the walk runs on the visible graph only.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import networkx as nx

from .corpus import Item
from .graph import visible


@dataclass(frozen=True)
class Hit:
    key: str
    score: float
    via: str          # "vector" (a seed or a vector result) or "graph" (reached by the walk)


def unit(key: str) -> str:
    """What a result counts as: a log line, or a whole page (any of its sections)."""
    return key.split("#", 1)[0]


class Retriever:
    def __init__(self, items: list[Item], store, embedder, graph: nx.Graph, tracer=None):
        self.items = {i.key: i for i in items}
        self.store, self.embedder, self.graph = store, embedder, graph
        self.tracer = tracer
        self._type_counts: Counter | None = None

    def rarity(self, key: str) -> float:
        """How unusual an item's type is in the corpus, from 0 (every item) to 1 (one item).
        Pages count as rare: there are few, and each is written about one thing."""
        if self._type_counts is None:
            self._type_counts = Counter(self.graph.nodes[n].get("type") for n, d in self.graph.nodes(data=True)
                                        if d["kind"] == "event")
            self._events = sum(self._type_counts.values())
        node = self.graph.nodes[key]
        if node["kind"] == "doc":
            return 1.0
        return math.log(self._events / self._type_counts[node["type"]]) / math.log(self._events)

    def rerank_by_rarity(self, hits: list[Hit], k: int) -> list[Hit]:
        """The control for the rarity prior: the same prior applied to vector results, with no graph."""
        scored = sorted(((h, h.score * self.rarity(h.key)) for h in hits), key=lambda r: (-r[1], r[0].key))
        return [Hit(h.key, s, "vector") for h, s in scored[:k]]

    def index(self, batch: int = 256) -> int:
        items = list(self.items.values())
        for i in range(0, len(items), batch):
            chunk = items[i:i + batch]
            vectors = self.embedder.embed_documents([it.text for it in chunk])
            self.store.upsert((it.key, v, {"readers": sorted(it.readers), "kind": it.kind})
                              for it, v in zip(chunk, vectors))
        return len(items)

    def _span(self, name: str, **data):
        from tracing import get_tracer

        return (self.tracer or get_tracer()).span("tool", name, **data)

    def vector_only(self, question: str, groups, k: int = 10) -> list[Hit]:
        with self._span("vector_search", k=k) as span:
            hits = self.store.query(self.embedder.embed_query(question), k, groups)
            span.set(returned=len(hits))
            span.content(input=question, output=[h for h, _ in hits])
        return [Hit(key, score, "vector") for key, score in hits]

    def graph_expanded(self, question: str, groups, k: int = 10, seeds: int = 5, hops: int = 4,
                       alpha: float = 0.5, idf: bool = False, rarity: bool = True, pool: int = 50) -> list[Hit]:
        """alpha is the walk's chance of continuing at each step (1 - alpha jumps back to a seed),
        so most of the score stays within a few hops of what the question matched.
        idf: an edge through a common entity (an office address on hundreds of logins) carries
        less than one through a rare one (an address seen five times). rarity: an item of a rare
        type (a privilege escalation) outranks one of a routine type (a successful login) the walk
        reaches equally. Both are switchable, so each is measured on its own. idf is off by
        default: on the development questions it changed nothing (33 of 43 with or without it)."""
        # Seeds: the nearest items, reranked by the same prior the walk uses (pool -> seeds).
        start = self.vector_only(question, groups, pool if rarity else seeds)
        if rarity:
            start = self.rerank_by_rarity(start, seeds)
        with self._span("graph_walk", seeds=len(start), hops=hops) as span:
            vg = visible(self.graph, groups)
            seed_keys = [h.key for h in start if h.key in vg]
            if not seed_keys:
                span.set(returned=0)
                return []
            near = set()
            for s in seed_keys:
                near |= set(nx.single_source_shortest_path_length(vg, s, cutoff=hops))
            sub = nx.Graph(vg.subgraph(near))
            for a, b in sub.edges():
                e = a if self.graph.nodes[a]["kind"] == "entity" else b
                sub[a][b]["w"] = 1 / math.log(2 + self.graph.degree(e)) if idf else 1.0
            weights = {h.key: max(h.score, 1e-6) for h in start if h.key in sub}
            ranks = nx.pagerank(sub, alpha=alpha, personalization=weights, weight="w")
            prior = self.rarity if rarity else (lambda n: 1.0)
            items = sorted(((n, r * prior(n)) for n, r in ranks.items() if sub.nodes[n]["kind"] != "entity"),
                           key=lambda r: (-r[1], r[0]))
            out, seen_units = [], set()
            for n, r in items:
                if len(out) == k:
                    break
                if sub.nodes[n]["kind"] == "doc" and unit(n) in seen_units:
                    continue          # one section per page is enough to find the page
                seen_units.add(unit(n))
                out.append(Hit(n, r, "vector" if n in weights else "graph"))
            span.set(returned=len(out), reached=len(near), from_graph=sum(h.via == "graph" for h in out))
            span.content(input=question, output=[h.key for h in out])
        return out
