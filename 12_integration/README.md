# 12 Integration

Agents started by events, not only by people: from a legacy batch file, a SaaS
webhook and a cloud event bus. Delivered at least once, handled exactly once,
with every bad input kept and explained rather than dropped or guessed. In
front of it all, a gateway with one set of policies and a versioned API whose
contract is checked in CI.

## The scenario

A fictional transit agency's operations team. Jira is its incident system.

```
legacy    nightly fixed-width work-order file (asset management)  --> workorder.upserted --+
SaaS      Jira webhook, HMAC-signed                                --> incident.created ---+--> broker --> consumer
cloud     service alerts on an event bus                           --> alert.raised -------+      |
                                                                                                  v
             triage, as svc-alert-triage: count open incidents with 08's bounded tool, list the asset's
             open work orders, propose a comment on the incident --> a person approves --> posted once
```

One envelope for all three sources (CloudEvents 1.0 attribute names: `id`,
`source`, `type`, `subject`, `time`, `data`), so the consumer never parses a
vendor's format.

| Source | Style | What the adapter enforces |
|---|---|---|
| Asset management, nightly file | Legacy batch, fixed-width | Header and trailer control totals: a truncated file is rejected whole, because loading part of a night would look like a quiet night. A bad record is rejected alone, with its line number and raw text |
| Jira | SaaS webhook, pushed | HMAC-SHA256 signature over the raw body, compared in constant time, before the body is parsed: unsigned, forged and altered requests are refused |
| Service alerts | Cloud-native event bus | Required fields and known severities |

## Design decisions

**The worker runs as a service identity.** No person asked a question, so there
is no user to act for. `svc-alert-triage` has its own Jira permissions and gets
the same guardrails and bound as any caller. Its one write, a comment, waits for
a person, and it can never approve its own request (tested). An event cannot
widen what the service can reach: an alert naming a SECOPS issue halts in the
guardrail.

