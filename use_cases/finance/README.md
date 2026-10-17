# Use case 1: finance engineering assistant

The assistant from `08_tools`, moved onto the paved road. Billing and payments engineers ask how many Jira issues match, find wiki pages they are allowed to open, and post notes to an issue once another person approves.

| | |
|---|---|
| Owner | Luis Lugo, AI Engineering |
| Projects | BILL and PAY served; INFRA known but out of scope; SECOPS restricted (halts) |
| Data | `08_tools/tests/fixtures/mini_jira.json`, `07_rag/tests/fixtures/mini_confluence.json` |
| Platform | 07 retrieval, 08 tools and guardrails, 09 memory, 10 tracing, 11 budget |
| Gate | `golden.yaml`, 11 cases; run `cd 13_paved_road && python run_gate.py ../use_cases/finance` |

What is specific to this use case is `use_case.yaml`, `golden.yaml` and two lines in `prompts/router.md`. Everything else comes from the platform.
