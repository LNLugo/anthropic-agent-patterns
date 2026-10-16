# Design review checklist

Used at design review for any change that adds an agent, a tool, a write, a model,
an external integration or an API version (the triggers are in the
[COE charter](coe-charter.md#when-a-review-is-required)). Each question maps to the
[standards](standards.md) it checks. "Bring" is the evidence the team brings; a
claim without it is an open action, not a pass.

An answer of "no" is not a rejection. It is either a design change before build,
or an [exception](standards.md#exception-register) with an owner, a compensating
control and an expiry.

## Shape of the solution

| # | Question | Standards | Bring |
|---|---|---|---|
| 1 | Is this the simplest pattern that reliably works? What did the simpler option score? | AI-01 | The baseline's number next to the proposal's |
| 2 | Where a model is used, what does it decide that code could not? | AI-01, AI-02 | The decision the model makes, and the arguments it fills |
| 3 | Does every run end in a stated outcome, including "I can't"? | AI-03 | The list of end states and a test for each |
| 4 | When the evidence is missing, does it abstain or ask, rather than guess? | AI-04 | A test where the evidence is absent |

## Identity and access

| # | Question | Standards | Bring |
|---|---|---|---|
| 5 | As whom does each call run? Is there a named identity for work no person started? | APP-01 | The identity per call path |
| 6 | Can any argument, prompt or event change who the caller is? | APP-02 | The tool and route signatures; a test that names another user |
| 7 | Are permissions the source system's own, or a copy? If a copy, how is it reconciled? | APP-01 | The reconciliation job and its test |
| 8 | What happens on an unknown permission, model or input? | APP-03 | A fail-closed test for each |

## Actions and policy

| # | Question | Standards | Bring |
|---|---|---|---|
| 9 | Which actions change data? Who approves each, and can the requester? | AI-06 | The approval flow and the self-approval test |
| 10 | Does a pending approval survive a restart? Does a retry write twice? | AI-06, INT-03 | The restart test and the lost-response test |
| 11 | Which guardrails apply, in what order, and which halt rather than coach? | AI-05 | The policy list and the order test |

## Operations and cost

| # | Question | Standards | Bring |
|---|---|---|---|
| 12 | Is every run traced? What content could reach the trace? | OPS-01 | The span list and leak-check markers for any new content |
| 13 | Is every model priced from the file, with a source? | OPS-02 | The prices.yaml entry |
| 14 | Is each identity's model spend checked before the call? | OPS-03 | The budget wiring and a refusal test |
| 15 | What does a correct answer cost, against the baseline? | AI-07, OPS-02 | Cost per correct answer on the held-out set |

## Interfaces and integration

| # | Question | Standards | Bring |
|---|---|---|---|
| 16 | Is the interface behind the gateway, with a scope per route? | API-01 | The route table |
| 17 | Is the contract committed, and is any change breaking? If so, what is the new version and the old one's sunset? | API-02, API-03, API-04 | The contract diff and the gate's output |
| 18 | Do errors avoid revealing what the caller can't see? | API-05 | The not-found test for a hidden resource |
| 19 | Do events enter through one envelope? What is the business key for idempotency? | INT-01, INT-02 | The key, and a test with two sources |
| 20 | Which failures retry and which dead-letter at once? Can a dead letter be replayed? | INT-04 | The error classification and the replay test |
| 21 | For batch files and webhooks: control totals checked? Signatures verified before parsing? | INT-05, INT-06 | The truncated-file and forged-signature tests |

## Build

| # | Question | Standards | Bring |
|---|---|---|---|
| 22 | Do the tests run with no API key? Does every "never happens" test have a run where it does? | APP-04 | The control for each zero |
| 23 | Does the component get its model through `llm_provider`? | APP-05 | The import |

## Outcome

| Result | Meaning |
|---|---|
| Approved | Every applicable question answered with its evidence |
| Approved with actions | Open items with owners and dates, none touching identity, approval or fail-closed behaviour |
| Approved with exception | A standard not met, registered with an owner, a compensating control and an expiry (at most six months) |
| Not approved | A gap in identity, approval or fail-closed behaviour with no compensating control |
