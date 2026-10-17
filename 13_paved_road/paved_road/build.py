"""build(use_case_dir): one use case's configuration, assembled from the platform.

    08  the bounded Jira tool, the catalog, the guardrail policy in order, the router,
        the coverage check, approval for writes with a checkpointer
    07  permission-filtered wiki search, if the use case declares a wiki
    09  memory, if declared: a session per user, with retention
    10  tracing, always on, content off (OPS-01); not configurable
    11  a spend meter on the trace and a budget checked before every model call

Nothing here knows a project code or a use case's name. What differs between use
cases is in their folder: use_case.yaml, prompts/, golden.yaml and hooks.py.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from agent_tools.assistant import ask, build_assistant, checkpoint_serde, pending, resume
from agent_tools.comments import CommentStore, CommentTool
from agent_tools.guardrails import (ToolGuardrailTripwireTriggered, deployment_scope, keys_match_project,
                                    question_coverage, restricted_project)
from agent_tools.jira_mock import Caller, MockJira
from agent_tools.jql_tool import BoundedJiraTool
from agent_tools.model_router import ModelRouter
from agent_tools.router import RulesRouter
from finops.budget import BudgetPolicy, SpendMeter
from finops.prices import PriceBook
from tracing import JsonlExporter, Tracer, set_tracer

from . import ROOT
from .spec import UseCaseSpec, load_spec


def policy_for(spec: UseCaseSpec) -> list:
    """The declared guardrails, in the declared order, over the use case's catalog."""
    make = {
        "restricted_project": lambda: restricted_project(spec.catalog.restricted),
        "deployment_scope": lambda: deployment_scope(spec.catalog.served),
        "question_coverage": lambda: question_coverage(spec.catalog),
        "keys_match_project": keys_match_project,
    }
    return [make[name]() for name in spec.policy]


def wiki_search(path: Path, min_score: float):
    """07's permission-filtered BM25, as (question, caller, k) -> [(page_id, title)]."""
    rag = str(ROOT / "07_rag")
    if rag not in sys.path:
        sys.path.append(rag)
    from rag.acl import PermissionFilteredBM25, User, build_index, load_wiki

    wiki, _ = load_wiki(path)
    index = build_index(wiki)
    retriever = PermissionFilteredBM25(index)

    def search(question: str, caller: Caller, k: int):
        seen, out = set(), []
        for s in retriever.retrieve(question, User(caller.id, caller.groups), k):
            pid = index.page_of[s.chunk.id]
            if pid not in seen and s.score >= min_score:
                seen.add(pid)
                out.append((pid, wiki.pages[pid].title))
        return out

    return search


def no_wiki(question: str, caller: Caller, k: int):
    return []


def _hooks(folder: Path):
    path = folder / "hooks.py"
    if not path.exists():
        return None
    mod_name = f"use_case_hooks_{folder.name}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class UseCase:
    spec: UseCaseSpec
    app: object
    users: dict[str, Caller]
    tool: BoundedJiraTool
    router: object
    tracer: Tracer
    meter: SpendMeter | None
    comments: CommentStore | None
    today: date
    sessions: dict = field(default_factory=dict)

    def caller(self, as_: str | None) -> Caller:
        """Delegated: the named user, with their own Jira permissions. Service: the service
        identity, whoever asks (APP-01)."""
        if self.spec.identity["mode"] == "service":
            if as_ not in (None, self.spec.identity["id"]):
                raise PermissionError(f"this use case runs as {self.spec.identity['id']}, not {as_}")
            as_ = self.spec.identity["id"]
        if as_ not in self.users:
            raise PermissionError(f"unknown user {as_!r}")
        return self.users[as_]

    def _traced(self, run: Callable[[], dict]) -> dict:
        previous = set_tracer(self.tracer)
        try:
            return run()
        finally:
            set_tracer(previous)

    def ask(self, question: str, as_: str | None = None, thread_id: str | None = None,
            session_id: str | None = None) -> dict:
        """One question. Returns 08's state plus "ended", the typed outcome (AI-03).
        A halt (a guardrail's raise_exception) stops the run and comes back as ended=halted."""
        caller = self.caller(as_)

        def run() -> dict:
            try:
                if self.spec.memory is not None:
                    out = self._ask_with_memory(question, caller, thread_id, session_id)
                else:
                    out = ask(self.app, question, caller, self.today, thread_id=thread_id)
            except ToolGuardrailTripwireTriggered as halt:
                return {"ended": "halted", "guardrail": halt.guardrail.name, "route": None,
                        "answer": "Stopped: this request is not allowed, and it has been recorded."}
            return out | {"ended": ended(out)}

        return self._traced(run)

    def _ask_with_memory(self, question, caller, thread_id, session_id):
        from memory import strategies
        from memory.assistant import ask_with_memory
        from memory.session import InMemorySession, SQLiteSession

        m = self.spec.memory
        sid = session_id or f"{caller.id}-default"
        key = (caller.id, sid)
        if key not in self.sessions:
            self.sessions[key] = (SQLiteSession(m.path, sid, caller.id, retention_days=m.retention_days)
                                  if m.path else InMemorySession(sid, caller.id, retention_days=m.retention_days))
        pick = getattr(strategies, m.strategy)
        strategy = pick if m.strategy == "append_all" else pick()
        return ask_with_memory(self.app, self.sessions[key], question, caller, self.today, strategy=strategy,
                               thread_id=thread_id)

    def pending(self, thread_id: str) -> dict | None:
        return pending(self.app, thread_id)

    def decide(self, thread_id: str, approved: bool, approver: str, reason: str = "") -> dict:
        """A person's decision on a waiting write. The approver is checked by 08: never the requester."""
        out = self._traced(lambda: resume(self.app, thread_id, approved=approved, approver=approver, reason=reason))
        return out | {"ended": ended(out)}


