"""08's assistant, with memory: each question goes in with the part of the history the
strategy chooses, and the exchange is saved when it ends.

    session = SQLiteSession("sessions.db", "s1", owner=caller.id, retention_days=30)
    out = ask_with_memory(app, session, "What about PAY?", caller, today)

What memory may and may not do:
  * The session must belong to the caller. Memory is one person's history; asking with
    someone else's session is refused before anything runs.
  * The model sees the chosen history, so "what about PAY?" can mean "the same count,
    for PAY".
  * Memory never widens a check. The coverage check still needs the project named in
    the current question, or in one of the user's pinned standing instructions. A
    project mentioned earlier in passing is not evidence for this question.
  * What was said is saved as the user said it; the answer is saved as given. A
    standing instruction is pinned when its wording says so (strategies.is_standing).
"""

from __future__ import annotations

from datetime import date

from agent_tools.assistant import ask
from agent_tools.jira_mock import Caller

from .session import Session, get_tracer
from .strategies import Strategy, context_tokens, is_standing, retrieve_pinned


def ask_with_memory(app, session: Session, question: str, caller: Caller, today: date,
                    strategy: Strategy | None = None, thread_id: str | None = None) -> dict:
    owner = getattr(session, "owner", None)
    if owner != caller.id:
        raise PermissionError("this session belongs to someone else")
    tracer = get_tracer()
    # One trace per turn, memory included: the assistant run inside joins it.
    with tracer.trace("assistant", group_id=thread_id or session.session_id, metadata={"caller": caller.id}):
        context = (strategy or retrieve_pinned())(session.get_items(), question)
        with tracer.span("custom", "memory.context", session=session.session_id) as span:
            # Which turns the model saw, by position, never by text: an audit can rebuild the
            # model's context from the session, the way 07's ledger names chunk ids.
            span.set(items=len(context), pinned=sum(bool(i.get("pinned")) for i in context),
                     seqs=[i["seq"] for i in context if "seq" in i], summary=any(i.get("summary") for i in context),
                     tokens=context_tokens(context))
        out = ask(app, question, caller, today, thread_id=thread_id, context=context)
        session.add_items([{"role": "user", "content": question, "pinned": is_standing(question)},
                           {"role": "assistant", "content": out.get("answer", "")}])
    return out
