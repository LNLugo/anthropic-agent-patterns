"""Answer from the retrieved evidence only, citing it, or say the evidence is not there.

The model sees the question and the evidence, each line under its key ([vpn:249],
[doc:RB-VPN-LOCKOUT#2]). Its answer must cite keys, and every key it cites must be one it
was shown. An answer with no citation, or with a key it was not shown, is not returned:
the asker gets the evidence and a note that no grounded answer was produced (07's rule,
AI-04). The evidence was already filtered by the asker's groups, so the model never sees
a line they may not read.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .retrieve import Hit

PROMPT = """You are a network operations analyst. Answer the question using only the evidence below.
Cite every statement with the evidence key in square brackets, for example [vpn:249].
If the evidence does not answer the question, say so in one sentence and cite nothing.
Be brief: two to five sentences.

Evidence:
{evidence}

Question: {question}"""

CITE = re.compile(r"\[((?:vpn|firewall|syslog|auth|inventory):\d+|doc:[A-Za-z0-9_\-]+#\d+)\]")


@dataclass(frozen=True)
class Answer:
    text: str
    cited: tuple[str, ...]
    evidence: tuple[str, ...]
    grounded: bool
    reason: str = ""


def answer(question: str, hits: list[Hit], items: dict, llm) -> Answer:
    from tracing import get_tracer, model_name, usage

    keys = tuple(h.key for h in hits)
    if not hits:
        return Answer("Nothing you can read matches this question.", (), keys, False, "no_evidence")
    evidence = "\n".join(f"[{h.key}] {items[h.key].text}" for h in hits)
    t0 = time.perf_counter()
    with get_tracer().span("generation", "answer", model=model_name(llm)) as span:
        msg = llm.invoke(PROMPT.format(evidence=evidence, question=question))
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        span.set(**usage(msg), latency_s=round(time.perf_counter() - t0, 3))
        span.content(input=question, output=text)
    cited = tuple(dict.fromkeys(CITE.findall(text)))
    unknown = [c for c in cited if c not in keys]
    if unknown:
        return Answer("The model cited evidence it was not shown, so its answer is withheld. "
                      "The evidence is listed instead.", cited, keys, False, "unknown_citation")
    if not cited:
        return Answer(text.strip(), (), keys, False, "no_citation")
    return Answer(text.strip(), cited, keys, True)
