# ADR 0002: The model produces typed arguments; the tool writes the query

**Status:** Accepted, 2026-10-12 · **Standards:** AI-02

## Context

The count tool queries Jira. Letting the model write JQL would make every field of the question a possible
injection, and every query a review problem.

## Decision

The model fills a typed `QuerySpec`: an allowlisted project, enum values for type, priority and status, and dates
within a 366-day cap. The tool validates it and compiles the JQL itself. A raw JQL argument is refused.

## Alternatives rejected

- Model-written JQL with a validator: the validator would have to parse JQL and anticipate every injection.
- A text-to-SQL style allowlist of query templates: rigid, and still string-built.

## Consequences

Questions the spec can't express (for example "resolved this month", which needs a resolution date) are refused or
asked about, not approximated. Adding a filter is a code change with a test.

## Evidence

Tests: an injection through a field is rejected, raw JQL cannot pass through, and the JQL is built by the tool
(08_tools/tests/test_jql_tool.py).

## The strongest objection

*"Isn't this too rigid for real users?"* It is bounded on purpose. A rigid tool that is always right beats a flexible
one that is sometimes wrong in a way nobody can see. The bound is widened one field at a time, with a test.
