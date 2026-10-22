# Standards conformance scorecard

<!-- Written by conformance.py --write. Do not edit by hand. -->

Computed from the repository: 30 standards, 85 standard-module pairs.

| Status | Pairs | Meaning |
|---|---|---|
| Tested | 69 | Named tests enforce it for that module |
| Checked | 8 | A static check over the module's code passes |
| Excepted | 5 | Not met; a registered exception owns the gap |
| Attested | 3 | Enforced by design review only |
| Gap | 0 | Not met and not registered |

**Conforming: 77 of 85 pairs (91%).** Attested pairs are not counted as conforming.

## By module

| Module | Applicable | Conforming | Excepted | Attested | Gaps |
|---|---|---|---|---|---|
| 01_prompt_chaining | 1 | 1 (100%) | 0 | 0 | 0 |
| 02_routing | 1 | 1 (100%) | 0 | 0 | 0 |
| 03_parallelization | 1 | 1 (100%) | 0 | 0 | 0 |
| 04_orchestrator_workers | 1 | 1 (100%) | 0 | 0 | 0 |
| 05_evaluator_optimizer | 1 | 1 (100%) | 0 | 0 | 0 |
| 06_agents | 6 | 4 (67%) | 1 | 1 | 0 |
| 07_rag | 9 | 6 (67%) | 3 | 0 | 0 |
| 08_tools | 17 | 15 (88%) | 1 | 1 | 0 |
| 09_memory | 4 | 4 (100%) | 0 | 0 | 0 |
| 10_observability | 1 | 1 (100%) | 0 | 0 | 0 |
| 11_finops | 3 | 3 (100%) | 0 | 0 | 0 |
| 12_integration | 20 | 19 (95%) | 0 | 1 | 0 |
| 13_paved_road | 8 | 8 (100%) | 0 | 0 | 0 |
| 14_knowledge_graph | 7 | 7 (100%) | 0 | 0 | 0 |
| 15_document_control | 5 | 5 (100%) | 0 | 0 | 0 |

## By standard

| Standard | 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 | 09 | 10 | 11 | 12 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AI-01 |  |  |  |  |  | review |  | review |  |  |  | review |  |  |  |
| AI-02 |  |  |  |  |  |  |  | tested |  |  |  |  |  |  |  |
| AI-03 |  |  |  |  |  | tested |  | tested |  |  |  |  |  |  |  |
| AI-04 |  |  |  |  |  |  | tested | tested |  |  |  |  |  | tested |  |
| AI-05 |  |  |  |  |  |  |  | tested |  |  |  | tested | tested |  |  |
| AI-06 |  |  |  |  |  |  |  | tested |  |  |  | tested | tested |  |  |
| AI-07 |  |  |  |  |  |  | exception | tested |  |  |  |  |  | tested |  |
| AI-08 |  |  |  |  |  |  |  |  | tested |  |  |  |  |  |  |
| APP-01 |  |  |  |  |  |  | tested | tested |  |  |  | tested |  | tested |  |
| APP-02 |  |  |  |  |  |  |  | tested |  |  |  | tested |  |  |  |
| APP-03 |  |  |  |  |  |  | tested |  |  |  | tested | tested |  | tested | tested |
| APP-04 |  |  |  |  |  | tested | tested | tested | tested | tested | tested | tested | tested | tested | tested |
| APP-05 | checked | checked | checked | checked | checked | checked | exception | checked |  |  |  |  |  | checked |  |
| APP-06 |  |  |  |  |  |  |  |  | tested |  |  |  |  |  |  |
| OPS-01 |  |  |  |  |  | tested | tested | tested | tested |  |  | tested | tested | tested |  |
| OPS-02 |  |  |  |  |  |  | tested | tested |  |  | tested |  | tested |  |  |
| OPS-03 |  |  |  |  |  | exception | exception | tested |  |  |  | tested | tested |  |  |
| API-01 |  |  |  |  |  |  |  | exception |  |  |  | tested |  |  |  |
| API-02 |  |  |  |  |  |  |  |  |  |  |  | tested |  |  |  |
| API-03 |  |  |  |  |  |  |  |  |  |  |  | tested |  |  |  |
| API-04 |  |  |  |  |  |  |  |  |  |  |  | tested |  |  |  |
| API-05 |  |  |  |  |  |  |  | tested |  |  |  | tested |  |  |  |
| INT-01 |  |  |  |  |  |  |  |  |  |  |  | tested |  |  | tested |
| INT-02 |  |  |  |  |  |  |  |  |  |  |  | tested |  |  | tested |
| INT-03 |  |  |  |  |  |  |  | tested |  |  |  | tested |  |  |  |
| INT-04 |  |  |  |  |  |  |  |  |  |  |  | tested |  |  | tested |
| INT-05 |  |  |  |  |  |  |  |  |  |  |  | tested |  |  |  |
| INT-06 |  |  |  |  |  |  |  |  |  |  |  | tested |  |  |  |
| ENB-01 |  |  |  |  |  |  |  |  |  |  |  |  | tested |  |  |
| ENB-02 |  |  |  |  |  |  |  |  |  |  |  |  | tested |  |  |

## Exceptions

| ID | Standard | Modules | Expires | Found by the check |
|---|---|---|---|---|
| EXC-001 | APP-05 | 07 | 2026-12-31 | `07_rag/rag/graph.py:74`, `07_rag/rag/graph.py:88` |
| EXC-002 | API-01 | 08 | 2027-03-31 | n/a |
| EXC-003 | OPS-03 | 06, 07 | 2027-03-31 | n/a |
| EXC-004 | AI-07 | 07 | 2027-04-16 | n/a |
