"""What the model sees from the history on the next turn.

The history is the session; the context is the part of it put in front of the
model. Five ways to choose it:

    append_all          everything. Never forgets; cost grows with every turn.
    window(n)           the last n items. Bounded; forgets everything older.
    compact(keep, budget, summarize)
                        the last `keep` items verbatim, plus a summary of the rest
                        within `budget` tokens. Bounded; the summary decides what survives.
    retrieve(k, recent) the `recent` latest items, plus the k earlier exchanges most
                        relevant to the question (BM25). Bounded; finds what the
                        question's words point at.
    retrieve_pinned     retrieve, plus every pinned item: standing instructions the
                        user gave ("from now on", "always"), kept whatever the question.

Relevance alone misses a standing instruction that shares no words with the
question it should change: "only count Signals from now on" against "how many
open incidents were created this week?". That is why pinned items are kept
apart. Pinning depends on wording, so an instruction given without the trigger
words is not pinned; it is found only if the later question happens to share
its words (the benchmark's "night shift" case does, through "today").
"""

from __future__ import annotations

import re
from typing import Callable

from rank_bm25 import BM25Okapi

Strategy = Callable[[list[dict], str], list[dict]]

STANDING = re.compile(r"\b(from now on|going forward|always|never|by default|remember that|unless i say)\b", re.I)


def is_standing(text: str) -> bool:
    """A user turn that sets an instruction for later turns, by its wording."""
    return bool(STANDING.search(text))


def tokens(text: str) -> int:
    """Estimated tokens: 4 characters per token. An estimate, used the same way for every strategy."""
    return max(1, len(text) // 4)


def context_tokens(items: list[dict]) -> int:
    return sum(tokens(i["content"]) for i in items)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())


def _exchanges(items: list[dict]) -> list[list[dict]]:
    """User turns with the assistant replies that follow them, as retrieval units."""
    out: list[list[dict]] = []
    for i in items:
        if i["role"] == "user" or not out:
            out.append([i])
        else:
            out[-1].append(i)
    return out


def append_all(history: list[dict], question: str) -> list[dict]:
    return list(history)


def window(n: int) -> Strategy:
    def pick(history: list[dict], question: str) -> list[dict]:
        return history[-n:] if n else []
    return pick


def _first(text: str) -> str:
    return re.split(r"(?<=[.?!])\s", text.strip(), maxsplit=1)[0]


def first_sentences(items: list[dict], budget: int) -> str:
    """A deterministic stand-in for a model summary: for each older exchange, newest first,
    the first sentence of the question and of the answer, until the budget is spent. Like
    any bounded summary, it keeps less of the oldest history as the history grows. A model
    summarizer can be passed instead, and measured in a live run."""
    lines, used = [], 0
    for unit in reversed(_exchanges(items)):
        line = " / ".join(_first(i["content"]) for i in unit)
        if used + tokens(line) > budget:
            break
        lines.append(line)
        used += tokens(line)
    return "Summary of earlier turns: " + " | ".join(reversed(lines)) if lines else ""


def compact(keep: int = 6, budget: int = 120,
            summarize: Callable[[list[dict], int], str] = first_sentences) -> Strategy:
    def pick(history: list[dict], question: str) -> list[dict]:
        older, recent = history[:-keep] if keep else history, history[-keep:] if keep else []
        summary = summarize(older, budget) if older else ""
        return ([{"role": "assistant", "content": summary, "pinned": False, "summary": True}] if summary else []) + recent
    return pick


def retrieve(k: int = 3, recent: int = 4, pinned: bool = False) -> Strategy:
    def pick(history: list[dict], question: str) -> list[dict]:
        tail = history[-recent:] if recent else []
        older = history[:-recent] if recent else history
        chosen: list[dict] = []
        units = _exchanges(older)
        if units and k:
            bm25 = BM25Okapi([_words(" ".join(i["content"] for i in u)) for u in units])
            scores = bm25.get_scores(_words(question))
            best = sorted(range(len(units)), key=lambda n: (-scores[n], n))[:k]
            chosen = [i for n in sorted(best) if scores[n] > 0 for i in units[n]]
        if pinned:
            have = {i["seq"] for i in chosen + tail}
            chosen = [i for i in older if i.get("pinned") and i["seq"] not in have] + chosen
        return sorted(chosen, key=lambda i: i["seq"]) + tail
    return pick


def retrieve_pinned(k: int = 3, recent: int = 4) -> Strategy:
    return retrieve(k, recent, pinned=True)
