"""Permission leakage tests for rag/acl.py.

The headline assertion is test_zero_leakage_with_filter: every persona asks
every probe query, and not one returned chunk is from a page that persona is
not allowed to open. test_unfiltered_baseline_leaks is its positive control:
the same harness, with the filter switched off, must catch leaks. Without the
control, a zero could mean the test is blind rather than the system is safe.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from rag.acl import (
    AclIndex,
    PermissionFilteredBM25,
    PermissionFilteredRerank,
    Wiki,
    build_index,
    can_view,
    effective_gates,
    load_wiki,
    measure_leakage,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mini_confluence.json"


@pytest.fixture
def wiki_users():
    return load_wiki(FIXTURE)


@pytest.fixture
def wiki(wiki_users):
    return wiki_users[0]


@pytest.fixture
def users(wiki_users):
    return wiki_users[1]


@pytest.fixture
def probes():
    import json

    return json.loads(FIXTURE.read_text(encoding="utf-8"))["probe_queries"]


@pytest.fixture
def index(wiki):
    return build_index(wiki)


def pages_returned(retriever, index: AclIndex, query: str, user, k: int = 5) -> list[str]:
    return [index.page_of[s.chunk.id] for s in retriever.retrieve(query, user, k)]


# ---------------------------------------------------------------- access model

# Who may open which page, written out by hand from the fixture's rules.
EXPECTED = {
    "new_hire":       {"ENG-1", "ENG-2"},
    "billing_oncall": {"ENG-1", "ENG-2", "ENG-3", "ENG-4"},
    "sec_engineer":   {"ENG-1", "ENG-2", "ENG-5", "SEC-1"},
    "alice":          {"ENG-1", "ENG-2", "ENG-5"},
    "hr_partner":     {"HR-2"},
    "comp_analyst":   {"HR-1", "HR-2"},
    "cfo":            {"HR-1", "HR-2"},
    "contractor":     set(),
}


@pytest.mark.parametrize("user_id", sorted(EXPECTED))
def test_visibility_matches_hand_written_matrix(wiki, users, user_id):
    visible = {pid for pid in wiki.pages if can_view(users[user_id], effective_gates(pid, wiki))}
    assert visible == EXPECTED[user_id]


def test_child_inherits_parent_restriction(wiki):
    # ENG-4 has no restriction of its own, but its parent ENG-3 does.
    assert not wiki.pages["ENG-4"].restricted
    assert effective_gates("ENG-4", wiki) == effective_gates("ENG-3", wiki)
    assert len(effective_gates("ENG-4", wiki)) == 2  # space gate + inherited page gate


def test_user_named_in_restriction_still_needs_space_permission(wiki, users):
    # cfo is named on HR-1, and also holds hr-team, so passes both gates.
    assert can_view(users["cfo"], effective_gates("HR-1", wiki))
    # Being named on a page is not enough without the space gate.
    no_space = replace(users["cfo"], groups=frozenset())
    assert not can_view(no_space, effective_gates("HR-1", wiki))


# ---------------------------------------------------------------- leakage

def test_zero_leakage_with_filter(index, users, probes):
    result = measure_leakage(PermissionFilteredBM25(index), index, list(users.values()), probes, k=5)
    assert result.queries == len(users) * len(probes)
    assert result.chunks_returned > 0
    assert result.restricted_returned == 0, result.leaked


def test_unfiltered_baseline_leaks(index, users, probes):
    """Positive control: the harness must detect leaks when the filter is off."""
    result = measure_leakage(PermissionFilteredBM25(index, enforce=False), index, list(users.values()), probes, k=5)
    assert result.restricted_returned > 0


def test_restricted_title_query_returns_nothing_restricted(index, users):
    r = PermissionFilteredBM25(index)
    got = pages_returned(r, index, "Billing API Credential Rotation", users["new_hire"])
    assert got, "the user should still get their allowed pages"
    assert not {"ENG-3", "ENG-4"} & set(got)


def test_user_with_no_groups_gets_nothing(index, users):
    r = PermissionFilteredBM25(index)
    assert r.retrieve("how do I get VPN access", users["contractor"], 5) == []


# ---------------------------------------------------------------- no over-blocking

@pytest.mark.parametrize(
    "user_id, query, page",
    [
        ("billing_oncall", "how do I rotate the billing API credentials", "ENG-3"),
        ("billing_oncall", "emergency revocation break glass", "ENG-4"),
        ("alice", "legacy TLS security exception", "ENG-5"),
        ("sec_engineer", "credential exposure postmortem", "SEC-1"),
        ("cfo", "engineering compensation bands", "HR-1"),
        ("new_hire", "how do I get VPN access", "ENG-1"),
    ],
)
def test_allowed_user_finds_the_page(index, users, user_id, query, page):
    r = PermissionFilteredBM25(index)
    assert pages_returned(r, index, query, users[user_id], k=1) == [page]


# ---------------------------------------------------------------- rerank stage

class SpyReranker:
    """Records every text it is shown, and scores by word overlap."""

    name = "spy"

    def __init__(self):
        self.seen: list[str] = []

    def score(self, query, texts):
        self.seen.extend(texts)
        q = set(query.lower().split())
        return [float(len(q & set(t.lower().split()))) for t in texts]


def test_reranker_never_sees_restricted_text(index, users, probes):
    spy = SpyReranker()
    r = PermissionFilteredRerank(PermissionFilteredBM25(index), spy, candidate_k=20)
    user = users["new_hire"]
    for q in probes:
        r.retrieve(q, user, 3)
    allowed_text = {c.text for c in index.chunks if can_view(user, index.gates[c.id])}
    assert spy.seen, "the reranker should have been called"
    assert set(spy.seen) <= allowed_text


def test_rerank_pipeline_zero_leakage(index, users, probes):
    r = PermissionFilteredRerank(PermissionFilteredBM25(index), SpyReranker(), candidate_k=20)
    result = measure_leakage(r, index, list(users.values()), probes, k=3)
    assert result.restricted_returned == 0, result.leaked


# ---------------------------------------------------------------- reconciliation

def _with_page(wiki: Wiki, page) -> Wiki:
    pages = dict(wiki.pages)
    pages[page.id] = page
    return Wiki(pages, wiki.spaces)


def test_stale_acl_is_a_leak_until_restamped(wiki, index, users):
    """A page gains a restriction at the source. Until reconciliation runs, the
    index still carries the old gates, and the leakage check catches it."""
    locked = _with_page(wiki, replace(wiki.pages["ENG-2"], restrict_groups=("release-eng",)))
    user = users["new_hire"]
    query = ["deploy pipeline approval before production"]

    def leaks():
        # Judge against the source of truth, not the index's own (possibly stale) gates.
        truth = AclIndex(index.chunks, {c.id: effective_gates(index.page_of[c.id], locked) for c in index.chunks}, index.page_of)
        return measure_leakage(PermissionFilteredBM25(index), truth, [user], query, k=5).restricted_returned

    assert leaks() > 0  # stale gates: new_hire still gets ENG-2

    changed = index.restamp(locked)
    assert changed and all(index.page_of.get(cid, "ENG-2") == "ENG-2" for cid in changed)
    assert leaks() == 0


def test_restamp_drops_deleted_pages(wiki, index, users):
    pages = {k: v for k, v in wiki.pages.items() if k != "ENG-1"}
    removed = index.restamp(Wiki(pages, wiki.spaces))
    assert removed and all(cid.startswith("ENG-1:") for cid in removed)
    assert "ENG-1" not in pages_returned(PermissionFilteredBM25(index), index, "how do I get VPN access", users["new_hire"])


# ---------------------------------------------------------------- fail closed

def test_missing_ancestor_fails_closed(wiki):
    broken = _with_page(wiki, replace(wiki.pages["ENG-4"], parent_id="ENG-404"))
    with pytest.raises(KeyError):
        effective_gates("ENG-4", broken)


def test_unknown_space_fails_closed(wiki):
    broken = _with_page(wiki, replace(wiki.pages["ENG-1"], space="NOPE"))
    with pytest.raises(KeyError):
        effective_gates("ENG-1", broken)


def test_chunk_without_gates_is_never_returned(index, users):
    victim = next(c for c in index.chunks if index.page_of[c.id] == "ENG-1")
    del index.gates[victim.id]
    got = PermissionFilteredBM25(index).retrieve("how do I get VPN access", users["new_hire"], 10)
    assert victim.id not in {s.chunk.id for s in got}
