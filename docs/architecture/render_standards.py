"""Render standards.md from standards.yaml and exceptions.yaml.

    python docs/architecture/render_standards.py

The YAML files are the source of truth; tests/test_architecture.py fails if
standards.md is out of date, if a standard cites a test that does not exist, or
if an exception names a standard that does not.
"""

from __future__ import annotations

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOMAINS = ("AI", "Application", "API", "Integration")


def load() -> tuple[list[dict], list[dict]]:
    standards = yaml.safe_load((HERE / "standards.yaml").read_text())["standards"]
    exceptions = yaml.safe_load((HERE / "exceptions.yaml").read_text())["exceptions"]
    for s in standards:
        s.setdefault("evidence", {})
        s.setdefault("checks", [])
        s["enforced_by"] = list(dict.fromkeys(t for tests in s["evidence"].values() for t in tests))
    return standards, exceptions


def adr_files() -> dict[str, Path]:
    return {p.name[:4]: p for p in sorted((HERE / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))}


def _test_link(ref: str) -> str:
    path, name = ref.split("::")
    return f"[`{name}`](../../{path})"


def _adr_links(ids, adrs) -> str:
    return ", ".join(f"[{i}](adr/{adrs[i].name})" for i in ids) or "none"


def render() -> str:
    standards, exceptions = load()
    adrs = adr_files()
    by_standard: dict[str, list[dict]] = {}
    for e in exceptions:
        by_standard.setdefault(e["standard"], []).append(e)
    tested = sum(1 for s in standards if s["enforced_by"] or s["checks"])
    out = [
        "# Architecture standards",
        "",
        "<!-- Rendered from standards.yaml and exceptions.yaml by render_standards.py. Edit those, not this. -->",
        "",
        f"{len(standards)} standards. {tested} are enforced by named tests or static checks that run in CI; "
        f"{len(standards) - tested} {'is' if len(standards) - tested == 1 else 'are'} enforced by design review only, and "
        f"{'says' if len(standards) - tested == 1 else 'say'} so. "
        "A test checks that every test cited here exists. Conformance per module is in the "
        "[scorecard](scorecard.md).",
        "",
        "Each standard has an ID used by the [design review checklist](design-review-checklist.md), "
        "the ADRs that explain it, and the modules it applies to. Where a module doesn't meet a standard yet, "
        "the gap is in the [exception register](#exception-register) with an owner and an expiry date.",
        "",
    ]
    for domain in DOMAINS:
        out += [f"## {domain}", ""]
        for s in (s for s in standards if s["domain"] == domain):
            out += [f"### {s['id']} · {s['title']}", "", s["statement"], "",
                    f"*Why:* {s['rationale']}", "",
                    f"*Applies to:* {', '.join(s['applies_to'])} · *ADRs:* {_adr_links(s['adr'], adrs)}", ""]
            if s["evidence"]:
                out += ["*Enforced by:*", ""]
                for module, tests in sorted(s["evidence"].items()):
                    out.append(f"- {module}: " + ", ".join(_test_link(t) for t in tests))
                out.append("")
            if s["checks"]:
                out += [f"*Checked by:* static check `{', '.join(s['checks'])}` "
                        "([conformance.py](conformance.py)) over the code of every module it applies to.", ""]
            if not s["evidence"] and not s["checks"]:
                out += ["*Enforced by:* design review only. No test checks this yet.", ""]
            for e in by_standard.get(s["id"], []):
                out += [f"*Exception {e['id']}* for {', '.join(e['modules'])}, until {e['expires']}: {e['gap']}", ""]
    out += ["## Exception register", "",
            "| ID | Standard | Modules | Gap | Compensating control | Owner | Expires |", "|---|---|---|---|---|---|---|"]
    for e in exceptions:
        out.append(f"| {e['id']} | {e['standard']} | {', '.join(e['modules'])} | {e['gap']} | "
                   f"{e['compensating_control']} | {e['owner']} | {e['expires']} |")
    return "\n".join(out) + "\n"


def main() -> None:
    (HERE / "standards.md").write_text(render())
    print(f"wrote {HERE / 'standards.md'}")


if __name__ == "__main__":
    main()
