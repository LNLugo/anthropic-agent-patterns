"""Tool guardrails on the JQL tool, for a finance assistant scoped to BILL and PAY.

    python demo_guardrails.py           # three scripted runs, offline: allow, reject_content, raise_exception
    python demo_guardrails.py --live "How many INFRA incidents last quarter?" --user new_hire

Offline, the model's tool calls are scripted so the output is reproducible; the
guardrails, the tool and the mock Jira are real. --live sends the question to
the model set by LLM_PROVIDER (default openai / gpt-6-luna) instead.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # llm_provider.py at the repo root

from langchain_core.messages import AIMessage  # noqa: E402

from llm_provider import Step, get_chat_model, scripted, scripted_model  # noqa: E402

from agent_tools.guardrails import ToolGuardrailTripwireTriggered, finance_assistant_policy  # noqa: E402
from agent_tools.jira_mock import MockJira  # noqa: E402
from agent_tools.jql_tool import BoundedJiraTool  # noqa: E402
from agent_tools.tool_loop import ToolLoop  # noqa: E402

TODAY = date(2026, 10, 12)
Q3 = {"created_from": "2026-07-01", "created_before": "2026-10-01"}


def count(project):
    return AIMessage(content="", tool_calls=[{"name": "count_jira_issues", "id": "c1",
                                              "args": {"project": project, "project_evidence": project,
                                                       "issue_type": "Incident", **Q3}}])


SCENARIOS = [
    ("allow", "How many BILL incidents in Q3?", "billing_oncall",
     [count("BILL"), Step(responder=lambda c: "Answered from the tool result: " + c.messages[-1].content)]),
    ("reject_content", "How many INFRA incidents in Q3?", "new_hire",
     [count("INFRA"), AIMessage(content="", tool_calls=[{"name": "ask_clarification", "id": "q",
                                                         "args": {"question": "I can count BILL or PAY. Which one?"}}])]),
    ("raise_exception", "How many SECOPS incidents in Q3?", "sec_engineer", [count("SECOPS")]),
]


def show(label, run):
    print(f"\n=== {label}")
    try:
        result = run()
    except ToolGuardrailTripwireTriggered as e:
        for r in e.records:
            print(f"  {r.stage:6} {r.guardrail:20} {r.behavior}")
        print(f"  HALTED by {e.guardrail.name}: {e.output.output_info}")
        return
    for r in result.records:
        print(f"  {r.stage:6} {r.guardrail:20} {r.behavior}")
    print(f"  status: {result.status} after {result.turns} turn(s), Jira queries run: {len(result.tool_results)}")
    print(f"  {result.text}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", metavar="QUESTION", help="ask the configured model instead of replaying scripts")
    ap.add_argument("--user", default="new_hire", help="mock Jira user to act as (live mode)")
    args = ap.parse_args()

    jira, users = MockJira.from_fixture(HERE / "tests" / "fixtures" / "mini_jira.json")
    tool, policy = BoundedJiraTool(jira), finance_assistant_policy()

    if args.live:
        loop = ToolLoop(get_chat_model(), tool, policy)
        show(f"live: {args.live}", lambda: loop.run(args.live, users[args.user], TODAY))
        return 0

    for behavior, question, user, steps in SCENARIOS:
        def run(question=question, user=user, steps=steps):
            with scripted(steps):
                return ToolLoop(scripted_model(), tool, policy).run(question, users[user], TODAY)
        show(f"{behavior}: {question} (as {user})", run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
