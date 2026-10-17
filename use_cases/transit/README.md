# Use case 2: transit operations assistant

Built from the template. Station supervisors and maintenance leads ask how many elevator, escalator and signal incidents match, and post notes to an incident once a second person approves. It reads the synthetic transit Jira that `12_integration`'s triage service writes to.

| | |
|---|---|
| Owner | Luis Lugo, Transit Operations |
| Projects | ELEV and SIG served; SECOPS restricted (halts) |
| Data | `12_integration/fixtures/transit_jira.json`; no wiki yet |
| Platform | 08 tools and guardrails, 10 tracing, 11 budget |
| Gate | `golden.yaml`, 11 cases; run `cd 13_paved_road && python run_gate.py ../use_cases/transit` |

12's triage service and this assistant share the Jira and the guardrails. The triage service runs as a service identity with no model; this assistant acts for each user, with the model router and the coverage check.
