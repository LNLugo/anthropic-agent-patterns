"""Count what the paved road saves, from the repository itself.

    python run_reuse_metrics.py                      # counts, and the time from scaffold to a green gate
    python run_reuse_metrics.py --clone-to-green .. # also: clone, new venv, install, test, timed

Writes results/reuse.json and prints the tables the README quotes.

Code lines are non-blank lines that are not comments or docstrings (AST and tokenize).
Configuration lines are non-blank lines that are not comments. "Reused" counts only
the platform files a use case actually imports while its gate runs, measured in a
fresh process per use case, not whole modules.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RESULTS = HERE / "results"


def code_lines(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    docs = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docs.update(range(first.lineno, first.end_lineno + 1))
    code = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                            tokenize.ENDMARKER):
            code.update(range(tok.start[0], tok.end[0] + 1))
    return len(code - docs)


def config_lines(path: Path) -> int:
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def node_lines(path: Path, name: str) -> int:
    """Code lines of one top-level assignment or function, e.g. the FINANCE catalog."""
    src = path.read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        target = getattr(node, "name", None) or next(
            (t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)), None)
        if target == name:
            segment = "\n".join(src.splitlines()[node.lineno - 1:node.end_lineno])
            tmp = Path(tempfile.mkstemp(suffix=".py")[1])
            tmp.write_text(segment, encoding="utf-8")
            try:
                return code_lines(tmp)
            finally:
                tmp.unlink()
    raise KeyError(name)


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


# ---------------------------------------------------------------- new lines per use case

def use_case_lines(folder: Path) -> dict:
    py = [p for p in folder.rglob("*.py") if "tests" not in p.relative_to(folder).parts]
    tests = list((folder / "tests").rglob("*.py")) if (folder / "tests").exists() else []
    prompts = list((folder / "prompts").glob("*")) if (folder / "prompts").exists() else []
    return {
        "code": sum(code_lines(p) for p in py),
        "code_files": [rel(p) for p in py],
        "generated_tests": sum(code_lines(p) for p in tests),
        "use_case_yaml": config_lines(folder / "use_case.yaml"),
        "prompts": sum(config_lines(p) for p in prompts),
        "golden_cases": len(__import__("yaml").safe_load((folder / "golden.yaml").read_text())),
        "golden_lines": config_lines(folder / "golden.yaml"),
    }


def hand_built() -> dict:
    """The finance assistant as it was wired before the paved road: its demo's wiring, plus the
    finance facts that sat in six shared modules (gathered into the FINANCE catalog), plus
    the finance policy. And 12's triage, the second hand-built assistant, for comparison."""
    demo = ROOT / "08_tools" / "demo_assistant.py"
    catalog = ROOT / "08_tools" / "agent_tools" / "catalog.py"
    triage = ROOT / "12_integration" / "integration" / "triage.py"
    finance = {"wiring": code_lines(demo), "catalog": node_lines(catalog, "FINANCE"),
               "policy": node_lines(ROOT / "08_tools" / "agent_tools" / "guardrails.py", "finance_assistant_policy")}
    return {"finance": finance | {"total": sum(finance.values())},
            "transit_triage_12": {"total": code_lines(triage)}}


# ---------------------------------------------------------------- reuse, measured by import

PROBE = """
import json, sys
sys.path.insert(0, {here!r})
from paved_road.gate import run
result = run({folder!r}, "offline")
files = sorted({{m.__file__ for m in list(sys.modules.values()) if getattr(m, "__file__", None)}})
print(json.dumps({{"passed": result.passed, "files": files}}))
"""

GROUPS = {"07_rag": "07 retrieval", "08_tools": "08 tools, guardrails, approval", "09_memory": "09 memory",
          "10_observability": "10 tracing", "11_finops": "11 FinOps", "llm_provider.py": "llm_provider",
          "13_paved_road": "13 paved road", "use_cases": "the use case"}


