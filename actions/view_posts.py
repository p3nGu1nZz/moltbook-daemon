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
from pathlib import Path
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
    p.add_argument(
        "--state-file",
        default=None,
        help=(
            "State JSON path (default: env STATE_FILE or "
            ".moltbook_daemon_state.json)"
        ),
    )
    p.add_argument(
        "--agent-name",
        default=None,
        help="Override agent name (otherwise read from state)",
    )
    return p.parse_args(argv)


def _as_submolt_name(v: Any) -> str:
    if isinstance(v, dict):
        return (v.get("name") or v.get("display_name") or "").strip()
    if isinstance(v, str):
        return v.strip()
    return ""


def _get_profile(client: MoltbookClient, name: str) -> Dict[str, Any]:
    return client.get_profile(name)


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "projects": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "projects": {}}


def main(argv: List[str]) -> int:
    load_dotenv()

    args = _parse_args(argv)

    timeout_s = (
        int(args.timeout_s)
        if args.timeout_s is not None
        else int(os.getenv("MOLTBOOK_TIMEOUT_S", "300"))
    )

    state_path = Path(
        args.state_file
        or os.getenv("STATE_FILE")
        or (
            Path(__file__).resolve().parents[1]
            / ".moltbook_daemon_state.json"
        )
    )
    state = _load_state(state_path)
    mb = state.get("moltbook") if isinstance(state, dict) else None
    mb_agent = (mb.get("agent") if isinstance(mb, dict) else None) or {}

    name = (
        (args.agent_name or "").strip()
        or (mb_agent.get("name") or "").strip()
    )
    if not name:
        print(
            "ERROR: agent name not found in state. "
            "Run: python -m core.authorize",
            file=sys.stderr,
        )
        return 2

    # Public endpoint: do not send API key.
    client = MoltbookClient(api_key=None, timeout_s=timeout_s)

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
