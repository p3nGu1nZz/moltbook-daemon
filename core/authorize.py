#!/usr/bin/env python3
"""Authorize / validate credentials against the Moltbook API.

This is a small helper intended for quick troubleshooting:
- verifies MOLTBOOK_API_KEY works (GET /agents/me)
- prints the authenticated agent name
- optionally prints claim/status details

Recommended invocation:
- python -m core.authorize

Env vars (loaded from `.env`):
- MOLTBOOK_API_KEY (required)
- MOLTBOOK_TIMEOUT_S (optional, default: 30)
- MOLTBOOK_API_BASE (optional, default: https://www.moltbook.com/api/v1)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

from core.moltbook_client import MoltbookClient


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Authenticate with Moltbook API")
    p.add_argument(
        "--timeout-s",
        type=int,
        default=None,
        help="HTTP timeout seconds (default: env MOLTBOOK_TIMEOUT_S or 30)",
    )
    p.add_argument(
        "--retries",
        type=int,
        default=None,
        help="Retries for GET/HEAD (default: env MOLTBOOK_RETRIES or 2)",
    )
    p.add_argument(
        "--status",
        action="store_true",
        help="Also fetch claim/status info (GET /agents/status)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON responses",
    )
    return p.parse_args(argv)


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

    retries = (
        int(args.retries)
        if args.retries is not None
        else int(os.getenv("MOLTBOOK_RETRIES", "2"))
    )

    client = MoltbookClient(api_key, timeout_s=timeout_s, retries=retries)

    try:
        me = client.get_me()
    except (requests.RequestException, RuntimeError) as e:
        print(f"ERROR: authorization failed: {e}", file=sys.stderr)
        return 1

    agent = (me.get("agent") or {}) if isinstance(me, dict) else {}
    name = (agent.get("name") or "").strip()

    if args.json:
        print(json.dumps({"me": me}, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        if name:
            print(f"Authorized as agent: {name}")
        else:
            print("Authorized, but could not parse agent name from response")

    if args.status:
        try:
            status = client.get_agent_status()
        except (requests.RequestException, RuntimeError) as e:
            print(f"WARN: failed to fetch agent status: {e}", file=sys.stderr)
            return 3

        if args.json:
            print(
                json.dumps(
                    {"status": status},
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
            )
        else:
            print(f"Agent status: {status}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
