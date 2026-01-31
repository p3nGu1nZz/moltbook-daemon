#!/usr/bin/env python3
"""View your posts on Moltbook.

This uses the Moltbook profile endpoint to fetch the current agent's
`recentPosts` and prints a small, human-friendly summary.

Skill refs:
- GET /agents/me
- GET /agents/profile?name=MOLTY_NAME
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

from dotenv import load_dotenv

from core.moltbook_client import MoltbookClient


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="View your recent Moltbook posts")
    p.add_argument("--limit", type=int, default=10, help="Max posts to show")
    p.add_argument(
        "--submolt",
        default=None,
        help="Only show posts from this submolt (optional)",
    )
    p.add_argument(
        "--contains",
        default=None,
        help="Only show posts whose content contains this string (optional)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON for matching posts",
    )
    p.add_argument(
        "--timeout-s",
        type=int,
        default=None,
        help="HTTP timeout seconds (default: env MOLTBOOK_TIMEOUT_S or 30)",
    )
    return p.parse_args(argv)


def _as_submolt_name(v: Any) -> str:
    if isinstance(v, dict):
        return (v.get("name") or v.get("display_name") or "").strip()
    if isinstance(v, str):
        return v.strip()
    return ""


def _get_my_name(client: MoltbookClient) -> str:
    me = client.get_me()
    agent = (me.get("agent") or {}) if isinstance(me, dict) else {}
    return (agent.get("name") or "").strip()


def _get_profile(client: MoltbookClient, name: str) -> Dict[str, Any]:
    return client.get_profile(name)


def main(argv: List[str]) -> int:
    load_dotenv()

    api_key = os.getenv("MOLTBOOK_API_KEY")
    if not api_key:
        print(
            "ERROR: MOLTBOOK_API_KEY not set (check your .env)",
            file=sys.stderr,
        )
        return 2

    args = _parse_args(argv)

    timeout_s = (
        int(args.timeout_s)
        if args.timeout_s is not None
        else int(os.getenv("MOLTBOOK_TIMEOUT_S", "300"))
    )

    client = MoltbookClient(api_key, timeout_s=timeout_s)

    name = _get_my_name(client)
    if not name:
        print(
            "ERROR: Could not determine agent name from /agents/me",
            file=sys.stderr,
        )
        return 1

    profile = _get_profile(client, name)
    recent = (
        (profile.get("recentPosts") if isinstance(profile, dict) else None)
        or []
    )
    if not isinstance(recent, list):
        recent = []

    shown = 0
    for p in recent:
        if not isinstance(p, dict):
            continue

        submolt_name = _as_submolt_name(p.get("submolt"))
        if args.submolt and submolt_name != args.submolt.strip():
            continue

        content = (p.get("content") or "")
        if args.contains and args.contains not in content:
            continue

        if args.json:
            print(json.dumps(p, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print("-")
            print(f"id:       {p.get('id')}")
            print(f"created:  {p.get('created_at')}")
            print(f"submolt:  {submolt_name}")
            print(f"title:    {p.get('title')}")
            if p.get("url"):
                print(f"url:      {p.get('url')}")

        shown += 1
        if shown >= max(1, int(args.limit)):
            break

    if shown == 0:
        print("No posts matched.")
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
