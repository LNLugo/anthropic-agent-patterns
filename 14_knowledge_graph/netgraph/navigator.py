"""Everything the app and the live run need, assembled once.

    nav = Navigator.offline()          # hashing stand-in, in memory: no key, no network
    nav = Navigator.live()             # OpenAI embeddings, Upstash Vector and Redis
    nav.ask("Why can't alice.smith reach the database server?", "netops_analyst", method="graph")
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from .answer import Answer, answer
from .corpus import USERS, Item, load
from .graph import build, visible
from .retrieve import Hit, Retriever
from .stores import InMemoryGraphStore, InMemoryVectorStore


@dataclass
class Result:
    hits: list[Hit]
    answer: Answer | None
    subgraph: nx.Graph


class Navigator:
    def __init__(self, items: list[Item], retriever: Retriever, graph: nx.Graph, llm=None):
        self.items = {i.key: i for i in items}
        self.retriever, self.graph, self.llm = retriever, graph, llm

    @classmethod
    def offline(cls, llm=None) -> "Navigator":
        from llm_provider import HashingEmbeddings

        items = load()
        store = InMemoryGraphStore()
        store.save(build(items))
        graph = store.load()
        r = Retriever(items, InMemoryVectorStore(), HashingEmbeddings(), graph)
        r.index()
        return cls(items, r, graph, llm)

    @classmethod
    def live(cls, reindex: bool = False) -> "Navigator":
        from llm_provider import get_chat_model, get_embeddings

        from .stores import UpstashGraphStore, UpstashVectorStore

        items = load()
        vstore, gstore = UpstashVectorStore(), UpstashGraphStore()
        embedder = get_embeddings("openai")
        vstore.check_dimension(embedder)
        try:
            graph = gstore.load()
        except LookupError:                    # first run against empty stores: build them
            reindex = True
        if reindex:
            gstore.save(build(items))
            graph = gstore.load()
        r = Retriever(items, vstore, embedder, graph)
        if reindex:
            vstore.reset()
            r.index()
        return cls(items, r, graph, get_chat_model())

    def ask(self, question: str, as_user: str, method: str = "graph", k: int = 10) -> Result:
        groups = USERS[as_user]
        if method == "graph":
            hits = self.retriever.graph_expanded(question, groups, k)
        elif method == "vector":
            hits = self.retriever.vector_only(question, groups, k)
        else:
            raise ValueError("method: graph or vector")
        ans = answer(question, hits, self.items, self.llm) if self.llm is not None else None
        return Result(hits, ans, self.neighbourhood([h.key for h in hits], groups))

    def neighbourhood(self, keys: list[str], groups) -> nx.Graph:
        """The returned items and the entities joining them, as the asker may see them."""
        vg = visible(self.graph, groups)
        keep = set(keys)
        for k in keys:
            if k in vg:
                keep |= {e for e in vg.neighbors(k) if sum(1 for n in vg.neighbors(e) if n in keys) >= 2}
        return vg.subgraph(keep)
