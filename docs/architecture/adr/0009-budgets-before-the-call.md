# ADR 0009: Budgets are measured from the trace and checked before each call

**Status:** Accepted, 2026-10-16 · **Standards:** OPS-03

## Context

Spend must be held to a limit per identity, not reported afterwards.

## Decision

A `SpendMeter` is a trace exporter: it prices each generation span as it ends, per identity per day. Before every model
call, the policy checks the identity's spend plus a reserve (the expected cost of the next call) against the limit. Over
budget is `reject_content` with no model call, not a halt. Unpriced models are refused (fail closed). 08 takes the
budget through a one-method protocol, so 08 doesn't depend on 11.

## Alternatives rejected

- Count requests instead of spend: a cheap and an expensive call count the same.
- Check after the call: the call that crosses the limit has already been paid for.

## Consequences

The reserve is a setting; a call costing more than it can pass the limit by the difference. The meter is in memory
and rebuilt from the day's traces.

## Evidence

A $0.001 daily budget at $0.0000923 a call answered 10 and refused from the 11th with no model call; checking without
the reserve would have ended the day at $0.001015. A looping model was stopped mid-run after 2 calls instead of 4
(11_finops).

## The strongest objection

*"Why not just use the provider's spend limits?"* They work per account, not per user, and they act after the money
is spent. This one acts per identity, before the call.
