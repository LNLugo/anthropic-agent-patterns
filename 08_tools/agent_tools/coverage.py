"""Coverage check: does the query cover what the question asked for?

The bound in jql_tool.py stops queries outside policy. It cannot stop a query
that is inside policy but answers a different question: asked about BILL and
PAY, a model that counts only BILL sends valid arguments and gets a valid,
incomplete number. This check runs in code, after the model has chosen its
arguments and before anything is sent to Jira, and asks the user instead of
running when the query would not cover the question.

The model is required to quote the words in the question that name the
project (project_evidence). That makes the choice checkable, the same way
07_rag ships an answer only when its citation resolves to text the model was
shown: here a query runs only when its project resolves to words the user
actually typed.

It fires on positive evidence only:

  several projects    the question names more than one known project
  not in question     the quoted evidence is not text from the question
  generic evidence    the evidence is only words like "incidents" or "tickets"
  mismatch            the evidence names a different project than the one chosen

An informal name the code does not know ("the invoice stack") passes, because
it is real text from the question. What the code cannot judge is whether that
name really means the chosen project; that stays the model's call, and the
quoted evidence is logged so a reviewer can see it.
"""

from __future__ import annotations

import re

from .router import PROJECT_ALIASES

CODES = {"BILL", "PAY", "INFRA", "SECOPS"}
NAMES = {**{alias: code for alias, code in PROJECT_ALIASES.items()}, **{c.lower(): c for c in CODES}}

GENERIC = {
    "incident", "incidents", "outage", "outages", "bug", "bugs", "ticket", "tickets", "tix",
    "issue", "issues", "task", "tasks", "change", "changes", "work", "item", "items",
    "p1", "p2", "p3", "p4", "p1s", "p2s", "p3s", "p4s", "open", "opened", "closed", "resolved",
    "the", "a", "an", "our", "my", "team", "project", "projects", "board", "jira", "all", "any",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def projects_named(text: str) -> set[str]:
    """Known projects named in the text, by code or alias, as whole words."""
    t = _norm(text)
    return {code for name, code in NAMES.items() if re.search(rf"\b{re.escape(name)}s?\b", t)}


def check(question: str, project: str, evidence: str | None, standing: list[str] | tuple = ()) -> str | None:
    """None when the query covers the question; otherwise the reason to ask instead.

    standing: the user's own standing instructions from memory ("from now on, only
    Payments"). The evidence may come from one of them. Nothing else from the
    conversation counts: a project named earlier is not evidence for this question,
    and only the current question is checked for a second project."""
    named = projects_named(question)
    if named - {project}:
        listed = ", ".join(sorted(named | {project}))
        return f"the question names {listed}; the tool counts one project at a time"
    ev = _norm(evidence or "")
    if not ev:
        return "no words in the question identify the project"
    if not any(f" {ev} " in f" {_norm(text)} " for text in (question, *standing)):
        return f"the project evidence {evidence!r} is not in the question"
    if set(ev.split()) <= GENERIC:
        return f"the project evidence {evidence!r} does not name a project"
    ev_named = projects_named(evidence)
    if ev_named and project not in ev_named:
        return f"the evidence {evidence!r} names {', '.join(sorted(ev_named))}, not {project}"
    return None
