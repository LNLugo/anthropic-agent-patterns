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
import numpy as np


class VectorStore(Protocol):
    def upsert(self, rows: Iterable[tuple[str, list[float], dict]]) -> None: ...
    def query(self, vector: list[float], top_k: int, readers: Iterable[str]) -> list[tuple[str, float]]: ...


class GraphStore(Protocol):
    def save(self, graph: nx.Graph) -> None: ...
    def load(self) -> nx.Graph: ...


# ---------------------------------------------------------------- in memory

class InMemoryVectorStore:
    def __init__(self):
        self.keys: list[str] = []
        self.meta: list[dict] = []
        self._rows: list[np.ndarray] = []
        self._matrix: np.ndarray | None = None
        self.queries = 0

    def upsert(self, rows):
        index = {k: i for i, k in enumerate(self.keys)}
        for key, vector, meta in rows:
            v = np.asarray(vector, dtype=np.float32)
            v = v / (np.linalg.norm(v) or 1.0)
            if key in index:
                self._rows[index[key]], self.meta[index[key]] = v, dict(meta)
            else:
                index[key] = len(self.keys)
                self.keys.append(key)
                self.meta.append(dict(meta))
                self._rows.append(v)
        self._matrix = None

    def query(self, vector, top_k, readers):
        self.queries += 1
        allowed = set(readers)
        mask = np.array([bool(allowed & set(m["readers"])) for m in self.meta])   # filtered before ranking
        if self._matrix is None:
            self._matrix = np.vstack(self._rows) if self._rows else np.zeros((0, 1), dtype=np.float32)
        q = np.asarray(vector, dtype=np.float32)
        scores = self._matrix @ (q / (np.linalg.norm(q) or 1.0))
        order = sorted((i for i in range(len(self.keys)) if mask[i]), key=lambda i: (-scores[i], self.keys[i]))
        return [(self.keys[i], float(scores[i])) for i in order[:top_k]]


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

    def check_dimension(self, embedder) -> None:
        """Fail before indexing when the index was created for another model's vectors."""
        want, have = len(embedder.embed_query("dimension check")), self.dimension()
        if want != have:
            raise ValueError(f"the Upstash index has {have} dimensions; {getattr(embedder, 'model', 'the model')} "
                             f"makes {want}. Create the index with {want} dimensions and cosine similarity.")

    def reset(self):
        self.index.reset(namespace=self.namespace)

    def upsert(self, rows, batch: int = 100):
        rows = list(rows)
        for i in range(0, len(rows), batch):
            self.index.upsert([{"id": k, "vector": list(v), "metadata": m} for k, v, m in rows[i:i + batch]],
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
