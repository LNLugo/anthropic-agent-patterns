# ADR 0010: Key the work on the incident, not the event

**Status:** Accepted, 2026-10-16 · **Standards:** INT-02

## Context

Events arrive at least once, and one outage arrives twice from two sources: a service alert and Jira's webhook.

## Decision

Two levels. The consumer skips an event it has handled (by source and id) in a durable store. Triage keys its work on
the incident: the approval thread is `triage-<incident>`, so a second event about the same outage finds the work
already started.

## Alternatives rejected

- Event deduplication only: two sources become two triages.
- Broker-side duplicate detection (Service Bus, SQS FIFO): works only within a time window, and per message id.

## Consequences

The store becomes an optimisation, not the guarantee: it saves repeated handler runs.

## Evidence

One measured night: 4 outages, 4 approval requests, 4 comments. Keyed on events: 8 and 8, with 10 Jira queries
instead of 6. Without the store: still 4 and 4, with 12 extra handler runs (12_integration).

## The strongest objection

*"Isn't the event store enough?"* Only if every real-world thing arrives as exactly one event from exactly one source.
It doesn't, and the approver gets the duplicate work.
