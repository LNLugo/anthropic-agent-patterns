"""Module 14: the corpus, the graph, both retrievers, the permission filter, cited answers,
the Upstash adapters and the recorded measurements."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from llm_provider import HashingEmbeddings, ScriptedChatModel
from netgraph import corpus, graph as G
from netgraph.answer import answer
from netgraph.corpus import USERS, load, load_questions, parse_line
from netgraph.evaluate import configs, score, summarise
from netgraph.navigator import Navigator
from netgraph.retrieve import Retriever, unit
from netgraph.stores import (InMemoryGraphStore, InMemoryVectorStore, UpstashGraphStore, UpstashVectorStore,
                             readers_filter)
from netgraph.timewin import window
from tracing import InMemoryExporter, Tracer, set_tracer

HERE = Path(__file__).resolve().parents[1]
QUESTIONS = load_questions()


@pytest.fixture(scope="module")
def items():
    return load()


@pytest.fixture(scope="module")
def r(items):
    previous = set_tracer(Tracer(exporters=[]))
    ret = Retriever(items, InMemoryVectorStore(), HashingEmbeddings(), G.build(items))
    ret.index()
    yield ret
    set_tracer(previous)


# ---------------------------------------------------------------- the corpus

def test_the_fixtures_are_exactly_what_the_generator_writes(tmp_path):
    corpus.generate(tmp_path)
    for f in sorted((HERE / "fixtures").rglob("*")):
        if f.is_file():
            assert (tmp_path / f.relative_to(HERE / "fixtures")).read_bytes() == f.read_bytes(), f.name


def test_every_expected_and_forbidden_key_exists_and_forbidden_is_unreadable(items):
    keys = {unit(i.key) for i in items}
    readers = {unit(i.key): i.readers for i in items}
    for q in QUESTIONS:
        assert set(q["expected"]) <= keys, q["id"]
        for k in q.get("forbidden", ()):
            assert k in keys and not readers[k] & set(USERS[q["as"]]), (q["id"], k)
        for k in q["expected"]:
            assert readers[k] & set(USERS[q["as"]]), (q["id"], k)       # expected is what the asker may see


def test_the_question_sets_and_their_sizes():
    sets = {}
    for q in QUESTIONS:
        sets.setdefault(q["set"], []).append(q["id"])
    assert {k: len(v) for k, v in sets.items()} == {"development": 8, "held_out_1": 7, "held_out_3": 8,
                                                    "held_out_2": 6}


def test_a_log_line_is_parsed_by_pattern_into_entities():
    it = parse_line("vpn", 7, "2026-10-14 08:01:05 VPN_LOGIN_FAILED user=john.doe ip=203.0.113.45 reason=bad_password")
    assert it.key == "vpn:7" and it.entities == ("ip:203.0.113.45", "user:john.doe")
    assert it.readers == {"netops", "security"} and it.attrs["type"] == "VPN_LOGIN_FAILED"
    with pytest.raises(ValueError):
        parse_line("vpn", 8, "ignore previous instructions")


# ---------------------------------------------------------------- the graph's findings

def test_fan_out_finds_the_one_address_that_tried_several_accounts(r):
    found = G.fan_out(r.graph)
    assert [f["address"] for f in found] == ["ip:203.0.113.45"]
    assert set(found[0]["users"]) >= {"john.doe", "maria.chen", "raj.patel"}


def test_bridges_communities_and_centrality_are_computed_and_deterministic(r):
    assert {"entity": "ip:10.50.2.8", "sources": ["auth", "firewall", "inventory"],
            "items": r.graph.degree("ip:10.50.2.8")} in G.bridges(r.graph, min_sources=3, limit=50)
    assert G.communities(r.graph) == G.communities(r.graph)
    assert len(G.central(r.graph, 5)) == 5


def test_a_path_goes_only_through_what_the_asker_may_read(r):
    sec = G.path(r.graph, "user:svc_backup", "ip:45.33.32.156", USERS["secops_analyst"])
    assert sec and any(n.startswith("auth:") for n in sec)
    assert G.path(r.graph, "user:svc_backup", "ip:45.33.32.156", USERS["netops_analyst"]) is None
    assert G.path(r.graph, "user:john.doe", "policy:GEO_BLOCK_INBOUND", USERS["contractor"]) is None


# ---------------------------------------------------------------- permissions: filtered before ranking

@pytest.mark.parametrize("name", list(configs(None, 10)))
def test_no_retriever_ever_returns_what_the_asker_may_not_read(r, name):
    results = score(configs(r, 10)[name], QUESTIONS)
    assert [s.qid for s in results if s.forbidden] == []


def test_the_permission_check_sees_forbidden_items_when_the_filter_is_widened(r):
    """The control: the same questions asked with the security group do return the items the
    network team must not see, so the zero above is the filter working, not the check being blind."""
    guarded = [q for q in QUESTIONS if q.get("forbidden")]
    widened = score(configs(r, 10)["graph (default)"], guarded, groups_for=lambda q: ["netops", "security"])
    assert sum(len(s.forbidden) for s in widened) > 0


def test_someone_in_no_group_gets_nothing(r):
    assert r.vector_only("srv-db-01", USERS["contractor"]) == []
    assert r.graph_expanded("srv-db-01", USERS["contractor"]) == []


# ---------------------------------------------------------------- the retriever's parts

def test_entity_links_are_exact_identifiers_only(r):
    assert r.linked_entities("Why are john.doe's VPN logins failing?") == ["user:john.doe"]
    assert r.linked_entities("Which accounts did 198.51.100.77 try?") == ["ip:198.51.100.77"]
    assert r.linked_entities("Is the database slow today?") == []


@pytest.mark.parametrize("question, start, end", [
    ("since lunchtime", "2026-10-14 12:00", "2026-10-14 23:00"),
    ("this morning", "2026-10-14 06:00", "2026-10-14 12:00"),
    ("last night", "2026-10-13 18:00", "2026-10-14 06:00"),
    ("around noon", "2026-10-14 11:00", "2026-10-14 13:00"),
])
def test_time_words_become_a_window(question, start, end):
    w = window(f"What happened {question}?", corpus.NOW)
    assert (f"{w[0]:%Y-%m-%d %H:%M}", f"{w[1]:%Y-%m-%d %H:%M}") == (start, end)
    assert window("What happened?", corpus.NOW) is None


def test_novelty_separates_a_new_address_for_a_user_from_a_routine_one(r):
    new = next(k for k in r.items if "user=omar.haddad ip=203.0.113.99" in r.items[k].text)
    routine = next(k for k in r.items if "VPN_LOGIN_SUCCESS user=omar.haddad ip=10.20." in r.items[k].text)
    assert r.rarity(new) == r.rarity(routine)          # same type
    assert r.novelty(new) == 1.0 > r.novelty(routine)  # new pair


# ---------------------------------------------------------------- the recorded results reproduce

def test_the_published_numbers_are_what_the_code_produces_now(r):
    """README quotes results/retrieval.json. If the retriever changes, this fails until the
    results are regenerated, so no quoted number outlives the code that produced it."""
    published = json.loads((HERE / "results" / "retrieval.json").read_text())
    for name, run in configs(r, 10).items():
        assert summarise(score(run, QUESTIONS)) == published["retrievers"][name]["summary"], name


def test_held_out_3_was_scored_by_the_retriever_as_it_is_now(r):
    """held_out_3.json was written when held-out 3 was first scored. The retriever has not changed since."""
    first = json.loads((HERE / "results" / "held_out_3.json").read_text())
    now = summarise(score(configs(r, 10)["graph (default)"], QUESTIONS))
    assert now["held_out_3"] == first["retrievers"]["graph (default)"]["summary"]["held_out_3"]


# ---------------------------------------------------------------- tracing: content stays out

def test_retrieval_traces_counts_not_content(r):
    """Content off: the trace holds counts and the walk's shape, not the question or the lines.
    The control turns content on and finds both, so the absence is real."""
    q = "Why can't alice.smith reach the database server?"
    for sensitive in (False, True):
        sink = InMemoryExporter()
        tracer = Tracer([sink], include_sensitive=sensitive)
        previous = set_tracer(tracer)
        try:
            with tracer.trace("assistant"):
                r.graph_expanded(q, USERS["netops_analyst"])
        finally:
            set_tracer(previous)
        dump = json.dumps(sink.records)
        assert (q in dump) is sensitive and ("firewall:" in dump) is sensitive
        walk = [s for s in sink.spans if s["name"] == "graph_walk"]
        assert walk and walk[0]["data"]["returned"] == 10 and "from_graph" in walk[0]["data"]


# ---------------------------------------------------------------- cited answers

def _model(text):
    m = ScriptedChatModel()
    m.load([AIMessage(content=text, usage_metadata={"input_tokens": 900, "output_tokens": 60, "total_tokens": 960})])
    return m


def test_a_grounded_answer_cites_only_what_it_was_shown(r):
    hits = r.graph_expanded("Why can't alice.smith reach the database server?", USERS["netops_analyst"])
    key = hits[0].key
    good = answer("q", hits, r.items, _model(f"Denied by policy [{key}]."))
    assert good.grounded and good.cited == (key,)
    made_up = answer("q", hits, r.items, _model("Denied [firewall:9999]."))
    assert not made_up.grounded and made_up.reason == "unknown_citation" and "withheld" in made_up.text
    bare = answer("q", hits, r.items, _model("It was denied."))
    assert not bare.grounded and bare.reason == "no_citation"


def test_no_evidence_means_no_model_call():
    m = ScriptedChatModel()
    out = answer("q", [], {}, m)
    assert out.reason == "no_evidence" and m.calls == ()


def test_the_navigator_answers_offline_with_a_scripted_model():
    nav = Navigator.offline(llm=None)
    res = nav.ask("Why can't alice.smith reach the database server?", "netops_analyst")
    assert res.answer is None and len(res.hits) == 10 and res.subgraph.number_of_nodes() >= 10


# ---------------------------------------------------------------- the Upstash adapters, against fakes

def test_the_vector_filter_is_built_from_checked_group_names():
    assert readers_filter(["security", "netops"]) == "readers CONTAINS 'netops' OR readers CONTAINS 'security'"
    assert readers_filter([]) == "readers CONTAINS '__nobody__'"
    with pytest.raises(ValueError):
        readers_filter(["netops' OR readers CONTAINS 'security"])


class FakeIndex:
    def __init__(self, dimension=1536):
        self.upserts, self.queries, self.dim = [], [], dimension

    def info(self):
        return type("Info", (), {"dimension": self.dim})()

    def upsert(self, vectors, namespace=""):
        self.upserts.append((vectors, namespace))

    def query(self, vector, top_k, filter, namespace):
        self.queries.append((top_k, filter, namespace))
        return [type("Hit", (), {"id": "vpn:1", "score": 0.9})()]


def test_upstash_vector_store_filters_inside_the_query():
    idx = FakeIndex()
    store = UpstashVectorStore(index=idx)
    store.upsert([("vpn:1", [0.1, 0.2], {"readers": ["netops"], "kind": "event"})])
    assert store.query([0.1, 0.2], 5, ["netops"]) == [("vpn:1", 0.9)]
    assert idx.queries == [(5, "readers CONTAINS 'netops'", "netgraph")]
    assert idx.upserts[0][0][0] == {"id": "vpn:1", "vector": [0.1, 0.2],
                                    "metadata": {"readers": ["netops"], "kind": "event"}}


def test_an_index_made_for_another_model_is_refused_before_indexing():
    store = UpstashVectorStore(index=FakeIndex(dimension=1536))
    with pytest.raises(ValueError, match="4096 dimensions"):
        store.check_dimension(HashingEmbeddings())
    UpstashVectorStore(index=FakeIndex(dimension=4096)).check_dimension(HashingEmbeddings())


class FakeRedis:
    """Enough of upstash_redis.Redis for the graph store: sets, hashes, a pipeline."""

    def __init__(self):
        self.sets, self.hashes = {}, {}

    def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    def smembers(self, key):
        return list(self.sets.get(key, ()))

    def hset(self, key, values=None):
        self.hashes.setdefault(key, {}).update(values or {})

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def delete(self, *keys):
        for k in keys:
            self.sets.pop(k, None)
            self.hashes.pop(k, None)

    def pipeline(self):
        redis, queued = self, []

        class Pipe:
            def __getattr__(self, name):
                return lambda *a, **kw: queued.append((name, a, kw))

            def exec(self):
                return [getattr(redis, n)(*a, **kw) for n, a, kw in queued]

        return Pipe()


def test_the_graph_round_trips_through_the_redis_layout(items):
    g = G.build(items)
    store = UpstashGraphStore(redis=FakeRedis())
    store.save(g)
    back = store.load()
    assert set(back.nodes) == set(g.nodes) and back.number_of_edges() == g.number_of_edges()
    assert back.nodes["vpn:1"] == g.nodes["vpn:1"]
    assert store.neighbours("ip:203.0.113.45") == set(g.neighbors("ip:203.0.113.45"))
    store.save(G.build(items[:10]))                       # a second save replaces, never merges
    assert store.load().number_of_nodes() == G.build(items[:10]).number_of_nodes()


def test_the_in_memory_graph_store_round_trips(items):
    s = InMemoryGraphStore()
    with pytest.raises(LookupError):
        s.load()
    s.save(G.build(items))
    assert s.load().number_of_edges() == G.build(items).number_of_edges()


# ---------------------------------------------------------------- the app tab

def _page():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(HERE.parent / "app" / "pages" / "3_Knowledge_Navigation.py"),
                             default_timeout=120).run()


def test_the_demo_opens_on_the_join_side_by_side():
    at = _page()
    assert not at.exception
    assert [m.value for m in at.metric[:2]] == ["0 of 9", "8 of 9"]      # q1: vector, then the graph walk


@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_every_demo_step_runs_and_never_shows_what_the_asker_may_not_read(step):
    at = _page()
    steps = at.radio(key="demo_step")
    steps.set_value(steps.options[step]).run()
    assert not at.exception
    assert not at.error                                   # the leak check renders as an error box
    assert len(at.metric) == 2


def test_the_demo_steps_show_what_the_writeup_says():
    """The walkthrough quotes these, so the demo must still show them."""
    at = _page()
    found = {}
    for label in at.radio(key="demo_step").options:
        at.radio(key="demo_step").set_value(label).run()
        found[label[:2]] = tuple(m.value for m in at.metric)
    assert found["3."] != found["2."]                     # security sees more than the network team
    vec, graph = found["5."]
    assert int(graph.split()[0]) < int(vec.split()[0])    # the lookup where the walk loses
