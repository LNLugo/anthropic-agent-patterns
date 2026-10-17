"""The paved road: configuration refused when it breaks a standard, both use cases built
from it with no use-case code in shared modules, and a gate that actually blocks."""

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agent_tools import coverage
from agent_tools.catalog import FINANCE, Catalog
from llm_provider import ScriptedChatModel
from paved_road import ROOT, ConfigError, build, load_spec
from paved_road.gate import load_golden, run, scripted_for
from tracing import InMemoryExporter

USE_CASES = ROOT / "use_cases"
FINANCE_UC, TRANSIT_UC = USE_CASES / "finance", USE_CASES / "transit"
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from new_use_case import scaffold  # noqa: E402


def variant(tmp_path: Path, source: Path = TRANSIT_UC, **changes) -> Path:
    """A copy of a use case with some top-level sections changed (None deletes one)."""
    raw = yaml.safe_load((source / "use_case.yaml").read_text())
    raw["data"] = {k: str((source / v).resolve()) for k, v in raw["data"].items()}
    raw["router"] = dict(raw["router"])
    if raw["router"].get("prompt"):
        raw["router"]["prompt"] = str((source / raw["router"]["prompt"]).resolve())
    for k, v in changes.items():
        if v is None:
            raw.pop(k, None)
        else:
            raw[k] = v
    dest = tmp_path / "uc"
    dest.mkdir()
    (dest / "use_case.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    shutil.copy(source / "golden.yaml", dest / "golden.yaml")
    return dest


# ---------------------------------------------------------------- configuration that breaks a standard

@pytest.mark.parametrize("changes, standard", [
    ({"tools": {"count": {}, "comment": {"approval": "optional"}}}, "AI-06"),
    ({"tools": {"count": {}, "comment": {}}}, "AI-06"),
    ({"policy": ["deployment_scope", "restricted_project", "keys_match_project"]}, "AI-05"),
    ({"policy": ["deployment_scope", "keys_match_project"]}, "AI-05"),
    ({"router": {"kind": "model", "model": "gpt-unpriced-1"}}, "OPS-02"),
    ({"budget": None}, "OPS-03"),
    ({"memory": {"strategy": "retrieve_pinned"}}, "APP-06"),
    ({"identity": {"mode": "service"}}, "APP-01"),
])
def test_a_configuration_that_breaks_a_standard_is_a_build_error(tmp_path, changes, standard):
    with pytest.raises(ConfigError, match=standard):
        load_spec(variant(tmp_path, **changes))


@pytest.mark.parametrize("changes, message", [
    ({"tools": {"count": {}, "coment": {"approval": "required"}}}, "unknown key"),   # a typo is not "no tool"
    ({"policy": ["restricted_project", "trust_me"]}, "unknown guardrail"),
    ({"tracing": {"include_sensitive": True}}, "unknown key"),                       # OPS-01 is not configurable
    ({"catalog": {"projects": [{"code": "A", "name": "A", "aliases": ["x"]},
                               {"code": "B", "name": "B", "aliases": ["x"]}]}}, "names both"),
    ({"catalog": {"projects": [{"code": "A", "name": "A", "restricted": True}]}}, "restricted and queryable"),
    ({"router": {"kind": "rules"}}, "question_coverage needs a model router"),  # it would refuse every count
])
def test_unknown_or_contradictory_configuration_is_refused(tmp_path, changes, message):
    with pytest.raises(ConfigError, match=message):
        load_spec(variant(tmp_path, **changes))


def test_the_positive_control_builds(tmp_path):
    assert load_spec(variant(tmp_path)).name == "transit"


# ---------------------------------------------------------------- generality

def _string_constants(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.lineno, n.value


SHARED = sorted([*(ROOT / "08_tools" / "agent_tools").glob("*.py"), *(HERE / "paved_road").glob("*.py")])
DEFAULT_CATALOG = ROOT / "08_tools" / "agent_tools" / "catalog.py"


def every_catalog_code() -> set[str]:
    codes = set(FINANCE.codes)
    for f in [*USE_CASES.glob("*/use_case.yaml"), ROOT / "templates" / "new_use_case" / "use_case.yaml"]:
        codes |= {p["code"] for p in yaml.safe_load(f.read_text())["catalog"]["projects"]}
    return codes


def test_no_project_code_appears_in_shared_code():
    """Comments and docstrings may use an example; code and anything sent to a model may not.
    The one place allowed is the default catalog, which keeps 08's own tests unchanged."""
    codes = every_catalog_code()
    assert {"BILL", "ELEV", "DEMO"} <= codes
    found = [f"{p.relative_to(ROOT)}:{line} {value!r}" for p in SHARED if p != DEFAULT_CATALOG
             for line, value in _string_constants(p) if any(re.search(rf"\b{c}\b", value) for c in codes)]
    assert found == []


def test_the_static_check_sees_a_planted_code(tmp_path):
    planted = tmp_path / "planted.py"
    planted.write_text('"""Docstrings may say ELEV."""\nSCOPE = {"ELEV"}\n')
    assert [v for _, v in _string_constants(planted)] == ["ELEV"]


def test_the_finance_use_case_declares_the_same_catalog_08_defaults_to():
    assert load_spec(FINANCE_UC).catalog == FINANCE


def test_the_comment_tool_shows_each_use_case_its_own_example_key():
    from agent_tools.comments import CommentStore, CommentTool
    from agent_tools.jira_mock import MockJira

    jira, _ = MockJira.from_fixture(load_spec(TRANSIT_UC).jira)
    schema = str(CommentTool(jira, CommentStore(), load_spec(TRANSIT_UC).catalog.queryable).schema)
    assert "ELEV-107" in schema and "BILL" not in schema


def test_the_catalog_reaches_the_routing_prompt_and_the_clarifying_question(tmp_path):
    case = {"reference_call": {"tool": "ask_clarification", "args": {"question": "Which one?"}}}
    llm = scripted_for(case)
    uc = build(TRANSIT_UC, llm=llm, state_dir=tmp_path, exporters=[])
    uc.ask("How many incidents?", "station_supervisor", thread_id="t")
    prompt = llm.calls[0].prompt
    assert "ELEV = Elevators and Escalators" in prompt and "SIG = Signals" in prompt
    assert "BILL" not in prompt and "Billing" not in prompt
    assert "track circuits are signals" in prompt          # the use case's own prompt lines
    assert Catalog.from_dict({"projects": [{"code": "ELEV", "name": "Elevators and Escalators"},
                                           {"code": "SIG", "name": "Signals"}]}).clarifying_question \
        == "Which project do you mean: Elevators and Escalators or Signals?"


# ---------------------------------------------------------------- the platform is really there

def test_every_run_is_traced_with_content_off_and_spend_metered(tmp_path):
    golden = {c["id"]: c for c in load_golden(TRANSIT_UC / "golden.yaml", True)}
    case = golden["open-elev-since-october"]
    sink = InMemoryExporter()
    uc = build(TRANSIT_UC, llm=scripted_for(case), state_dir=tmp_path, exporters=[sink],
               today=load_spec(TRANSIT_UC).gate.today)
    out = uc.ask(case["question"], case["as"], thread_id="t1")
    assert out["ended"] == "answered"
    kinds = {s["kind"] for s in sink.spans}
    assert {"agent", "generation", "guardrail", "tool"} <= kinds
    dump = str(sink.records)
    assert case["question"] not in dump and "ELEV-41" not in dump   # content stays out (OPS-01)
    assert uc.meter.spent("station_supervisor") > 0                  # priced from the trace (11)
    # The control: with content switched on, the same run does put the question in the trace.
    sink.clear()
    uc.tracer.include_sensitive = True
    uc.router.llm = scripted_for(case).bind_tools([])
    uc.ask(case["question"], case["as"], thread_id="t2")
    assert case["question"] in str(sink.records) and "ELEV-41" in str(sink.records)


def test_the_budget_is_checked_before_the_model_is_called(tmp_path):
    uc_dir = variant(tmp_path, budget={"daily_limit_usd": 0.0001, "reserve_usd": 0.0002})  # one call would pass the limit
    llm = scripted_for({"reference_call": {"tool": "search_wiki", "args": {"query": "x"}}})
    uc = build(uc_dir, llm=llm, state_dir=tmp_path / "s", exporters=[])
    out = uc.ask("What is the escalator procedure?", "station_supervisor", thread_id="t")
    assert out["ended"] == "budget" and llm.calls == ()


def test_an_approval_waits_across_a_restart(tmp_path):
    case = {c["id"]: c for c in load_golden(TRANSIT_UC / "golden.yaml", True)}["note-approved-by-lead"]
    first = build(TRANSIT_UC, llm=scripted_for(case), state_dir=tmp_path, exporters=[])
    assert first.ask(case["question"], case["as"], thread_id="w1")["ended"] == "pending_approval"
    again = build(TRANSIT_UC, llm=ScriptedChatModel(), state_dir=tmp_path, exporters=[])
    assert again.pending("w1")["issue_key"] == "ELEV-41"
    assert again.decide("w1", True, "maintenance_lead")["ended"] == "posted"


def test_memory_is_per_user_and_feeds_the_router(tmp_path):
    golden = {c["id"]: c for c in load_golden(FINANCE_UC / "golden.yaml", True)}
    case = golden["q3-p1-billing-oncall"]
    llm = scripted_for(case)
    llm.extend([scripted_for(case).invoke("replay")])   # the same reference call, twice
    uc = build(FINANCE_UC, llm=llm, state_dir=tmp_path, exporters=[], today=load_spec(FINANCE_UC).gate.today)
    uc.ask("From now on, only Payments.", "billing_oncall", thread_id="m1", session_id="s")
    uc.ask(case["question"], "billing_oncall", thread_id="m2", session_id="s")
    assert "From now on, only Payments." in llm.calls[1].prompt     # the second call saw the first turn
    assert set(uc.sessions) == {("billing_oncall", "s")}
    assert len(uc.sessions[("billing_oncall", "s")].get_items()) == 4


def test_a_service_identity_runs_as_itself_whoever_asks(tmp_path):
    uc_dir = variant(tmp_path, identity={"mode": "service", "id": "svc-alert-triage"})
    uc = build(uc_dir, llm=ScriptedChatModel(), state_dir=tmp_path / "s", exporters=[])
    assert uc.caller(None).id == "svc-alert-triage"
    with pytest.raises(PermissionError):
        uc.caller("station_supervisor")


# ---------------------------------------------------------------- the gate

@pytest.mark.parametrize("folder", [FINANCE_UC, TRANSIT_UC], ids=["finance", "transit"])
def test_both_use_cases_pass_the_offline_gate(folder):
    result = run(folder, "offline")
    assert result.passed and result.score == 1.0 and len(result.cases) >= 10


def test_the_gate_fails_a_wrong_expectation(tmp_path):
    uc_dir = variant(tmp_path)
    g = uc_dir / "golden.yaml"
    g.write_text(g.read_text().replace("expect: {ended: answered, count: 3,", "expect: {ended: answered, count: 4,"))
    result = run(uc_dir, "offline")
    assert not result.passed
    [failed] = [c for c in result.cases if not c.passed]
    assert failed.id == "open-elev-since-october" and failed.failures == ["count: expected 4, got 3"]


def test_the_gate_catches_a_regression_in_shared_code(monkeypatch):
    """The deliberate breakage: the coverage check stops checking. Nothing in the use case changed."""
    monkeypatch.setattr(coverage, "check", lambda *a, **k: None)
    result = run(FINANCE_UC, "offline")
    failed = {c.id for c in result.cases if not c.passed}
    assert not result.passed and "two-projects-asks" in failed


def test_the_gate_script_exits_nonzero_below_the_threshold(tmp_path):
    uc_dir = variant(tmp_path)
    g = uc_dir / "golden.yaml"
    g.write_text(g.read_text().replace("guardrail: restricted_project", "guardrail: deployment_scope"))
    bad = subprocess.run([sys.executable, "run_gate.py", str(uc_dir)], cwd=HERE, capture_output=True, text=True)
    good = subprocess.run([sys.executable, "run_gate.py", str(TRANSIT_UC)], cwd=HERE, capture_output=True, text=True)
    assert (bad.returncode, good.returncode) == (1, 0), bad.stdout + bad.stderr
    assert "secops-halts" in bad.stdout


def test_live_mode_scores_the_models_choice_and_does_not_block():
    def wrong(case):  # a model that always searches the wiki
        return scripted_for({"reference_call": {"tool": "search_wiki", "args": {"query": "anything"}}})

    live = run(TRANSIT_UC, "live", llm_for=wrong)
    assert [c.call_matched for c in live.cases].count(True) == 1     # only the wiki case matches
    perfect = run(TRANSIT_UC, "live", llm_for=scripted_for)
    assert perfect.passed and all(c.call_matched for c in perfect.cases)


def test_a_golden_set_with_a_duplicate_id_is_refused(tmp_path):
    uc_dir = variant(tmp_path)
    g = uc_dir / "golden.yaml"
    g.write_text(g.read_text() + g.read_text().split("\n\n")[1])
    with pytest.raises(ConfigError, match="duplicate"):
        run(uc_dir, "offline")


# ---------------------------------------------------------------- the template

def test_a_scaffolded_use_case_is_green_before_it_is_edited(tmp_path):
    dest = scaffold("claims", "A. Owner", "Claims Ops", tmp_path)
    spec = load_spec(dest)
    assert (spec.name, spec.owner) == ("claims", {"person": "A. Owner", "team": "Claims Ops"})
    assert {p.name for p in dest.iterdir()} >= {"use_case.yaml", "golden.yaml", "prompts", "hooks.py", "tests"}
    assert run(dest, "offline").passed


@pytest.mark.parametrize("name", ["Claims", "1claims", "claims-ops", "x"])
def test_the_scaffold_refuses_bad_names(tmp_path, name):
    with pytest.raises(ValueError):
        scaffold(name, "A", "B", tmp_path)


def test_the_scaffold_never_overwrites(tmp_path):
    scaffold("claims", "A", "B", tmp_path)
    with pytest.raises(FileExistsError):
        scaffold("claims", "A", "B", tmp_path)


# ---------------------------------------------------------------- the catalog in the second lines of defence

def test_the_policy_is_built_over_the_use_cases_catalog():
    """The router's coverage check stops a half answer first, so the gate never reaches the
    guardrail copy of it; this checks the copy directly."""
    from agent_tools.guardrails import ToolCallData
    from agent_tools.jira_mock import Caller
    from paved_road.build import policy_for

    policy = {g.name: g for g in policy_for(load_spec(TRANSIT_UC))}
    who = Caller("station_supervisor", frozenset({"ops-all"}))
    both = ToolCallData("count_jira_issues", {"project": "ELEV", "project_evidence": "elevator"},
                        "How many elevator and signal incidents?", who)
    assert policy["question_coverage"].check(both).behavior == "reject_content"
    assert policy["restricted_project"].check(
        ToolCallData("count_jira_issues", {"project": "SECOPS"}, "q", who)).behavior == "raise_exception"
    assert policy["deployment_scope"].check(
        ToolCallData("count_jira_issues", {"project": "SIG"}, "q", who)).behavior == "allow"


def test_the_rules_router_reads_the_use_cases_catalog(tmp_path):
    uc_dir = variant(tmp_path, router={"kind": "rules"}, budget=None,
                     policy=["restricted_project", "deployment_scope", "keys_match_project"])
    uc = build(uc_dir, state_dir=tmp_path / "s", exporters=[], today=load_spec(TRANSIT_UC).gate.today)
    out = uc.ask("How many open escalator incidents in 2026?", "station_supervisor", thread_id="r1")
    assert (out["ended"], out["result"].count) == ("answered", 3)
    asked = uc.ask("How many open incidents in 2026?", "station_supervisor", thread_id="r2")
    assert asked["answer"] == "Which project do you mean: Elevators and Escalators or Signals?"


def test_an_empty_clarifying_question_falls_back_to_the_catalogs(tmp_path):
    llm = scripted_for({"reference_call": {"tool": "ask_clarification", "args": {"question": ""}}})
    uc = build(TRANSIT_UC, llm=llm, state_dir=tmp_path, exporters=[])
    out = uc.ask("How many incidents?", "station_supervisor", thread_id="c1")
    assert out["answer"] == "Which project do you mean: Elevators and Escalators or Signals?"
