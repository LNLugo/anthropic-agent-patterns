# ADR 0004: Guardrails allow, coach or halt, and security runs first

**Status:** Accepted, 2026-10-15 · **Standards:** AI-05

## Context

Tool calls need deployment policy around the tool's own bound: which projects this assistant serves, what is a
security event, whether the question is covered. Some violations are mistakes to coach; some must stop the run.

## Decision

Each guardrail returns `allow`, `reject_content` (the model is told why and gets another turn) or `raise_exception`
(the run halts with a tripwire). Guardrails run before the bound's refusal, in a fixed order: the restricted-project
check first, so an attempt on SECOPS halts instead of being redirected. The contract follows openai-agents-python's
tool guardrails; only the contract is borrowed.

## Alternatives rejected

- Fold policy into the bound: mixes the tool's contract (same everywhere) with deployment policy (differs per deployment).
- Halt on every violation: turns a fixable mistake (wrong project) into a failed run.

## Consequences

Policy lives in an ordered list per deployment (`finance_assistant_policy`, `transit_policy`). Every verdict is
recorded and traced, including `allow`.

## Evidence

Jira calls are counted with a control: with the scope guardrail, an out-of-scope query reaches Jira 0 times; without
it, once (08). The order is tested: the restricted halt fires before the scope rule could redirect.

## The strongest objection

*"Why not just block and log?"* Because a blocked call with no message leaves the model guessing, and it retries the
same call. `reject_content` tells it what to do instead, and a loop that keeps trying runs out of turns, not into Jira.
