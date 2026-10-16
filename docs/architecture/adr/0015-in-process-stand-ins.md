# ADR 0015: Stand-ins in process, mapped to managed services

**Status:** Accepted, 2026-10-16 · **Standards:** INT-01, INT-04

## Context

The patterns need a broker, a gateway, Jira and a wiki, and the repo must run offline in CI.

## Decision

In-process stand-ins behind the same small interfaces the managed services offer: receive, ack and nack for the
broker; a policy chain for the gateway; a mock Jira that applies real permission rules. Each README maps them to Azure
and AWS: Service Bus and SQS, Event Grid and EventBridge, API Management and API Gateway.

## Alternatives rejected

- Run real brokers in CI (containers): slower, flakier, and proves the broker works rather than the pattern.

## Consequences

Ordering, partitions and shared state across processes are not modelled. Nothing here depends on ordering. Rate
limits and the spend meter would need a shared store behind a load balancer.

## Evidence

Every guarantee is measured on the stand-ins with failures injected on purpose: duplicates, crashes, timeouts, poison
messages and lost responses (12_integration).

## The strongest objection

*"Would this survive a real broker?"* The consumer assumes only at-least-once delivery, which every managed broker
gives, and keys its work so that duplicates and redeliveries are harmless. The parts that need production
infrastructure are listed as limits, not hidden.
