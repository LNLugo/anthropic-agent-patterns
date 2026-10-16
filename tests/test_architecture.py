"""The architecture documents stay true to the repository.

A standard that cites a test which does not exist, an ADR link that goes
nowhere, or an exception for a standard nobody wrote would each make the
documents claim more than the code does. These tests fail first.
"""

import ast
import importlib.util
import re
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "docs" / "architecture"

_spec = importlib.util.spec_from_file_location("render_standards", ARCH / "render_standards.py")
render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render)
STANDARDS, EXCEPTIONS = render.load()
IDS = {s["id"] for s in STANDARDS}


def test_standards_md_is_rendered_from_the_yaml():
    assert (ARCH / "standards.md").read_text() == render.render(), \
        "run python docs/architecture/render_standards.py"


def test_ids_are_unique_and_well_formed():
    assert len(IDS) == len(STANDARDS)
    for s in STANDARDS:
        assert re.fullmatch(r"(AI|APP|OPS|API|INT)-\d\d", s["id"]) and s["domain"] in render.DOMAINS
        assert s["statement"].strip() and s["rationale"].strip() and s["applies_to"]


def _tests_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


@pytest.mark.parametrize("ref", sorted({t for s in STANDARDS for t in s["enforced_by"]}))
def test_every_cited_test_exists(ref):
    path, name = ref.split("::")
    assert (ROOT / path).is_file(), f"{path} does not exist"
    assert name in _tests_in(ROOT / path), f"{name} is not a test in {path}"


def test_evidence_is_keyed_by_a_module_the_standard_applies_to():
    for s in STANDARDS:
        assert set(s["evidence"]) <= set(s["applies_to"]), s["id"]


def test_every_adr_a_standard_cites_exists_and_cites_it_back():
    adrs = render.adr_files()
    for s in STANDARDS:
        for a in s["adr"]:
            assert a in adrs, f"{s['id']} cites ADR {a}, which does not exist"
            assert s["id"] in adrs[a].read_text().split("\n", 3)[2], f"ADR {a} does not list {s['id']}"


def test_every_standard_an_adr_names_exists():
    for path in render.adr_files().values():
        line = next(l for l in path.read_text().splitlines() if l.startswith("**Status:**"))
        for sid in re.findall(r"[A-Z]+-\d\d", line):
            assert sid in IDS, f"{path.name} names {sid}, which is not a standard"


def test_every_standard_is_asked_about_at_design_review():
    checklist = (ARCH / "design-review-checklist.md").read_text()
    asked = set(re.findall(r"\b(?:AI|APP|OPS|API|INT)-\d\d\b", checklist))
    assert asked <= IDS, f"the checklist names {sorted(asked - IDS)}, which are not standards"
    assert IDS <= asked, f"no checklist question covers {sorted(IDS - asked)}"


@pytest.mark.parametrize("exc", EXCEPTIONS, ids=lambda e: e["id"])
def test_every_exception_is_for_a_real_gap_with_an_owner_and_an_end(exc):
    standard = next((s for s in STANDARDS if s["id"] == exc["standard"]), None)
    assert standard is not None, f"{exc['id']} is for {exc['standard']}, which is not a standard"
    assert set(exc["modules"]) <= set(standard["applies_to"]), "an exception for a module the standard does not govern"
    assert exc["owner"] and exc["compensating_control"] and exc["plan"]
    assert exc["expires"] <= exc["approved"] + timedelta(days=183), "exceptions last at most six months"


def _slug(heading: str) -> str:
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    return {_slug(m.group(1)) for m in re.finditer(r"^#+ (.+)$", path.read_text(), re.M)}


DOCS = sorted(ARCH.rglob("*.md"))


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ARCH)))
def test_every_relative_link_and_anchor_resolves(doc):
    for target in re.findall(r"\]\(([^)\s]+)\)", doc.read_text()):
        if re.match(r"[a-z]+://", target):
            continue
        file, _, anchor = target.partition("#")
        dest = (doc.parent / file).resolve() if file else doc
        assert dest.exists(), f"{doc.name}: link to {target} goes nowhere"
        if anchor and dest.suffix == ".md":
            assert anchor in _anchors(dest), f"{doc.name}: no heading #{anchor} in {dest.name}"
