import json

from rag.chunking import Chunk, chunk_contract
from rag.citations import Citation, citation_precision, locate, validate
from rag.graph import build_graph, initial_state
from rag.ledger import ContextLedger
from rag.retrievers import BM25Retriever

CHUNK = Chunk("x:ch0001", "x", "12. Governing Law.  This Agreement shall be\n governed by the laws of New York.", 1000, 1080)


def test_locate_normalizes_whitespace_and_maps_offsets():
    span = locate("this agreement shall be governed by", CHUNK)
    assert span is not None
    s, e = span
    assert CHUNK.text[s - CHUNK.start : e - CHUNK.start] == "This Agreement shall be\n governed by"


def test_validate_statuses():
    checked = validate(
        [
            Citation("x:ch0001", "governed by the laws of New York"),
            Citation("x:ch9999", "governed by the laws of New York"),
            Citation("x:ch0001", "governed by the laws of Delaware"),
            Citation("x:ch0001", "New York"),
        ],
        [CHUNK],
    )
    assert [c.status for c in checked] == ["valid", "unknown_chunk", "quote_not_found", "quote_too_short"]


def test_citation_precision_against_gold():
    checked = validate([Citation("x:ch0001", "governed by the laws of New York")], [CHUNK])
    s, e = checked[0].span
    assert citation_precision(checked, ((s, e),)) == 1.0
    assert citation_precision(checked, ((0, 10),)) == 0.0
    assert citation_precision([], ((0, 10),)) is None


def test_ledger_cost_needs_prices():
    led = ContextLedger("q")
    with led.stage("generate") as rec:
        rec.input_tokens, rec.output_tokens = 2_000_000, 500_000
    assert led.cost(None, None) is None
    assert led.cost(1.0, 2.0) == 3.0


# ---------------------------------------------------------------- graph


def _graph(contract, reply_fn):
    kids, _ = chunk_contract(contract.text, contract.id, size=300, overlap=60, parent_size=None)
    r = BM25Retriever()
    r.fit(kids)
    calls = []

    def fake_llm(prompt):
        calls.append(prompt)
        return {"text": reply_fn(prompt), "input_tokens": 1200, "output_tokens": 80}

    return build_graph(r, fake_llm, k=3), calls


def _first_chunk_id(prompt):
    return prompt.split("[", 1)[1].split("]", 1)[0]


def test_graph_answers_with_valid_citation(contract, queries):
    q = queries["Governing Law"]

    def reply(prompt):
        return json.dumps(
            {
                "abstain": False,
                "answer": "New York law governs.",
                "citations": [{"chunk_id": _first_chunk_id(prompt), "quote": "governed by the laws of the State of New York"}],
            }
        )

    graph, calls = _graph(contract, reply)
    ledger = ContextLedger(q.id)
    final = graph.invoke(initial_state(q, ledger))
    assert final["status"] == "answered"
    assert citation_precision(final["checked"], q.gold_spans) == 1.0
    assert [s.stage for s in ledger.stages] == ["retrieve", "generate", "validate"]
    assert ledger.input_tokens == 1200 and ledger.model_calls == 1 and len(calls) == 1


def test_graph_abstains_when_citation_is_fabricated(contract, queries):
    q = queries["Governing Law"]

    def reply(prompt):
        return json.dumps(
            {"abstain": False, "answer": "Delaware law.", "citations": [{"chunk_id": _first_chunk_id(prompt), "quote": "laws of the State of Delaware apply"}]}
        )

    final = _graph(contract, reply)[0].invoke(initial_state(q))
    assert final["status"] == "abstained" and final["reason"] == "no_valid_citation"


def test_graph_respects_model_abstention(contract, queries):
    q = queries["Non-Compete"]
    final = _graph(contract, lambda p: '{"abstain": true, "reason": "no non-compete language"}')[0].invoke(initial_state(q))
    assert final["status"] == "abstained" and "non-compete" in final["reason"]


def test_graph_survives_unparseable_output(contract, queries):
    final = _graph(contract, lambda p: "I think it is New York")[0].invoke(initial_state(queries["Governing Law"]))
    assert final["status"] == "abstained"
