"""Does any prompt, document, model reply or tool result reach the trace file?

Runs 06, 07 and 08 offline, with a distinct marker planted in every place content
comes from, writes their traces with the real JSONL exporter, and counts each
marker in the file. Then runs the same scenarios again with sensitive data on, as
the control: a marker that shows up there proves this check would have caught it.

    python run_leak_check.py                    # writes results/leak_check.json
    python run_leak_check.py --keep traces_demo # also keep the trace files, for audit_report.py

No API key is needed: the model is the scripted stand-in.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ["LLM_PROVIDER"] = "scripted"  # before any module builds its model

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (HERE, ROOT, ROOT / "06_agents", ROOT / "07_rag", ROOT / "08_tools", ROOT / "12_integration"):
    sys.path.insert(0, str(p))

from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402

from llm_provider import scripted, scripted_model  # noqa: E402
from tracing import JsonlExporter, Tracer, set_tracer  # noqa: E402

TODAY = date(2026, 10, 12)
Q3 = {"created_from": "2026-07-01", "created_before": "2026-10-01"}

# Where content comes from, and the marker planted there. Each marker is unique, so
# a hit says exactly which kind of content leaked.
MARKERS = {
    "06 question":            "QX06-question-1d4e",
    "06 model reply":         "QX06-reply-77ab",
    "06 bad model reply":     "QX06-badreply-c210",
    "06 tool result":         "Key risk areas identified",
    "07 contract text":       "QX07-doc-5f19",
    "07 model answer":        "QX07-answer-90ce",
    "08 question":            "QX08-question-3b7d",
    "08 restricted issue key": "BILL-107",
    "08 wiki page title":     "QX08-title-a4c2",
    "08 model clarification": "QX08-clarify-e8f0",
    "08 model final answer":  "QX08-answer-2c61",
    "08 comment body":        "QX08-body-6d93",
    "08 approver reason":     "QX08-reason-f05b",
    "08 model tool argument": "QX08-arg-9e1d",
    "08 project evidence":    "QX08-evidence-41aa",
    "08 tool bound error":    "QX08-date-c77e",
    "12 work order text":     "QX12-workorder-5b0e",
}
M = MARKERS


def ai(name, call_id="c1", **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def run_06():
    import agents_06_v3_1 as agents

    with scripted([{"decision": "act", "tool": "risk_analysis"}, {"decision": "finish"}, f"Answer {M['06 model reply']}"]):
        agents.run(f"Assess the platform {M['06 question']}")
    with scripted([f"Go with Azure {M['06 bad model reply']}"]):
        agents.run(f"Which cloud? {M['06 question']}")


def run_07():
    from rag.data import load_cuad
    from rag.chunking import chunk_contract
    from rag.graph import answer_query, build_graph
    from rag.retrievers import BM25Retriever

    contracts, queries = load_cuad(ROOT / "07_rag" / "tests" / "fixtures" / "mini_cuad.json")
    clause = "governed by the laws of the State of New York"
    contract = contracts[0]
    assert clause in contract.text
    planted = f"{clause} {M['07 contract text']}"
    contract = dataclasses.replace(contract, text=contract.text.replace(clause, planted))
    q = next(q for q in queries if q.category == "Governing Law")
    kids, _ = chunk_contract(contract.text, contract.id, size=300, overlap=60, parent_size=None)
    retriever = BM25Retriever()
    retriever.fit(kids)

    def llm(prompt):
        chunk_id = prompt.split("[", 1)[1].split("]", 1)[0]
        return {"text": json.dumps({"abstain": False, "answer": f"New York law {M['07 model answer']}",
                                    "citations": [{"chunk_id": chunk_id, "quote": planted}]}),
                "input_tokens": 1200, "output_tokens": 80}

    final = answer_query(build_graph(retriever, llm, k=3), q)
    assert final["status"] == "answered", final  # the planted text was retrieved and cited


def run_08(workdir: Path):
    from agent_tools.assistant import ask, build_assistant, checkpoint_serde, resume
    from agent_tools.comments import CommentStore, CommentTool
    from agent_tools.guardrails import ToolInputGuardrailTripwireTriggered, finance_assistant_policy
    from agent_tools.jira_mock import MockJira
    from agent_tools.jql_tool import BoundedJiraTool
    from agent_tools.model_router import ModelRouter
    from agent_tools.tool_loop import ToolLoop

    jira, users = MockJira.from_fixture(ROOT / "08_tools" / "tests" / "fixtures" / "mini_jira.json")
    tool = BoundedJiraTool(jira)
    oncall, sec = users["billing_oncall"], users["sec_engineer"]
    wiki = lambda q, c, k: [("ENG-3", f"Credential rotation {M['08 wiki page title']}")]  # noqa: E731
    app = build_assistant(tool, wiki, ModelRouter(scripted_model(), tool), guardrails=finance_assistant_policy())
    count = lambda p: ai("count_jira_issues", project=p, project_evidence=p, **Q3)  # noqa: E731

    with scripted([count("BILL")]):  # billing_oncall can see the restricted BILL-107
        out = ask(app, f"How many BILL issues in Q3? {M['08 question']}", oncall, TODAY)
    assert M["08 restricted issue key"] in out["answer"]
    with scripted([count("SECOPS")]):
        try:
            ask(app, f"How many SECOPS issues? {M['08 question']}", sec, TODAY)
        except ToolInputGuardrailTripwireTriggered:
            pass
    with scripted([ai("search_wiki", query="rotation")]):
        ask(app, f"How do we rotate credentials? {M['08 question']}", oncall, TODAY)
    with scripted([ai("ask_clarification", question=f"Which project? {M['08 model clarification']}")]):
        ask(app, f"How many incidents? {M['08 question']}", oncall, TODAY)
    # A model can put any text in an argument. Out of scope, then evidence not in the
    # question, then a valid call; each rejection's message quotes what the model sent.
    with scripted([count(M["08 model tool argument"]),
                   ai("count_jira_issues", "c2", project="BILL", project_evidence=M["08 project evidence"], **Q3),
                   ai("count_jira_issues", "c3", project="BILL", project_evidence="BILL", **Q3),
                   f"There were 9. {M['08 model final answer']}"]):
        ToolLoop(scripted_model(), tool, finance_assistant_policy()).run(
            f"How many BILL issues in Q3? {M['08 question']}", oncall, TODAY)
    with scripted([ai("count_jira_issues", project="BILL", project_evidence="BILL",
                      created_from=M["08 tool bound error"], created_before="2026-10-01"), "Bad date."]):
        ToolLoop(scripted_model(), tool, finance_assistant_policy()).run(
            f"How many BILL issues? {M['08 question']}", oncall, TODAY)

    store, db = CommentStore(), workdir / "approvals.db"

    def writer():
        comments = CommentTool(jira, store)
        conn = sqlite3.connect(db, check_same_thread=False)
        return build_assistant(tool, wiki, ModelRouter(scripted_model(), tool, comment_tool=comments),
                               comment_tool=comments, checkpointer=SqliteSaver(conn, serde=checkpoint_serde()))

    body = f"Q3 P1 count: 4 {M['08 comment body']}"
    for thread, approver, approved, reason in (("req-a", "incident_lead", True, ""),
                                               ("req-b", "incident_lead", False, f"Wrong issue {M['08 approver reason']}")):
        with scripted([ai("comment_on_issue", issue_key="BILL-101", body=body)]):
            ask(writer(), f"Add a note to BILL-101 {M['08 question']}", oncall, TODAY, thread_id=thread)
        resume(writer(), thread, approved=approved, approver=approver, reason=reason)


def run_12(workdir: Path):
    """Event-triggered triage: a work order's free text from the legacy file ends up in the
    proposed comment, so it must stay out of the trace like any other content."""
    from datetime import date as d

    from agent_tools.comments import CommentStore
    from agent_tools.jira_mock import MockJira
    from integration.adapters import detail_record, parse_workorders, service_alert
    from integration.delivery import Broker, Consumer, DeadLetters, ProcessedStore
    from integration.triage import Triage

    jira, users = MockJira.from_fixture(ROOT / "12_integration" / "fixtures" / "transit_jira.json")
    triage = Triage(jira, users, CommentStore(), workdir / "triage.db", TODAY)
    broker = Broker()
    line = detail_record("WO-9001", "ESC-127-02", "S127", "OP", d(2026, 10, 9), f"Chain {M['12 work order text']}")
    events, _ = parse_workorders(f"H20261012\n{line}\nT000000001\n")
    for e in events + [service_alert({"alert_id": "A-1", "asset": "ESC-127-02", "station": "S127",
                                      "kind": "out_of_service", "severity": "major",
                                      "raised_at": "2026-10-12T07:00:00Z", "issue_key": "ELEV-41"})]:
        broker.publish(e)
    Consumer(broker, triage.handlers(), ProcessedStore(workdir / "p.db"), DeadLetters(workdir / "d.db")).run_until_idle()
    assert M["12 work order text"] in triage.pending("triage-ELEV-41")["body"]
    triage.decide("triage-ELEV-41", True, "maintenance_lead")   # posting traces the body, as content


def run_all(folder: Path, include_sensitive: bool) -> Path:
    exporter = JsonlExporter(folder)
    tracer = Tracer([exporter], include_sensitive=include_sensitive)
    previous = set_tracer(tracer)
    try:
        run_06()
        run_07()
        run_08(folder)
        run_12(folder)
    finally:
        set_tracer(previous)
    assert tracer.export_errors == 0
    return exporter.path()


def summarize(path: Path) -> dict:
    text = path.read_text()
    records = [json.loads(line) for line in text.splitlines()]
    spans = [r for r in records if r["type"] == "span"]
    return {
        "traces": sum(1 for r in records if r["type"] == "trace" and r["ended_at"]),
        "spans": len(spans),
        "spans_by_kind": {k: sum(s["kind"] == k for s in spans) for k in sorted({s["kind"] for s in spans})},
        "marker_hits": {source: text.count(marker) for source, marker in MARKERS.items()},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", help="folder to keep the trace files in (default: a temporary folder)")
    ap.add_argument("--out", default=str(HERE / "results" / "leak_check.json"))
    args = ap.parse_args()

    base = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="leak_check_"))
    off = summarize(run_all(base / "sensitive_off", include_sensitive=False))
    on = summarize(run_all(base / "sensitive_on", include_sensitive=True))

    print(f"{'Content source':26} {'off (default)':>14} {'on (control)':>13}")
    for source in MARKERS:
        print(f"{source:26} {off['marker_hits'][source]:>14} {on['marker_hits'][source]:>13}")
    leaked = sum(off["marker_hits"].values())
    blind = [s for s, n in on["marker_hits"].items() if n == 0]
    print(f"\n{off['traces']} traces, {off['spans']} spans: {off['spans_by_kind']}")
    print(f"Markers found with sensitive data off: {leaked}")
    print("Control: every marker found with sensitive data on" if not blind
          else f"Control: not recorded even when on, so not checked: {blind}")

    result = {"sensitive_off": off, "sensitive_on": on, "leaked": leaked, "unchecked_sources": blind}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nWrote {args.out}" + (f"; traces kept in {base}" if args.keep else ""))
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(main())
