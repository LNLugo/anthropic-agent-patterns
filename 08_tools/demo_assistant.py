"""Ask the assistant a question as one or more users.

Counting questions go to the bounded Jira tool. Knowledge questions go to
07_rag's permission-filtered retriever over the synthetic wiki. Dates are
relative to --today (default 2026-10-12) so the output is reproducible.

    python demo_assistant.py
    python demo_assistant.py "How many P1 incidents did the billing platform have last quarter?" --users billing_oncall new_hire
    python demo_assistant.py "How do I rotate the billing API credentials?" --users billing_oncall new_hire
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from agent_tools.assistant import ask, build_assistant
from agent_tools.jira_mock import Caller, MockJira
from agent_tools.jql_tool import BoundedJiraTool

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
    args = ap.parse_args()

    jira, users = MockJira.from_fixture(JIRA)
    app = build_assistant(BoundedJiraTool(jira), wiki_search())

    print(f'Question: "{args.question}"   (today = {args.today.isoformat()})\n')
    for uid in args.users:
        if uid not in users:
            print(f"unknown user {uid!r}; choose from: {', '.join(users)}")
            return 2
        caller = users[uid]
        out = ask(app, args.question, caller, args.today)
        print(f"{uid}  ({', '.join(sorted(caller.groups)) or 'no groups'})   route: {out['route']}")
        for line in out["answer"].splitlines():
            print(f"   {line}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
