"""Human approval on a write: the run pauses, the service restarts, the approval resumes it.

    python demo_approval.py                       # offline: the model's tool call is scripted
    python demo_approval.py --live "Add 'Q3 P1 count: 4' to BILL-101 for the postmortem"

Step 1 asks to post a comment and stops at the approval. Step 2 builds a new
graph on a new connection to the same checkpoint file, as a restarted service
would, finds the pending request there, and answers it. The comment is posted
once, as the requester, with the approver recorded. A self-approval is refused.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # llm_provider.py at the repo root

from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402

from llm_provider import get_chat_model, scripted, scripted_model  # noqa: E402

from agent_tools.assistant import ask, build_assistant, checkpoint_serde, pending, resume  # noqa: E402
from agent_tools.comments import CommentStore, CommentTool  # noqa: E402
from agent_tools.jira_mock import MockJira  # noqa: E402
from agent_tools.jql_tool import BoundedJiraTool  # noqa: E402
from agent_tools.model_router import ModelRouter  # noqa: E402

TODAY = date(2026, 10, 12)
QUESTION = "Add 'Q3 P1 count: 4' to BILL-101 for the postmortem"
CALL = AIMessage(content="", tool_calls=[{"name": "comment_on_issue", "id": "c1",
                                          "args": {"issue_key": "BILL-101", "body": "Q3 P1 count: 4"}}])


def service(db: Path, jira, store, llm):
    """What a deployed service builds at start-up: a graph on its own connection to the checkpoints."""
    conn = sqlite3.connect(db, check_same_thread=False)
    tool, comments = BoundedJiraTool(jira), CommentTool(jira, store)
    app = build_assistant(tool, lambda q, c, k: [], ModelRouter(llm, tool, comment_tool=comments),
                          comment_tool=comments, checkpointer=SqliteSaver(conn, serde=checkpoint_serde()))
    return app, conn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", metavar="QUESTION", help="ask the configured model instead of replaying a script")
    args = ap.parse_args()
    question = args.live or QUESTION

    jira, users = MockJira.from_fixture(HERE / "tests" / "fixtures" / "mini_jira.json")
    store = CommentStore()
    requester = users["billing_oncall"]
    llm = get_chat_model() if args.live else scripted_model()

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "checkpoints.db"
        for thread, approver in (("request-1", "incident_lead"), ("request-2", requester.id)):
            print(f"\n=== {thread}: {question!r} (as {requester.id})")
            app, conn = service(db, jira, store, llm)
            if args.live:
                out = ask(app, question, requester, TODAY, thread_id=thread)
            else:
                with scripted([CALL]):
                    out = ask(app, question, requester, TODAY, thread_id=thread)
            if "__interrupt__" not in out:
                print(f"  no approval needed: {out.get('answer')}")
                conn.close()
                continue
            print("  paused for approval; comments posted so far:", len(store.comments))
            conn.close()
            del app
            print("  -- service restarted --")

            app, conn = service(db, jira, store, llm)
            p = pending(app, thread)
            print(f"  found pending in the checkpoint file: {p['action']} on {p['issue_key']}: {p['body']!r}")
            out = resume(app, thread, approved=True, approver=approver)
            print(f"  approved by {approver}: {out['answer']}")
            conn.close()

    print("\nComments posted:")
    for c in store.comments:
        print(f"  #{c.id} {c.issue_key} by {c.author}, approved by {c.approved_by}: {c.body!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
