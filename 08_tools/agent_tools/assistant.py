"""Assistant graph: route each question to retrieval or to the bounded Jira tool.

    decide --knowledge--> retrieve ----------------------------------------> respond
           --aggregation--> run_tool (input guardrails, bound, Jira, output guardrails) --> respond
           --clarify / none------------------------------------------------> respond

`decide` is a router with one interface, decide(question, today) -> Decision:
ModelRouter (model_router.py, the model choosing a tool, with the coverage
check) or RulesRouter (router.py, the offline baseline). The graph is built
with one explicitly; nothing falls back to the rules silently. On the held-out
questions the model router routed 18 to 19 of 20 where the rules routed 9.

Guardrails (guardrails.py) run in the tool node: reject_content becomes the
answer, since this graph composes answers in code rather than looping back to
a model; raise_exception halts the run. The numbers in an aggregation answer
come from the tool, never from a model. Answers are composed deterministically
so every part is testable; in production a model would phrase them, with the
count, keys and JQL passed through unchanged.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Protocol, TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from .comments import Comment, CommentTool
from .guardrails import GuardrailRecord, ToolCallData, ToolGuardrail, run_guardrails
from .jira_mock import Caller
from .jql_tool import BoundedJiraTool, QuerySpec, ToolRejected, ToolResult
from .observe import get_tracer

# Knowledge retrieval is injected: (question, caller, k) -> [(page_id, title), ...].
# demo_assistant.py wires it to 07_rag's permission-filtered retriever.
KnowledgeSearch = Callable[[str, Caller, int], list[tuple[str, str]]]


class Router(Protocol):
    def decide(self, question: str, today: date): ...


class State(TypedDict, total=False):
    question: str
    caller: Caller
    today: date
    route: str
    spec: QuerySpec | None
    arguments: dict
    bound_rejection: str
    assumptions: list[str]
    result: ToolResult
    pages: list[tuple[str, str]]
    guardrail_records: list[GuardrailRecord]
    proposal: dict          # the write awaiting approval
    approval: dict          # the approver's decision, as given on resume
    comment: Comment
    stop_reason: str
    answer: str


# The module's own types that can sit in a checkpoint. LangGraph is moving to blocking
# unregistered types on load; naming them here keeps a saved approval loadable after
# that change, and keeps the current version from warning on every resume.
CHECKPOINT_TYPES = [
    ("agent_tools.jira_mock", "Caller"),
    ("agent_tools.jql_tool", "QuerySpec"),
    ("agent_tools.jql_tool", "ToolResult"),
    ("agent_tools.guardrails", "GuardrailRecord"),
    ("agent_tools.comments", "Comment"),
]


def checkpoint_serde() -> JsonPlusSerializer:
    """The serializer to give a checkpointer for this graph, e.g. SqliteSaver(conn, serde=checkpoint_serde())."""
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


def spec_arguments(spec: QuerySpec) -> dict:
    """A QuerySpec as the tool arguments a model would have sent, for guardrails to inspect."""
    args = {"project": spec.project, "created_from": spec.created_from.isoformat(),
            "created_before": spec.created_before.isoformat()}
    for f in ("issue_type", "priority", "status", "group_by"):
        if getattr(spec, f):
            args[f] = getattr(spec, f)
    return args


def _describe(spec) -> str:
    words = [w for w in (spec.priority, spec.status.lower() if spec.status else None) if w]
    words.append((spec.issue_type or "issue").lower() + "s")
    last_day = spec.created_before - timedelta(days=1)
    return f"{' '.join(words)} in {spec.project} created {spec.created_from.isoformat()} to {last_day.isoformat()}"


def build_assistant(tool: BoundedJiraTool, knowledge: KnowledgeSearch, router: Router,
                    guardrails: list[ToolGuardrail] = (), k: int = 3,
                    comment_tool: CommentTool | None = None, checkpointer=None):
    """comment_tool enables the write path, which pauses for human approval; that pause
    has to survive until someone answers, so a checkpointer is required with it."""
    guardrails = list(guardrails)
    if comment_tool is not None and checkpointer is None:
        raise ValueError("comment_tool needs a checkpointer: approval waits for a person, and the run must survive the wait")

    def decide_node(s: State) -> State:
        d = router.decide(s["question"], s["today"])
        if d.route == "aggregation":
            if d.rejected:
                # The bound refused the call, but the guardrails still see it first: an
                # attempt on a restricted project must halt, not be politely refused.
                return {"route": "aggregation", "arguments": dict(d.arguments or {}), "bound_rejection": d.rejected}
            args = dict(d.arguments) if d.arguments else spec_arguments(d.spec)
            return {"route": "aggregation", "spec": d.spec, "arguments": args, "assumptions": list(d.assumptions)}
        if d.route == "knowledge":
            return {"route": "knowledge"}
        if d.route == "comment":
            if comment_tool is None:
                return {"route": "none", "stop_reason": "none", "answer": "This assistant cannot post comments."}
            return {"route": "comment", "arguments": dict(d.arguments or {})}
        if d.route == "clarify":
            return {"route": "clarify", "stop_reason": "clarify",
                    "answer": d.message or "Which project do you mean: Billing Platform, Payments or Infrastructure?"}
        return {"route": "none", "stop_reason": "none",
                "answer": "I couldn't tell whether that is a count or a documentation question. Could you rephrase it?"}

    def tool_node(s: State) -> State:
        records: list[GuardrailRecord] = []
        data = ToolCallData(tool.name, s["arguments"], s["question"], s["caller"])
        rejected = run_guardrails(guardrails, "input", data, records)  # raise_exception propagates and halts
        if rejected is not None:
            return {"guardrail_records": records, "stop_reason": "guardrail", "answer": rejected}
        with get_tracer().span("tool", tool.name, caller=s["caller"].id) as span:
            span.content(input=s["arguments"])
            if s.get("bound_rejection"):
                span.set(outcome="rejected")  # the tool's own bound refused the arguments
                return {"guardrail_records": records, "stop_reason": "rejected",
                        "answer": f"I can't run that query: {s['bound_rejection']}."}
            try:
                result = tool.run(s["spec"], s["caller"])  # the tool's bound runs inside
            except ToolRejected as e:
                span.set(outcome="rejected")
                return {"guardrail_records": records, "stop_reason": "rejected", "answer": f"I can't run that query: {e}."}
            span.set(outcome="ok", truncated=result.truncated)
            span.content(output={"count": result.count, "keys": list(result.keys), "jql": result.jql})
        rejected = run_guardrails(guardrails, "output", ToolCallData(
            tool.name, s["arguments"], s["question"], s["caller"], output=result), records)
        if rejected is not None:
            return {"guardrail_records": records, "stop_reason": "guardrail", "answer": rejected}
        return {"guardrail_records": records, "result": result}

    # ------------------------------------------------ the write path: propose, approve, post

    def propose_node(s: State) -> State:
        """Everything that can be checked before a person is asked: guardrails, arguments,
        and that the requester can see the issue. Nobody is asked to approve an impossible write."""
        records: list[GuardrailRecord] = []
        args = s["arguments"]
        rejected = run_guardrails(guardrails, "input", ToolCallData(comment_tool.name, args, s["question"], s["caller"]), records)
        if rejected is not None:
            return {"guardrail_records": records, "stop_reason": "guardrail", "answer": rejected}
        try:
            key, body = comment_tool.check(args, s["caller"])
        except ToolRejected as e:
            return {"guardrail_records": records, "stop_reason": "rejected", "answer": f"Not posted: {e}."}
        # Control passes to a person: the run pauses here until someone decides.
        get_tracer().event("handoff", "approval_request", from_agent="assistant", to_agent="human_approver",
                           action=comment_tool.name, issue_key=key, requested_by=s["caller"].id)
        return {"guardrail_records": records, "proposal": {
            "action": comment_tool.name, "issue_key": key, "body": body,
            "requested_by": s["caller"].id, "question": s["question"],
        }}

    def approve_node(s: State) -> State:
        """Pause for a person. On resume LangGraph runs this node again from the top, so
        nothing here may have side effects; the write happens in post_node, once."""
        decision = interrupt(s["proposal"])
        return {"approval": dict(decision) if isinstance(decision, dict) else {"approved": False, "reason": "malformed decision"}}

    def post_node(s: State) -> State:
        p, a = s["proposal"], s["approval"]
        tracer = get_tracer()
        tracer.event("custom", "approval", decision="approved", approver=a["approver"], requested_by=p["requested_by"],
                     issue_key=p["issue_key"])
        with tracer.span("tool", comment_tool.name, caller=s["caller"].id, issue_key=p["issue_key"]) as span:
            span.content(input={"body": p["body"]})
            try:
                comment = comment_tool.post(p["issue_key"], p["body"], s["caller"], approved_by=a["approver"])
            except ToolRejected as e:
                span.set(outcome="rejected")
                return {"stop_reason": "rejected", "answer": f"Not posted: {e}."}
            span.set(outcome="posted")
        return {"comment": comment, "stop_reason": "posted",
                "answer": f"Posted to {comment.issue_key} as {comment.author}, approved by {comment.approved_by}."}

    def after_approval(s: State) -> str:
        a, p = s.get("approval") or {}, s["proposal"]
        return "post" if a.get("approved") is True and a.get("approver") and a["approver"] != p["requested_by"] else "declined"

    def declined_node(s: State) -> State:
        a, p = s.get("approval") or {}, s["proposal"]
        if a.get("approved") is True and a.get("approver") == p["requested_by"]:
            why = "the requester cannot approve their own write"
        elif a.get("approved") is True:
            why = "no approver was named"
        else:
            why = a.get("reason") or f"{a.get('approver') or 'the approver'} declined"
        with get_tracer().span("custom", "approval", decision="declined", approver=a.get("approver"),
                               requested_by=p["requested_by"], issue_key=p["issue_key"],
                               self_approval=a.get("approved") is True and a.get("approver") == p["requested_by"]) as span:
            span.content(reason=why)  # the approver's own words
        return {"stop_reason": "declined", "answer": f"Not posted to {p['issue_key']}: {why}."}

    def retrieve_node(s: State) -> State:
        with get_tracer().span("tool", "search_wiki", caller=s["caller"].id, k=k) as span:
            pages = knowledge(s["question"], s["caller"], k)
            span.set(outcome="ok", page_ids=[pid for pid, _ in pages])
            span.content(input=s["question"], output=[title for _, title in pages])
        return {"pages": pages}

    def respond_node(s: State) -> State:
        if s.get("stop_reason"):
            return {}
        if s["route"] == "knowledge":
            pages = s.get("pages") or []
            if not pages:
                return {"answer": "Not in the wiki, or not in any page you can open."}
            listing = "; ".join(f"{title} [{pid}]" for pid, title in pages)
            return {"answer": f"Relevant pages you can open: {listing}"}
        r, spec = s["result"], s["spec"]
        if spec.group_by and r.groups:
            body = ", ".join(f"{k}: {v}" for k, v in r.groups.items())
            head = f"{r.count} {_describe(spec)}, by {spec.group_by.replace('_', ' ')}: {body}."
        else:
            head = f"{r.count} {_describe(spec)}."
        keys = (" Issues: " + ", ".join(r.keys) + (" and more" if r.truncated else "") + ".") if r.keys else ""
        notes = " ".join(s.get("assumptions") or [])
        return {"answer": (
            f"{head}{keys} Counts include only issues you can see; Jira applied your permissions."
            + (f" {notes}" if notes else "") + f"\nJQL: {r.jql}"
        )}

    def after_decide(s: State) -> str:
        if s.get("stop_reason"):
            return "respond"
        return {"knowledge": "retrieve", "comment": "propose"}.get(s["route"], "run_tool")

    g = StateGraph(State)
    g.add_node("decide", decide_node)
    g.add_node("run_tool", tool_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("respond", respond_node)
    g.set_entry_point("decide")
    routes = {"run_tool": "run_tool", "retrieve": "retrieve", "respond": "respond"}
    if comment_tool is not None:
        g.add_node("propose", propose_node)
        g.add_node("approve", approve_node)
        g.add_node("post", post_node)
        g.add_node("declined", declined_node)
        routes["propose"] = "propose"
        g.add_conditional_edges("propose", lambda s: "respond" if s.get("stop_reason") else "approve",
                                {"respond": "respond", "approve": "approve"})
        g.add_conditional_edges("approve", after_approval, {"post": "post", "declined": "declined"})
        g.add_edge("post", "respond")
        g.add_edge("declined", "respond")
    g.add_conditional_edges("decide", after_decide, routes)
    g.add_edge("run_tool", "respond")
    g.add_edge("retrieve", "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer)


def _config(thread_id: str | None) -> dict | None:
    return {"configurable": {"thread_id": thread_id}} if thread_id else None


def _ended(out: State) -> str:
    if "__interrupt__" in out:
        return "pending_approval"
    return out.get("stop_reason") or "answered"


def ask(app, question: str, caller: Caller, today: date, thread_id: str | None = None) -> State:
    """Run one question. With a checkpointer, pass a thread_id; a write then returns with
    "__interrupt__" set and waits for resume(). One trace per call, grouped by thread_id."""
    tracer = get_tracer()
    with tracer.trace("assistant", group_id=thread_id, metadata={"caller": caller.id}):
        with tracer.span("agent", "assistant", caller=caller.id) as span:
            span.content(input=question)
            out = app.invoke({"question": question, "caller": caller, "today": today}, _config(thread_id))
            span.set(route=out.get("route"), ended=_ended(out))
            span.content(output=out.get("answer"))
    return out


def pending(app, thread_id: str) -> dict | None:
    """The write awaiting approval on this thread, or None. Reads the saved checkpoint, so it
    works from a different process or after a restart."""
    for task in app.get_state(_config(thread_id)).tasks:
        for i in task.interrupts:
            return i.value
    return None


def resume(app, thread_id: str, approved: bool, approver: str, reason: str = "") -> State:
    """Answer a pending approval and finish the run. The model is not called again."""
    tracer = get_tracer()
    with tracer.trace("assistant.resume", group_id=thread_id, metadata={"approver": approver}):
        with tracer.span("agent", "assistant", approver=approver) as span:
            out = app.invoke(Command(resume={"approved": approved, "approver": approver, "reason": reason}),
                             _config(thread_id))
            span.set(route=out.get("route"), ended=_ended(out))
            span.content(output=out.get("answer"))
    return out
