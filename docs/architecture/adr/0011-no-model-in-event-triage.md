# ADR 0011: No model in the event-triggered path

**Status:** Accepted, 2026-10-16 · **Standards:** AI-01

## Context

An outage event starts triage: count open incidents, list open work orders, propose a comment. A model could route
the work or write the comment.

## Decision

The event already names the project, asset and incident, so the decision is made in code (08's `PresetRouter`). The
numbers come from the tool. A model could later draft the comment's wording; it would not supply the numbers.

## Alternatives rejected

- A model router on the event: cost and a way to be wrong, for a decision that's already in the data.

## Consequences

The path is deterministic, testable to the exact count, and costs nothing per event.

## Evidence

Every handler run and Jira query in the measured night is accounted for (51 runs, 6 queries), which a model in the
loop would make unpredictable (12_integration).

## The strongest objection

*"Isn't this then not really an agent?"* It runs through the same agent graph, with the same guardrails, bound,
approval and trace. The rule is the repo's thesis: add autonomy when a measurement says the simpler thing falls short.
