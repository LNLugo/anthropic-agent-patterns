"""Tracing for 08, from 10_observability. Imported here once so every module in the
package gets the same tracer. (The path setup goes away with the single pyproject.)"""

import sys
from pathlib import Path

try:
    import tracing  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "10_observability"))
    import tracing  # noqa: F401

from tracing import get_tracer  # noqa: E402

__all__ = ["get_tracer"]
