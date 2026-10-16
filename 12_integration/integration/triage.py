"""An agent started by an event: triage an outage the moment it is raised.

    service alert (bus) ---------+
    incident created (Jira) -----+--> triage, as svc-alert-triage --> count open incidents (08's bounded tool)
    work orders (nightly file) --> work order index -------------^   propose a comment on the incident
                                                                      --> a person approves --> posted once

Who it runs as. Nobody asked a question, so there is no user to act for. The
worker runs as a service identity, svc-alert-triage, with its own Jira
permissions and the same guardrails and bound as any caller. Its one write, a
comment, waits for a person, and the service can never approve its own request.

Why no model. The event already says what to count and where to write: the
project, the asset, the incident. A router would add cost and a way to be wrong,
so the decision is made in code (08's PresetRouter). A model could be added to
draft the comment's wording; the numbers would still come from the tool.

Idempotency, at two levels:
  * the delivery layer skips an event it has handled (same source and id)
  * triage keys its work on the incident, not the event: an alert and a Jira
    webhook about the same outage are two events and one triage. The approval's
    thread id is "triage-<incident>", so a second event finds the request already
    waiting and does nothing. That also covers a crash after the request was made
    but before the event was marked handled.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from agent_tools.assistant import ask, build_assistant, checkpoint_serde, pending, resume
from agent_tools.comments import CommentStore, CommentTool
from agent_tools.guardrails import (ToolGuardrailTripwireTriggered, deployment_scope, keys_match_project,
                                    restricted_project)
from agent_tools.jira_mock import Caller, MockJira
from agent_tools.jql_tool import BoundedJiraTool, QuerySpec
from agent_tools.mcp_server import PresetRouter
from agent_tools.model_router import Decision

from .envelope import Event, InvalidEvent

SERVICE = "svc-alert-triage"
PROJECTS = frozenset({"ELEV", "SIG"})


def transit_policy():
    """The operations deployment: its two projects, SECOPS halted, results checked."""
    return [restricted_project(), deployment_scope(PROJECTS), keys_match_project()]


@dataclass
class WorkOrders:
    """Latest known state of each work order, from the nightly file. An upsert by work
    order number, so loading the same night twice changes nothing."""

    orders: dict[str, dict] = field(default_factory=dict)

    def upsert(self, e: Event) -> None:
        self.orders[e.data["work_order"]] = dict(e.data)

    def open_on(self, asset: str) -> list[dict]:
        return sorted((w for w in self.orders.values() if w["asset"] == asset and w["status"] != "closed"),
                      key=lambda w: w["work_order"])


class Triage:
    def __init__(self, jira: MockJira, users: dict[str, Caller], store: CommentStore, db: str | Path,
                 today: date, key_on: str = "incident"):
        """key_on="incident" (the design) or "event" (the control: one triage per event)."""
        self.jira, self.users, self.store, self.db, self.today, self.key_on = jira, users, store, Path(db), today, key_on
        self.tool = BoundedJiraTool(jira, allowed_projects=PROJECTS)
        self.conn = sqlite3.connect(self.db, check_same_thread=False)
        self.workorders = WorkOrders()
        self.requested: list[str] = []          # approval requests made, for the measurements

    @property
    def service(self) -> Caller:
        return self.users[SERVICE]

    def graph(self, decision: Decision, comments: bool = False):
        kw = {"comment_tool": CommentTool(self.jira, self.store, allowed_projects=PROJECTS),
              "checkpointer": SqliteSaver(self.conn, serde=checkpoint_serde())} if comments else {}
        return build_assistant(self.tool, lambda q, c, k: [], PresetRouter(decision), guardrails=transit_policy(), **kw)

    # ---------------------------------------------------------------- handlers

    def on_workorder(self, e: Event) -> None:
        self.workorders.upsert(e)

    def on_outage(self, e: Event) -> None:
        try:
            self._triage(e)
        except ToolGuardrailTripwireTriggered as halt:
            # A security event, not a fault: retrying would only repeat the attempt.
            raise InvalidEvent(f"halted by guardrail {halt.guardrail.name}") from None

    def _triage(self, e: Event) -> None:
        issue_key, asset = e.data.get("issue_key"), e.data.get("asset")
        if not issue_key or not asset:
            raise InvalidEvent("an outage event must name its incident and its asset")
        thread = f"triage-{issue_key}" if self.key_on == "incident" else f"triage-{e.key}"
        app = self.graph(Decision("none"), comments=True)
        if app.get_state({"configurable": {"thread_id": thread}}).values:
            return                                  # already triaged: waiting, approved or declined
        project = issue_key.split("-", 1)[0]
        spec = QuerySpec(project=project, created_from=self.today - timedelta(days=30),
                         created_before=self.today + timedelta(days=1), status="Open")
        counted = ask(self.graph(Decision("aggregation", spec)), f"open {project} incidents", self.service, self.today)
        if "result" not in counted:
            raise InvalidEvent(f"count refused: {counted.get('answer')}")
        orders = self.workorders.open_on(asset)
        listed = "; ".join(f"{w['work_order']} ({w['description']})" for w in orders) or "none on file"
        body = (f"Automated triage for {e.type} {e.id}: {asset} at {e.data.get('station', '?')}. "
                f"Open {project} incidents in the last 30 days: {counted['result'].count}. "
                f"Open work orders on {asset}: {listed}.")
        args = {"issue_key": issue_key, "body": body}
        out = ask(self.graph(Decision("comment", arguments=args), comments=True), f"triage {issue_key}",
                  self.service, self.today, thread_id=thread)
        if "__interrupt__" not in out:
            raise InvalidEvent(f"comment refused: {out.get('answer')}")
        self.requested.append(thread)

    def handlers(self) -> dict:
        return {"workorder.upserted": self.on_workorder, "alert.raised": self.on_outage,
                "incident.created": self.on_outage}

    def pending(self, thread: str) -> dict | None:
        return pending(self.graph(Decision("none"), comments=True), thread)

    def decide(self, thread: str, approved: bool, approver: str, reason: str = "") -> str:
        """A person's decision on a waiting triage comment."""
        return resume(self.graph(Decision("none"), comments=True), thread, approved=approved,
                      approver=approver, reason=reason)["answer"]
