# 10 Observability

One trace for every run, one span for every unit of work, and nothing
sensitive recorded unless you turn it on.

> **Status:** the tracing core is built and tested. Instrumenting 06, 07 and 08,
> and the measurements, follow in the next change.

## Why this module

07 already records what went into the model's context (the context ledger), and
08 records every guardrail verdict and approval. Those records answer "what
happened in this run", but each module keeps its own, in memory, in its own
shape. Observability is the same evidence in one shape, written somewhere that
outlives the process, so an auditor or an on-call engineer can answer:

* Who asked, and which tools ran as them?
* Which guardrail stopped the run, and did it coach the model or halt it?
* How many model calls and tokens did that answer cost? (Module 11 prices this.)
* Did any prompt, document or tool result end up in the logs? (It should not.)

## The taxonomy

| Kind | One span per | Records (metadata, always) |
|---|---|---|
| `agent` | run of an agent or workflow | name, status, why it ended |
| `generation` | model call | model, input and output tokens, latency |
| `tool` | tool call (retrieval is a tool) | tool name, outcome, ids and counts |
| `guardrail` | guardrail verdict | name, stage, behaviour: `allow`, `reject_content` or `raise_exception` |
| `handoff` | control passing elsewhere | from, to: another agent, or a person approving |
| `custom` | anything else | name, data |

A trace groups the spans of one run, and a `group_id` groups traces: an approval
that pauses for a day is two traces (the request and the decision) in one group.

The kinds follow the span types in
[openai/openai-agents-python](https://github.com/openai/openai-agents-python)
v0.24.0 (agent, generation, function, guardrail, handoff, custom). Only the
taxonomy is borrowed, with three deliberate differences:

| | openai-agents-python v0.24.0 | This repo |
|---|---|---|
| Sensitive content | on by default (`OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA`, default `true`) | **off** by default (`TRACE_INCLUDE_SENSITIVE_DATA`, default off) |
| Default exporter | OpenAI's traces endpoint | **local JSONL**; nothing leaves the machine |
| Guardrail span | whether it triggered | **which behaviour** it returned, so a coached retry and a halt look different |

## Metadata and content

Instrumented code decides, per field, what kind of value it is:

```python
with tracer.span("generation", "router", model="gpt-6-luna") as s:
    msg = llm.invoke(messages)
    s.set(input_tokens=..., output_tokens=...)        # metadata: always exported
    s.content(input=messages, output=msg.content)     # content: dropped unless enabled
```

`content()` is dropped at the call, not filtered on export, so with sensitive
data off the text is never held by the tracer at all. An exception keeps its
type and loses its message, since messages often quote the input.

## Exporters

| Exporter | Use |
|---|---|
| `JsonlExporter` (default) | One JSON line per record in `./traces/traces-YYYY-MM-DD.jsonl`. Each span is written when it ends, so a crash keeps every span that finished |
| `InMemoryExporter` | Tests, and showing a run's trace in the app |
| `FailingExporter` | Proves a broken exporter never breaks a run |

Anything with `export(record)` and `shutdown()` is an exporter, so an
OpenTelemetry or vendor exporter plugs in without touching instrumented code.
A failing exporter is isolated: the run continues, the other exporters still
receive every record, and the failure is counted in `Tracer.export_errors`.

| Setting | Default |
|---|---|
| `TRACE_EXPORT_DIR` | `./traces` |
| `TRACE_INCLUDE_SENSITIVE_DATA` | off |
| `TRACING_DISABLED` | off |

A span opened with no active trace records nothing, so instrumented code costs
nothing until a caller starts a trace.

## Tests

From the repo root, `pytest 10_observability`. The tests pin down the
taxonomy, parenting across LangGraph's worker threads, content dropped by
default and kept when enabled, error messages dropped, a failing exporter
isolated, nested runs joining one trace, and a crash keeping finished spans.

Each control was checked against deliberate breakage: storing content
regardless of the setting, or letting an exporter's error propagate, each fails
the suite. The second also exposed a real fault, fixed here: an exporter that
failed as a trace started left that trace open, and every later run in the
process joined it.
