# ADR 0013: Tests run offline; model quality is measured separately

**Status:** Accepted, 2026-10-15 · **Standards:** APP-04, AI-07

## Context

Tests that call a live model are slow, cost money and are non-deterministic. Tests that only mock the model can't
show the model is any good.

## Decision

Two separate things. CI runs every suite offline: a scripted model replays exact replies and fails on an unscripted
call or an unused step. Model quality is measured live, on held-out sets written without seeing the prompt, over
three runs, against a non-model baseline, and the results are committed with their date. Every "never happens" test has
a positive control.

## Alternatives rejected

- Live models in CI: flaky, costly, needs secrets.
- Mocks with loose matching: pass on calls nobody scripted.

## Consequences

A prompt change needs a live re-run to claim a quality change; CI only proves the plumbing.

## Evidence

Six suites, all offline, run in CI on Python 3.12 and 3.13 (the counts are in the root README). Controls are checked
against deliberate breakage: 38 breakages across modules 10 to 12 alone, each failing a test.

## The strongest objection

*"How do you know the scripted replies are realistic?"* They don't need to be: they test the code's handling of each
kind of reply, including the bad ones. Realism is the live evaluation's job, and its numbers are kept apart.
