"""use_case.yaml: what a use case declares, read strictly.

Unknown keys are refused, not ignored (fail closed, APP-03): a misspelt
`aproval:` must not quietly become "no approval". A configuration that breaks a
standard is a build error here, before anything runs, rather than a review finding:

    AI-05  the policy must start with restricted_project, the security check that halts
    AI-06  a write (the comment tool) must declare approval: required
    OPS-02 a model router's model must be in the price file
    OPS-03 a model router needs a budget
    APP-06 memory needs a retention period
    OPS-01 is not configurable: every use case is traced, with content off
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from agent_tools.catalog import Catalog

GUARDRAILS = ("restricted_project", "deployment_scope", "question_coverage", "keys_match_project")
STRATEGIES = ("append_all", "window", "compact", "retrieve", "retrieve_pinned")


class ConfigError(ValueError):
    """The use case's configuration is invalid or breaks a standard. Nothing was built."""


def _only(section: str, data: dict, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(data, dict):
        raise ConfigError(f"{section}: expected a mapping, got {type(data).__name__}")
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{section}: unknown key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")
    missing = required - set(data)
    if missing:
        raise ConfigError(f"{section}: missing {sorted(missing)}")
    return data


@dataclass(frozen=True)
class Router:
    kind: str                       # rules | model
    model: str | None = None
    coverage: bool = True
    prompt: Path | None = None      # the use case's own lines, added to the platform's routing prompt


@dataclass(frozen=True)
class Memory:
    strategy: str
    retention_days: int
    path: Path | None = None        # SQLite file; None keeps sessions in memory


@dataclass(frozen=True)
class Budget:
    daily_limit_usd: float
    reserve_usd: float = 0.0
    limits: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Gate:
    today: date
    offline_threshold: float = 1.0
    live_threshold: float = 0.9


@dataclass(frozen=True)
class UseCaseSpec:
    name: str
    folder: Path
    owner: dict
    catalog: Catalog
    jira: Path
    wiki: Path | None
    identity: dict
    count_max_keys: int
    comment: bool
    wiki_min_score: float
    policy: tuple[str, ...]
    router: Router
    memory: Memory | None
    budget: Budget | None
    gate: Gate

    @property
    def golden(self) -> Path:
        return self.folder / "golden.yaml"


TOP = {"name", "description", "owner", "catalog", "data", "identity", "tools", "policy", "router",
       "memory", "budget", "gate"}


def load_spec(folder: str | Path) -> UseCaseSpec:
    folder = Path(folder).resolve()
    path = folder / "use_case.yaml"
    if not path.exists():
        raise ConfigError(f"no use_case.yaml in {folder}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _only("use_case.yaml", raw, TOP, {"name", "owner", "catalog", "data", "identity", "tools", "policy", "router", "gate"})

    def rel(p) -> Path:
        q = (folder / p).resolve()
        if not q.exists():
            raise ConfigError(f"{p}: no such file (relative to {folder.name}/)")
        return q

    owner = _only("owner", raw["owner"], {"person", "team"}, {"person", "team"})

    try:
        catalog = Catalog.from_dict(_only("catalog", raw["catalog"], {"projects", "scope"}, {"projects"}))
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"catalog: {e}") from None

    data = _only("data", raw["data"], {"jira", "wiki"}, {"jira"})
    identity = _only("identity", raw["identity"], {"mode", "id"}, {"mode"})
    if identity["mode"] not in ("delegated", "service"):
        raise ConfigError("identity.mode: delegated (each user's own permissions) or service")
    if identity["mode"] == "service" and not identity.get("id"):
        raise ConfigError("identity: a service identity needs an id (APP-01)")

    tools = _only("tools", raw["tools"], {"count", "comment", "wiki"}, {"count"})
    count = _only("tools.count", tools["count"] or {}, {"max_keys"})
    comment = tools.get("comment")
    if comment is not None:
        comment = _only("tools.comment", comment, {"approval"})
        if comment.get("approval") != "required":
            raise ConfigError("tools.comment: a write must declare approval: required (AI-06)")
    wiki = _only("tools.wiki", tools.get("wiki") or {}, {"min_score"})
    if "wiki" in tools and not data.get("wiki"):
        raise ConfigError("tools.wiki is declared but data.wiki names no wiki")

    policy = tuple(raw["policy"] or ())
    bad = [g for g in policy if g not in GUARDRAILS]
    if bad:
        raise ConfigError(f"policy: unknown guardrail(s) {bad}; the platform's set is {list(GUARDRAILS)}")
    if not policy or policy[0] != "restricted_project":
        raise ConfigError("policy: restricted_project must run first, so a security event halts "
                          "before anything can redirect it (AI-05)")
    if len(set(policy)) != len(policy):
        raise ConfigError("policy: a guardrail is listed twice")

    r = _only("router", raw["router"], {"kind", "model", "coverage", "prompt"}, {"kind"})
    if r["kind"] not in ("rules", "model"):
        raise ConfigError("router.kind: rules or model")
    router = Router(r["kind"], r.get("model"), bool(r.get("coverage", True)),
                    rel(r["prompt"]) if r.get("prompt") else None)
    if router.kind == "model":
        if not router.model:
            raise ConfigError("router: a model router names its model (APP-05)")
        from finops.prices import PriceBook

        if router.model not in PriceBook.load():
            raise ConfigError(f"router.model {router.model!r} is not in 11_finops/prices.yaml, "
                              "so its spend cannot be measured (OPS-02)")
        if raw.get("budget") is None:
            raise ConfigError("budget: a model router needs a budget, checked before each call (OPS-03)")
    if "question_coverage" in policy and not (router.kind == "model" and router.coverage):
        # The guardrail checks the words the model quotes as evidence. The rules router, or a
        # model router without the coverage field, quotes none, so every count would be refused.
        raise ConfigError("policy: question_coverage needs a model router with coverage on")

    memory = None
    if raw.get("memory") is not None:
        m = _only("memory", raw["memory"], {"strategy", "retention_days", "path"}, {"strategy"})
        if m["strategy"] not in STRATEGIES:
            raise ConfigError(f"memory.strategy: one of {list(STRATEGIES)}")
        if not m.get("retention_days"):
            raise ConfigError("memory: history needs a retention period in days (APP-06)")
        memory = Memory(m["strategy"], int(m["retention_days"]), folder / m["path"] if m.get("path") else None)

    budget = None
    if raw.get("budget") is not None:
        b = _only("budget", raw["budget"], {"daily_limit_usd", "reserve_usd", "limits"}, {"daily_limit_usd"})
        budget = Budget(float(b["daily_limit_usd"]), float(b.get("reserve_usd", 0.0)), dict(b.get("limits") or {}))

    g = _only("gate", raw["gate"], {"today", "offline_threshold", "live_threshold"}, {"today"})
    today = g["today"] if isinstance(g["today"], date) else date.fromisoformat(str(g["today"]))
    gate = Gate(today, float(g.get("offline_threshold", 1.0)), float(g.get("live_threshold", 0.9)))
    if not 0 < gate.offline_threshold <= 1 or not 0 < gate.live_threshold <= 1:
        raise ConfigError("gate: thresholds are fractions between 0 and 1")

    return UseCaseSpec(
        name=str(raw["name"]), folder=folder, owner=dict(owner), catalog=catalog,
        jira=rel(data["jira"]), wiki=rel(data["wiki"]) if data.get("wiki") else None,
        identity=dict(identity), count_max_keys=int(count.get("max_keys", 25)),
        comment=comment is not None, wiki_min_score=float(wiki.get("min_score", 1.0)),
        policy=policy, router=router, memory=memory, budget=budget, gate=gate,
    )
