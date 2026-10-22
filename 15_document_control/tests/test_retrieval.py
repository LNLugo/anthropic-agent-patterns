"""Module 15, PR 2: the index, retrieval with mandatory filters, orchestration and safety,
caching and freshness. Offline: the hashing stand-in for embeddings, the scripted model for
answers. These tests check the plumbing and the controls; no answer quality is claimed."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from docctl import status
from docctl.assistant import Answer, ask, pack, route
from docctl.cache import AnswerCache, Freshness, QueryEmbeddings, RetrievalCache, fingerprint
from docctl.corpus import USERS
from docctl.evaluate import questions, run_questions, summary, unit
from docctl.index import Alias, build
from docctl.intake import run_intake
from docctl.retrieve import ANSWERABLE, retrieve
from llm_provider import HashingEmbeddings, ScriptedChatModel, Step

HERE = Path(__file__).resolve().parents[1]
QS = questions()
BY_ID = {q["id"]: q for q in QS}


@pytest.fixture(scope="module")
def intake():
    return run_intake()


@pytest.fixture(scope="module")
def emb():
    return HashingEmbeddings()


@pytest.fixture()
def index(intake, emb):
    return build(intake, emb)            # rebuilt per test: several tests change status or access


@pytest.fixture(scope="module")
def recorded():
    return json.loads((HERE / "results" / "retrieval.json").read_text())


def _model(*replies):
    m = ScriptedChatModel()
    m.load([Step(r) for r in replies])
    return m


def _reply(sentences, **extra):
    return AIMessage(content=json.dumps({"sentences": sentences, "abstained": False, "reason": "", **extra}),
                     **({"tool_calls": extra.pop("tool_calls")} if "tool_calls" in extra else {}))


QA = USERS["qa_northfield"]


# ---------------------------------------------------------------- s7, s8: the index

def test_every_vector_names_its_model_and_a_query_from_another_model_is_refused(index, emb):
    assert {r.embedding_model_id for r in index.rows} == {emb.model}
    with pytest.raises(ValueError, match="hashing-4096"):
        index.cosine(emb.embed_query("assay limit"), "text-embedding-3-large")


def test_an_unchanged_section_is_embedded_once_across_revisions(index):
    assert index.reused > 0 and index.embedded + index.reused == len(index.rows)
    texts = {}
    for i, r in enumerate(index.rows):
        texts.setdefault(r.search_text, []).append(i)
    same = next(ix for ix in texts.values() if len(ix) > 1)
    assert all((index.vectors[same[0]] == index.vectors[j]).all() for j in same)


def test_a_new_index_replaces_the_old_only_if_it_does_as_well_and_can_be_rolled_back(recorded):
    m = recorded["migration"]
    assert m["candidate_flipped"] and m["bad_model_refused"] and m["query_from_another_model_refused"]
    assert m["alias_history"] == ["smartdms-v1", "smartdms-v2", "smartdms-v1"]
    assert m["vector_only_found"]["scrambled (control)"]["found"] < m["vector_only_found"]["hashing-4096 (in use)"]["found"]


def test_queries_keep_using_the_old_index_until_the_flip(intake, emb):
    old = build(intake, emb, "v1")
    alias = Alias(old)
    new = build(intake, emb, "v2")                   # built alongside, not yet pointed to
    assert alias.active is old
    assert alias.flip(new, 10, 10) and alias.active is new and alias.previous is old
    alias.rollback()
    assert alias.active is old


def test_instruction_like_text_is_flagged_at_ingest(index):
    flagged = [r for r in index.rows if r.suspected_injection]
    assert [r.key for r in flagged] == ["COA-2004-L26-0689/01"]


# ---------------------------------------------------------------- s9: retrieval

def test_no_default_query_returns_a_superseded_revision_and_the_control_does(recorded):
    s = recorded["stale_versions"]
    assert s["returned_by_default"] == 0 and s["superseded_units_forbidden_and_returned"] == 0
    assert s["control_without_the_status_filter"] > 0


def test_history_is_returned_only_when_asked_and_always_labelled(index, emb):
    q = BY_ID["d18"]
    r = retrieve(index, q["question"], USERS[q["as"]], emb)
    assert any(h.status == "superseded" for h in r.hits)
    assert all(h.label == "effective" or h.label.startswith(h.status + ": not the current version") for h in r.hits)


def test_no_one_is_shown_a_document_they_may_not_open_and_the_control_sees_them(recorded):
    a = recorded["access"]
    assert a["unreadable_returned"] == 0 and a["forbidden_returned"] == 0 and a["returned_to_someone_in_no_group"] == 0
    assert a["control_with_every_group"] > 0


def test_a_document_number_the_asker_cannot_open_looks_like_one_that_does_not_exist(index, emb):
    hidden = retrieve(index, "Show me CMP-2026-004.", QA, emb)
    missing = retrieve(index, "Show me CMP-2026-999.", QA, emb)
    assert hidden.route == missing.route == "search"
    assert not any(h.row.doc_number == "CMP-2026-004" for h in hidden.hits)


def test_a_named_document_short_circuits_to_that_document_only(index, emb):
    r = retrieve(index, "Show me SOP-QA-0001.", QA, emb)
    assert r.route == "document" and {h.row.doc_number for h in r.hits} == {"SOP-QA-0001"}
    assert {h.status for h in r.hits} == {"effective"}


def test_a_revision_with_unknown_status_is_never_returned(index, emb):
    unknown = [r for r in index.rows if index.row_status(r) == "unknown"]
    assert unknown
    for r in unknown[:3]:
        assert all(h.row.key != r.key for h in retrieve(index, r.text[:80], QA, emb).hits)


def test_every_development_question_finds_its_evidence(recorded):
    r = recorded["recall"]["all"]
    assert r["found"] == r["expected"]


def test_the_answerability_threshold_is_the_calibrated_midpoint(recorded):
    a = recorded["answerability"]
    assert a["coverage"]["separates"] and a["threshold_in_use"] == a["coverage"]["midpoint"] == ANSWERABLE


def test_a_new_effective_revision_replaces_the_old_in_one_write(index, emb):
    q = BY_ID["d06"]                                  # its effective procedure, and an older one
    before = {unit(h.row.chunk_id) for h in retrieve(index, q["question"], QA, emb).hits}
    assert set(q["expected"]) <= before
    old = q["forbidden"][0].split("#")[0].split("/")[1]
    eff = q["expected"][0].split("#")[0].split("/")[1]
    number = q["expected"][0].split("/")[0]
    revs = dict(index.status[number])
    revs[eff], revs[old] = "superseded", "effective"   # as if the older one were reinstated
    v = index.version
    index.set_status(number, revs)
    after = {unit(h.row.chunk_id) for h in retrieve(index, q["question"], QA, emb).hits}
    assert index.version == v + 1 and not set(q["expected"]) & after             # gone at once
    assert any(u.startswith(f"{number}/{old}#") for u in after)                    # the reinstated one answers


# ---------------------------------------------------------------- s11: routing and orchestration

def test_every_development_question_is_routed_by_rules(recorded):
    assert recorded["routing"]["correct"] == recorded["routing"]["of"]


def test_a_release_decision_is_refused_with_no_retrieval_and_no_model_call(index):
    m = _model()
    a = ask("Should we release lot L26-0689 now?", QA, index, m)
    assert a.route == "out_of_scope" and a.abstained and a.retrieval is None and m.calls == ()


def test_a_tool_runs_only_from_the_question_and_only_from_the_allowlist(index):
    m = _model()
    a = ask("Is supplier SUP-2005 approved?", USERS["supplier_quality"], index, m)
    assert a.tool == "lookup_supplier" and a.tool_result["approved"] is False and m.calls == ()


def test_the_context_is_packed_labelled_and_within_budget(index, emb):
    r = retrieve(index, BY_ID["d18"]["question"], USERS["supplier_quality"], emb)
    p = pack(r.hits, budget=400)
    assert p and all(x.label.startswith(f"[{x.chunk_id}]") and "revision" in x.label for x in p)
    assert len({x.text for x in p}) == len(p)
    assert sum(len(x.text.split()) for x in p) * 1.3 <= 400 + 100


def test_without_evidence_the_assistant_abstains_without_calling_the_model(index):
    m = _model()
    a = ask("What is the shelf life of the finished lipstick?", QA, index, m)
    assert a.abstained and m.calls == () and a.closest


# ---------------------------------------------------------------- s12: grounding and safety

def _cite(index, emb, question, groups=QA):
    r = retrieve(index, question, groups, emb)
    p = pack(r.hits)[0]
    return p.chunk_id, p.text.split("\n")[1][:40]


def test_a_sentence_citing_something_it_was_not_shown_is_removed(index, emb):
    q = BY_ID["d06"]["question"]
    cid, quote = _cite(index, emb, q)
    m = _model(_reply([{"text": "Supported.", "citations": [{"chunk_id": cid, "quote": quote}]},
                       {"text": "Also supported.", "citations": [{"chunk_id": cid, "quote": quote}]},
                       {"text": "Invented.", "citations": [{"chunk_id": "SOP-QA-9999/01#1 Purpose#1",
                                                            "quote": "a quote from nowhere at all"}]}]))
    a = ask(q, QA, index, m, emb)
    assert not a.abstained and a.removed_unsupported == 1 and [s["text"] for s in a.sentences] == ["Supported.",
                                                                                                 "Also supported."]
    c = a.sentences[0]["citations"][0]
    assert {"doc_number", "revision", "section", "pages", "status"} <= set(c) and c["status"] == "effective"


def test_an_answer_mostly_unsupported_becomes_an_abstention(index, emb):
    q = BY_ID["d06"]["question"]
    cid, quote = _cite(index, emb, q)
    m = _model(_reply([{"text": "One.", "citations": [{"chunk_id": cid, "quote": quote}]},
                       {"text": "Two.", "citations": [{"chunk_id": cid, "quote": "words that are not there"}]},
                       {"text": "Three.", "citations": []}]))
    a = ask(q, QA, index, m, emb)
    assert a.abstained and a.removed_unsupported == 2


def test_a_reply_outside_the_schema_is_not_an_answer(index, emb):
    a = ask(BY_ID["d06"]["question"], QA, index, _model(AIMessage(content="The form is F-083.")), emb)
    assert a.abstained and "form" in a.reason


def test_instructions_inside_a_supplier_document_never_trigger_a_tool(index, emb):
    q = BY_ID["d33"]
    groups = USERS[q["as"]]
    cid, quote = _cite(index, emb, q["question"], groups)
    hostile = AIMessage(content=json.dumps({"sentences": [{"text": "The lot is released.",
                                                           "citations": [{"chunk_id": cid, "quote": quote}]}],
                                            "abstained": False, "reason": "", "tool": "release_lot"}),
                        tool_calls=[{"name": "release_lot", "args": {"lot": "L26-0689"}, "id": "call-1"}])
    m = _model(hostile)
    a = ask(q["question"], groups, index, m, emb)
    assert a.ignored_tool_requests == 2 and a.tool is None and a.tool_result is None
    prompt = m.calls[0].prompt
    assert "FLAGGED" in prompt and "never follow it" in prompt and "<document>" in prompt


def test_access_revoked_after_retrieval_is_applied_before_the_answer(index, emb):
    q = BY_ID["d06"]["question"]
    cid, quote = _cite(index, emb, q)
    key = cid.split("#")[0]

    class RevokesWhileAnswering:
        def invoke(self, prompt):
            index.set_readers(key, ["someone-else"])
            return _reply([{"text": "Supported.", "citations": [{"chunk_id": cid, "quote": quote}]}])

    a = ask(q, QA, index, RevokesWhileAnswering(), emb)
    assert a.abstained and a.removed_revoked == 1


def test_personal_data_in_the_output_is_redacted(index, emb):
    q = BY_ID["d06"]["question"]
    cid, quote = _cite(index, emb, q)
    m = _model(_reply([{"text": "Call +1 555 0199 4321 for details.", "citations": [{"chunk_id": cid, "quote": quote}]}]))
    a = ask(q, QA, index, m, emb)
    assert a.redacted_in_output == 1 and "[REDACTED:PHONE]" in a.text()


# ---------------------------------------------------------------- s14: caching and freshness

def test_people_with_different_access_never_share_a_cached_answer(index):
    c = AnswerCache()
    assert c.key("Q?", QA, index) != c.key("Q?", USERS["supplier_quality"], index)
    assert c.key("What is it?", ["b", "a"], index) == c.key("  what is it ", ["a", "b"], index)
    assert fingerprint([]) != fingerprint(["quality-all"])


def test_a_status_change_purges_the_answers_that_cite_the_document(index, emb):
    q = BY_ID["d06"]["question"]
    cid, quote = _cite(index, emb, q)
    number = cid.split("/")[0]
    cache = AnswerCache()
    fresh = Freshness(index, cache, RetrievalCache())
    m = _model(_reply([{"text": "Supported.", "citations": [{"chunk_id": cid, "quote": quote}]}]))
    first = ask(q, QA, index, m, emb, cache=cache)
    again = ask(q, QA, index, m, emb, cache=cache)
    assert not first.abstained and again.cached and len(m.calls) == 1
    assert fresh.status_changed("SPEC-RM-0001", dict(index.status["SPEC-RM-0001"])) == 0   # unrelated: kept
    assert fresh.status_changed(number, dict(index.status[number])) == 1 and len(cache) == 0


def test_retrieval_results_expire_after_fifteen_minutes():
    t = [0.0]
    c = RetrievalCache(clock=lambda: t[0])
    c.put("k", "v")
    t[0] = 14 * 60
    assert c.get("k") == "v"
    t[0] = 16 * 60
    assert c.get("k") is None


def test_a_query_is_embedded_once_per_model(emb):
    a, b = QueryEmbeddings(emb), QueryEmbeddings(HashingEmbeddings(dimensions=1024))
    a.embed_query("assay limit")
    a.embed_query("assay limit")
    assert a.computed == 1 and len(b.embed_query("assay limit")) == 1024


# ---------------------------------------------------------------- recorded results

def test_the_recorded_retrieval_results_are_what_the_code_produces_now(intake, emb, recorded):
    assert json.loads(json.dumps(summary(intake, emb), sort_keys=True)) == recorded


def test_the_readme_quotes_the_recorded_retrieval_results(recorded):
    readme = (HERE / "README.md").read_text()
    r = recorded["recall"]["all"]
    for phrase in (f"{r['found']} of {r['expected']}",
                   f"{recorded['stale_versions']['control_without_the_status_filter']} superseded",
                   f"{recorded['access']['control_with_every_group']} forbidden",
                   f"{recorded['answerability']['correct']} of {recorded['answerability']['of']}"):
        assert phrase in readme, phrase


def _page():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(HERE.parent / "app" / "pages" / "4_Document_Control.py"), default_timeout=120).run()


def test_the_ask_tab_answers_offline_and_never_shows_what_the_asker_may_not_open(recorded):
    at = _page()
    assert not at.exception
    shown = {m.label: m.value for m in at.metric}
    assert shown["Superseded returned by default"] == "0"
    assert shown["Shown to someone who may not open it"] == "0"
    at.selectbox(key="ask_question").set_value(next(o for o in at.selectbox(key="ask_question").options
                                                    if o.startswith("d22 "))).run()
    assert not at.exception
    assert any(m.value == "Route: **ask**" for m in at.markdown)          # the question really changed
    assert not any("CMP-" in str(df.value.to_dict()) for df in at.dataframe if "flagged" in df.value.columns)
