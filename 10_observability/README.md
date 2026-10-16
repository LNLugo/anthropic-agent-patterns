# 10 Observability

One trace for every run, one span for every unit of work, and nothing
sensitive recorded unless you turn it on.

**Result:** 06, 07, 08 and 12 traced end to end, 72 spans across 16 runs.
With sensitive data off, **0 of 17** planted content markers reached the
trace file. With it on, all 17 did, so the check would have caught any of
them.
Building the check found **3 real leaks**, fixed here: guardrail metadata
was echoing the model's arguments, words quoted from the question, and the
tool bound's error text.

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

## What is traced

| Module | Trace | Spans |
|---|---|---|
| 06 Agents | `agents_06`, one per `run()` | `agent`; `generation` per decision and for the answer; `tool` per tool; `handoff` to the risk specialist |
| 07 RAG | `rag`, one per `answer_query()` | the context ledger's stages: `retrieve` as a `tool` (chunk ids only), `generate` as a `generation` (tokens), `validate` as a `guardrail` on the output (`allow`, or `reject_content` when no citation resolves) |
| 08 assistant | `assistant`, one per `ask()`; `assistant.resume` per approval decision, same `group_id` | `agent`; `generation` for the router; `guardrail` per verdict; `tool` for Jira, the wiki and the comment; a `handoff` to `human_approver` when a write waits; a `custom` `approval` span with the decision |
| 08 tool loop | `tool_loop`, one per `run()` | `generation` per turn; `tool` per call; `guardrail` per verdict, including the tool's own bound |
| 08 over MCP | `mcp.<tool>`, one per call; the assistant run joins it | a `token_scope` guardrail span (allow, or `raise_exception` for a missing scope), then the assistant's spans. The audit entry carries the `trace_id` |

The app's Agents page shows each run's trace.

The spans reconcile with what happened, and the tests check it: one
`generation` span per model call the scripted model received, one `tool` span
per Jira call, one `guardrail` span per `GuardrailRecord`, in order. The approval
handoff is emitted once, even though LangGraph re-runs the paused node on resume.

## Results

### Nothing sensitive reaches the trace file

[`run_leak_check.py`](run_leak_check.py) plants a distinct marker in every place
content comes from, runs 06, 07 and 08 offline, writes their traces with the
real JSONL exporter, and counts each marker in the file. It then runs the same
scenarios with sensitive data on, as the control.

| Content source | Markers in the file, sensitive off (default) | Sensitive on (control) |
|---|---|---|
| 06 question, model reply, bad model reply, tool result | 0 | 6 |
| 07 contract text, model answer | 0 | 3 |
| 08 question | 0 | 15 |
| 08 restricted issue key (BILL-107, in a tool result) | 0 | 3 |
| 08 wiki page title | 0 | 2 |
| 08 model clarification, final answer | 0 | 4 |
| 08 comment body, approver's reason | 0 | 5 |
| 08 model tool argument, project evidence, tool bound error | 0 | 8 |
| 12 work order text from the legacy file, quoted in a triage comment | 0 | 1 |
| **All 17 sources** | **0** | **every source found** |

16 runs, 72 spans: 16 agent, 17 generation, 24 guardrail, 9 tool, 3 handoff, 3 custom.

The 12 row was added when the [conformance check](../docs/architecture/scorecard.md)
found 12's traces had never been checked. Its first version was blind: the
comment body is traced only when the comment posts, and the scenario never
approved it, so the control found 0 even with content on. The scenario now
approves the comment, and the control finds it.
Turning sensitive data on changes what spans contain, never which spans exist.
Full counts are in [`results/leak_check.json`](results/leak_check.json).

### The three leaks the check found

The last three rows were added after the first version of the check passed.
That version had no marker where a guardrail could echo the model's
arguments, so it could not have seen the leak. With those markers added and
the fix removed, 3 markers reached the file with sensitive data off:

| Leak | What was echoed | Fix |
|---|---|---|
| `deployment_scope` metadata | the project exactly as the model sent it: any text, an injected instruction included | kept only if it has the shape of a Jira project key (`project_key`) |
| `question_coverage` metadata | the coverage reason, which quotes the model's `project_evidence`, copied from the question | a fixed code (`coverage_code`); the full reason stays in the message, which is content |
| the tool bound's verdict in the tool loop | the bound's error text, which quotes argument values | moved to content |

The rule that came out of it is in [`guardrails.py`](../08_tools/agent_tools/guardrails.py):
`output_info` is trace metadata, so it holds only identities, counts and
values checked against a known format or set. The same rule changed
`keys_match_project`: its record keeps how many stray keys came back and from
which projects, not the keys themselves, since they may be issues the caller
cannot see.

### Audit questions from the file alone

[`audit_report.py`](audit_report.py) reads only the trace files, written with
sensitive data off, and answers per identity: runs, model calls and tokens;
tools by outcome; guardrail verdicts by behaviour; writes requested, approved
and declined. It then lists every halt and every declined write.

```
sec_engineer
  guardrail raise_exception                  1
  runs error                                 1
Halts (raise_exception):
  ...  sec_engineer in assistant: restricted_project {'project': 'SECOPS', 'caller': 'sec_engineer', 'reason': 'restricted project'}
Declined writes:
  ...  BILL-101 requested by billing_oncall, declined by incident_lead
```

A tool is counted against the identity it ran as. The approved comment was
posted during the approver's resume, but it is counted against
`billing_oncall`, who requested it and as whom Jira wrote it.

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

## Run it

```bash
cd 10_observability
pip install -r requirements-test.txt
python run_leak_check.py --keep traces_demo      # offline, no API key
python audit_report.py traces_demo/sensitive_off
```

Every traced entry point (06 `run()`, 07 `answer_query()`, 08 `ask()`,
`resume()`, `ToolLoop.run()` and the MCP server) writes to `./traces` by
default, so the 08 demos and `serve_mcp.py` leave trace files there. Set
`TRACING_DISABLED=1` to turn that off. `traces/` is in `.gitignore`.

## Tests

From `10_observability`, `pytest` (14 tests): the tracing core, the leak check
with its control, and the audit report. The reconciliation tests live with the
modules they cover: `06_agents/test_agents_06_v3_1.py`, `07_rag/tests/test_traces.py`,
`08_tools/tests/test_traces.py` and `08_tools/tests/test_mcp_server.py`. Every
suite runs with an in-memory tracer, so no test writes a trace file.

Each control was checked against deliberate breakage. Each of these fails a test:

* storing content regardless of the setting
* letting an exporter's error propagate
* recording a Jira result, a guardrail's message or 06's model reply as metadata
* tracing guardrail verdicts as `custom` instead of `guardrail`
* emitting the approval handoff in the node LangGraph re-runs on resume
* letting 07's citation check always allow

The exporter mutation also exposed a real fault, fixed in the core: an
exporter that failed as a trace started left that trace open, and every later
run in the process joined it.

## Limits

* **Local only.** No OpenTelemetry or vendor exporter is included. The
  interface is one method, so adding one doesn't touch instrumented code, but
  none has been run here.
* **Tokens are only as good as the model reports.** The scripted model reports
  none, so offline runs show 0 tokens except in 07's fixture. Pricing them is
  module 11.
* **The leak check covers the content sources it plants.** A new kind of
  content needs a new marker, which is how this module found its own leaks.
* **Metadata is a judgment at each call site.** The rule (identities, counts,
  values checked against a known format or set) is written down and tested,
  but a new guardrail can still break it. The leak check is the backstop.
