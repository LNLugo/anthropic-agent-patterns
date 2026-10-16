"""Tracing for the agent patterns: one trace per run, one span per unit of work.

    from tracing import get_tracer
    tracer = get_tracer()
    with tracer.trace("assistant", group_id=thread_id, metadata={"caller": caller.id}):
        with tracer.span("generation", "router", model="gpt-6-luna") as s:
            msg = llm.invoke(messages)
            s.set(input_tokens=..., output_tokens=...)    # metadata: always exported
            s.content(input=messages, output=msg.content)  # content: dropped unless enabled

See spans.py for the taxonomy and exporters.py for where spans go.
"""

from .exporters import FailingExporter, InMemoryExporter, JsonlExporter, SpanExporter
from .spans import KINDS, Span, Trace, Tracer, get_tracer, set_tracer

__all__ = ["KINDS", "Span", "Trace", "Tracer", "get_tracer", "set_tracer",
           "SpanExporter", "JsonlExporter", "InMemoryExporter", "FailingExporter"]
