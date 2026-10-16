# 12 Integration

Agents started by events, not only by people: from a legacy batch file, a SaaS
webhook and a cloud event bus. Delivered at least once, handled exactly once,
with every bad input kept and explained rather than dropped or guessed.

> **Status:** events, delivery guarantees and event-triggered triage are built.
> The gateway (per-identity quotas, versioned routes) and API lifecycle
> (contracts, breaking-change tests, deprecation) follow in the next change.

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

## Mapping to managed services

The broker here is in process, with a clock the tests control. The consumer
uses three calls, receive, ack and nack, which managed brokers offer:

| Here | Azure | AWS |
|---|---|---|
| Broker, at least once, redelivery after a crash | Service Bus queue, peek-lock | SQS, visibility timeout |
| Dead-letter after N attempts | Service Bus max delivery count and dead-letter subqueue | SQS redrive policy, `maxReceiveCount` |
| Event bus for service alerts | Event Grid | EventBridge |
| Idempotency store | a table keyed on source and id | a DynamoDB conditional write |

Both clouds also offer broker-side duplicate detection: Service Bus duplicate
detection, and SQS FIFO deduplication. Both work only within a time window. The
incident key here has no window, which is why the design doesn't rely on the
broker for exactly-once handling.

## Run it

```bash
cd 12_integration
pip install -r requirements-test.txt
python run_integration_check.py
pytest
```

## Tests

From `12_integration`, `pytest` (34 tests): the adapters (control totals,
signatures, rejection with reasons), delivery (duplicates, durable store,
exponential backoff, poison isolation, immediate dead-lettering, replay once,
redelivery after a crash), triage through 08 (one request per incident, no
self-approval, posted once as the service, halts and refusals dead-lettered
without retry, traced as the service identity), and the measured night against
each control.

Each of these deliberate breakages fails a test:

* skipping the idempotency check
* a constant backoff instead of an exponential one
* retrying an invalid event
* not checking the trailer count
* not checking the webhook signature
* keying triage on the event instead of the incident
* posting without the request id
* retrying a guardrail halt like a fault

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
