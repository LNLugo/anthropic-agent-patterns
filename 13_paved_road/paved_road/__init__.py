"""The paved road: a new agent use case as configuration, prompts, a golden set
and minimal glue, assembled from the platform modules instead of rebuilt.

    from paved_road import build
    uc = build("use_cases/transit")
    out = uc.ask("How many open ELEV incidents this month?", "station_supervisor")

Design: docs/designs/paved-road.md.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The platform modules this package composes. (The path setup goes away with a single pyproject.)
for _d in ("08_tools", "10_observability", "11_finops", "09_memory", "."):
    _p = str(ROOT / _d)
    if _p not in sys.path:
        sys.path.append(_p)

from .spec import ConfigError, UseCaseSpec, load_spec  # noqa: E402
from .build import UseCase, build  # noqa: E402

__all__ = ["ROOT", "ConfigError", "UseCase", "UseCaseSpec", "build", "load_spec"]
