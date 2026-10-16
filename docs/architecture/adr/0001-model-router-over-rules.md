# ADR 0001: Route with a model, keep the rules as the baseline

**Status:** Accepted, 2026-10-14 · **Standards:** AI-01, AI-07

## Context

08's assistant must decide whether a question is a count (Jira) or a knowledge question (the wiki), and extract the
count's arguments. A rules router (keywords and patterns) runs offline for free. A model can do it through tool
calling at about $0.0001 a question.

## Decision

Route with gpt-6-luna through tool calling, behind one `decide(question, today)` interface. Keep the rules router
behind the same interface as the offline baseline and for evaluation. The graph is built with one router explicitly;
nothing falls back to the rules silently.

## Alternatives rejected

- Rules only: free and deterministic, but 9 of 20 held-out questions routed correctly.
- A model with the rules as a silent fallback: hides model failures behind a second answer path that nobody measured.

## Consequences

The model is a dependency with a cost, a latency (p50 about 1.2 s) and a failure mode, so every evaluation reports it
next to the rules. Offline tests use the scripted model, so CI never needs a key.

## Evidence

Held-out set (20 questions written without seeing the prompt): the model routed 18 to 19 of 20 over three runs, the rules 9
(08_tools). That is +50 points for about $96 per million questions, or $0.00019 per extra correct route (11_finops). On
the coverage set the rules nearly match the model (14 against 13 to 15), so the model's value is concentrated where the
phrasing varies.

## The strongest objection

*"Why pay for a model when rules are free and predictable?"* Because on questions nobody tuned for, the rules are
wrong more than half the time, and a wrong count reaching an executive costs more than $0.0001. Where the model is no
better than rules, the repo says so and claims no price for it.
