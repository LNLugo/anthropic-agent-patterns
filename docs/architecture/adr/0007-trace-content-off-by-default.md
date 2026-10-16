# ADR 0007: Traces hold no content unless enabled

**Status:** Accepted, 2026-10-16 · **Standards:** OPS-01

## Context

Every run must be auditable, so every run is traced. Traces are read by more people than the data they describe.

## Decision

Sensitive content (prompts, documents, model output, tool inputs and results, exception messages) is recorded only
with `TRACE_INCLUDE_SENSITIVE_DATA=1`. The choice is made per field at the call site (`span.set` for metadata,
`span.content` for content). Metadata holds only identities, counts and values checked against a known format. The
default exporter writes local JSONL. The taxonomy follows openai-agents-python, which defaults to sensitive data on
and exports to OpenAI; this repo defaults to off and local.

## Alternatives rejected

- Record everything and redact on export: the content is still held, and redaction misses what it doesn't anticipate.
- No traces: no audit.

## Consequences

A planted-marker leak check runs in CI. A new kind of content needs a new marker.

## Evidence

0 of 16 planted markers reached the trace file with sensitive data off; all 16 with it on (the control). Building
the check found 3 real leaks through guardrail metadata, now fixed (10_observability).

## The strongest objection

*"How do you debug without prompts in the trace?"* Turn sensitive data on for that environment, on purpose. The
default protects production; the switch is one setting, and the leak check proves the default.
