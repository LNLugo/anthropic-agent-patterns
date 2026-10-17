"""Which Jira projects a deployment knows about, in one place.

The router, the coverage check, the model's system prompt, the tool's allowlist,
the restricted check and the clarifying question all need the same facts: the
project codes, their display names, the words people use for them, which ones
may be queried and which are restricted. They used to be written out in each of
those six places for the finance assistant. Now each of them takes a Catalog,
and the finance catalog below is the default, so module 08 behaves as before.

A second use case (paved_road, use_cases/transit) passes its own catalog
instead of copying the router. tests/test_paved_road.py fails if a project code
appears anywhere else in the shared code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Project:
    code: str
    name: str
    aliases: tuple[str, ...] = ()
    queryable: bool = True     # in the tool's allowlist
    restricted: bool = False   # any attempt halts the run (a security event, not a mistake)


@dataclass(frozen=True)
class Catalog:
    projects: tuple[Project, ...]
    scope: frozenset[str] | None = None  # what this deployment serves; None means every queryable project

    def __post_init__(self):
        codes = [p.code for p in self.projects]
        if len(set(codes)) != len(codes):
            raise ValueError(f"duplicate project codes in catalog: {codes}")
        owners: dict[str, str] = {}
        for p in self.projects:
            if p.restricted and p.queryable:
                raise ValueError(f"{p.code} is both restricted and queryable")
            for a in (p.name.lower(), *p.aliases):
                if owners.setdefault(a, p.code) != p.code:
                    raise ValueError(f"alias {a!r} names both {owners[a]} and {p.code}")
        if self.scope is not None and not self.scope <= self.queryable:
            raise ValueError(f"scope {sorted(self.scope - self.queryable)} is not queryable")

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(p.code for p in self.projects)

    @property
    def queryable(self) -> frozenset[str]:
        return frozenset(p.code for p in self.projects if p.queryable)

    @property
    def restricted(self) -> frozenset[str]:
        return frozenset(p.code for p in self.projects if p.restricted)

    @property
    def served(self) -> frozenset[str]:
        return self.scope if self.scope is not None else self.queryable

    @property
    def aliases(self) -> dict[str, str]:
        """Every word for a project (lower case) -> its code. The display name counts as one."""
        out: dict[str, str] = {}
        for p in self.projects:
            for a in (p.name.lower(), *p.aliases):
                out.setdefault(a, p.code)
        return out

    @property
    def names(self) -> dict[str, str]:
        """Queryable code -> display name, in catalog order, for the model's prompt."""
        return {p.code: p.name for p in self.projects if p.queryable}

    @property
    def clarifying_question(self) -> str:
        names = list(self.names.values())
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
        return f"Which project do you mean: {listed}?"

    @classmethod
    def from_dict(cls, data: dict) -> "Catalog":
        projects = tuple(
            Project(code=p["code"], name=p["name"], aliases=tuple(a.lower() for a in p.get("aliases", ())),
                    queryable=p.get("queryable", True), restricted=p.get("restricted", False))
            for p in data["projects"])
        scope = data.get("scope")
        return cls(projects, frozenset(scope) if scope is not None else None)


FINANCE = Catalog(
    projects=(
        Project("BILL", "Billing Platform", ("billing platform", "billing", "bill")),
        Project("PAY", "Payments", ("payments", "payment", "pay")),
        Project("INFRA", "Infrastructure", ("infrastructure", "infra")),
        Project("SECOPS", "Security Operations", ("security operations", "secops"),
                queryable=False, restricted=True),
    ),
    scope=frozenset({"BILL", "PAY"}),
)