def ended(out: dict) -> str:
    if "__interrupt__" in out:
        return "pending_approval"
    return out.get("stop_reason") or "answered"


def build(folder: str | Path, llm=None, state_dir: str | Path | None = None, trace_dir: str | Path | None = None,
          exporters: list | None = None, today: date | None = None) -> UseCase:
    """Assemble a use case from its folder.

    llm        the router's chat model; by default llm_provider.get_chat_model(model=<router.model>).
               The gate passes a scripted model that replays each case's reference call.
    state_dir  where approvals wait (SQLite, survives a restart); default <folder>/.state
    trace_dir  where traces are written (JSONL); default <folder>/traces
    exporters  replace the trace file (tests); the spend meter is always added
    today      the date questions are relative to; default today
    """
    spec = load_spec(folder)
    today = today or date.today()
    jira, users = MockJira.from_fixture(spec.jira)
    tool = BoundedJiraTool(jira, allowed_projects=spec.catalog.queryable, max_keys=spec.count_max_keys)

    comment_tool = store = checkpointer = None
    if spec.comment:
        from langgraph.checkpoint.sqlite import SqliteSaver

        store = CommentStore()
        comment_tool = CommentTool(jira, store, allowed_projects=spec.catalog.queryable)
        state = Path(state_dir) if state_dir else spec.folder / ".state"
        state.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(state / "approvals.sqlite", check_same_thread=False)
        checkpointer = SqliteSaver(conn, serde=checkpoint_serde())

    meter = budget = None
    if spec.budget is not None:
        meter = SpendMeter(PriceBook.load(), today=lambda: today)
        budget = BudgetPolicy(meter, limits=spec.budget.limits, default_limit=spec.budget.daily_limit_usd,
                              reserve=spec.budget.reserve_usd)
    sinks = list(exporters) if exporters is not None else [JsonlExporter(trace_dir or spec.folder / "traces")]
    tracer = Tracer([*sinks, *([meter] if meter else [])], include_sensitive=False)

    if spec.router.kind == "model":
        if llm is None:
            from llm_provider import get_chat_model

            llm = get_chat_model(model=spec.router.model)
        instructions = ("\n" + spec.router.prompt.read_text(encoding="utf-8")) if spec.router.prompt else ""
        router = ModelRouter(llm, tool, coverage=spec.router.coverage, comment_tool=comment_tool,
                             catalog=spec.catalog, instructions=instructions)
        router.model = spec.router.model  # what the budget and the trace price, whatever stands in for it
    else:
        router = RulesRouter(spec.catalog)

    knowledge = wiki_search(spec.wiki, spec.wiki_min_score) if spec.wiki else no_wiki
    app = build_assistant(tool, knowledge, router, guardrails=policy_for(spec), comment_tool=comment_tool,
                          checkpointer=checkpointer, budget=budget, catalog=spec.catalog)
    uc = UseCase(spec, app, users, tool, router, tracer, meter, store, today)
    hooks = _hooks(spec.folder)
    if hooks is not None and hasattr(hooks, "configure"):
        hooks.configure(uc)
    return uc
