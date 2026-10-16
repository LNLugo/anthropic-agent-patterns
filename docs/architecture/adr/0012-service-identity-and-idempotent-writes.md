# ADR 0012: Event work runs as a service identity, and writes carry a request id

**Status:** Accepted, 2026-10-16 · **Standards:** APP-01, INT-03

## Context

Triage starts from an event, so there's no user to act for. And a write can be retried after its response was lost.

## Decision

Event-triggered work runs as `svc-alert-triage`, a named identity with its own Jira permissions and the same guardrails
as any caller. It cannot approve its own writes. Every post carries the approval's request id as a marker (in Jira, a
comment property), and a retry finds its own comment and stops, because Jira's comment API takes no idempotency key.

## Alternatives rejected

- Run event work as an admin account: an event could reach anything.
- Retry without a marker: a lost response becomes a second comment.

## Consequences

A read before every write. An event can't widen access: an alert naming a SECOPS issue halts in the guardrail and is
dead-lettered without a retry.

## Evidence

This fixed a real fault in 08: Jira committed the comment, the response was lost, and the retried run posted it again.
The control without the marker posts 5 comments for 4 approvals; with it, 4 (12_integration, 08 test).

## The strongest objection

*"Why not an outbox?"* An outbox solves the dual write between a local database and a message. Here the second write
is to Jira itself, so the fix belongs at that write. An outbox would still need the marker to make its relay safe.
