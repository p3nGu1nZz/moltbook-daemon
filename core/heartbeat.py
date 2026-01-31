#!/usr/bin/env python3
"""Moltbook heartbeat helper.

This script implements a simple periodic check-in routine based on Moltbook's
HEARTBEAT.md guidance:
- check claim status
- check DM activity
- browse your personalized feed
- optionally browse global new posts
- optionally check skill version

It does *not* auto-post (posting is cooldown-limited and should be intentional).

Env vars (loaded from `.env`):
- MOLTBOOK_API_KEY (required)
- MOLTBOOK_TIMEOUT_S (optional, default: 30)
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

from core.moltbook_client import MoltbookClient


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Moltbook heartbeat checks")
    p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="How many items to fetch for feeds (default: 10)",
    )
    p.add_argument(
        "--sort",
        default="new",
        help="Sort for feeds (default: new)",
    )
    p.add_argument(
        "--check-skill-version",
        action="store_true",
        help="Fetch https://www.moltbook.com/skill.json and print version",
    )
    p.add_argument(
        "--also-global",
        action="store_true",
        help="Also fetch global new posts (/posts)",
    )
    p.add_argument(
        "--timeout-s",
        type=int,
        default=None,
        help="HTTP timeout seconds (default: env MOLTBOOK_TIMEOUT_S or 30)",
    )
    return p.parse_args(argv)


def _maybe_print_skill_version(timeout_s: int) -> None:
    url = "https://www.moltbook.com/skill.json"
    r = requests.get(url, timeout=timeout_s, allow_redirects=False)
    r.raise_for_status()
    data = r.json() if r.headers.get("content-type", "").startswith("application") else {}
    if isinstance(data, dict):
        name = data.get("name")
        version = data.get("version")
        print(f"Skill: {name} version={version}")
    else:
        print("Skill: (unexpected response)")


def _print_feed_summary(label: str, payload: Dict[str, Any]) -> None:
    posts = payload.get("posts") or payload.get("data") or []
    if isinstance(posts, dict):
        posts = posts.get("items") or posts.get("posts") or []

    print(f"{label}: {len(posts) if isinstance(posts, list) else 0} item(s)")

    if not isinstance(posts, list):
        return

    for p in posts[:5]:
        if not isinstance(p, dict):
            continue
        submolt = p.get("submolt")
        submolt_name = submolt.get("name") if isinstance(submolt, dict) else submolt
        print(f"- [{submolt_name}] {p.get('title')}")


def main(argv: List[str]) -> int:
    load_dotenv()

    api_key = os.getenv("MOLTBOOK_API_KEY")
    if not api_key:
        print("ERROR: MOLTBOOK_API_KEY not set (check your .env)", file=sys.stderr)
        return 2

    args = _parse_args(argv)

    timeout_s = (
        int(args.timeout_s)
        if args.timeout_s is not None
        else int(os.getenv("MOLTBOOK_TIMEOUT_S", "30"))
    )

    if args.check_skill_version:
        try:
            _maybe_print_skill_version(timeout_s)
        except Exception as e:
            print(f"WARN: failed to fetch skill.json: {e}", file=sys.stderr)

    client = MoltbookClient(api_key, timeout_s=timeout_s)

    # 1) Claimed?
    try:
        status = client.get_agent_status()
        print(f"Agent status: {status}")
    except Exception as e:
        print(f"WARN: failed to fetch agent status: {e}", file=sys.stderr)

    # 2) DMs
    try:
        dm = client.dm_check()
        if isinstance(dm, dict) and dm.get("has_activity"):
            print(f"DM activity: {dm.get('summary')}")
        else:
            print("DM activity: none")
    except Exception as e:
        print(f"WARN: failed to check DMs: {e}", file=sys.stderr)

    # 3) Personalized feed
    try:
        feed = client.get_feed(sort=args.sort, limit=max(1, int(args.limit)))
        _print_feed_summary("Your feed", feed)
    except Exception as e:
        print(f"WARN: failed to fetch personalized feed: {e}", file=sys.stderr)

    # 4) Global new posts (optional)
    if args.also_global:
        try:
            global_posts = client.list_posts(sort=args.sort, limit=max(1, int(args.limit)))
            _print_feed_summary("Global posts", global_posts)
        except Exception as e:
            print(f"WARN: failed to fetch global posts: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
