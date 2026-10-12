"""Live demo: the same question, asked by different people, returns different pages.

    python demo_permissions.py
    python demo_permissions.py --query "emergency revocation break glass" --users new_hire billing_oncall
    python demo_permissions.py --no-filter      # show what leaks without the filter
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rag.acl import PermissionFilteredBM25, build_index, can_view, effective_gates, load_wiki

DEFAULT = Path(__file__).parent / "tests" / "fixtures" / "mini_confluence.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wiki", type=Path, default=DEFAULT)
    ap.add_argument("--query", default="how do I rotate the billing API credentials")
    ap.add_argument("--users", nargs="+", default=["new_hire", "billing_oncall", "contractor"])
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--no-filter", action="store_true", help="turn the permission filter off")
    args = ap.parse_args()

    wiki, users = load_wiki(args.wiki)
    index = build_index(wiki)
    retriever = PermissionFilteredBM25(index, enforce=not args.no_filter)

    print(f'Query: "{args.query}"')
    print(f"Permission filter: {'OFF' if args.no_filter else 'ON'}\n")

    for uid in args.users:
        if uid not in users:
            print(f"unknown user {uid!r}; choose from: {', '.join(users)}")
            return 2
        user = users[uid]
        groups = ", ".join(sorted(user.groups)) or "no groups"
        print(f"{uid}  ({groups})")
        hits = retriever.retrieve(args.query, user, args.k)
        if not hits:
            print("   nothing this user is allowed to see matches\n")
            continue
        for rank, s in enumerate(hits, start=1):
            page = wiki.pages[index.page_of[s.chunk.id]]
            allowed = can_view(user, effective_gates(page.id, wiki))
            flag = "" if allowed else "   <-- LEAK: user cannot open this page"
            print(f"   {rank}. [{page.id}] {page.title}  (owner {page.owner}, updated {page.last_modified}){flag}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())