# Design: a paved road for new agent use cases

> **Status:** design only. Nothing here is built yet. Each section names the
> evidence that will show it works, and the build will mark it built.

## The problem

Every use case in this repository so far was wired by hand. The finance
assistant in 08 assembles its own tool, guardrail policy, router and approval
path. The transit triage in 12 assembles them again, in 82 more lines of glue.
Each one re-decides things the platform should decide once: how to trace, how to
budget, which guardrails run in which order, how to evaluate.

Worse, the first use case is baked into the shared code. 08's finance specifics
sit in six places in the modules meant for reuse:

| Where | What is hard-coded |
|---|---|
| `08_tools/agent_tools/router.py` | project aliases ("billing", "payments", "infra") |
| `08_tools/agent_tools/coverage.py` | the known project codes |
| `08_tools/agent_tools/model_router.py` | project display names, which shape the prompt |
| `08_tools/agent_tools/jql_tool.py` | the tool's default allowlist |
| `08_tools/agent_tools/assistant.py` | the clarifying question ("Billing Platform, Payments or Infrastructure?") |
| `08_tools/agent_tools/guardrails.py` | the finance policy (BILL and PAY) |

12's triage avoided these only by bypassing the model router. A transit use
case on the model router would hit all six: the coverage check doesn't know ELEV
or SIG exists.

## Goal

A team with a new use case declares what is different about it, and inherits
everything else: retrieval (07), tools, guardrails and approval (08), memory
(09), tracing (10), cost and budgets (11), and an evaluation gate in CI. A use
case is configuration, prompts, a golden set and minimal glue, not a new copy of
the platform.

**Out of scope:** a UI, multi-tenant hosting, and new capabilities. The paved
road only composes what is built.

## What a use case declares

```text
use_cases/<name>/
├── use_case.yaml      who owns it, its data, tools, policy, router, memory, budget, gate thresholds
├── prompts/           the use case's wording: system prompt additions, clarifying questions
├── golden.yaml        the cases its evaluation gate runs
├── hooks.py           optional glue: only what config can't express
└── tests/             generated from the template; run the gate offline
```

`use_case.yaml`, by section:

| Section | Declares | Composed from |
|---|---|---|
| `owner` | a person and a team | the COE exception and review records |
| `catalog` | projects or spaces, display names, aliases, which are restricted | replaces the six hard-coded places above |
| `identity` | per-user (delegated) or a named service identity | 08, 12 |
| `tools` | count, comment (with approval), wiki search | 07, 08 |
| `policy` | guardrails in order, from the platform's set | 08 |
| `router` | `rules` or `model`, and the model's name | 08, `llm_provider` |
| `memory` | a strategy and retention, or none | 09 |
| `budget` | per-identity daily limit and reserve | 11 |
| `gate` | thresholds for the offline and live evaluations | this design |

Tracing (10) is not optional and not configured. Every use case is traced, with
content off.

## What the platform provides

A `paved_road` package with one entry point:
`build(use_case_dir) -> assistant`. It reads the YAML and assembles the 08
assistant graph with:
- the catalog;
- the declared tools and the bounded tool's allowlist;
- the guardrail policy, in order;
- the router;
- approval with a checkpointer, if a write is declared;
- memory, if declared;
- the budget.

It refuses a configuration that breaks a standard. For example, a write without
approval (AI-06), a model missing from the price file (OPS-02), or a policy
without the restricted-project check first (AI-05) is a build error, not a
review finding.

The six hard-coded places become one `Catalog` passed through. The finance
catalog stays the default, so 08's existing tests pass unchanged.

## The evaluation gate

Each use case's `golden.yaml` lists cases:

```yaml
- id: count-open-elev
  as: station_supervisor
  question: "How many open ELEV incidents since October 1?"
  reference_call: {tool: count_jira_issues, args: {project: ELEV, status: Open, created_from: 2026-10-01, created_before: 2026-10-17}}
  expect: {ended: answered, count: 3}
- id: secops-attempt
  as: svc-alert-triage
  question: "How many SECOPS incidents this month?"
  reference_call: {tool: count_jira_issues, args: {project: SECOPS, ...}}
  expect: {ended: halted, guardrail: restricted_project}
```

Two modes, because a model can't run in CI without a key:

| Mode | Runs | What it gates | Where |
|---|---|---|---|
| Offline | the reference call through the real pipeline: bound, guardrails, coverage, permissions, approval, Jira | everything except the model's choice | every pull request; **blocks merge** below the threshold |
| Live | the configured model chooses the call | the model's choice, scored against the reference call | on demand or nightly, with the API key as a secret; reports, doesn't block |

The offline threshold defaults to 100%. Every layer it runs is deterministic,
so anything less is a regression. The live threshold is per use case, starting
at the measured held-out level (08 routed 18 to 19 of 20).

Blocking a merge needs the CI job marked as a required check in the GitHub
repository settings. The repository owner sets that; the workflow can't.

## Measuring reuse: real counts only

| Metric | How it is counted |
|---|---|
| New lines for use case 2 against use case 1 | Code lines in each use case's folder (non-blank, not comments or docstrings, by the same counter), YAML and prompt lines counted separately. Use case 1, before the paved road, is the finance assistant's specific code, wherever it sits today, including the six places above |
| Shared modules reused against written new | Platform modules each use case imports, with their code lines, against modules written for the use case |
| Clone to green | A clean clone, a new virtual environment, the use case's requirements and its tests, timed end to end on one machine; plus the CI job's duration from GitHub Actions |

For scale, the shared modules a use case would reuse, counted with that counter
today: 07 retrieval 846 lines, 08 tools and policy 1,360, 09 memory 203,
10 tracing 245, 11 FinOps 213, 12 integration 357 and gateway 336, and
`llm_provider` 192.

No figure goes in the README until it is counted from the repository, the
same rule the conformance check enforces for adoption.

## Build order

1. Extract the catalog from 08, with the finance catalog as the default; 08's tests unchanged.
2. `paved_road.build` and the template in `templates/new_use_case/`, with a scaffold script.
3. Use case 1, the finance assistant, moved onto the template: this proves the template fits what exists.
4. Use case 2, transit operations (12's Jira, ELEV and SIG), built from the template.
5. The offline gate in CI per use case; the live gate as a manual workflow.
6. The reuse metrics, counted, in the README.

## Decisions

Taken on 2026-10-16:

1. **Use case 1 is the finance assistant, counted before and after.** "Before" is its specific code as hand-built, spread across 08. "After" is the same assistant on the template. This shows what the paved road removed, and that the template fits what exists. Comparing two template-built use cases would measure the template, not the reuse it enables.
2. **The offline gate blocks merges; the live gate reports.** Blocking on a live model run would need a key in CI and make merges depend on run-to-run variation.
3. **A standard-library scaffold script.** Cookiecutter would add a dependency for little gain at this size.

## Evidence that will show it works

- **Reuse:** 08's tests pass unchanged after the catalog is extracted.
- **Generality:** both use cases run on `paved_road.build` with no use-case code in shared modules. A static check fails if a project code appears in shared code.
- **The gate:** it blocks a pull request that breaks a golden case (tested with a deliberate breakage).
- **The metrics:** the three counts above, from the counter.
