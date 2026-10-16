# ADR 0014: A breaking change is a new version, with a dated end for the old one

**Status:** Accepted, 2026-10-16 · **Standards:** API-02, API-03, API-04

## Context

v1's count used a hidden 30-day window. Fixing it means new required parameters, which break v1 clients.

## Decision

The contract is generated from one route description, committed and served; drift fails the build. A breaking
change needs a new major version: v2. v1 is marked deprecated in its contract, sends `Deprecation` (RFC 9745), `Sunset`
(RFC 8594) and `Link` headers, and returns 410 naming its successor from the sunset date, 2027-01-31. In CI, a pull
request's v2 contract is compared with main's, and a breaking change fails the build.

## Alternatives rejected

- Change v1 in place: breaks clients without warning.
- Keep v1 forever: two semantics to maintain indefinitely.

## Consequences

Four months of notice. The checker covers the schema shapes this API uses; a full OpenAPI diff tool replaces it as
the API grows.

## Evidence

The checker finds exactly the two new required parameters between v1 and v2, and classifies 14 labeled edits
correctly. That's a regression suite, since the same author wrote both (12_integration).

## The strongest objection

*"Who decides the sunset date?"* The API owner proposes it at design review; the review board approves it as part of
the version's lifecycle. The headers make it visible to every client on every call, and the date is enforced in code.
