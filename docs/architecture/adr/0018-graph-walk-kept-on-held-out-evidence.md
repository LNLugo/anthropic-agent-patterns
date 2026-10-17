# ADR 0018: Graph-expanded retrieval over logs, kept on held-out evidence only

**Status:** Accepted, 2026-10-17 · **Standards:** AI-07, AI-04, APP-01

## Context

The question in plain English came from an article on RAG over firewall and VPN logs: "reason of failure for user john.doe?" It needs a join. The VPN lines name the user. The firewall lines that deny the same address do not. Nearest-line search returns each line on its own merit, so it cannot make that join.

A graph of lines and the entities they name can make it. But a graph retriever has many settings to tune, and with them comes the risk of fitting the questions it was built on.

## Decision

Build both retrievers over the same store and the same k. Ship the graph walk only for what it wins on questions written after it was frozen. The graph walk is personalised PageRank from the best vector matches and from entities the question names exactly, over the asker's visible graph, with three priors:
- **Rarity:** a rare event type ranks higher.
- **Novelty:** a new pair of actors ranks higher.
- **Time window:** items outside the period the question names rank lower.

The method:
- Freeze the retriever in a commit.
- Write a new question set and score it once.
- Keep the result in `results/` even when it is bad.
- Fix what it exposed.
- Repeat with a fresh set.

## Alternatives rejected

- **Ship on the development numbers:** 24/33 against 3/33 for vector search, but measured on questions the retriever was built on.
- **A model extracting entities from logs:** the fields are `key=value`, so a pattern is exact and free. A model is used only to write the answer.
- **A graph database:** at 1,300 lines, networkx in memory is enough. Upstash Redis is the system of record for the graph.
- **Keep tuning against held-out 3:** that would have spent it like the first two.

## Consequences

- **Gains on fresh questions.** On held-out 3, the walk beat the strongest control without a walk on multi-hop questions (16/26 against 13/26). It lost on single-hop (2/4 against 4/4).
- **What remains unproven.**
  - Eight questions is a small set.
  - Entity linking hurt on held-out 3 (20/26 without it).
  - Novelty showed no clear effect.
  - These are reported, not tuned away.
- **The next step.** A router that sends single-entity questions to a direct lookup. That needs a fourth held-out set.

## Evidence

From `14_knowledge_graph/results/` (`held_out_v1.json`, `held_out_2.json`, `held_out_3.json`):

| Multi-hop recall@10 | Vector only | Best control without the walk | Graph walk |
|---|---|---|---|
| Held-out 1 | 6/26 | 11/26 | 10/26 |
| Held-out 2 | 4/14 | 5/14 | 3/14 |
| Held-out 3 | 9/26 | 13/26 | 16/26 |

All runs use the offline lexical stand-in. Across every set, no retriever returned a line the asker may not read. A widened-filter control does return those lines.

## The strongest objection

"You wrote the questions, the data and the retriever, and the graph lost twice before it won once. Isn't the third result the same overfitting?"

Partly, and the README says so. What changed between sets were fixes for named failures: common words matching, people with routine histories, and time words being ignored. None of them was a setting turned until a number moved. Each fix was committed before the next set was written; the commits are kept under the tag `kg-freeze-history`, since the pull request was squash-merged. Held-out 3 is reproduced by a test against the retriever as it stands.

The honest claim is narrow: on fresh questions, the walk adds about three found items in 26 over the same signals without a walk, and it costs single-hop accuracy. A live run with a real embedding model, and questions written by someone else, would settle more.
