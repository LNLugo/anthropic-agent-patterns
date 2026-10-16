"""A RAG query is one trace whose spans are the ledger's stages: retrieval as a tool,
generation as a generation, citation validation as a guardrail on the output.
Chunk ids are recorded; chunk text, the prompt and the reply are not."""

import json

from rag.graph import answer_query
from rag.ledger import ContextLedger
from test_citations_and_graph import _first_chunk_id, _graph


def cited(quote):
    def reply(prompt):
        return json.dumps({"abstain": False, "answer": "New York law governs.",
                           "citations": [{"chunk_id": _first_chunk_id(prompt), "quote": quote}]})
    return reply


def test_the_spans_are_the_ledger_stages(contract, queries, traces):
    q = queries["Governing Law"]
    graph, calls = _graph(contract, cited("governed by the laws of the State of New York"))
    ledger = ContextLedger(q.id)
    final = answer_query(graph, q, ledger, group_id="run-1")
    assert final["status"] == "answered"
    retrieve, generate, validate, agent = traces.spans
    assert [(s["kind"], s["name"]) for s in (retrieve, generate, validate)] == [
        ("tool", "retrieve"), ("generation", "generate"), ("guardrail", "validate")]
    assert [s["name"] for s in (retrieve, generate, validate)] == [s.stage for s in ledger.stages]
    assert retrieve["data"]["chunk_ids"] == ledger.stages[0].chunk_ids
    assert generate["data"]["input_tokens"] == ledger.input_tokens == 1200
    assert len(traces.of_kind("generation")) == len(calls) == 1
    assert validate["data"]["behavior"] == "allow" and validate["data"]["valid"] == 1
    assert agent["data"]["status"] == "answered" and traces.traces[-1]["group_id"] == "run-1"


def test_a_fabricated_citation_is_a_rejecting_guardrail(contract, queries, traces):
    q = queries["Governing Law"]
    graph, _ = _graph(contract, cited("laws of the State of Delaware apply"))
    final = answer_query(graph, q)
    [validate] = traces.of_kind("guardrail")
    assert final["status"] == "abstained"
    assert validate["data"]["behavior"] == "reject_content" and validate["data"]["invalid"] == 1
    assert traces.of_kind("agent")[0]["data"]["reason"] == "no_valid_citation"


def test_no_contract_text_prompt_or_reply_reaches_the_trace(contract, queries, traces):
    q = queries["Governing Law"]
    graph, calls = _graph(contract, cited("governed by the laws of the State of New York"))
    answer_query(graph, q)
    dumped = json.dumps(traces.records)
    assert "governed by the laws" not in dumped and "New York law governs" not in dumped
    assert calls[0][:40] not in dumped
