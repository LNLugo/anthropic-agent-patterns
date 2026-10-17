"""Where the vectors and the graph live: Upstash in a live run, memory in tests.

    VectorStore   upsert(rows); query(vector, top_k, readers) -> [(key, score)]
                  readers: the asker's groups. The store filters by them inside the query,
                  before ranking, so a row the asker may not read never competes for the top k
                  (07's rule). Upstash Vector applies it as a metadata filter on the query.
    GraphStore    save(graph); load() -> networkx graph. Upstash Redis keeps each node's
                  attributes as a hash and its neighbours as a set, so a neighbourhood can be
                  read without loading the whole graph; analysis loads it once into memory.

The Upstash classes read UPSTASH_VECTOR_REST_URL / _TOKEN and UPSTASH_REDIS_REST_URL / _TOKEN.
Nothing in the tests reaches them.
"""

from __future__ import annotations

import json
import math
import os
from typing import Iterable, Protocol

import networkx as nx


class VectorStore(Protocol):
    def upsert(self, rows: Iterable[tuple[str, list[float], dict]]) -> None: ...
    def query(self, vector: list[float], top_k: int, readers: Iterable[str]) -> list[tuple[str, float]]: ...


class GraphStore(Protocol):
    def save(self, graph: nx.Graph) -> None: ...
    def load(self) -> nx.Graph: ...


# ---------------------------------------------------------------- in memory

class InMemoryVectorStore:
    def __init__(self):
        self.rows: dict[str, tuple[list[float], dict]] = {}
        self.queries = 0

    def upsert(self, rows):
        for key, vector, meta in rows:
            self.rows[key] = (list(vector), dict(meta))

    def query(self, vector, top_k, readers):
        self.queries += 1
        allowed = set(readers)
        scored = []
        for key, (v, meta) in self.rows.items():
            if not allowed & set(meta["readers"]):        # filtered before ranking
                continue
            scored.append((key, sum(a * b for a, b in zip(vector, v)) / (_norm(vector) * _norm(v) or 1.0)))
        scored.sort(key=lambda r: (-r[1], r[0]))
        return scored[:top_k]


def _norm(v) -> float:
    return math.sqrt(sum(x * x for x in v))


class InMemoryGraphStore:
    def __init__(self):
        self.data: str | None = None

    def save(self, graph):
        self.data = json.dumps(nx.node_link_data(graph, edges="edges"))

    def load(self):
        if self.data is None:
            raise LookupError("no graph saved")
        return nx.node_link_graph(json.loads(self.data), edges="edges")


# ---------------------------------------------------------------- Upstash

def readers_filter(readers: Iterable[str]) -> str:
    """Upstash Vector's metadata filter for "readable by any of these groups". Group names are
    checked against a fixed pattern first: the filter is a query language, and a name is data."""
    groups = sorted(set(readers))
    for g in groups:
        if not g.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"not a group name: {g!r}")
    return " OR ".join(f"readers CONTAINS '{g}'" for g in groups) or "readers CONTAINS '__nobody__'"


class UpstashVectorStore:
    def __init__(self, index=None, namespace: str = "netgraph"):
        if index is None:
            from upstash_vector import Index

            index = Index(url=os.environ["UPSTASH_VECTOR_REST_URL"], token=os.environ["UPSTASH_VECTOR_REST_TOKEN"])
        self.index, self.namespace = index, namespace

    def dimension(self) -> int:
        return self.index.info().dimension

    def reset(self):
        self.index.reset(namespace=self.namespace)

    def upsert(self, rows, batch: int = 100):
        from upstash_vector import Vector

        rows = list(rows)
        for i in range(0, len(rows), batch):
            self.index.upsert([Vector(id=k, vector=v, metadata=m) for k, v, m in rows[i:i + batch]],
                              namespace=self.namespace)

    def query(self, vector, top_k, readers):
        hits = self.index.query(vector=vector, top_k=top_k, filter=readers_filter(readers),
                                namespace=self.namespace)
        return [(h.id, float(h.score)) for h in hits]


class UpstashGraphStore:
    """ng:nodes (set of ids), ng:n:<id> (hash of attributes), ng:adj:<id> (set of neighbours)."""

    def __init__(self, redis=None, prefix: str = "ng", batch: int = 400):
        if redis is None:
            from upstash_redis import Redis

            redis = Redis(url=os.environ["UPSTASH_REDIS_REST_URL"], token=os.environ["UPSTASH_REDIS_REST_TOKEN"])
        self.r, self.p, self.batch = redis, prefix, batch

    def _run(self, commands):
        for i in range(0, len(commands), self.batch):
            pipe = self.r.pipeline()
            for name, args, kw in commands[i:i + self.batch]:
                getattr(pipe, name)(*args, **kw)
            yield from pipe.exec()

    def save(self, graph):
        old = self.r.smembers(f"{self.p}:nodes") or []
        stale = [f"{self.p}:{t}:{n}" for n in old for t in ("n", "adj")]
        for i in range(0, len(stale), 500):
            self.r.delete(*stale[i:i + 500])
        self.r.delete(f"{self.p}:nodes")
        cmds = []
        for n, attrs in graph.nodes(data=True):
            cmds.append(("sadd", (f"{self.p}:nodes", n), {}))
            cmds.append(("hset", (f"{self.p}:n:{n}",), {"values": {k: json.dumps(v) for k, v in attrs.items()}
                                                          or {"_": "1"}}))
            nbrs = list(graph.neighbors(n))
            if nbrs:
                cmds.append(("sadd", (f"{self.p}:adj:{n}", *nbrs), {}))
        list(self._run(cmds))

    def neighbours(self, node: str) -> set[str]:
        return set(self.r.smembers(f"{self.p}:adj:{node}") or [])

    def load(self):
        nodes = sorted(self.r.smembers(f"{self.p}:nodes") or [])
        if not nodes:
            raise LookupError("no graph saved")
        attrs = list(self._run([("hgetall", (f"{self.p}:n:{n}",), {}) for n in nodes]))
        adj = list(self._run([("smembers", (f"{self.p}:adj:{n}",), {}) for n in nodes]))
        g = nx.Graph()
        for n, a in zip(nodes, attrs):
            pairs = dict(zip(a[::2], a[1::2])) if isinstance(a, list) else dict(a or {})
            g.add_node(n, **{k: json.loads(v) for k, v in pairs.items() if k != "_"})
        for n, nb in zip(nodes, adj):
            g.add_edges_from((n, m) for m in nb or [])
        return g
