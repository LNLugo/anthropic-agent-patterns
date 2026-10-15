"""Ask the assistant a question as one or more users.

Counting questions go to the bounded Jira tool. Knowledge questions go to
07_rag's permission-filtered retriever over the synthetic wiki. Dates are
relative to --today (default 2026-10-12) so the output is reproducible.

    python demo_assistant.py
    python demo_assistant.py "How many P1 incidents did the billing platform have last quarter?" --users billing_oncall new_hire
    python demo_assistant.py "How do I rotate the billing API credentials?" --users billing_oncall new_hire
    python demo_assistant.py --router rules          # offline: the rules instead of the model
    python demo_assistant.py "How many INFRA incidents last quarter?" --policy finance

The model router (default) needs the key for LLM_PROVIDER (default openai / gpt-6-luna).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from agent_tools.assistant import ask, build_assistant
from agent_tools.jira_mock import Caller, MockJira
from agent_tools.guardrails import ToolGuardrailTripwireTriggered, finance_assistant_policy
from agent_tools.jql_tool import BoundedJiraTool
from agent_tools.model_router import ModelRouter
from agent_tools.router import RulesRouter

HERE = Path(__file__).parent
JIRA = HERE / "tests" / "fixtures" / "mini_jira.json"
RAG = HERE.parent / "07_rag"
WIKI = RAG / "tests" / "fixtures" / "mini_confluence.json"

HEADLINE = "How many P1 incidents did the billing platform have last quarter?"

# Pre-generation abstention gate. On the synthetic wiki, real matches score 3 to 8
# and pages that share only words like "how" and "the" score about 0.5. This value
# is set by eye on 8 pages, not calibrated; the design calibrates the threshold on
# an evaluation set, over reranker scores rather than BM25.
MIN_SCORE = 1.0


def wiki_search():
    """07_rag's permission-filtered BM25, adapted to (question, caller, k) -> [(page_id, title)]."""
    sys.path.insert(0, str(RAG))
    from rag.acl import PermissionFilteredBM25, User, build_index, load_wiki

    wiki, _ = load_wiki(WIKI)
    index = build_index(wiki)
    retriever = PermissionFilteredBM25(index)

    def search(question: str, caller: Caller, k: int):
        user = User(caller.id, caller.groups)
        seen, out = set(), []
        for s in retriever.retrieve(question, user, k):
            pid = index.page_of[s.chunk.id]
            if pid not in seen and s.score >= MIN_SCORE:
                seen.add(pid)
                out.append((pid, wiki.pages[pid].title))
        return out

    return search


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("question", nargs="?", default=HEADLINE)
    ap.add_argument("--users", nargs="+", default=["billing_oncall", "new_hire", "contractor"])
    ap.add_argument("--today", type=date.fromisoformat, default=date(2026, 10, 12))
    ap.add_argument("--router", choices=["model", "rules"], default="model",
                    help="model: a model choosing a tool, with the coverage check (default); rules: offline")
    ap.add_argument("--policy", choices=["none", "finance"], default="none",
                    help="finance: the guardrail policy for a finance assistant serving BILL and PAY")
    args = ap.parse_args()

    jira, users = MockJira.from_fixture(JIRA)
    tool = BoundedJiraTool(jira)
    if args.router == "model":
        sys.path.insert(0, str(HERE.parent))  # llm_provider.py at the repo root
        from llm_provider import get_chat_model
        router = ModelRouter(get_chat_model(), tool)
    else:
        router = RulesRouter()
    policy = finance_assistant_policy() if args.policy == "finance" else []
    app = build_assistant(tool, wiki_search(), router, guardrails=policy)
    print(f"router: {args.router}   policy: {args.policy}")

    print(f'Question: "{args.question}"   (today = {args.today.isoformat()})\n')
    for uid in args.users:
        if uid not in users:
            print(f"unknown user {uid!r}; choose from: {', '.join(users)}")
            return 2
        caller = users[uid]
        try:
            out = ask(app, args.question, caller, args.today)
        except ToolGuardrailTripwireTriggered as e:
            print(f"{uid}: HALTED by guardrail {e.guardrail.name}: {e.output.output_info}\n")
            continue
        print(f"{uid}  ({', '.join(sorted(caller.groups)) or 'no groups'})   route: {out['route']}")
        for line in out["answer"].splitlines():
            print(f"   {line}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
