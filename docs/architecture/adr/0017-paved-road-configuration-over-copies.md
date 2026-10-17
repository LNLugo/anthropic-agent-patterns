# ADR 0017: A new use case is configuration on a paved road, gated offline

**Status:** Accepted, 2026-10-17 · **Standards:** ENB-01, ENB-02, AI-05, AI-06, OPS-02, OPS-03

## Context

Two assistants in this repository were wired by hand: the finance assistant in 08
(85 code lines of wiring and finance facts, 73 of them in its demo) and the
transit triage in 12 (82 lines). The finance facts sat in six shared modules, so
a second use case on the model router would have meant editing them or copying
the router. Each hand-wired use case also re-decided the guardrail order, tracing
and budgets, and nothing checked that it got them right before merge.

## Decision

A use case is a folder: `use_case.yaml`, `prompts/`, `golden.yaml`, an optional
`hooks.py`, and tests generated from `templates/new_use_case/`.
`paved_road.build(folder)` assembles it from 07, 08, 09, 10 and 11. The finance
facts moved out of the shared modules into a `Catalog` that every component takes,
with finance as the default so 08 is unchanged. The build refuses configuration
that breaks a standard. Every use case has a golden set. Its offline replay runs
on every pull request and blocks the merge. The live run reports and does not block.

## Alternatives rejected

- Copy 08 per use case: every later fix to a guardrail would reach one copy.
- A plugin API with Python subclasses per use case: more power than the two use cases need, and every subclass is code to review.
- Cookiecutter for the template: a dependency for one copy and three substitutions.
- A live-model gate that blocks: needs a key in CI and makes merges depend on run-to-run variation.

## Consequences

- A use case states only what differs. Shared fixes reach every use case at once.
- The platform's surface is now a contract. A new guardrail or tool is a platform change with its own review, not a use case's edit.
- Anything the YAML cannot express goes in `hooks.py`. Both use cases need none today; if hooks grow, the schema is missing something.
- The offline gate cannot judge the model's choice. That is the live gate's job, and it only reports.

## Evidence

From `13_paved_road/results/reuse.json`, produced by `run_reuse_metrics.py`:

- **Finance on the template.** No use case code. 35 lines of configuration and prompt replace 85 hand-wired code lines.
- **Transit, built from the template.** No use case code, 31 lines of configuration and prompt, and an 11-case golden set. It reuses 4 platform modules (1,582 code lines); finance reuses 6 (2,153).
- **The gate passes.** Both use cases score 11 of 11 offline.
- **The gate blocks.** It fails a wrong expectation and a regression in shared code (the coverage check switched off). A mutation run killed all 10 mutants with the tests, 7 of them with the gate alone.
- **The cost.** The paved road itself is 616 code lines, written once.
- **Configuration checks catch real problems.** The check found that 08's demo, run with the rules router and the finance policy, refused every count: the rules quote no evidence for the coverage guardrail. The demo is fixed, and that configuration is now a build error.

## The strongest objection

"616 lines of platform to save 85 lines per use case pays back only after seven use
cases. Why not keep copying?" Lines are the smaller saving. A copied use case also
copies the decisions: guardrail order, approval, budget, tracing. Nothing checks a
copy against the standards until review, if then. On the road, a broken decision
fails the build, and a regression in shared code fails every use case's gate before
merge. The line count is reported because it is countable, not because it is the
reason.
