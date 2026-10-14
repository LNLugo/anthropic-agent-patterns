"""Answer graph: retrieve, generate with citations, validate, then answer or abstain.

    retrieve -> generate -> validate --(>=1 valid citation)--> answer
                                     +--(none / model abstained)--> abstain

Policy: an answer ships only if at least one citation resolves to text the
model was actually shown. Invalid citations are dropped and counted. When the
evidence is missing the graph abstains instead of guessing, which is the
behavior a legal reviewer needs on clauses a contract does not contain.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from .chunking import Chunk
from .citations import CheckedCitation, Citation, validate
from .ledger import ContextLedger
from .retrievers import Retriever

PROMPT = """You review commercial contracts. Use only the numbered excerpts below.

Clause category: {category}
Definition: {details}

Excerpts:
{excerpts}

Task: decide whether this contract contains language for the clause category.
If it does, summarize it in one or two sentences and cite the exact supporting
text. If the excerpts do not contain it, abstain. Do not use outside knowledge.

Return only JSON:
{{"abstain": false,
  "answer": "one or two sentence summary",
  "citations": [{{"chunk_id": "<id from the excerpt header>", "quote": "<exact text copied from that excerpt>"}}]}}
or
{{"abstain": true, "reason": "why the excerpts do not support an answer"}}"""


class LLMReply(TypedDict):
    text: str
    input_tokens: int
    output_tokens: int


LLM = Callable[[str], LLMReply]


def _reply_from_message(msg) -> LLMReply:
    """Normalize a LangChain chat message into text plus token counts."""
    usage = getattr(msg, "usage_metadata", None) or {}
    content = msg.content
    text = content if isinstance(content, str) else "".join(
        b.get("text", "") for b in content if isinstance(b, dict)
    )
    return {
        "text": text,
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
    }


def claude_llm(model: str | None = None, max_tokens: int = 800) -> LLM:
    """Claude via langchain-anthropic. Model defaults to $CLAUDE_MODEL."""
    from langchain_anthropic import ChatAnthropic

    # Claude Sonnet 5.5 accepts only its default temperature, so none is set here.
    chat = ChatAnthropic(model=model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=max_tokens)
    return lambda prompt: _reply_from_message(chat.invoke(prompt))


def openai_llm(model: str | None = None, max_tokens: int = 800) -> LLM:
    """OpenAI via langchain-openai. Model defaults to $OPENAI_MODEL, then gpt-6-luna.

    gpt-6-luna is a reasoning model and takes a reasoning effort, not a temperature;
    the GPT-4 series (retired January 14, 2027) still gets temperature 0."""
    from langchain_openai import ChatOpenAI

    name = model or os.environ.get("OPENAI_MODEL", "gpt-6-luna")
    settings = ({"temperature": 0} if name.startswith("gpt-4")
                else {"reasoning_effort": os.environ.get("OPENAI_REASONING_EFFORT", "none")})
    chat = ChatOpenAI(model=name, max_tokens=max_tokens, **settings)
    return lambda prompt: _reply_from_message(chat.invoke(prompt))


PROVIDERS: dict[str, Callable[..., LLM]] = {"anthropic": claude_llm, "openai": openai_llm}


def make_llm(provider: str, model: str | None = None) -> LLM:
    """The graph is model agnostic; the provider is a run setting, not a code change."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; choose from {sorted(PROVIDERS)}")
    return PROVIDERS[provider](model)


class State(TypedDict, total=False):
    query_id: str
    contract_id: str
    category: str
    details: str
    query_text: str
    chunks: list[Chunk]
    reply: dict[str, Any]
    checked: list[CheckedCitation]
    status: str  # answered | abstained
    answer: str
    reason: str
    ledger: ContextLedger


def _parse_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"abstain": True, "reason": "unparseable_model_output"}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"abstain": True, "reason": "unparseable_model_output"}


def build_graph(retriever: Retriever, llm: LLM, k: int = 5):
    def retrieve(state: State) -> State:
        ledger = state["ledger"]
        with ledger.stage("retrieve") as rec:
            hits = retriever.retrieve(state["query_text"], state["contract_id"], k)
            chunks = [h.chunk for h in hits]
            rec.chunk_ids = [c.id for c in chunks]
            rec.context_chars = sum(len(c.text) for c in chunks)
        return {"chunks": chunks}

    def generate(state: State) -> State:
        ledger = state["ledger"]
        excerpts = "\n\n".join(f"[{c.id}]\n{c.text}" for c in state["chunks"])
        prompt = PROMPT.format(category=state["category"], details=state["details"], excerpts=excerpts)
        with ledger.stage("generate") as rec:
            reply = llm(prompt)
            rec.chunk_ids = [c.id for c in state["chunks"]]
            rec.context_chars = len(prompt)
            rec.input_tokens, rec.output_tokens, rec.model_calls = reply["input_tokens"], reply["output_tokens"], 1
        return {"reply": _parse_json(reply["text"])}

    def check(state: State) -> State:
        reply = state["reply"]
        cits = [
            Citation(str(c.get("chunk_id", "")), str(c.get("quote", "")))
            for c in reply.get("citations", []) or []
            if isinstance(c, dict)
        ]
        with state["ledger"].stage("validate"):
            checked = validate(cits, state["chunks"])
        return {"checked": checked}

    def route(state: State) -> str:
        if state["reply"].get("abstain"):
            return "abstain"
        return "answer" if any(c.status == "valid" for c in state["checked"]) else "abstain"

    def answer(state: State) -> State:
        return {"status": "answered", "answer": str(state["reply"].get("answer", ""))}

    def abstain(state: State) -> State:
        reason = state["reply"].get("reason") if state["reply"].get("abstain") else "no_valid_citation"
        return {"status": "abstained", "answer": "", "reason": str(reason)}

    g = StateGraph(State)
    g.add_node("retrieve", retrieve)
    g.add_node("generate", generate)
    g.add_node("validate", check)
    g.add_node("answer", answer)
    g.add_node("abstain", abstain)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "validate")
    g.add_conditional_edges("validate", route, {"answer": "answer", "abstain": "abstain"})
    g.add_edge("answer", END)
    g.add_edge("abstain", END)
    return g.compile()


def initial_state(query, ledger: ContextLedger | None = None) -> State:
    return {
        "query_id": query.id,
        "contract_id": query.contract_id,
        "category": query.category,
        "details": query.details,
        "query_text": query.text,
        "ledger": ledger or ContextLedger(query.id),
    }