def reuse(folder: Path) -> dict:
    out = subprocess.run([sys.executable, "-c", PROBE.format(here=str(HERE), folder=str(folder))],
                         capture_output=True, text=True, cwd=HERE, check=True)
    probe = json.loads(out.stdout.strip().splitlines()[-1])
    by_group: dict[str, dict] = {}
    for f in map(Path, probe["files"]):
        try:
            part = f.resolve().relative_to(ROOT).parts[0]
        except ValueError:
            continue                                   # the standard library and site-packages
        if part not in GROUPS or "tests" in f.parts:
            continue
        g = by_group.setdefault(GROUPS[part], {"files": 0, "code_lines": 0})
        g["files"] += 1
        g["code_lines"] += code_lines(f)
    platform_groups = {k: v for k, v in by_group.items() if k not in ("the use case", "13 paved road")}
    return {"gate_passed": probe["passed"], "by_module": by_group,
            "platform_modules_reused": len(platform_groups),
            "platform_code_lines_reused": sum(v["code_lines"] for v in platform_groups.values())}


# ---------------------------------------------------------------- time to green

def scaffold_to_green() -> dict:
    sys.path.insert(0, str(HERE))
    from new_use_case import scaffold

    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.perf_counter()
        dest = scaffold("timing_probe", "Owner", "Team", Path(tmp))
        out = subprocess.run([sys.executable, "run_gate.py", str(dest)], cwd=HERE, capture_output=True, text=True)
        seconds = time.perf_counter() - t0
    return {"seconds": round(seconds, 1), "green": out.returncode == 0}


def clone_to_green(source: Path) -> dict:
    """A clean clone of the committed HEAD, a new virtual environment, the paved road's
    requirements, its tests and the gate, timed end to end. pip's cache is disabled."""
    steps, t0 = {}, time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        repo, venv = Path(tmp) / "repo", Path(tmp) / "venv"

        def step(name, cmd, cwd=None):
            s = time.perf_counter()
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            steps[name] = round(time.perf_counter() - s, 1)
            if r.returncode != 0:
                raise RuntimeError(f"{name} failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")

        step("clone", ["git", "clone", "--quiet", "--no-local", str(source), str(repo)])
        step("venv", [sys.executable, "-m", "venv", str(venv)])
        py = str(venv / "bin" / "python")
        step("install", [py, "-m", "pip", "install", "--quiet", "--no-cache-dir", "-r",
                         "13_paved_road/requirements-test.txt"], cwd=repo)
        step("tests", [py, "-m", "pytest", "-q"], cwd=repo / "13_paved_road")
        step("gate", [py, "run_gate.py"], cwd=repo / "13_paved_road")
    return {"seconds": round(time.perf_counter() - t0, 1), "steps": steps,
            "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
            "measured": date.today().isoformat()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clone-to-green", type=Path, metavar="REPO", help="also time a clean clone of REPO to green")
    args = ap.parse_args()

    use_cases = sorted(p.parent for p in (ROOT / "use_cases").glob("*/use_case.yaml"))
    platform_cost = {
        "paved_road": sum(code_lines(p) for p in (HERE / "paved_road").glob("*.py")),
        "catalog_class": code_lines(ROOT / "08_tools" / "agent_tools" / "catalog.py")
        - node_lines(ROOT / "08_tools" / "agent_tools" / "catalog.py", "FINANCE"),
        "scaffold_and_gate_scripts": code_lines(HERE / "new_use_case.py") + code_lines(HERE / "run_gate.py"),
    }
    report = {
        "hand_built": hand_built(),
        "on_the_paved_road": {uc.name: use_case_lines(uc) for uc in use_cases},
        "reuse": {uc.name: reuse(uc) for uc in use_cases},
        "platform_written_once": platform_cost | {"total": sum(platform_cost.values())},
        "scaffold_to_green": scaffold_to_green(),
    }
    previous = json.loads((RESULTS / "reuse.json").read_text()) if (RESULTS / "reuse.json").exists() else {}
    if args.clone_to_green:
        report["clone_to_green"] = clone_to_green(args.clone_to_green.resolve())
    elif "clone_to_green" in previous:
        report["clone_to_green"] = previous["clone_to_green"]   # kept from the last timed run
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "reuse.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
