# Anthropic Workflow Patterns

Implementations of the workflow and agent patterns described in Anthropic's
**Building effective agents**.

This project uses LangGraph to implement the patterns incrementally, starting
with predictable workflows and progressing toward dynamic agent behavior.

> **Effective agents are not about adding more autonomy.**
>
> **They're about choosing the simplest architecture that reliably solves the problem.**

---

## Architecture Progression

| # | Anthropic Architecture | Status | Core Idea |
|---|---|---|---|
| Foundation | Augmented LLM | 🟡 Supporting capability | LLM + retrieval + tools + memory |
| 01 | Prompt Chaining | ✅ Complete | Fixed sequence of LLM calls |
| 02 | Routing | ✅ Complete | Classify → select specialized path |
| 03 | Parallelization | ✅ Complete | Independent work in parallel; sectioning / voting |
| 04 | Orchestrator-Workers | ✅ Complete | LLM dynamically decomposes → workers → synthesis |
| 05 | Evaluator-Optimizer | ✅ Complete | Generate → evaluate → feedback → refine |
| 06 | Agents | ✅ Complete | LLM dynamically directs its own process and tool use |
| **Phase 2** | **Capability layer** | | |
| 07 | Retrieval (RAG) | 🟡 In progress | Hybrid retrieval, reranking, cited answers, abstention, measured on CUAD |
 | 08 | Tools and MCP | 🟡 In progress | Bounded, read only Jira tool behind a router; typed queries, delegated permissions 
| 09 | Memory | ⬜ Planned | Retrieve relevant history instead of appending it |
| 10 | Guardrails and observability | ⬜ Planned | Policy checks and traces built on the context ledger |
| 11 | AI FinOps | ⬜ Planned | Cost per answer and cost per correct answer |

---

## Control Questions

Each pattern answers a different architectural control question.

| Pattern | Control Question |
|---|---|
| Prompt Chaining | What step comes next? |
| Routing | Which path should I take? |
| Parallelization | What can I do independently? |
| Orchestrator-Workers | What work needs to be decomposed? |
| Evaluator-Optimizer | Is the result good enough? |
| Agents | What should I do next? |
| Retrieval | What evidence did the model see, and can every claim be traced back to it? |

---

## Conceptual Architecture

```text
                         AUGMENTED LLM
                  Retrieval + Tools + Memory
                              │
                              ▼
                     ┌─────────────────┐
                     │ 01 Prompt       │
                     │    Chaining     │
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │ 02 Routing      │
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │ 03 Parallel-    │
                     │    ization      │
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │ 04 Orchestrator │
                     │    -Workers     │
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │ 05 Evaluator-   │
                     │    Optimizer    │
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │ 06 Agents       │
                     │                 │
                     │ Dynamic         │
                     │ decision-making │
                     │ + tool use      │
                     └─────────────────┘
```

---

## Phase 2: The Capability Layer

Phase 1 covered how control flows. Phase 2 covers what each pattern runs on:
retrieval, tools, memory, guardrails, observability and cost. The rule from
Phase 1 still holds. Each capability is added only where a measured gap
justifies it, and every claim in this repo comes with a number the repo
itself produces.

The first capability is retrieval, built on **CUAD**, a public set of
commercial contracts where lawyers annotated the exact text for 41 clause
types. Those annotations make retrieval quality and citation accuracy
measurable instead of anecdotal.

**[07_rag](07_rag/)**: contract review RAG with

* every retrieval strategy defined as a YAML config (BM25, dense, hybrid with
  Reciprocal Rank Fusion, cross encoder reranking, parent retrieval)
* an ablation runner that scores each strategy against the lawyer annotations
* a LangGraph answer flow that ships an answer only when a citation resolves
  to text the model was shown, and abstains otherwise
* a context ledger recording what entered the model's context, the tokens it
  cost and how long it took

Results on all 102 CUAD test contracts, 1,244 answerable clause queries:

| Strategy | k | Hit@k | MRR |
|---|---|---|---|
| BM25 | 5 | 0.667 | 0.503 |
| Dense embeddings | 5 | 0.612 | 0.438 |
| Hybrid (BM25 + dense, RRF) | 5 | 0.652 | 0.511 |
| BM25 + cross encoder rerank | 5 | 0.721 | 0.567 |
| Hybrid + rerank | 5 | 0.719 | 0.564 |
| Hybrid + rerank + parent windows | 3 | 0.774 | 0.638 |

The measured answer is not the fashionable one. On contract language, BM25
beat a small dense embedder, hybrid fusion added nothing measurable, and the
cross encoder reranker did the real work. Once reranking was in place, the
dense model could be removed with no loss. That is the Phase 1 thesis applied
to retrieval: the simplest pipeline that holds up under measurement wins.
Details and costs are in [07_rag/README.md](07_rag/README.md).

**[Design: permission-aware RAG over Atlassian Data Center](docs/designs/atlassian-rag.md)**: how modules 07, 08, 10 and 11 compose into one governed system. Design only; each piece ships with its own number as the module lands.

