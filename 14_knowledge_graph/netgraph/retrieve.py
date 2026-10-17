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
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import networkx as nx

from .corpus import NOW, Item
from .graph import visible
from .timewin import window

OUTSIDE_WINDOW = 0.2          # an item outside the question's period keeps a fifth of its weight


@dataclass(frozen=True)
class Hit:
    key: str
    score: float
    via: str          # "vector" (a seed or a vector result) or "graph" (reached by the walk)


def unit(key: str) -> str:
    """What a result counts as: a log line, or a whole page (any of its sections)."""
    return key.split("#", 1)[0]


class Retriever:
    def __init__(self, items: list[Item], store, embedder, graph: nx.Graph, tracer=None, now: datetime = NOW):
        self.items = {i.key: i for i in items}
        self.store, self.embedder, self.graph = store, embedder, graph
        self.tracer = tracer
        self._type_counts: Counter | None = None
        self._entity_names: dict | None = None
        self._pair_counts: Counter | None = None
        self.now = now

    def rarity(self, key: str) -> float:
        """How unusual an item's type is in the corpus, from 0 (every item) to 1 (one item).
        Page sections count as one type, "page"."""
        if self._type_counts is None:
            self._type_counts = Counter(self._type(n) for n, d in self.graph.nodes(data=True)
                                        if d["kind"] != "entity")
            self._total = sum(self._type_counts.values())
        return math.log(self._total / self._type_counts[self._type(key)]) / math.log(self._total)

    def novelty(self, key: str) -> float:
        """How new an item's combination of actors is: 1 when a pair it names (this user from
        this address, this host to that address) appears on no other item, falling as the pair
        recurs. A per-entity baseline: a routine login is common for its type and old for its
        pair; the first login from a new address is common for its type but new for its pair."""
        if self._pair_counts is None:
            self._pair_counts = Counter(p for n, d in self.graph.nodes(data=True) if d["kind"] == "event"
                                        for p in self._pairs(n))
        pairs = self._pairs(key)
        return max((1 / self._pair_counts[p] for p in pairs), default=0.0)

    def _pairs(self, key: str) -> list[tuple[str, str]]:
        actors = sorted(e for e in self.graph.neighbors(key) if e.split(":", 1)[0] in ("user", "ip", "host"))
        return [(a, b) for i, a in enumerate(actors) for b in actors[i + 1:]]

    def prior(self, key: str, when: tuple | None = None, novelty: bool = True, time: bool = True) -> float:
        """What makes an item worth an analyst's attention before relevance: an unusual type or
        a new pair of actors, inside the period the question names."""
        node = self.graph.nodes[key]
        p = self.rarity(key)
        if novelty and node["kind"] == "event":
            p = max(p, self.novelty(key))
        if time and when and node["kind"] == "event":
            ts = self.items[key].attrs["ts"]
            if not (f"{when[0]:%Y-%m-%d %H:%M:%S}" <= ts <= f"{when[1]:%Y-%m-%d %H:%M:%S}"):
                p *= OUTSIDE_WINDOW
        return p

    def _type(self, key: str) -> str:
        node = self.graph.nodes[key]
        return "page" if node["kind"] == "doc" else node["type"]

    def linked_entities(self, question: str) -> list[str]:
        """Entities the question names, by exact match on a whole word: "john.doe's" names
        user:john.doe, "198.51.100.77" names ip:198.51.100.77. Generic words never match,
        because entity names here are identifiers, not words."""
        if self._entity_names is None:
            self._entity_names = {}
            for n, d in self.graph.nodes(data=True):
                if d["kind"] == "entity":
                    self._entity_names.setdefault(n.split(":", 1)[1].lower(), []).append(n)
        words = set(re.findall(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", question.lower()))
        return sorted(e for w in words for e in self._entity_names.get(w, ()))

    def rerank(self, hits: list[Hit], k: int, question: str = "", novelty: bool = True,
               time: bool = True) -> list[Hit]:
        """The control for the priors: the same priors applied to vector results, with no graph."""
        when = window(question, self.now) if time else None
        scored = sorted(((h, h.score * self.prior(h.key, when, novelty, time)) for h in hits),
                        key=lambda r: (-r[1], r[0].key))
        return [Hit(h.key, s, "vector") for h, s in scored[:k]]

    def rerank_by_rarity(self, hits: list[Hit], k: int) -> list[Hit]:
        """The first control: the type prior alone."""
        return self.rerank(hits, k, novelty=False, time=False)

    def vector_with_entities(self, question: str, groups, k: int = 10, pool: int = 50) -> list[Hit]:
        """The control for the walk: everything the graph retriever uses except the walk. The
        priors, plus every readable item that names a linked entity directly (one hop, no further)."""
        hits = {h.key: h for h in self.vector_only(question, groups, pool)}
        top = max((h.score for h in hits.values()), default=1.0)
        vg = visible(self.graph, groups)
        for e in self.linked_entities(question):
            for n in (vg.neighbors(e) if e in vg else ()):
                hits.setdefault(n, Hit(n, top, "vector"))
        return self.rerank(list(hits.values()), k, question)

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
                       alpha: float = 0.5, idf: bool = False, rarity: bool = True, pool: int = 50,
                       link: bool = True, novelty: bool = True, time: bool = True) -> list[Hit]:
        """alpha is the walk's chance of continuing at each step (1 - alpha jumps back to a seed),
        so most of the score stays within a few hops of what the question matched.
        idf: an edge through a common entity (an office address on hundreds of logins) carries
        less than one through a rare one (an address seen five times). rarity: an item of a rare
        type (a privilege escalation) outranks one of a routine type (a successful login) the walk
        reaches equally. Both are switchable, so each is measured on its own. idf is off by
        default: on the development questions it changed nothing (33 of 43 with or without it).
        link: entities the question names outright (an address, a user, a host, a change) are
        seeds too, matched exactly against the graph's entity names; no model is involved.
        novelty and time: the priors in prior(), each switchable. rarity=False turns all priors off."""
        # Seeds: the nearest items, reranked by the same prior the walk uses (pool -> seeds).
        start = self.vector_only(question, groups, pool if rarity else seeds)
        if rarity:
            start = self.rerank(start, seeds, question, novelty, time)
        with self._span("graph_walk", seeds=len(start), hops=hops) as span:
            vg = visible(self.graph, groups)
            linked = self.linked_entities(question) if link else []
            seed_keys = [h.key for h in start if h.key in vg] + [e for e in linked if e in vg]
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
            top = max(weights.values(), default=1.0)
            weights.update({e: top for e in linked if e in sub})       # a named entity counts as the best match
            ranks = nx.pagerank(sub, alpha=alpha, personalization=weights, weight="w")
            when = window(question, self.now) if (rarity and time) else None
            prior = (lambda n: self.prior(n, when, novelty, time)) if rarity else (lambda n: 1.0)
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
