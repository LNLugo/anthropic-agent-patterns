# 09 Memory

**Control question:** *what should the model see from earlier in the conversation, and whose history is it?*

A follow-up ("what about PAY?") needs the earlier turn, and a standing instruction
("from now on, only Signals") has to keep working twenty turns later. The usual
answers each fail a different way as a conversation grows:

- **Append everything:** cost grows with every turn.
- **Keep a window of the latest turns:** forgets the start.
- **Append and compact:** keeps whatever the summarizer chose.

This module keeps the whole history in a session and chooses, each turn, what
part of it the model sees. It measures that choice against the alternatives.

## The session

History sits behind four methods, the Session protocol from
[openai/openai-agents-python](https://github.com/openai/openai-agents-python) v0.24.0.
Only the contract is borrowed.

| Method | Does |
|---|---|
| `get_items(limit=None)` | The history, oldest first; with `limit`, the latest items |
| `add_items(items)` | Append |
| `pop_item()` | Remove and return the latest item: undo a turn, or retract one a guardrail rejected |
| `clear_session()` | Delete the whole history |

Two stores, with the same contract: `SQLiteSession`, which is durable and
survives a restart, and `InMemorySession`. Three differences from the library's
version:

| | openai-agents-python v0.24.0 | This repo |
|---|---|---|
| Calls | async | synchronous, like the rest of the repo |
| Ownership | a session id | a session belongs to one identity; opening it as anyone else raises `PermissionError`, because a session id is not a credential |
| Retention | kept until cleared | `retention_days`: older items are not returned, and are deleted on the next write, not just hidden |

## Choosing the context

[`memory/strategies.py`](memory/strategies.py) builds the context for a question
from the history:

| Strategy | What the model sees |
|---|---|
| `append_all` | everything |
| `window(n)` | the last `n` items |
| `compact(keep, budget)` | the last `keep` items, plus a summary of the rest within `budget` tokens |
| `retrieve(k, recent)` | the latest `recent` items, plus the `k` earlier exchanges most relevant to the question (BM25) |
| `retrieve_pinned` | `retrieve`, plus every **pinned** item |

An item is pinned when the user's wording sets an instruction for later turns:
"from now on", "always", "never", "by default", "remember that", "unless I say".
Relevance alone misses a standing instruction that shares no words with the
question it should change. "Only count Signals from now on" shares nothing with
"how many open incidents were created this week?".

## Results

[`run_memory_benchmark.py`](run_memory_benchmark.py) builds conversations of 10 to
240 exchanges. Six things the user establishes come first
([fixtures](fixtures/memory_benchmark.json)), then filler that reuses the same
projects, months and priorities as distractors, then six later questions, each
needing one earlier exchange. That's 5 conversations per length and 30 probes
per cell. *Recall* means the text the question needs is in the context built for
it. Tokens are estimated at 4 characters a token, the same for every strategy.

| Strategy | 10 exchanges | 60 | 240 | Context tokens at 240 | Recall, all lengths |
|---|---|---|---|---|---|
| Append everything | 30/30 | 30/30 | 30/30 | 4,346 | 150/150 |
| Last 10 items | 5/30 | 0/30 | 0/30 | 88 | 5/150 |
| Append and compact (stand-in summary) | 15/30 | 0/30 | 0/30 | 179 | 15/150 |
| Retrieve relevant | 26/30 | 21/30 | 23/30 | 109 | 115/150 |
| **Retrieve relevant + pinned** | **30/30** | **26/30** | **28/30** | **126** | **139/150** |

At 240 exchanges, retrieval with pins recalls 28 of 30 with 34 times fewer
context tokens than appending everything ($0.013 against $0.43 per thousand
questions at gpt-6-luna's input price, from 11's price file).

By question, over all 25 conversations:

| Question | Kind | Append all | Retrieve | Retrieve + pinned |
|---|---|---|---|---|
| "Remind me, what was the September P1 number for ELEV?" | follow-up | 25 | 22 | 22 |
| "Is WO-1001 on ESC-127-02 still the only open one?" | follow-up | 25 | 25 | 25 |
| "When does the v1 count API go away again?" | follow-up | 25 | 25 | 25 |
| "How many open incidents were created this week?" (after "from now on, only Signals") | standing instruction | 25 | **1** | **25** |
| "Count the P2s from yesterday." (after "always show me the JQL") | standing instruction | 25 | 25 | 25 |
| "How many incidents came in today?" (after "I work nights, so today means since 7 pm") | instruction with no trigger words | 25 | 17 | 17 |

What it shows:

* **Pinning is what keeps a standing instruction.** Without it, "only Signals"
  was found in 1 conversation of 25; with it, in all 25.
* **Pinning depends on wording.** "I work nights, so today means since 7 pm"
  sets an instruction without saying so. It is found only when the later
  question shares its words ("today"): 17 of 25.
* **Appending everything is right for short conversations.** At 10 exchanges it
  costs 247 tokens and never forgets. The case for retrieval grows with the
  conversation.
* **The compaction result measures the stand-in, not compaction.** The summary
  here is deterministic: the first sentences of each exchange, newest first,
  within the budget. So it forgets the oldest turns by construction. A model
  summarizer, especially one that rolls the summary forward, may keep more. That
  needs a live run, not yet made, and the 0/30 is not evidence against it.

These are the author's probes, so treat the benchmark as a regression check of
the strategies, not a model evaluation. No model is called.

## Memory in the assistant

[`memory/assistant.py`](memory/assistant.py) runs 08's assistant with memory:
`ask_with_memory(app, session, question, caller, today)`.

| Rule | How it holds |
|---|---|
| The session must be the caller's | Refused before anything runs, and no model call is made (tested) |
| A follow-up sees the earlier turn | The chosen history goes to the router as messages before the question |
| **Memory never widens a check** | The coverage check (08) still needs the project in the current question, or in one of the user's pinned standing instructions. A project named earlier in passing is not evidence for this question, and the same words without the pin are not either (both tested) |
| One turn is one trace | The memory selection and the assistant run share a trace, grouped by session |
| The trace names what the model saw, never its text | A `memory.context` span records the positions of the items shown, how many were pinned and their tokens, so an audit can rebuild the context from the session. Memory text stays out of the trace (tested with a planted marker) |

## Run it

```bash
cd 09_memory
pip install -r requirements-test.txt
python run_memory_benchmark.py
pytest
```

## Tests

From `09_memory`, `pytest` (40 tests):
- **The session contract:** order, limit, pop, clear, owner, restart, and retention that deletes.
- **Each strategy:** what it keeps and what it drops.
- **The benchmark's findings,** as assertions.
- **Memory in 08's assistant:** follow-ups; pinned instructions as evidence and unpinned ones not; someone else's session refused; history kept out of the trace while the trace names what was shown.

Each of these deliberate breakages fails a test:

* letting another identity open a SQLite session
* letting `ask_with_memory` run on someone else's session
* not keeping pinned items
* accepting any earlier user turn as evidence, pinned or not
* hiding expired items instead of deleting them
* recording memory text in the trace

## Limits

* **Pinning is a wording rule.** An instruction phrased without a trigger word is
  not pinned. A model could classify instructions instead, at a cost, and that
  would need measuring.
* **Retrieval is lexical.** BM25 misses a follow-up that shares no words with the
  earlier exchange. 07 found BM25 beat a small dense model on contracts; on
  conversation it has not been compared.
* **Compaction with a model summary is not measured yet.** See above.
* **Superseded facts are not handled.** If the user changes an instruction,
  both versions can be retrieved. The items keep their order, so the newer one
  comes later, but nothing marks the older one as replaced.
