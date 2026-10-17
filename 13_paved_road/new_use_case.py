"""Start a new use case from the template.

    python new_use_case.py claims --person "A. Owner" --team "Claims Ops"

Copies templates/new_use_case/ to use_cases/<name>/ and fills in the name and the
owner. Standard library only. Then edit use_case.yaml (catalog, data, tools) and
golden.yaml, and run the gate:

    python run_gate.py ../use_cases/<name>
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "new_use_case"
NAME = re.compile(r"[a-z][a-z0-9_]{1,39}")
FILLED = (".yaml", ".md", ".py")


def scaffold(name: str, person: str, team: str, dest_root: Path = ROOT / "use_cases") -> Path:
    if not NAME.fullmatch(name):
        raise ValueError("name: lower case letters, digits and underscores, starting with a letter")
    dest = dest_root / name
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")
    shutil.copytree(TEMPLATE, dest, ignore=shutil.ignore_patterns("__pycache__", ".state", "traces"))
    values = {"{{name}}": name, "{{person}}": person, "{{team}}": team}
    for path in dest.rglob("*"):
        if path.is_file() and path.suffix in FILLED:
            text = path.read_text(encoding="utf-8")
            for k, v in values.items():
                text = text.replace(k, v)
            path.write_text(text, encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name")
    ap.add_argument("--person", required=True, help="the accountable owner")
    ap.add_argument("--team", required=True)
    args = ap.parse_args()
    try:
        dest = scaffold(args.name, args.person, args.team)
    except (ValueError, FileExistsError) as e:
        print(e, file=sys.stderr)
        return 2
    print(f"created {dest.relative_to(ROOT)}/")
    for p in sorted(dest.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(dest)}")
    print(f"next: edit use_case.yaml and golden.yaml, then: python run_gate.py ../use_cases/{args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