**No model in the loop.** The event already says what to count and where to
write. A model router would add cost and a way to be wrong, so the decision is
made in code (08's `PresetRouter`). The repo's rule applies to integration
too: use the simplest thing that reliably works.

**Idempotency at two levels.** The consumer skips an event it has already
handled, by source and id, in a durable store. Triage also keys its work on the
**incident**, not the event: the approval thread is `triage-<incident>`, so an
alert and a webhook about the same outage are two events and one triage.

**Errors are sorted by whether retrying can help.** A malformed message, a
missing handler or a guardrail halt goes to the dead-letter queue at once. Any
other error is retried with exponential backoff, then dead-lettered after 4
attempts. One poison message never blocks the ones behind it (tested). A
dead letter keeps the event or the raw input and the reason, and can be
replayed, once, after the cause is fixed.

## Results

[`run_integration_check.py`](run_integration_check.py) runs one night through
the whole pipeline with failures injected on purpose. The inputs:

- a work-order file of 40 records, 2 of them malformed
- 4 outages, each arriving twice (a service alert and Jira's webhook)
- a forged alert pointing at a SECOPS issue
- an alert for a project this deployment doesn't serve

Then 12 of the 48 messages are redelivered and the consumer process dies
mid-handling. Jira times out twice, a person approves every triage comment,
and the first post's response is lost. Each guarantee is measured against a
control without it:

| Run | Approval requests | Comments posted | Handler runs | Jira queries |
|---|---|---|---|---|
| **The design** | **4** | **4** | 51 | 6 |
| No idempotency store | 4 | 4 | 63 | 6 |
| Triage keyed on the event, not the incident | 8 | 8 | 51 | 10 |
| No request id on the post (08 before this module) | 4 | **5** | 51 | 6 |

Every number is accounted for, and the test pins each one:
- **Handler runs:** 51 = 48 messages + 2 retries + 1 redelivery after the crash.
- **Jira queries:** 6 = one per outage plus the 2 timeouts.
- **Dead letters:** 4, each with its reason: `line 13: unknown status 'XX'`,
  `line 25: opened date '20261399' is not a real date`,
  `halted by guardrail restricted_project`, and the out-of-scope refusal.
  None were retried.

What it shows:

* **The incident key is what makes triage exactly once; the event store only
  saves work.** Without the store, duplicates run the handler again (12 extra
  runs) but find the triage already started and stop, so no extra request and
  no extra Jira query.
* **Deduplicating events is not enough when one thing has several sources.**
  Keyed on events, two sources gave the approver 8 requests for 4 outages and
  sent Jira 10 queries instead of 6.
* **A retry after a lost response posted twice, in code already merged.** This
  was a real fault in 08's approval path. Jira committed the comment, the
  response was lost, and the service retried the run from its checkpoint, so
  one approval became two comments. Every post now carries the approval's
  request id as a marker (in Jira, a comment property), and a retry finds its
  own comment and stops. Jira's comment API takes no idempotency key, so the
  check is made before every post. 08 has a test for it.

## The gateway

[`gateway/app.py`](gateway/app.py) puts every request through the same
policies, in the same order, before any agent runs:

| Step | Policy | Refusal |
|---|---|---|
| 1 | Identity from the bearer token (08's verifier) | 401 with `WWW-Authenticate` |
| 2 | Version lifecycle: v1 carries `Deprecation`, `Sunset` and `Link` headers; after its sunset date it is gone | 410, naming the successor |
| 3 | The route's scope must be on the token: `jira:read` for counts and triage status, `model:invoke` for the one route that calls a model | 403 |
| 4 | Rate limit, a token bucket per subject (10 a minute, bursts of 10) | 429 with `Retry-After` |
| 5 | The contract: query and body checked against the route's schema, unknown parameters refused | 400 |
| 6 | The agent runs as the caller: 08's guardrails, bound and Jira permissions | 422 if a guardrail refuses |
| 7 | On the model route, 11's budget before the model call | 429, `budget-exceeded` |

Errors are `application/problem+json` (RFC 9457). Every request is one trace
for the identity that made it: the agent run inside joins the request's trace,
and the response carries `x-trace-id`. So 11's meter charges a model call to the
caller, and an audit sees a refused request like any other.

The API-led layers, as this repo has them:
- **System API:** 08's bounded Jira tool.
- **Process API:** triage, which combines Jira, the work-order file and approvals.
- **Experience API:** the gateway's versioned routes.

| Route | Scope | What it does |
|---|---|---|
| `GET /v2/incidents/count` | `jira:read` | Incidents by project, creation window and status, as the caller can see them |
| `GET /v2/triage/{issue_key}` | `jira:read` | Where an incident's automated triage stands: none, pending approval, posted or declined. An incident the caller can't see is a 404, so the route doesn't reveal it exists |
| `POST /v2/ask` | `model:invoke` | A model routes a free-text question, within the caller's budget |
| `GET /v1/incidents/count` | `jira:read` | Deprecated: a fixed 30-day window the client can't see or change |

What the tests show, with every response checked against the published
contract (a 200 against its route's schema, anything else as problem+json):

* **A burst of 25 requests** from one subject: 10 answered, 15 refused with
  `Retry-After: 6`. Another subject is unaffected, and 6 seconds later the
  first is answered again.
* **The model route, with a budget for 3.5 calls** at gpt-6-luna's held-out
  mean: 3 answered, then 429 with no model call. The meter charged the caller
  exactly 3 calls.
* **Requests the contract refuses never reach an agent:** an unknown `user`
  parameter, a missing date, project `SECOPS`, or a date in the wrong format
  produces no tool span and no guardrail span.
* **The gateway doesn't widen anything:** the contractor's count through the
  gateway is 0, the same as Jira gives them directly.

## API lifecycle

| Stage | How it is held |
|---|---|
| Design | Routes are described once ([`gateway/routes.py`](gateway/routes.py)). The OpenAPI 3.1 contract, the handlers and the checks all come from that description |
| Publish | The contracts are committed ([`contracts/`](contracts/)) and served at `/v1/openapi.json` and `/v2/openapi.json`. A test fails if the served contract and the committed file differ, so every API change shows up in review as a contract diff |
| Change | [`run_contract_check.py`](run_contract_check.py) lists every way a client of the old contract could fail against the new one. In CI, a pull request's v2 contract is compared against `main`'s, and a breaking change fails the build |
| Deprecate | v1 is marked `deprecated` in its contract and sends `Deprecation: @1790812800` (1 October 2026) (RFC 9745), `Sunset: Sun, 31 Jan 2027 00:00:00 GMT` (RFC 8594), and `Link` headers to its successor and this policy |
| Retire | From the sunset date, v1 answers 410 and names its successor |

**Why v1 to v2 is a new major version.** The checker finds exactly two breaking
changes: `created_from` and `created_before` are new required parameters. v1's
hidden 30-day window couldn't be kept compatibly, so v2 is a new version and
v1 has a published end.

**What counts as breaking**, from the client's side:

| Request | Response |
|---|---|
| an operation removed | a property removed |
| a parameter removed, or a required one added | a property no longer always present |
| a parameter made required, or its type changed | a property's type changed |
| an allowed value removed | a new enum value (a client switching on it meets a value it never saw) |
| a body property made required, or no longer accepted | |

Adding an operation, an optional parameter, an allowed value or a response
property is not breaking.

On 14 labeled edits to v2 (10 breaking, 4 safe), the checker classifies all
14 correctly. The same person wrote the edits and the checker, so treat this
as a regression suite, not an independent evaluation. The CI gate on real pull
requests is the check that counts.

## Mapping to managed services

The broker here is in process, with a clock the tests control. The consumer
uses three calls, receive, ack and nack, which managed brokers offer:

| Here | Azure | AWS |
|---|---|---|
| Broker, at least once, redelivery after a crash | Service Bus queue, peek-lock | SQS, visibility timeout |
| Dead-letter after N attempts | Service Bus max delivery count and dead-letter subqueue | SQS redrive policy, `maxReceiveCount` |
| Event bus for service alerts | Event Grid | EventBridge |
| Idempotency store | a table keyed on source and id | a DynamoDB conditional write |
| Gateway: identity, scopes, rate limits, versions | API Management: `validate-jwt`, `rate-limit-by-key`, API versions and revisions | API Gateway: authorizers, usage plans and throttling, stages |
| Budget as a quota | API Management's token limits for model APIs, or a policy calling the meter | a Lambda authorizer reading the meter |

Both clouds also offer broker-side duplicate detection: Service Bus duplicate
detection, and SQS FIFO deduplication. Both work only within a time window. The
incident key here has no window, which is why the design doesn't rely on the
broker for exactly-once handling.

## Run it

```bash
cd 12_integration
pip install -r requirements-test.txt
python run_integration_check.py
python run_contract_check.py
pytest
```

## Tests

From `12_integration`, `pytest` (56 tests): the adapters (control totals,
signatures, rejection with reasons), delivery (duplicates, durable store,
exponential backoff, poison isolation, immediate dead-lettering, replay once,
redelivery after a crash), triage through 08 (one request per incident, no
self-approval, posted once as the service, halts and refusals dead-lettered
without retry, traced as the service identity), the measured night against
each control, the gateway's seven policies, and the contracts (drift, why v2 is
a major version, the labeled edits, and the gate failing a pull request that
breaks v2).

Each of these deliberate breakages fails a test:

* skipping the idempotency check
* a constant backoff instead of an exponential one
* retrying an invalid event
* not checking the trailer count
* not checking the webhook signature
* keying triage on the event instead of the incident
* posting without the request id
* retrying a guardrail halt like a fault

And for the gateway and contracts:

* accepting any token's subject
* dropping the `Deprecation` header
* not enforcing the sunset
* not checking the route's scope
* removing the rate limit
* allowing unknown parameters
* returning a field the contract doesn't have
* not holding the model route to the budget
* the checker missing a parameter made required
* showing triage status to someone who can't see the incident

## Limits

* **The broker is in process.** Ordering, partitions and consumer groups are not
  modelled. Nothing here depends on ordering: work orders are upserts by number,
  and triage is keyed on the incident.
* **The work-order index is in memory.** It is rebuilt from the night's events.
  A real one would be a table, upserted the same way.
* **The marker check costs a read before every write.** A downstream API that
  takes an idempotency key would make that read unnecessary.
* **The webhook signature format varies by vendor.** The header name and
  encoding here are generic; check the vendor's documentation.
* **The rate limiter is in memory, per process.** Behind a load balancer it
  would need a shared store, which is what API Management and API Gateway
  provide.
* **The breaking-change checker covers the schema shapes this API uses:** flat
  objects, query and path parameters, and JSON bodies. It doesn't follow `$ref`
  or nested objects. A full OpenAPI diff tool would replace it as the API grows.
* **MCP is not behind this gateway.** 08's MCP server enforces the same
  identity and scopes itself. In production, one gateway would front both.
