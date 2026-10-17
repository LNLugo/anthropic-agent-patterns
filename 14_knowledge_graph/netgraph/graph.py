"""The knowledge graph and what network analysis finds in it.

Nodes are the retrievable items (log lines and page sections) and the entities they
name (users, addresses, hosts, policies, changes). An edge joins an item to each entity
it names. Two log lines are therefore two hops apart when they share an address, which
is exactly the join an analyst makes by hand and a vector search does not.

Findings are computed, not asked of a model:
    fan_out           addresses that failed VPN logins for several different users
    bridges           entities seen in several sources (firewall, VPN, auth, syslog)
    central           entities ranked by PageRank over the entity graph
    communities       groups of entities that keep appearing together (Louvain, seeded)
    path              how two entities are connected, through what the asker may read
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

import networkx as nx

from .corpus import Item


def build(items: list[Item]) -> nx.Graph:
    g = nx.Graph()
    for it in items:
        g.add_node(it.key, kind=it.kind, readers=sorted(it.readers), label=it.text.splitlines()[0][:120],
                   source=it.attrs.get("source") or "doc", type=it.attrs.get("type") or it.attrs.get("doc"))
        for e in it.entities:
            if e not in g:
                g.add_node(e, kind="entity", etype=e.split(":", 1)[0])
            g.add_edge(it.key, e)
    return g


def visible(g: nx.Graph, groups) -> nx.Graph:
    """The graph as this asker may see it: items they cannot read are removed, with their edges,
    before anything is ranked or walked. Entities stay; an entity reached only through hidden
    items is left with no path to anything."""
    allowed = set(groups)
    keep = [n for n, d in g.nodes(data=True) if d["kind"] == "entity" or allowed & set(d["readers"])]
    return g.subgraph(keep)


def entity_graph(g: nx.Graph) -> nx.Graph:
    """Entities joined when an item names both, weighted by how many items do."""
    eg = nx.Graph()
    for n, d in g.nodes(data=True):
        if d["kind"] == "entity":
            eg.add_node(n)
    for n, d in g.nodes(data=True):
        if d["kind"] != "entity":
            for a, b in combinations(sorted(g.neighbors(n)), 2):
                w = eg.get_edge_data(a, b, {"weight": 0})["weight"]
                eg.add_edge(a, b, weight=w + 1)
    return eg


def fan_out(g: nx.Graph, min_users: int = 3) -> list[dict]:
    """Addresses that failed VPN logins for at least min_users different users."""
    users_by_ip: dict[str, set] = defaultdict(set)
    lines: dict[str, list] = defaultdict(list)
    for n, d in g.nodes(data=True):
        if d.get("type") == "VPN_LOGIN_FAILED":
            ents = list(g.neighbors(n))
            ips = [e for e in ents if e.startswith("ip:")]
            users = [e for e in ents if e.startswith("user:")]
            for ip in ips:
                users_by_ip[ip].update(users)
                lines[ip].append(n)
    out = [{"address": ip, "users": sorted(u.split(":", 1)[1] for u in us), "lines": sorted(lines[ip])}
           for ip, us in users_by_ip.items() if len(us) >= min_users]
    return sorted(out, key=lambda r: (-len(r["users"]), r["address"]))


def bridges(g: nx.Graph, min_sources: int = 2, limit: int = 10) -> list[dict]:
    """Entities named in more than one source, the joins between logs."""
    out = []
    for n, d in g.nodes(data=True):
        if d["kind"] == "entity":
            sources = sorted({g.nodes[m]["source"] for m in g.neighbors(n)} - {"doc"})
            if len(sources) >= min_sources:
                out.append({"entity": n, "sources": sources, "items": g.degree(n)})
    return sorted(out, key=lambda r: (-len(r["sources"]), r["items"], r["entity"]))[:limit]


def central(g: nx.Graph, limit: int = 10) -> list[tuple[str, float]]:
    ranks = nx.pagerank(entity_graph(g), weight="weight")
    return sorted(ranks.items(), key=lambda r: (-r[1], r[0]))[:limit]


def communities(g: nx.Graph, min_size: int = 3, max_items: int = 60, seed: int = 14) -> list[list[str]]:
    """Louvain communities of entities, leaving out hubs (entities on more than max_items items,
    such as a policy that fires hundreds of times) so they don't pull everything into one group."""
    eg = entity_graph(g)
    eg.remove_nodes_from([n for n in list(eg) if g.degree(n) > max_items])
    eg.remove_nodes_from(list(nx.isolates(eg)))
    groups = nx.community.louvain_communities(eg, weight="weight", seed=seed)
    return sorted((sorted(c) for c in groups if len(c) >= min_size), key=lambda c: (-len(c), c[0]))


def path(g: nx.Graph, a: str, b: str, groups) -> list[str] | None:
    """The shortest chain from entity a to entity b through items the asker may read."""
    vg = visible(g, groups)
    try:
        return nx.shortest_path(vg, a, b)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
