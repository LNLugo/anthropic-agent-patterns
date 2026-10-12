"""Assistant graph: route each question to retrieval or to the bounded Jira tool.

    classify --knowledge--> retrieve ------------------------> respond
             --aggregation--> extract --spec--> run_tool ----> respond
                                      --clarify / rejected---> respond

The numbers in an aggregation answer come from the tool, never from a model.
Answers are composed deterministically here so every part is testable; in
production a model would phrase them, with the count, keys and JQL passed
through unchanged.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from .jira_mock import Caller
from .jql_tool import BoundedJiraTool, ToolRejected, ToolResult
from .router import NeedsClarification, classify, extract

# Knowledge retrieval is injected: (question, caller, k) -> [(page_id, title), ...].
# demo_assistant.py wires it to 07_rag's permission-filtered retriever.
KnowledgeSearch = Callable[[str, Caller, int], list[tuple[str, str]]]


class State(TypedDict, total=False):
    question: str
    caller: Caller
    today: date
    route: str
    spec: Any
    assumptions: list[str]
    result: ToolResult
    pages: list[tuple[str, str]]
    stop_reason: str
    answer: str


def _describe(spec) -> str:
    words = [w for w in (spec.priority, spec.status.lower() if spec.status else None) if w]
    words.append((spec.issue_type or "issue").lower() + "s")
    last_day = spec.created_before - timedelta(days=1)
    return f"{' '.join(words)} in {spec.project} created {spec.created_from.isoformat()} to {last_day.isoformat()}"


def build_assistant(tool: BoundedJiraTool, knowledge: KnowledgeSearch, k: int = 3):
    def classify_node(s: State) -> State:
        return {"route": classify(s["question"])}

    def extract_node(s: State) -> State:
        try:
            ex = extract(s["question"], s["today"])
        except NeedsClarification as e:
            return {"stop_reason": "clarify", "answer": str(e)}
        return {"spec": ex.spec, "assumptions": ex.assumptions}

    def tool_node(s: State) -> State:
        try:
            return {"result": tool.run(s["spec"], s["caller"])}
        except ToolRejected as e:
            return {"stop_reason": "rejected", "answer": f"I can't run that query: {e}."}

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

    g = StateGraph(State)
    g.add_node("classify", classify_node)
    g.add_node("extract", extract_node)
    g.add_node("run_tool", tool_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("respond", respond_node)
    g.set_entry_point("classify")
    g.add_conditional_edges("classify", lambda s: s["route"], {"aggregation": "extract", "knowledge": "retrieve"})
    g.add_conditional_edges("extract", lambda s: "respond" if s.get("stop_reason") else "run_tool",
                            {"respond": "respond", "run_tool": "run_tool"})
    g.add_edge("run_tool", "respond")
    g.add_edge("retrieve", "respond")
    g.add_edge("respond", END)
    return g.compile()


def ask(app, question: str, caller: Caller, today: date) -> State:
    return app.invoke({"question": question, "caller": caller, "today": today})
