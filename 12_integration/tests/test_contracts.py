"""API lifecycle: the contract is generated, committed and checked; a breaking change
inside a major version fails the gate."""

import copy
import json
import sys

import run_contract_check as gate
from gateway.contracts import breaking_changes, committed, contract


def test_the_committed_contracts_match_the_code():
    for v in ("v1", "v2"):
        assert committed(v) == contract(v)


def test_v2_is_a_new_major_version_for_a_reason():
    assert breaking_changes(contract("v1"), contract("v2")) == [
        "GET /incidents/count: new required parameter created_from",
        "GET /incidents/count: new required parameter created_before"]


def test_v1_is_marked_deprecated_in_its_contract():
    assert contract("v1")["paths"]["/incidents/count"]["get"]["deprecated"] is True
    assert "deprecated" not in contract("v2")["paths"]["/incidents/count"]["get"]


def test_the_checker_classifies_every_labeled_edit():
    rows = gate.score_checker()
    assert len(rows) == 14 and sum(r["breaking"] for r in rows) == 10
    assert [r["edit"] for r in rows if r["breaking"] != r["flagged"]] == []


def test_an_unchanged_contract_has_no_breaking_changes():
    assert breaking_changes(contract("v2"), contract("v2")) == []


def test_the_gate_fails_a_pr_that_breaks_v2(tmp_path, monkeypatch, capsys):
    """CI passes main's v2 contract as the baseline. A PR that removes a response
    property main promised fails, naming what broke."""
    base = copy.deepcopy(contract("v2"))
    base["paths"]["/incidents/count"]["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "properties"]["station"] = {"type": "string"}          # main promised a field this branch dropped
    path = tmp_path / "base-v2.json"
    path.write_text(json.dumps(base))
    monkeypatch.setattr(sys, "argv", ["run_contract_check.py", "--baseline-v2", str(path)])
    assert gate.main() == 1
    assert "response property station removed" in capsys.readouterr().out


def test_the_gate_passes_against_its_own_baseline(tmp_path, monkeypatch):
    path = tmp_path / "base-v2.json"
    path.write_text(json.dumps(committed("v2")))
    monkeypatch.setattr(sys, "argv", ["run_contract_check.py", "--baseline-v2", str(path)])
    assert gate.main() == 0


def test_the_gate_skips_when_main_has_no_contract_yet(tmp_path, monkeypatch):
    path = tmp_path / "base-v2.json"
    path.write_text("{}\n")                     # what the CI step writes when main has no file
    monkeypatch.setattr(sys, "argv", ["run_contract_check.py", "--baseline-v2", str(path)])
    assert gate.main() == 0
