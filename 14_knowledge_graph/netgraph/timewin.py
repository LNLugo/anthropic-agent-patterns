"""Time words in a question, turned into a window. Parsed, not asked of a model.

    this morning     06:00 to 12:00 today          since lunchtime   12:00 today to now
    this afternoon   12:00 to 18:00 today          around noon       11:00 to 13:00 today
    this evening     18:00 to now                  overnight         00:00 to 06:00 today
    last night       18:00 yesterday to 06:00      today             00:00 today to now
    yesterday        the whole of yesterday

A question with none of these has no window. A window down-weights items outside it; it
never removes them, since a cause can come before the period the question names.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

PHRASES = [
    (r"\bsince lunch(?:time)?\b", lambda d, now: (d + timedelta(hours=12), now)),
    (r"\b(?:around|at) noon\b|\bmidday\b", lambda d, now: (d + timedelta(hours=11), d + timedelta(hours=13))),
    (r"\bthis morning\b", lambda d, now: (d + timedelta(hours=6), d + timedelta(hours=12))),
    (r"\bthis afternoon\b", lambda d, now: (d + timedelta(hours=12), d + timedelta(hours=18))),
    (r"\bthis evening\b|\btonight\b", lambda d, now: (d + timedelta(hours=18), now)),
    (r"\blast night\b", lambda d, now: (d - timedelta(hours=6), d + timedelta(hours=6))),
    (r"\bovernight\b", lambda d, now: (d, d + timedelta(hours=6))),
    (r"\byesterday\b", lambda d, now: (d - timedelta(days=1), d)),
    (r"\btoday\b", lambda d, now: (d, now)),
]


def window(question: str, now: datetime) -> tuple[datetime, datetime] | None:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    q = question.lower()
    for pattern, span in PHRASES:
        if re.search(pattern, q):
            return span(day, now)
    return None
