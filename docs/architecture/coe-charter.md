# Architecture COE charter

A lightweight charter for an architecture center of excellence that governs
agentic AI, integration and APIs. This repository is its reference
implementation: the standards, ADRs, checklist and exception register here are
the artifacts the COE owns.

## Purpose

Help delivery teams ship agents that are safe, observable and affordable, faster
than they would alone, by providing standards that are enforced by tests,
reference implementations they can copy, and a review that is quick when the
evidence is there.

## Principles

1. **The simplest architecture that reliably works.** Autonomy is added when a
   measurement shows the simpler option falls short.
2. **Every claim is a number.** A standard names the test that enforces it, and a
   design names the baseline it beats.
3. **Paved roads over gates.** Teams that reuse a reference implementation inherit
   its controls and its tests, and their review is shorter.
4. **Exceptions are visible and temporary.** Every gap has an owner, a
   compensating control and an expiry date.

## Scope and decision rights

| Decision | COE | Delivery team | Security | Product owner |
|---|---|---|---|---|
| Standards: create, change, retire | **Decides** | Consulted | Consulted on identity and data | Informed |
| A design against the standards | **Approves** at review | Proposes, brings evidence | Approves identity and data handling | Informed |
| An exception | **Approves**, sets the expiry | Requests, owns the fix | Approves if identity or data is involved | Accepts the risk |
| API versions and sunset dates | Approves | Proposes | Informed | **Decides** the date with consumers |
| Model choice for a component | Approves against cost per correct answer | **Proposes**, measures | Consulted | Informed |
| Implementation details inside the standards | Informed | **Decides** | n/a | n/a |

## Forums

| Forum | Cadence | Who | Output |
|---|---|---|---|
| Design review | Weekly, 45 minutes, booked per change | Proposing team, a COE architect, security when identity or data is involved | A recorded outcome from the [checklist](design-review-checklist.md) |
| Standards review | Monthly | COE architects, team leads | Standards added, changed or retired, each as a reviewed change to standards.yaml |
| Exception review | Monthly | COE, exception owners | Each exception closed, extended once with a reason, or escalated |
| Scorecard | Quarterly | COE, engineering leadership | Adoption and exception trends; where the paved road needs work |

## When a review is required

A change needs design review if it adds any of:

- an agent, or a new tool for an existing agent
- an action that writes to a system of record
- a model, or a model change for an existing component
- an external integration: a new source, sink or webhook
- an API, or a breaking change to one

Changes inside an existing reference implementation, keeping its tests, need no
review.

## Exceptions

Requested at review, recorded in [exceptions.yaml](exceptions.yaml), rendered in
the [register](standards.md#exception-register). Each one names the standard, the
modules, the gap, the risk, the compensating control, the owner and an expiry no
more than six months out. An exception can be extended once, at exception review,
with a reason. Once the conformance check is in place, an expired exception fails CI.

## Training and playbooks

The onboarding path is this repository, in order: the patterns (01 to 06), then
retrieval (07), tools and approval (08), tracing (10), cost (11) and integration
(12). Each module's README explains the control question it answers, how the
control is built and the number that proves it. The playbook for a new agent is
the checklist plus the module closest to what the team is building.

## Metrics

| Metric | How it is measured | Status |
|---|---|---|
| Standards adoption | Modules meeting each applicable standard, from the conformance check | To be computed from the repository (the conformance check, next change) |
| Standards enforced by tests | Standards with at least one named enforcing test | Computed from standards.yaml |
| Open and expired exceptions | From exceptions.yaml | Recorded now; the conformance check will fail CI on an expired one |
| Reuse | Components built on a reference implementation rather than from scratch | Measured in operation |
| Review cycle time | Request to recorded outcome | Measured in operation |
| Review outcomes | Approved, with actions, with exception, not approved | Measured in operation |

The first three come from this repository. The last three need real teams
and are not filled in with estimates.
