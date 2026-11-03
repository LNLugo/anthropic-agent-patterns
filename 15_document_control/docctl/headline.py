"""The headline result (s10, ADR 0019): on revision pairs with planted, labelled changes, does
a deterministic diff followed by classification beat asking a model to compare directly?

    rules           diff, then the rules classifier: the non-model baseline
    diff + model    diff, then one model call per change to classify it
    model direct    one model call per pair, comparing the two revisions itself

Measured per arm: change recall (planted changes found), type and severity accuracy on the
changes found, and false findings. The design says recall matters most: a false alarm costs a
reviewer a minute, a missed change is a quality event.

The model arms run live (run_comparison.py --live, three runs, with a key) and every reply is
recorded in results/comparison_live.json. Offline, the same scoring replays those recorded
replies, so the numbers reproduce without a key (ADR 0013). Until a live run is recorded, the
model arms are reported as not run.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from langchain_core.messages import AIMessage

from .compare import classify_model, classify_rules, compare_direct, diff, score
from .corpus import FIXTURES
from .parse import parse_pdf

PAIRS = FIXTURES / "comparison"
RECORDED = Path(__file__).resolve().parents[1] / "results" / "comparison_live.json"


def pairs(folder: Path = PAIRS) -> list[dict]:
    return yaml.safe_load((folder / "pairs.yaml").read_text(encoding="utf-8"))["pairs"]


def load(p: dict, folder: Path = PAIRS):
    return parse_pdf((folder / p["a"]).read_bytes()), parse_pdf((folder / p["b"]).read_bytes())


class Recorder:
    """Wraps a live model and keeps every reply, in call order."""

    def __init__(self, llm):
        self.llm, self.replies = llm, []

    def invoke(self, prompt):
        msg = self.llm.invoke(prompt)
        self.replies.append(msg.content if isinstance(msg.content, str) else str(msg.content))
        return msg


class Replay:
    """Returns recorded replies in order; running out is an error, never a silent default."""

    def __init__(self, replies: list[str]):
        self.replies, self.i = list(replies), 0

    def invoke(self, prompt):
        if self.i >= len(self.replies):
            raise RuntimeError("the recording has fewer replies than this run asks for")
        self.i += 1
        return AIMessage(content=self.replies[self.i - 1])


def _total(rows: list[dict]) -> dict:
    keys = ("planted", "found", "false_findings", "type_correct", "severity_correct")
    t = {k: sum(r[k] for r in rows) for k in keys}
    t["recall"] = round(t["found"] / t["planted"], 3) if t["planted"] else None
    return t


def run_rules(ps: list[dict]) -> dict:
    rows = []
    for p in ps:
        a, b = load(p)
        rows.append(score([classify_rules(c) for c in diff(a, b, p["a"], p["b"])], p["changes"]))
    return _total(rows)


def run_model_arms(ps: list[dict], llm_for) -> tuple[dict, dict]:
    """llm_for(arm, pair_id) gives the model to call: live (recording) or a replay."""
    with_diff, direct = [], []
    for p in ps:
        a, b = load(p)
        m = llm_for("diff_model", p["id"])
        with_diff.append(score([classify_model(c, m) for c in diff(a, b, p["a"], p["b"])], p["changes"]))
        direct.append(score(compare_direct(a, b, llm_for("direct", p["id"])), p["changes"]))
    return _total(with_diff), _total(direct)


def record_live(llm, runs: int = 3, model: str = "unknown", measured: str = "") -> dict:
    ps = pairs()
    out = {"model": model, "measured": measured, "runs": []}
    for _ in range(runs):
        recs: dict[str, dict[str, Recorder]] = {"diff_model": {}, "direct": {}}

        def llm_for(arm, pid):
            recs[arm].setdefault(pid, Recorder(llm))
            return recs[arm][pid]

        run_model_arms(ps, llm_for)
        out["runs"].append({arm: {pid: r.replies for pid, r in d.items()} for arm, d in recs.items()})
    RECORDED.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return out


def replay_recorded(path: Path = RECORDED) -> dict | None:
    if not path.exists():
        return None
    rec = json.loads(path.read_text(encoding="utf-8"))
    ps = pairs()
    runs = []
    for run in rec["runs"]:
        replays = {arm: {pid: Replay(r) for pid, r in d.items()} for arm, d in run.items()}
        wd, dr = run_model_arms(ps, lambda arm, pid: replays[arm][pid])
        runs.append({"diff_model": wd, "direct": dr})
    return {"model": rec["model"], "measured": rec["measured"], "runs": runs}


def summary() -> dict:
    ps = pairs()
    planted = [c for p in ps for c in p["changes"]]
    from collections import Counter

    live = replay_recorded()
    return {"pairs": len(ps), "planted_changes": len(planted),
            "planted_by_type": dict(sorted(Counter(c["type"] for c in planted).items())),
            "rules": run_rules(ps),
            "model_arms": live if live else "not run: needs run_comparison.py --live with a key"}
