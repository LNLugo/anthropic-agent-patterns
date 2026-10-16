"""The tracing core: taxonomy, parenting, sensitive content off by default, and a
broken exporter never breaking the run it observes."""

import json
import threading
from typing import TypedDict

import pytest

from tracing import FailingExporter, InMemoryExporter, JsonlExporter, Tracer

SECRET = "CANARY-7f3a"  # stands in for a prompt, a document or a tool result


@pytest.fixture
def mem():
    return InMemoryExporter()


def test_a_span_outside_a_trace_records_nothing(mem):
    tracer = Tracer([mem])
    with tracer.span("tool", "count") as s:
        s.set(count=3).content(output=SECRET)
    assert mem.records == []


def test_spans_nest_under_their_trace_and_parent(mem):
    tracer = Tracer([mem])
    with tracer.trace("assistant", group_id="thread-1", metadata={"caller": "ana"}) as t:
        with tracer.span("agent", "assistant") as agent:
            with tracer.span("generation", "router", model="gpt-6-luna"):
                pass
            with tracer.span("tool", "count_jira_issues"):
                pass
    start, *spans, end = mem.records
    assert start["type"] == end["type"] == "trace" and start["status"] == "running" and end["status"] == "ok"
    assert end["group_id"] == "thread-1" and end["metadata"] == {"caller": "ana"} and end["span_count"] == 3
    gen, tool, outer = spans  # a span is exported when it ends, so children come first
    assert outer["span_id"] == agent.span_id and outer["parent_id"] is None
    assert gen["parent_id"] == tool["parent_id"] == agent.span_id
    assert {s["trace_id"] for s in spans} == {t.trace_id}
    assert gen["data"] == {"model": "gpt-6-luna"}


def test_content_is_dropped_by_default_and_never_held_in_memory(mem):
    tracer = Tracer([mem])
    with tracer.trace("run"):
        with tracer.span("generation", "answer") as s:
            s.set(input_tokens=12).content(input=f"prompt {SECRET}", output=f"reply {SECRET}")
    assert s.content_fields == {}
    assert SECRET not in json.dumps(mem.records)
    assert mem.spans[0]["data"] == {"input_tokens": 12} and mem.spans[0]["content"] is None


def test_content_is_kept_when_enabled(mem):
    tracer = Tracer([mem], include_sensitive=True)
    with tracer.trace("run"):
        with tracer.span("generation", "answer") as s:
            s.content(output=f"reply {SECRET}")
    assert mem.spans[0]["content"] == {"output": f"reply {SECRET}"}


def test_an_error_keeps_its_type_but_not_its_message(mem):
    tracer = Tracer([mem])
    with pytest.raises(ValueError):
        with tracer.trace("run"):
            with tracer.span("tool", "count"):
                raise ValueError(f"bad project {SECRET}")
    assert mem.spans[0]["error"] == {"type": "ValueError"}
    assert mem.traces[-1]["status"] == "error"
    assert SECRET not in json.dumps(mem.records)


def test_a_failing_exporter_never_fails_the_run(mem):
    tracer = Tracer([FailingExporter(), mem])
    with tracer.trace("run"):
        with tracer.span("tool", "count") as s:
            s.set(count=3)
        result = "answered"
    assert result == "answered"
    assert tracer.export_errors == 3          # trace start, the span, trace end
    assert len(mem.records) == 3              # the other exporter still got everything


def test_a_nested_trace_joins_the_open_one(mem):
    tracer = Tracer([mem])
    with tracer.trace("mcp") as outer:
        with tracer.trace("assistant") as inner:
            with tracer.span("tool", "count"):
                pass
    assert inner is outer and len(mem.traces) == 2 and mem.spans[0]["trace_id"] == outer.trace_id


def test_disabled_tracing_records_nothing(mem):
    tracer = Tracer([mem], disabled=True)
    with tracer.trace("run") as t:
        with tracer.span("tool", "count"):
            pass
    assert t is None and mem.records == []


def test_unknown_kinds_are_refused(mem):
    tracer = Tracer([mem])
    with tracer.trace("run"):
        with pytest.raises(ValueError, match="unknown span kind"):
            with tracer.span("retrieval", "search"):
                pass


def test_environment_sets_the_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACE_EXPORT_DIR", str(tmp_path))
    monkeypatch.delenv("TRACE_INCLUDE_SENSITIVE_DATA", raising=False)
    tracer = Tracer()
    assert not tracer.include_sensitive and isinstance(tracer.exporters[0], JsonlExporter)
    assert tracer.exporters[0].folder == tmp_path
    monkeypatch.setenv("TRACE_INCLUDE_SENSITIVE_DATA", "1")
    monkeypatch.setenv("TRACING_DISABLED", "true")
    tracer = Tracer()
    assert tracer.include_sensitive and tracer.disabled


def test_jsonl_keeps_every_finished_span_when_the_run_crashes(tmp_path):
    exporter = JsonlExporter(tmp_path)
    tracer = Tracer([exporter])
    with pytest.raises(RuntimeError):
        with tracer.trace("run"):
            with tracer.span("generation", "router"):
                pass
            raise RuntimeError("process dies here")
    lines = [json.loads(line) for line in exporter.path().read_text().splitlines()]
    assert [r["type"] for r in lines] == ["trace", "span", "trace"]
    assert lines[1]["kind"] == "generation" and lines[-1]["status"] == "error"


def test_spans_follow_the_run_into_langgraph_nodes(mem):
    """LangGraph runs nodes on worker threads; the trace must follow them."""
    from langgraph.graph import END, StateGraph

    class S(TypedDict, total=False):
        n: int
        thread: str

    tracer = Tracer([mem])

    def node(s: S) -> S:
        with tracer.span("tool", "inside_node"):
            return {"n": 1, "thread": threading.current_thread().name}

    g = StateGraph(S)
    g.add_node("node", node)
    g.set_entry_point("node")
    g.add_edge("node", END)
    app = g.compile()
    with tracer.trace("graph"):
        with tracer.span("agent", "outer") as outer:
            app.invoke({})
    inside = mem.of_kind("tool")[0]
    assert inside["parent_id"] == outer.span_id
