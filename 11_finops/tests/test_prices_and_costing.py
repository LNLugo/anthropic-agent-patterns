"""Prices come from the file, with a source; a cost that includes an unpriced model is
refused, never counted as zero."""

from datetime import date

import pytest

from finops import PriceBook, Unpriced
from finops.costing import cost_runs, rollup, spend_without_an_answer


@pytest.fixture(scope="module")
def book():
    return PriceBook.load()


def test_the_file_prices_what_the_repo_calls_and_says_where_from(book):
    luna = book.price("gpt-6-luna")
    assert (luna.input, luna.cached_input, luna.output) == (0.10, 0.01, 0.50)
    assert luna.source.startswith("https://developers.openai.com/") and luna.checked == date(2026, 10, 16)
    assert book.price("gpt-4o-mini").output == 0.60


def test_a_model_missing_from_the_file_is_refused(book):
    with pytest.raises(Unpriced, match="claude-sonnet-5-5"):
        book.cost("claude-sonnet-5-5", 728, 39)
    with pytest.raises(Unpriced):
        book.cost(None, 728, 39)  # a span that does not name its model


def test_one_router_call_at_the_published_averages(book):
    # 728 input and 39 output tokens: the held-out averages recorded for gpt-6-luna
    assert book.cost("gpt-6-luna", 728, 39) == pytest.approx(728 * 0.10e-6 + 39 * 0.50e-6)


def test_cached_input_bills_at_the_cached_rate(book):
    full = book.cost("gpt-6-luna", 10_000, 0)
    cached = book.cost("gpt-6-luna", 10_000, 0, cached_input_tokens=8_000)
    assert cached == pytest.approx((2_000 * 0.10 + 8_000 * 0.01) / 1e6) and cached < full


def test_long_context_reprices_the_whole_request(book):
    at, over = book.cost("gpt-6-luna", 272_000, 1_000), book.cost("gpt-6-luna", 272_001, 1_000)
    assert at == pytest.approx((272_000 * 0.10 + 1_000 * 0.50) / 1e6)
    assert over == pytest.approx((272_001 * 0.20 + 1_000 * 0.75) / 1e6)
    assert book.cost("gpt-4o-mini", 300_000, 0) == pytest.approx(300_000 * 0.15 / 1e6)  # no rule for it


def test_bad_counts_are_refused(book):
    with pytest.raises(ValueError):
        book.cost("gpt-6-luna", 100, 10, cached_input_tokens=200)


def test_a_file_entry_without_a_source_will_not_load(tmp_path):
    p = tmp_path / "prices.yaml"
    p.write_text("models:\n  m:\n    input: 1\n    cached_input: 1\n    output: 1\n    checked: 2026-10-16\n")
    with pytest.raises(ValueError, match="source"):
        PriceBook.load(p)


# ---------------------------------------------------------------- costing traces

def trace(tid, caller="ana", workflow="assistant"):
    return {"type": "trace", "trace_id": tid, "workflow": workflow, "metadata": {"caller": caller},
            "status": "ok", "ended_at": "x"}


def span(tid, kind, name, parent="a", **data):
    return {"type": "span", "trace_id": tid, "span_id": f"{tid}-{name}", "parent_id": parent, "kind": kind,
            "name": name, "data": data, "error": None}


def agent(tid, ended):
    return span(tid, "agent", "assistant", parent=None, ended=ended)


def gen(tid, model="gpt-6-luna", i=728, o=39, n=1):
    return span(tid, "generation", f"router{n}", model=model, input_tokens=i, output_tokens=o)


def test_runs_are_costed_from_their_generation_spans(book):
    traces = {"t1": trace("t1"), "t2": trace("t2", caller="bo")}
    spans = [agent("t1", "answered"), gen("t1"), gen("t1", n=2), agent("t2", "clarify"), gen("t2")]
    runs = {r.trace_id: r for r in cost_runs(traces, spans, book)}
    one = book.cost("gpt-6-luna", 728, 39)
    assert runs["t1"].cost == pytest.approx(2 * one) and runs["t1"].model_calls == 2
    assert (runs["t2"].identity, runs["t2"].ended) == ("bo", "clarify")
    wasted, share = spend_without_an_answer(list(runs.values()))
    assert wasted == pytest.approx(one) and share == pytest.approx(1 / 3)


def test_one_unpriced_call_makes_the_run_and_every_total_unpriced(book):
    traces = {"t1": trace("t1"), "t2": trace("t2")}
    spans = [agent("t1", "answered"), gen("t1"), gen("t1", model="ScriptedChatModel", n=2),
             agent("t2", "answered"), gen("t2")]
    runs = cost_runs(traces, spans, book)
    bad = next(r for r in runs if r.trace_id == "t1")
    assert bad.cost is None and bad.unpriced_models == {"ScriptedChatModel"}
    [ana] = rollup(runs, by="identity")
    assert ana.cost is None and ana.unpriced_runs == 1 and ana.runs == 2   # not a partial sum
    assert spend_without_an_answer(runs) == (None, None)


def test_a_halt_is_its_own_outcome(book):
    halted = agent("t1", None) | {"error": {"type": "ToolInputGuardrailTripwireTriggered"}}
    [r] = cost_runs({"t1": trace("t1")}, [halted, gen("t1")], book)
    assert r.ended == "halted" and r.cost > 0


def test_the_report_reads_a_real_trace_file(tmp_path, capsys):
    """End to end: spans written by the tracer, read back, priced, rolled up."""
    import run_cost_report
    from tracing import JsonlExporter, Tracer

    tracer = Tracer([JsonlExporter(tmp_path)])
    for caller, model in (("ana", "gpt-6-luna"), ("ana", "gpt-6-luna"), ("bo", "some-new-model")):
        with tracer.trace("assistant", metadata={"caller": caller}):
            with tracer.span("agent", "assistant") as a:
                with tracer.span("generation", "router", model=model) as g:
                    g.set(input_tokens=728, output_tokens=39)
                a.set(ended="answered")
    import sys
    sys.argv = ["run_cost_report.py", str(tmp_path)]
    assert run_cost_report.main() == 0
    out = capsys.readouterr().out
    two = 2 * PriceBook.load().cost("gpt-6-luna", 728, 39)
    assert f"${two:.6f}" in out
    assert "unpriced run(s): some-new-model" in out
    assert "Spend on runs that ended without an answer: unpriced" in out  # bo's run is in the total
