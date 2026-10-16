# ADR 0008: Prices come from a file with a source, and unpriced means unknown

**Status:** Accepted, 2026-10-16 · **Standards:** OPS-02, APP-03

## Context

Cost per answer needs prices. Prices change, and a cost computed from a stale or guessed price looks exactly like a
real one.

## Decision

Every price is in `11_finops/prices.yaml` with the page it was read from and the date. A cost that includes a call to
a model not in the file is reported as unpriced, along with the model to add. It is never reported as $0 or as a
partial sum.

## Alternatives rejected

- Prices in code: go stale silently.
- Treat unknown models as free: understates spend.
- Sum what is priced: a partial total that looks complete.

## Consequences

Adding a model is a reviewed change with a citation. The evaluation scripts default to the file.

## Evidence

The file reproduces all 9 costs recorded in 08 within 0.8%. Pricing gpt-4o-mini from the file showed it costs more
per question than gpt-6-luna and routes no better (11_finops).

## The strongest objection

*"Isn't refusing to report a cost unhelpful?"* It tells you exactly what to fix: which model to price. A wrong number
gets used in a budget; a missing one gets fixed.
