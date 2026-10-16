# Architecture decision records

Each record states the decision, the options rejected, what it costs, the evidence from this repo, and the
strongest objection with its answer. Standards cite these by number ([standards](../standards.md)).

| ADR | Decision | Standards |
|---|---|---|
| [0001](0001-model-router-over-rules.md) | Route with a model, keep the rules as the baseline | AI-01, AI-07 |
| [0002](0002-typed-arguments-not-jql.md) | The model produces typed arguments; the tool writes the query | AI-02 |
| [0003](0003-delegated-permissions.md) | Every call to Jira is made as the user | APP-01 |
| [0004](0004-guardrail-behaviours-and-order.md) | Guardrails allow, coach or halt, and security runs first | AI-05 |
| [0005](0005-approval-as-interrupt.md) | Writes pause for a person, on a durable checkpoint | AI-06 |
| [0006](0006-identity-from-the-token.md) | Over MCP, identity comes only from the verified token | APP-02 |
| [0007](0007-trace-content-off-by-default.md) | Traces hold no content unless enabled | OPS-01 |
| [0008](0008-prices-from-a-sourced-file.md) | Prices come from a file with a source, and unpriced means unknown | OPS-02, APP-03 |
| [0009](0009-budgets-before-the-call.md) | Budgets are measured from the trace and checked before each call | OPS-03 |
| [0010](0010-idempotency-on-the-business-key.md) | Key the work on the incident, not the event | INT-02 |
| [0011](0011-no-model-in-event-triage.md) | No model in the event-triggered path | AI-01 |
| [0012](0012-service-identity-and-idempotent-writes.md) | Event work runs as a service identity, and writes carry a request id | APP-01, INT-03 |
| [0013](0013-offline-tests-measured-live-runs.md) | Tests run offline; model quality is measured separately | APP-04, AI-07 |
| [0014](0014-api-versions-and-sunset.md) | A breaking change is a new version, with a dated end for the old one | API-02, API-03, API-04 |
| [0015](0015-in-process-stand-ins.md) | Stand-ins in process, mapped to managed services | INT-01, INT-04 |
| [0016](0016-memory-retrieved-and-pinned.md) | Memory is retrieved by relevance, with standing instructions pinned | AI-08, APP-06 |
