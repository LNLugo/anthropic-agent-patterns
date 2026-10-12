"""Permission-aware retrieval over a wiki with Confluence-style access rules.

The control question here is narrower than the rest of 07_rag: can a user ever
be shown text they are not allowed to open? The answer has to be "no" by
construction, and it has to be proven by a test, not by trusting the model.

Access model (the parts of Confluence Data Center that matter for leakage):
  space permission   a user must be able to view the space
  page restriction   a page may restrict viewing to listed groups and users
  inheritance        a page is visible only if the user also passes the
                     restrictions on every ancestor page

Each condition becomes a "gate": a set of principals ("g:<group>", "u:<user>").
A user passes a gate if they hold at least one principal in it, and may see a
page only if they pass every gate. Gates are computed once, at ingestion, and
stored with each chunk, the same way a vector store stores a payload.

Filtering happens before ranking. Restricted chunks are removed from the
candidate set before BM25 picks the top k and before the reranker sees any
text, so restricted content never reaches the reranker or the model, and an
allowed user does not lose recall to restricted pages crowding the top k.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .chunking import Chunk, fixed_size
from .retrievers import Reranker, Scored, tokenize

Gate = frozenset[str]


# ---------------------------------------------------------------- model

@dataclass(frozen=True)
class Page:
    id: str
    space: str
    title: str
    text: str
    parent_id: str | None = None
    owner: str = ""
    last_modified: str = ""
    restrict_groups: tuple[str, ...] = ()
    restrict_users: tuple[str, ...] = ()

    @property
    def restricted(self) -> bool:
        return bool(self.restrict_groups or self.restrict_users)


@dataclass(frozen=True)
class User:
    id: str
    groups: frozenset[str] = field(default_factory=frozenset)

    @property
    def principals(self) -> frozenset[str]:
        return frozenset({f"u:{self.id}"} | {f"g:{g}" for g in self.groups})


@dataclass(frozen=True)
class Wiki:
    pages: dict[str, Page]
    spaces: dict[str, tuple[str, ...]]  # space key -> groups with view permission


def load_wiki(path: str | Path) -> tuple[Wiki, dict[str, User]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    spaces = {k: tuple(v["view_groups"]) for k, v in raw["spaces"].items()}
    pages = {}
    for p in raw["pages"]:
        r = p.get("restrictions") or {}
        pages[p["id"]] = Page(
            id=p["id"], space=p["space"], title=p["title"], text=p["text"],
            parent_id=p.get("parent_id"), owner=p.get("owner", ""),
            last_modified=p.get("last_modified", ""),
            restrict_groups=tuple(r.get("groups", ())), restrict_users=tuple(r.get("users", ())),
        )
    users = {u["id"]: User(u["id"], frozenset(u.get("groups", ()))) for u in raw["users"]}
    return Wiki(pages, spaces), users


# ---------------------------------------------------------------- effective permissions

def effective_gates(page_id: str, wiki: Wiki) -> tuple[Gate, ...]:
    """Every gate a user must pass to view this page: space, page, all ancestors.

    Fails closed: an unknown space, a missing ancestor or a cycle raises, so a
    broken hierarchy can never produce a page with fewer gates than it should.
    """
    page = wiki.pages[page_id]
    if page.space not in wiki.spaces:
        raise KeyError(f"page {page_id} is in unknown space {page.space!r}")
    gates: list[Gate] = [frozenset(f"g:{g}" for g in wiki.spaces[page.space])]
    seen: set[str] = set()
    node: Page | None = page
    while node is not None:
        if node.id in seen:
            raise ValueError(f"ancestor cycle at page {node.id}")
        seen.add(node.id)
        if node.restricted:
            gates.append(frozenset(
                {f"g:{g}" for g in node.restrict_groups} | {f"u:{u}" for u in node.restrict_users}
            ))
        if node.parent_id is None:
            break
        if node.parent_id not in wiki.pages:
            raise KeyError(f"page {node.id} has missing ancestor {node.parent_id}")
        node = wiki.pages[node.parent_id]
    return tuple(gates)


def can_view(user: User, gates: tuple[Gate, ...]) -> bool:
    principals = user.principals
    return all(principals & gate for gate in gates)


# ---------------------------------------------------------------- index

@dataclass
class AclIndex:
    """Chunks plus their gates: what a vector store holds as text plus payload."""

    chunks: list[Chunk]
    gates: dict[str, tuple[Gate, ...]]  # chunk id -> gates
    page_of: dict[str, str]             # chunk id -> page id

    def restamp(self, wiki: Wiki) -> list[str]:
        """Reconciliation: recompute every chunk's gates from the source of truth.

        Drops chunks whose page no longer exists. Returns the ids of chunks
        whose gates changed or that were removed, which is what a nightly
        reconciliation crawl would report.
        """
        changed: list[str] = []
        keep: list[Chunk] = []
        for c in self.chunks:
            pid = self.page_of[c.id]
            if pid not in wiki.pages:
                changed.append(c.id)
                del self.gates[c.id]
                continue
            new = effective_gates(pid, wiki)
            if new != self.gates[c.id]:
                self.gates[c.id] = new
                changed.append(c.id)
            keep.append(c)
        self.chunks = keep
        self.page_of = {c.id: self.page_of[c.id] for c in keep}
        return changed


def build_index(wiki: Wiki, size: int = 1000, overlap: int = 200) -> AclIndex:
    chunks: list[Chunk] = []
    gates: dict[str, tuple[Gate, ...]] = {}
    page_of: dict[str, str] = {}
    for page in wiki.pages.values():
        page_gates = effective_gates(page.id, wiki)
        # Title travels with the body so short pages remain findable by name.
        for c in fixed_size(f"{page.title}\n\n{page.text}", page.id, size, overlap):
            chunks.append(c)
            gates[c.id] = page_gates
            page_of[c.id] = page.id
    return AclIndex(chunks, gates, page_of)


# ---------------------------------------------------------------- retrieval

class PermissionFilteredBM25:
    """BM25 over the whole wiki, with a pre-filter on the caller's permissions."""

    name = "bm25+acl"

    def __init__(self, index: AclIndex, k1: float = 1.5, b: float = 0.75, enforce: bool = True):
        from rank_bm25 import BM25Okapi

        self.index, self.enforce = index, enforce
        self._chunks = list(index.chunks)
        self._bm25 = BM25Okapi([tokenize(c.text) for c in self._chunks], k1=k1, b=b)

    def retrieve(self, query: str, user: User, k: int) -> list[Scored]:
        scores = np.asarray(self._bm25.get_scores(tokenize(query)), dtype=float)
        allowed = np.array([
            # A chunk missing from the gate map is treated as forbidden (fail closed).
            c.id in self.index.gates and can_view(user, self.index.gates[c.id])
            for c in self._chunks
        ])
        if self.enforce:
            scores = np.where(allowed, scores, -np.inf)
        order = [i for i in np.argsort(-scores, kind="stable") if np.isfinite(scores[i])][:k]
        return [Scored(self._chunks[i], float(scores[i])) for i in order]


