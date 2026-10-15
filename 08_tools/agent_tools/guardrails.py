"""Tool guardrails: checks that run around a tool call, each returning one of three behaviours.

  allow            the call (or its result) goes ahead
  reject_content   the call is blocked and the model is told why, in place of the
                   tool result, so it can correct course; nothing reaches Jira
  raise_exception  the run halts with a tripwire exception; for violations that
                   are not a mistake to recover from

Input guardrails see the model's raw arguments before the tool runs; output
guardrails see the result before the model does. Every verdict is recorded in
a GuardrailRecord, whatever the behaviour, so a run can be audited afterwards.

The behaviour names, the input/output split and the tripwire exceptions follow
tool_guardrails.py in openai/openai-agents-python v0.24.0; only the contract is
borrowed. These guardrails sit beside the tool's own bound (jql_tool.validate),
not instead of it: the bound is the tool's contract, the same in every
deployment; guardrails are deployment policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from .coverage import check as coverage_check
from .jira_mock import Caller

Behavior = Literal["allow", "reject_content", "raise_exception"]


@dataclass(frozen=True)
class ToolGuardrailFunctionOutput:
    behavior: Behavior = "allow"
    message: str = ""          # for reject_content: what the model is told instead of a result
    output_info: Any = None    # what the check found, for the record

    @classmethod
    def allow(cls, output_info: Any = None) -> "ToolGuardrailFunctionOutput":
        return cls("allow", "", output_info)

    @classmethod
    def reject_content(cls, message: str, output_info: Any = None) -> "ToolGuardrailFunctionOutput":
        return cls("reject_content", message, output_info)

    @classmethod
    def raise_exception(cls, output_info: Any = None) -> "ToolGuardrailFunctionOutput":
        return cls("raise_exception", "", output_info)


@dataclass(frozen=True)
class ToolCallData:
    """What a guardrail can see. `output` is set only for output guardrails."""

    tool_name: str
    arguments: dict
    question: str
    caller: Caller
    output: Any = None


@dataclass(frozen=True)
class ToolGuardrail:
    name: str
    stage: Literal["input", "output"]
    check: Callable[[ToolCallData], ToolGuardrailFunctionOutput]


@dataclass(frozen=True)
class GuardrailRecord:
    guardrail: str
    stage: str
    tool_name: str
    behavior: Behavior
    output_info: Any = None


class ToolGuardrailTripwireTriggered(Exception):
    def __init__(self, guardrail: ToolGuardrail, output: ToolGuardrailFunctionOutput, records: list[GuardrailRecord]):
        super().__init__(f"{guardrail.stage} guardrail {guardrail.name!r} halted the run: {output.output_info}")
        self.guardrail, self.output, self.records = guardrail, output, records


class ToolInputGuardrailTripwireTriggered(ToolGuardrailTripwireTriggered):
    """An input guardrail returned raise_exception; the tool did not run."""


class ToolOutputGuardrailTripwireTriggered(ToolGuardrailTripwireTriggered):
    """An output guardrail returned raise_exception; the result was not shown to the model."""


def run_guardrails(guardrails, stage: str, data: ToolCallData, log: list[GuardrailRecord]) -> str | None:
    """Run the guardrails for one stage, in order. Returns the rejection message for
    reject_content, None when every guardrail allows, and raises on raise_exception.
    The first guardrail that does not allow decides; later ones do not run."""
    tripwire = ToolInputGuardrailTripwireTriggered if stage == "input" else ToolOutputGuardrailTripwireTriggered
    for g in (g for g in guardrails if g.stage == stage):
        out = g.check(data)
        log.append(GuardrailRecord(g.name, stage, data.tool_name, out.behavior, out.output_info))
        if out.behavior == "reject_content":
            return out.message
        if out.behavior == "raise_exception":
            raise tripwire(g, out, log)
    return None


# ---------------------------------------------------------------- the demo policy for the JQL tool

RESTRICTED_PROJECTS = frozenset({"SECOPS"})


def target_project(d: ToolCallData) -> str | None:
    """The project a call touches: a count names it; a comment names an issue key like BILL-107."""
    if d.arguments.get("project"):
        return d.arguments["project"]
    key = str(d.arguments.get("issue_key", ""))
    return key.split("-", 1)[0] if "-" in key else None


def restricted_project(restricted: frozenset[str] = RESTRICTED_PROJECTS) -> ToolGuardrail:
    """Halt on any attempt to query a restricted project. This is not a mistake the
    model should be coached out of: it is a security event, so the run stops."""

    def check(d: ToolCallData) -> ToolGuardrailFunctionOutput:
        project = target_project(d)
        if project in restricted:
            return ToolGuardrailFunctionOutput.raise_exception(
                {"project": project, "caller": d.caller.id, "reason": "restricted project"}
            )
        return ToolGuardrailFunctionOutput.allow()

    return ToolGuardrail("restricted_project", "input", check)


def deployment_scope(projects: frozenset[str]) -> ToolGuardrail:
    """Block projects this deployment does not serve, and tell the model why. The tool
    itself allows them; this assistant was deployed for a narrower audience."""

    def check(d: ToolCallData) -> ToolGuardrailFunctionOutput:
        project = target_project(d)
        if project in projects:
            return ToolGuardrailFunctionOutput.allow({"project": project})
        allowed = ", ".join(sorted(projects))
        return ToolGuardrailFunctionOutput.reject_content(
            f"Not run: {project} is outside this assistant's scope ({allowed}). "
            "Do not retry with a different project. Tell the user which projects you can work with.",
            {"project": project, "scope": sorted(projects)},
        )

    return ToolGuardrail("deployment_scope", "input", check)


def question_coverage() -> ToolGuardrail:
    """The coverage check from coverage.py, expressed as a guardrail: a query that would
    answer only part of the question is blocked, and the model is told to ask."""

    def check(d: ToolCallData) -> ToolGuardrailFunctionOutput:
        if d.tool_name != "count_jira_issues":
            return ToolGuardrailFunctionOutput.allow()  # coverage is about counts
        reason = coverage_check(d.question, d.arguments.get("project"), d.arguments.get("project_evidence"))
        if reason:
            return ToolGuardrailFunctionOutput.reject_content(
                f"Not run: {reason}. Ask the user which single project they mean.", {"reason": reason}
            )
        return ToolGuardrailFunctionOutput.allow()

    return ToolGuardrail("question_coverage", "input", check)


def keys_match_project() -> ToolGuardrail:
    """Halt if the tool returns issue keys from a project other than the one queried.
    The tool should never do that; if it does, its result cannot be trusted."""

    def check(d: ToolCallData) -> ToolGuardrailFunctionOutput:
        project = d.arguments.get("project")
        stray = [k for k in getattr(d.output, "keys", ()) if not k.startswith(f"{project}-")]
        if stray:
            return ToolGuardrailFunctionOutput.raise_exception({"project": project, "stray_keys": stray})
        return ToolGuardrailFunctionOutput.allow()

    return ToolGuardrail("keys_match_project", "output", check)


def finance_assistant_policy() -> list[ToolGuardrail]:
    """The demo deployment: a finance assistant serving BILL and PAY. Order matters:
    the restricted check runs first, so a SECOPS attempt halts rather than being
    politely redirected."""
    return [
        restricted_project(),
        deployment_scope(frozenset({"BILL", "PAY"})),
        question_coverage(),
        keys_match_project(),
    ]
