"""Model based routing and extraction through native tool calling.

The rules in router.py are the measured baseline. Here a model makes the same
two decisions by choosing a tool:

  count_jira_issues   aggregation, with the QuerySpec as the tool arguments
  search_wiki         knowledge, answered by 07_rag's permission filtered retrieval
  ask_clarification   the question cannot be answered without guessing

The model must call exactly one tool, and which tool it picks is the route. The
bound does not move: the arguments go through BoundedJiraTool.spec_from_arguments
and validate(), the same code the rules path uses, so a model that invents a
project, an enum value or a raw "jql" argument is rejected before anything runs.

Conventions the rules encode in code (calendar quarters, an exclusive end date,
a 90 day default, "P1" meaning a P1 incident) are stated to the model in the
system prompt. They are the tool's contract, not hints toward the answers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta

from . import coverage as coverage_check
from .jql_tool import BoundedJiraTool, QuerySpec, ToolRejected, validate

PROJECT_NAMES = {"BILL": "Billing Platform", "PAY": "Payments", "INFRA": "Infrastructure"}

SEARCH_WIKI = {
    "name": "search_wiki",
    "description": (
        "Search the engineering wiki (Confluence) for pages, procedures, runbooks, explanations "
        "and how-to guidance. Use for any question answered by reading documentation rather than "
        "by counting Jira issues."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {"query": {"type": "string", "description": "What to search for"}},
    },
}

ASK_CLARIFICATION = {
    "name": "ask_clarification",
    "description": (
        "Ask the user one short question when the request cannot be answered without guessing, "
        "for example a Jira count with no project. Nothing is run."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["question"],
        "properties": {"question": {"type": "string"}},
    },
}

ROUTES = {"count_jira_issues": "aggregation", "search_wiki": "knowledge", "ask_clarification": "clarify"}


def system_prompt(today: date) -> str:
    projects = ", ".join(f"{k} = {v}" for k, v in PROJECT_NAMES.items())
    default_from = today - timedelta(days=89)
    return f"""You route questions from engineers. Call exactly one tool.

Use count_jira_issues when the user wants a number or a list of Jira issues
(incidents, outages, bugs, tasks, changes, tickets). Use search_wiki when they
want a page, a procedure, an explanation or how to do something, even if the
question mentions incidents or bugs. Use ask_clarification only when a count
is wanted but the project cannot be identified.

Today is {today.isoformat()}. Projects: {projects}.

Rules for count_jira_issues arguments:
- created_from is inclusive and created_before is exclusive (the day after the last day).
- Quarters and months are calendar periods. "Last quarter" is the previous full calendar
  quarter; "this quarter", "this month" and "this year" run through today, so
  created_before is tomorrow ({(today + timedelta(days=1)).isoformat()}).
- "Last N days" ends today and includes today.
- With no period given, use the last 90 days: created_from {default_from.isoformat()},
  created_before {(today + timedelta(days=1)).isoformat()}.
- P1 to P4 are incident priorities: if a priority is given and no other type is named,
  issue_type is Incident. Outages are incidents.
- Set status only when the user names a state (open, in progress, resolved, closed,
  or a verb like "closed"). Leave out any field the user did not specify.
- Use group_by only when the user asks for a breakdown ("by priority", "per status").
"""


EVIDENCE_FIELD = "project_evidence"


def with_evidence(schema: dict) -> dict:
    """The count tool's schema plus a required field quoting the words that name the project.
    The Jira tool itself is unchanged; the field is stripped before its arguments are parsed."""
    s = {**schema, "input_schema": {**schema["input_schema"]}}
    props = dict(s["input_schema"]["properties"])
    props[EVIDENCE_FIELD] = {
        "type": "string",
        "description": "The exact words from the user's question that name the project, copied verbatim.",
    }
    s["input_schema"]["properties"] = props
    s["input_schema"]["required"] = [*s["input_schema"]["required"], EVIDENCE_FIELD]
    return s


def openai_style(tool: dict) -> dict:
    """{name, description, input_schema} as the function format both LangChain chat models accept."""
    return {"type": "function", "function": {
        "name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"],
    }}


@dataclass(frozen=True)
class Decision:
    route: str                        # aggregation | knowledge | clarify | none
    spec: QuerySpec | None = None
    rejected: str | None = None       # set when count_jira_issues arguments failed the bound
    tool_name: str | None = None
    arguments: dict | None = None
    coverage: str | None = None       # set when the coverage check stopped a valid query
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


class ModelRouter:
    def __init__(self, llm, tool: BoundedJiraTool, coverage: bool = True):
        """coverage=False reproduces the router measured before the check existed: same schema, no check."""
        self.tool, self.coverage = tool, coverage
        count_schema = with_evidence(tool.schema) if coverage else tool.schema
        tools = [openai_style(count_schema), openai_style(SEARCH_WIKI), openai_style(ASK_CLARIFICATION)]
        self.llm = llm.bind_tools(tools, tool_choice="any")

    def decide(self, question: str, today: date) -> Decision:
        t0 = time.perf_counter()
        msg = self.llm.invoke([("system", system_prompt(today)), ("human", question)])
        latency = time.perf_counter() - t0
        usage = getattr(msg, "usage_metadata", None) or {}
        tokens = {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}

        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            return Decision("none", latency_s=latency, **tokens)
        call = calls[0]
        name, args = call["name"], dict(call.get("args") or {})
        route = ROUTES.get(name, "none")
        spec = rejected = covered = None
        if name == self.tool.name:
            tool_args = {k: v for k, v in args.items() if k != EVIDENCE_FIELD} if self.coverage else args
            try:
                spec = self.tool.spec_from_arguments(tool_args)
                validate(spec, self.tool.allowed_projects)
            except ToolRejected as e:
                spec, rejected = None, str(e)
            if spec is not None and self.coverage:
                covered = coverage_check.check(question, spec.project, args.get(EVIDENCE_FIELD))
                if covered:
                    route, spec = "clarify", None
        return Decision(route, spec, rejected, name, args, coverage=covered, latency_s=latency, **tokens)
