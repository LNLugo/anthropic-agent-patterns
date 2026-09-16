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