"""Traces and spans, with sensitive content off by default.

A trace is one run (an assistant question, an agent run, a RAG query). A span
is one unit of work inside it, of one kind:

    agent       a run of an agent or workflow: its name, status, why it ended
    generation  one model call: model, tokens, latency
    tool        one tool call: tool name, outcome (retrieval is a tool here)
    guardrail   one guardrail verdict: name, stage, behaviour (allow, reject_content, raise_exception)
    handoff     control passing elsewhere: from, to (another agent, or a person approving)
    custom      anything else, named

The kinds follow the span types in openai/openai-agents-python v0.24.0 (agent,
generation, function, guardrail, handoff, custom); only the taxonomy is
borrowed. Three deliberate differences:

  * Sensitive content is OFF by default. Prompts, model output, tool inputs and
    outputs and exception messages are recorded only with
    TRACE_INCLUDE_SENSITIVE_DATA=1. (Theirs defaults to on.)
  * The default exporter writes local JSONL. Nothing leaves the machine unless
    an exporter is added. (Theirs sends to the OpenAI traces endpoint.)
  * A guardrail span records which of the three behaviours it returned, not
    just whether it triggered, so an audit can tell a coached retry from a halt.

Instrumented code chooses, per field, whether a value is metadata (span.set,
always exported) or content (span.content, dropped unless enabled). The choice
is made at the call site, where a reviewer can see it.

A span opened with no active trace records nothing, so instrumented code costs
nothing until a caller starts a trace. A failing exporter never fails the run:
its errors are counted in Tracer.export_errors.
"""

from __future__ import annotations

import contextvars
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from .exporters import JsonlExporter, SpanExporter

KINDS = ("agent", "generation", "tool", "guardrail", "handoff", "custom")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _flag(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    return default if val is None else val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Trace:
    trace_id: str
    workflow: str
    group_id: str | None
    metadata: dict
    started_at: str
    ended_at: str | None = None
    status: str = "running"          # running, ok, error
    span_count: int = 0

    def record(self) -> dict:
        return {"type": "trace", "trace_id": self.trace_id, "workflow": self.workflow,
                "group_id": self.group_id, "metadata": self.metadata, "started_at": self.started_at,
                "ended_at": self.ended_at, "status": self.status, "span_count": self.span_count}


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str | None
    kind: str
    name: str
    started_at: str
    include_sensitive: bool
    data: dict = field(default_factory=dict)
    content_fields: dict = field(default_factory=dict)
    ended_at: str | None = None
    duration_ms: float = 0.0
    error: dict | None = None
    _t0: float = 0.0

    def set(self, **fields: Any) -> "Span":
        """Metadata: ids, names, counts, tokens, statuses. Always exported."""
        self.data.update(fields)
        return self

    def content(self, **fields: Any) -> "Span":
        """Content: prompts, model output, tool inputs and outputs. Kept only when
        sensitive data is enabled; otherwise never stored, not even in memory."""
        if self.include_sensitive:
            self.content_fields.update(fields)
        return self

    def record(self) -> dict:
        return {"type": "span", "trace_id": self.trace_id, "span_id": self.span_id, "parent_id": self.parent_id,
                "kind": self.kind, "name": self.name, "started_at": self.started_at, "ended_at": self.ended_at,
                "duration_ms": round(self.duration_ms, 3), "data": self.data,
                "content": self.content_fields if self.include_sensitive else None, "error": self.error}


_current_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar("trace", default=None)
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar("span", default=None)


class _NoopSpan:
    """What a span is when no trace is active: accepts everything, records nothing."""

    span_id = None

    def set(self, **fields: Any) -> "_NoopSpan":
        return self

    def content(self, **fields: Any) -> "_NoopSpan":
        return self


NOOP_SPAN = _NoopSpan()


class Tracer:
    def __init__(self, exporters: list[SpanExporter] | None = None, include_sensitive: bool | None = None,
                 disabled: bool | None = None):
        self.exporters = list(exporters) if exporters is not None else [
            JsonlExporter(os.environ.get("TRACE_EXPORT_DIR", "traces"))]
        self.include_sensitive = (_flag("TRACE_INCLUDE_SENSITIVE_DATA", False)
                                  if include_sensitive is None else include_sensitive)
        self.disabled = _flag("TRACING_DISABLED", False) if disabled is None else disabled
        self.export_errors = 0

    # ---------------------------------------------------------------- export

    def _export(self, record: dict) -> None:
        for exporter in self.exporters:
            try:
                exporter.export(record)
            except Exception:  # observability must never break the run it observes
                self.export_errors += 1

    # ---------------------------------------------------------------- traces and spans

    @property
    def active(self) -> bool:
        return not self.disabled and _current_trace.get() is not None

    @contextmanager
    def trace(self, workflow: str, group_id: str | None = None, metadata: dict | None = None) -> Iterator[Trace | None]:
        """One run. Nested calls join the trace already open rather than starting another,
        so a run started inside another (an MCP call running the assistant) stays one trace."""
        if self.disabled:
            yield None
            return
        if _current_trace.get() is not None:
            yield _current_trace.get()
            return
        t = Trace(f"trace_{uuid.uuid4().hex}", workflow, group_id, dict(metadata or {}), _now())
        trace_token, span_token = _current_trace.set(t), _current_span.set(None)
        try:
            self._export(t.record())
            yield t
            t.status = "ok"
        except BaseException:
            t.status = "error"
            raise
        finally:
            t.ended_at = _now()
            _current_trace.reset(trace_token)
            _current_span.reset(span_token)
            self._export(t.record())

    @contextmanager
    def span(self, kind: str, name: str, **data: Any) -> Iterator[Span | _NoopSpan]:
        if kind not in KINDS:
            raise ValueError(f"unknown span kind {kind!r}; choose from {KINDS}")
        t = _current_trace.get()
        if self.disabled or t is None:
            yield NOOP_SPAN
            return
        parent = _current_span.get()
        s = Span(t.trace_id, f"span_{uuid.uuid4().hex[:24]}", parent.span_id if parent else None, kind, name,
                 _now(), self.include_sensitive, dict(data), _t0=time.perf_counter())
        token = _current_span.set(s)
        try:
            yield s
        except BaseException as e:
            s.error = {"type": type(e).__name__}
            if self.include_sensitive:
                s.error["message"] = str(e)
            raise
        finally:
            s.duration_ms = (time.perf_counter() - s._t0) * 1000
            s.ended_at = _now()
            _current_span.reset(token)
            t.span_count += 1
            self._export(s.record())

    def event(self, kind: str, name: str, **data: Any) -> None:
        """A span with no duration, for something that happened rather than ran (a handoff)."""
        with self.span(kind, name, **data):
            pass

    def shutdown(self) -> None:
        for exporter in self.exporters:
            try:
                exporter.shutdown()
            except Exception:
                self.export_errors += 1


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """The process-wide tracer, created on first use from the environment:
    TRACE_EXPORT_DIR (default ./traces), TRACE_INCLUDE_SENSITIVE_DATA (default off),
    TRACING_DISABLED (default off)."""
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer


def set_tracer(tracer: Tracer | None) -> Tracer | None:
    """Replace the process-wide tracer (tests install an in-memory one). Returns the previous one."""
    global _tracer
    previous, _tracer = _tracer, tracer
    return previous
