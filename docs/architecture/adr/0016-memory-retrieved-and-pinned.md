# ADR 0016: Memory is retrieved by relevance, with standing instructions pinned

**Status:** Accepted, 2026-10-16 · **Standards:** AI-08, APP-06

## Context

A conversation's history has to reach the model for follow-ups ("what about
PAY?") and standing instructions ("from now on, only Signals") to work. The
usual approaches append the whole history, append it and compact the older part
into a summary, or keep only a window of the latest turns. Each fails a
different way as conversations grow.

## Decision

Keep history in a session behind four methods (`get_items`, `add_items`,
`pop_item`, `clear_session`), the contract from openai-agents-python. Choose
what the model sees each turn: the latest few items, the earlier exchanges most
relevant to the question (BM25), and every pinned item. An item is pinned when
the user's wording sets an instruction for later turns ("from now on",
"always"). Sessions belong to one identity and expire. Memory never widens a
check: the coverage check accepts a project from the current question or a
pinned instruction, nothing else.

## Alternatives rejected

- Append everything: never forgets, but the context grows with every exchange.
- Append and compact: bounded, but what survives depends on the summarizer. The benchmark's deterministic stand-in keeps the newest turns first, so it drops the start by construction; a model summarizer may keep more, and is measured only in a live run, not yet made.
- A window of the latest turns: bounded, forgets everything older.
- Relevance alone: misses an instruction that shares no words with the question it should change.

## Consequences

Pinning depends on wording, so an instruction phrased without a trigger word is
not pinned. It is found only when the later question happens to share its words.
A follow-up that names no project gets a clarifying question rather than a guess,
even if a project was mentioned earlier.

## Evidence

Over 150 probes per strategy (6 later questions, 5 conversation lengths from 10
to 240 exchanges, 5 conversations each): append-all recalled 150 at up to 4,346
context tokens; retrieval with pins recalled 139 at about 130 tokens, 34 times
less at 240 exchanges. Pinning took "only Signals" from 1 of 25 to 25 of 25. A
bounded summary recalled 15 and a last-10 window 5. These are the author's
probes: a regression check of the strategies, not a model evaluation
(09_memory).

## The strongest objection

*"Why not just use a long-context model and append everything?"* Because cost
and latency scale with every turn of every conversation, and the model still has
to find the instruction among everything else. Pinning makes the important part
small and certain; relevance finds the rest; and appending everything remains
the right choice for short conversations, where the benchmark shows it costs little.
