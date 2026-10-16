"""The API lifecycle gate: is the contract current, and is a change safe to ship?

    python run_contract_check.py                              # drift, and the labeled edits
    python run_contract_check.py --baseline-v2 base-v2.json   # in CI: the PR's v2 against main's

Three checks:
  1 drift      the contract the code serves must equal the committed file, so every
               API change shows up in review as a contract diff
  2 breaking   within a major version, a change a client of the old contract could
               fail on is refused; it needs a new version (as v1 to v2 did)
  3 the checker itself, on labeled edits to v2: each one is known breaking or not
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "08_tools", HERE.parent / "10_observability", HERE.parent):
    sys.path.insert(0, str(p))

from gateway.contracts import breaking_changes, committed, contract  # noqa: E402


def _op(c, method, path):
    return c["paths"][path][method]


def _resp(c, method, path):
    return _op(c, method, path)["responses"]["200"]["content"]["application/json"]["schema"]


def labeled_edits() -> list[tuple[str, bool, callable]]:
    """(what, breaking?, edit). Each edit takes a copy of v2 and changes one thing."""
    def param(c, name):
        return next(p for p in _op(c, "get", "/incidents/count")["parameters"] if p["name"] == name)

    edits = [
        ("remove the triage operation", True, lambda c: c["paths"].pop("/triage/{issue_key}")),
        ("add a required parameter", True, lambda c: _op(c, "get", "/incidents/count")["parameters"].append(
            {"name": "region", "in": "query", "required": True, "schema": {"type": "string"}})),
        ("make status required", True, lambda c: param(c, "status").update(required=True)),
        ("stop accepting project SIG", True, lambda c: param(c, "project")["schema"].update(enum=["ELEV"])),
        ("remove the jql response property", True, lambda c: _resp(c, "get", "/incidents/count")["properties"].pop("jql")),
        ("return count as a string", True, lambda c: _resp(c, "get", "/incidents/count")["properties"]["count"].update(type="string")),
        ("make visible_to optional in responses", True, lambda c: _resp(c, "get", "/incidents/count")["required"].remove("visible_to")),
        ("add a triage state clients have never seen", True, lambda c: _resp(c, "get", "/triage/{issue_key}")["properties"]["state"]["enum"].append("escalated")),
        ("require a new property in the ask body", True, lambda c: (
            _op(c, "post", "/ask")["requestBody"]["content"]["application/json"]["schema"]["properties"].update(
                station={"type": "string"}),
            _op(c, "post", "/ask")["requestBody"]["content"]["application/json"]["schema"]["required"].append("station"))),
        ("stop accepting question in the ask body", True, lambda c: (
            _op(c, "post", "/ask")["requestBody"]["content"]["application/json"]["schema"]["properties"].pop("question"),
            _op(c, "post", "/ask")["requestBody"]["content"]["application/json"]["schema"]["required"].remove("question"))),
        ("add an optional parameter", False, lambda c: _op(c, "get", "/incidents/count")["parameters"].append(
            {"name": "priority", "in": "query", "required": False, "schema": {"type": "string"}})),
        ("add an operation", False, lambda c: c["paths"].setdefault("/stations", {}).update(
            get=copy.deepcopy(_op(c, "get", "/incidents/count")))),
        ("add a response property", False, lambda c: _resp(c, "get", "/incidents/count")["properties"].update(
            generated_at={"type": "string"})),
        ("accept a new project", False, lambda c: param(c, "project")["schema"]["enum"].append("TRK")),
    ]
    return edits


def score_checker() -> list[dict]:
    rows = []
    base = contract("v2")
    for what, breaking, edit in labeled_edits():
        changed = copy.deepcopy(base)
        edit(changed)
        found = breaking_changes(base, changed)
        rows.append({"edit": what, "breaking": breaking, "flagged": bool(found), "reasons": found})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline-v2", help="v2 contract from the base branch; a breaking change against it fails")
    args = ap.parse_args()
    failed = False

    for v in ("v1", "v2"):
        drift = committed(v) != contract(v)
        print(f"drift {v}: {'the served contract differs from contracts/openapi-' + v + '.json' if drift else 'none'}")
        failed |= drift

    print("\nv1 to v2 (why v2 is a new major version):")
    for reason in breaking_changes(contract("v1"), contract("v2")):
        print(f"  {reason}")

    rows = score_checker()
    right = sum(r["breaking"] == r["flagged"] for r in rows)
    print(f"\nChecker on {len(rows)} labeled edits to v2: {right} classified correctly")
    for r in rows:
        print(f"  {'breaking' if r['breaking'] else 'safe    '}  {'ok ' if r['breaking'] == r['flagged'] else 'MISS'}  {r['edit']}")
    failed |= right != len(rows)

    if args.baseline_v2:
        base_path = Path(args.baseline_v2)
        base = json.loads(base_path.read_text() or "{}") if base_path.exists() else {}
        if base.get("paths"):
            found = breaking_changes(base, contract("v2"))
            print(f"\nAgainst the base branch: {len(found)} breaking change(s)")
            for reason in found:
                print(f"  {reason}")
            failed |= bool(found)
        else:
            print("\nNo v2 contract on the base branch yet; nothing to compare.")

    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "contract_check.json").write_text(json.dumps(
        {"v1_to_v2": breaking_changes(contract("v1"), contract("v2")), "labeled_edits": rows}, indent=2) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
