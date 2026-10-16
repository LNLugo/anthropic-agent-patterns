# ADR 0003: Every call to Jira is made as the user

**Status:** Accepted, 2026-10-12 · **Standards:** APP-01

## Context

The assistant answers questions about issues some users may not see. Options: a service account with permissions
replicated into the agent, or calling Jira as the user and letting Jira decide.

## Decision

Every Jira call names the user it acts for, and Jira applies its own project permissions and issue security levels.
Every count is "issues visible to you", said in the answer. For retrieval (07), where the source can't be queried per
user at speed, ACLs are replicated with fail-closed gates and a reconciliation job.

## Alternatives rejected

- A service account plus a permission filter in the agent: one more copy of the access model, which drifts.
- Only the true total: would reveal that restricted issues exist.

## Consequences

Counts differ by who asks, by design. Work no person started (12's triage) runs as a named service identity, with its
own permissions (ADR 0012).

## Evidence

The same count returns 4 for one user and 3 for another (08). The retrieval leakage test returns 0 restricted chunks
with the filter on, against 224 with it off, over 64 queries (07).

## The strongest objection

*"Isn't a service account simpler?"* Simpler to build, not to keep correct. Every permission change would have to
reach the agent's copy. Delegation means nothing can drift.
