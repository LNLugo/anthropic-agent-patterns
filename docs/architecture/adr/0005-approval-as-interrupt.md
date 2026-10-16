# ADR 0005: Writes pause for a person, on a durable checkpoint

**Status:** Accepted, 2026-10-15 · **Standards:** AI-06

## Context

The one write, a Jira comment, must not happen on the model's say-so, and the approval can take hours.

## Decision

The graph pauses with LangGraph's `interrupt()` and saves its state to a SQLite checkpoint. A person decides through
an approval desk, outside any agent interface. The approval node has no side effects (it re-runs on resume); the write
is its own node. The approver must not be the requester. Over MCP there is deliberately no approve tool.

## Alternatives rejected

- Approve in the same request (a confirm step): can't survive a restart or wait for someone else.
- An approve tool for agents: lets an agent approve its own write.

## Consequences

A checkpointer is required with the write path; the build refuses one without the other. Access is checked again
when the approval arrives.

## Evidence

The pending request survives a restart on a new connection (08). Self-approval is refused, for a person and for 12's
service identity.

## The strongest objection

*"Isn't human approval a bottleneck?"* For writes, it is the point. Reads never pause, and nobody is asked to approve a
write the requester couldn't make.
