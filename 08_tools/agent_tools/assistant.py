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
from typing import Any, Callable, Protocol, TypedDict

from langgraph.graph import END, StateGraph

from .guardrails import GuardrailRecord, ToolCallData, ToolGuardrail, run_guardrails
from .jira_mock import Caller
from .jql_tool import BoundedJiraTool, QuerySpec, ToolRejected, ToolResult

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
    spec: Any
    arguments: dict
    bound_rejection: str
    assumptions: list[str]
    result: ToolResult
    pages: list[tuple[str, str]]
    guardrail_records: list[GuardrailRecord]
    stop_reason: str
    answer: str


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
                    guardrails: list[ToolGuardrail] = (), k: int = 3):
    guardrails = list(guardrails)

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
        if s.get("bound_rejection"):
            return {"guardrail_records": records, "stop_reason": "rejected",
                    "answer": f"I can't run that query: {s['bound_rejection']}."}
        try:
            result = tool.run(s["spec"], s["caller"])  # the tool's bound runs inside
        except ToolRejected as e:
            return {"guardrail_records": records, "stop_reason": "rejected", "answer": f"I can't run that query: {e}."}
        rejected = run_guardrails(guardrails, "output", ToolCallData(
            tool.name, s["arguments"], s["question"], s["caller"], output=result), records)
        if rejected is not None:
            return {"guardrail_records": records, "stop_reason": "guardrail", "answer": rejected}
        return {"guardrail_records": records, "result": result}

    def retrieve_node(s: State) -> State:
        return {"pages": knowledge(s["question"], s["caller"], k)}

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
        return "retrieve" if s["route"] == "knowledge" else "run_tool"

    g = StateGraph(State)
    g.add_node("decide", decide_node)
    g.add_node("run_tool", tool_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("respond", respond_node)
    g.set_entry_point("decide")
    g.add_conditional_edges("decide", after_decide, {"run_tool": "run_tool", "retrieve": "retrieve", "respond": "respond"})
    g.add_edge("run_tool", "respond")
    g.add_edge("retrieve", "respond")
    g.add_edge("respond", END)
    return g.compile()


def ask(app, question: str, caller: Caller, today: date) -> State:
    return app.invoke({"question": question, "caller": caller, "today": today})
