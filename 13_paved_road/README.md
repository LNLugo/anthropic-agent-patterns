# 13 The paved road: new use cases from configuration

A new agent use case is a folder: `use_case.yaml`, `prompts/`, a golden set and, only if needed, `hooks.py`. `paved_road.build(folder)` assembles the platform around it:
- retrieval (07);
- the bounded tool, guardrails, coverage check and approval (08);
- memory (09);
- tracing with content off (10);
- prices and a budget (11).

An evaluation gate replays the golden set through the real pipeline on every pull request and blocks the merge below its threshold.

Design: [docs/designs/paved-road.md](../docs/designs/paved-road.md). Decision: [ADR 0017](../docs/architecture/adr/0017-paved-road-configuration-over-copies.md).

```bash
cd 13_paved_road
python new_use_case.py claims --person "A. Owner" --team "Claims Ops"   # copy the template
python run_gate.py ../use_cases/claims                                   # green before you edit anything
python run_gate.py                                                       # every use case, offline (CI)
python run_gate.py --mode live                                           # the model chooses; needs a key; reports
python run_reuse_metrics.py                                              # the numbers below
```

## Two use cases

| | Use case 1: [finance](../use_cases/finance/) | Use case 2: [transit](../use_cases/transit/) |
|---|---|---|
| What | 08's finance assistant, moved onto the template | Built from the template on 12's transit Jira |
| Projects | BILL, PAY served; INFRA out of scope; SECOPS restricted | ELEV, SIG served; SECOPS restricted |
| Tools | count, wiki search, comment with approval | count, comment with approval |
| Memory | retrieve with pins, 30-day retention | none |
| Golden set | 11 cases | 11 cases |
| Offline gate | 11 of 11 | 11 of 11 |

Each golden set covers the behaviours a reviewer asks about. Both include:
- an answer;
- the same question by users with different Jira permissions;
- an out-of-scope or restricted project;
- a question naming two projects;
- no project at all;
- a write approved by another person;
- the same write approved by the requester.

## Reuse, counted

All figures are from [results/reuse.json](results/reuse.json), written by `run_reuse_metrics.py`. Code lines are non-blank lines that are not comments or docstrings.

**New lines per use case**

| | Code | Configuration and prompt | Golden set |
|---|---|---|---|
| Finance, hand-built in 08 | 85: the demo's wiring (73), the finance catalog that sat in six shared modules (10), its policy (2) | n/a | none: 08's tests, not a per-use-case gate |
| Finance on the paved road | 0 | 35 | 11 cases |
| Transit on the paved road | 0 | 31 | 11 cases |

For comparison, 12's event-driven triage was the second hand-built assistant: 82 code lines of glue. It still bypassed the model router, because the router knew only the finance projects.

**Platform reused, measured by import.** Each use case's gate runs in a fresh process, and only the platform files it actually imports are counted:

| | Finance | Transit |
|---|---|---|
| Platform modules reused | 6: 07, 08, 09, 10, 11, `llm_provider` | 4: 08, 10, 11, `llm_provider` |
| Platform code lines reused | 2,153 | 1,582 |
| Code written for the use case | 0 | 0 |

**Written once.** The paved road is 616 code lines:
- `paved_road`: 480;
- the `Catalog` class: 61;
- the scaffold and gate scripts: 75.

On lines alone, that pays back after about seven use cases at 85 lines each. The larger saving is that a use case cannot get the controls wrong: the build refuses a configuration that breaks a standard, and the gate fails every use case when shared code regresses.

**Time to green**

| | Seconds | What was timed |
|---|---|---|
| New use case to a passing gate | 1.3 | `new_use_case.py`, then `run_gate.py`, on a machine already set up |
| Clean clone to green | 40.5 | clone 0.2, new venv 2.5, install 24.7 (no pip cache), tests 10.4, gate 2.1; Linux x86_64, Python 3.13 |

Install time depends on the network. An earlier run of the same steps took 193.6 s, 176.5 of them installing. Everything except the install took 15 to 17 s both times.

The CI job's duration will come from GitHub Actions after the first run. It is not recorded here yet.

## What the build refuses

Each refusal is a build error, before anything runs. Each one is tested in [tests/test_paved_road.py](tests/test_paved_road.py).

| Configuration | Standard |
|---|---|
| A comment tool without `approval: required` | AI-06 |
| A policy that does not start with `restricted_project` | AI-05 |
| A model missing from `11_finops/prices.yaml` | OPS-02 |
| A model router with no budget | OPS-03 |
| Memory with no retention period | APP-06 |
| A service identity with no id | APP-01 |
| `question_coverage` without a model router quoting evidence | AI-04 |
| Any unknown key, including a `tracing:` section (tracing is not configurable) | APP-03, OPS-01 |
| One alias for two projects; a project both restricted and queryable | catalog integrity |

The `question_coverage` rule came from a real failure. 08's demo with the rules router and the finance policy refused every count, because the rules quote no evidence for the guardrail to check. The demo is fixed, and the paved road makes that combination a build error.

## The gate

| Mode | Model | Runs | Gates |
|---|---|---|---|
| Offline | A scripted stand-in replays each case's `reference_call` | Everything after the model's choice, for real: the router's parsing and coverage check, the bound, the guardrails in order, Jira permissions, the budget, approval, the post | Every pull request (the "Evaluation gate" job). Blocks below the threshold, 100% by default |
| Live | The configured model | The same pipeline, and whether the model made the reference call (free text such as a search query or comment body is not compared) | On demand or nightly ([eval-live.yml](../.github/workflows/eval-live.yml)), with `OPENAI_API_KEY` as a secret. Reports only. Not run yet: no key in this environment |

Evidence that the gate blocks:
- **A wrong expectation fails it,** with the case and the difference named.
- **A shared-code regression fails it.** With the coverage check switched off in 08, the finance gate fails `two-projects-asks`; nothing in the use case changed.
- **`run_gate.py` exits 1 below the threshold,** and 0 at it.
- **Mutation testing.** Ten mutations to the build, the catalog threading and the gate were killed by the tests, and seven of them by the gate alone. The other three sit behind the router's own coverage check (the guardrail copy), in the rules router, or in a fallback message; each has its own test.

**The repository owner must mark "Evaluation gate" as a required status check in branch protection.** A workflow cannot set that itself.

## Generality

- **No project code in shared code.** A static check reads every string constant in 08's `agent_tools` and in `paved_road`, docstrings excluded. It fails if any project code from any catalog appears. The one place allowed is `catalog.py`, whose finance default keeps 08's 150 tests unchanged. The check has a control: a planted code is found.
- **The check found a real leak.** The comment tool told every model "for example BILL-107", so the transit model was shown a finance project. The example is now built from the deployment's own projects.

## Limits

- The data is fixtures: 08's and 12's synthetic Jira and 07's synthetic wiki. Production swaps in the real clients behind the same interfaces.
- The schema covers what two use cases needed: Jira counts, comments and wiki search. A use case with a different tool is a platform change, reviewed as one.
- The live gate has not been run, so there is no live score yet.