class PermissionFilteredRerank:
    """Rerank stage over an already filtered candidate pool."""

    def __init__(self, base: PermissionFilteredBM25, reranker: Reranker, candidate_k: int = 20):
        self.base, self.reranker, self.candidate_k = base, reranker, candidate_k
        self.name = f"rerank[{reranker.name}]({base.name},top{candidate_k})"

    def retrieve(self, query: str, user: User, k: int) -> list[Scored]:
        cands = self.base.retrieve(query, user, self.candidate_k)
        if not cands:
            return []
        scores = self.reranker.score(query, [s.chunk.text for s in cands])
        order = sorted(range(len(cands)), key=lambda i: (-scores[i], i))
        return [Scored(cands[i].chunk, scores[i]) for i in order[:k]]


# ---------------------------------------------------------------- leakage eval

@dataclass(frozen=True)
class LeakResult:
    queries: int
    chunks_returned: int
    restricted_returned: int
    leaked: tuple[tuple[str, str, str], ...]  # (user id, query, chunk id)


def measure_leakage(retriever, index: AclIndex, users: list[User], queries: list[str], k: int = 5) -> LeakResult:
    """Every user asks every query; count returned chunks that user cannot view.

    Runs at the retrieval layer, with no model in the loop, so it is
    deterministic and cheap enough to run on every change.
    """
    returned = 0
    leaked: list[tuple[str, str, str]] = []
    for user in users:
        for q in queries:
            for s in retriever.retrieve(q, user, k):
                returned += 1
                gates = index.gates.get(s.chunk.id)
                if gates is None or not can_view(user, gates):
                    leaked.append((user.id, q, s.chunk.id))
    return LeakResult(len(users) * len(queries), returned, len(leaked), tuple(leaked))
