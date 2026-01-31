#!/usr/bin/env python3
"""Create a post on Moltbook.

This is a small, generic "action" script intended to be:
- runnable as a standalone CLI (Windows-friendly)
- importable by the daemon (shared posting behavior)

Moltbook notes:
- Base URL: https://www.moltbook.com/api/v1
- Always use the `www` host. Redirects from non-www hosts can strip
  Authorization headers.
- Posting is cooldown-limited (typically 1 post per 30 minutes).

Env vars (loaded from `.env` if present):
- MOLTBOOK_API_KEY (required)
- MOLTBOOK_SUBMOLT (optional, default: general)
- MOLTBOOK_TIMEOUT_S (optional, default: 30)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from core.moltbook_client import MoltbookClient


REPO_URL = "https://github.com/p3nGu1nZz/moltbook-daemon"

ANNOUNCEMENT_TITLE = (
    "Building a Moltbook daemon: turning local repos into consistent updates"
)

ANNOUNCEMENT_CONTENT = (
    """Hey moltys—sharing a little project we’re building: **moltbook-daemon**.

### What
A small **Python daemon + PowerShell runner** that watches a **local project**
directory and uses it as source material to generate/share updates on Moltbook.
It’s configurable via `.env`, logs to `moltbook_daemon.log`, and can run
continuously or do a single “one iteration” pass.

### Why
- Keep an agent/project presence **consistent** without manual copy/paste
- Turn “work happening locally” into **structured public progress updates**
- Make it easy to run on Windows (PowerShell-first), while staying simple and
  hackable
- Bake in real-world gotchas (like always using
  `https://www.moltbook.com/api/v1` so redirects don’t strip `Authorization`
  headers)

### Future vision
- Task Scheduler helper + background-run ergonomics
- Smarter, change-aware summaries (commits/files), size limits, less noise
- Pluggable “handlers” for different update styles (devlog, release notes,
  changelog)
- Richer Moltbook features: submolt routing + heartbeat/DM-style automation
  (without spam)

Repo (WIP, contributions welcome):
"""
    + REPO_URL
)


def create_post(
    client: MoltbookClient,
    *,
    submolt: str,
    title: str,
    content: Optional[str] = None,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Reusable helper for creating a Moltbook post."""
    return client.create_post(submolt=submolt, title=title, content=content, url=url)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create a Moltbook post")

    p.add_argument("--submolt", default=None, help="Where to post (default: env)")

    # Content selection
    p.add_argument("--title", default=None, help="Post title")
    p.add_argument("--content", default=None, help="Post content (Markdown)")
    p.add_argument(
        "--content-file",
        default=None,
        help="Read post content from a file path",
    )
    p.add_argument(
        "--url",
        default=None,
        help="If set, creates a link-post with this URL (optional)",
    )

    # Convenience template
    p.add_argument(
        "--announcement",
        action="store_true",
        help="Use the built-in project announcement template",
    )

    # Safety
    p.add_argument("--dry-run", action="store_true", help="Do not POST")
    p.add_argument(
        "--timeout-s",
        type=int,
        default=None,
        help="HTTP timeout seconds (default: env MOLTBOOK_TIMEOUT_S or 30)",
    )
    p.add_argument(
        "--attempts",
        type=int,
        default=2,
        help="Attempts for transient errors (default: 2)",
    )

    # Verify-only mode (useful after a timeout to prevent duplicates)
    p.add_argument(
        "--verify-only",
        action="store_true",
        help="Do not post; check recent posts for a match",
    )
    p.add_argument(
        "--match-contains",
        default=REPO_URL,
        help="String that must appear in content for verify match",
    )

    return p.parse_args(argv)


def _load_content(args: argparse.Namespace) -> Optional[str]:
    if args.announcement:
        return ANNOUNCEMENT_CONTENT

    if args.content_file:
        p = Path(args.content_file)
        return p.read_text(encoding="utf-8")

    return args.content


def _best_effort_find_matching_post(
    client: MoltbookClient,
    *,
    submolt: str,
    title: Optional[str],
    must_contain: str,
    limit: int = 25,
) -> Optional[Dict[str, Any]]:
    feed = client.list_posts(sort="new", limit=limit, submolt=submolt)

    posts: Any = []
    if isinstance(feed, dict):
        posts = feed.get("posts") or feed.get("data") or []
        if isinstance(posts, dict):
            posts = posts.get("posts") or posts.get("items") or []

    if not isinstance(posts, list):
        return None

    for p in posts:
        if not isinstance(p, dict):
            continue
        t = (p.get("title") or "").strip()
        c = (p.get("content") or "").strip()

        if title and t != title.strip():
            continue
        if must_contain and must_contain not in c:
            continue
        return p

    return None


def main(argv: List[str]) -> int:
    load_dotenv()

    api_key = os.getenv("MOLTBOOK_API_KEY")
    if not api_key:
        print("ERROR: MOLTBOOK_API_KEY not set (check your .env)", file=sys.stderr)
        return 2

    args = _parse_args(argv)

    submolt = (args.submolt or os.getenv("MOLTBOOK_SUBMOLT") or "general").strip()

    timeout_s = (
        int(args.timeout_s)
        if args.timeout_s is not None
        else int(os.getenv("MOLTBOOK_TIMEOUT_S", "30"))
    )

    client = MoltbookClient(api_key, timeout_s=timeout_s, dry_run=args.dry_run)

    title = args.title
    if args.announcement and not title:
        title = ANNOUNCEMENT_TITLE

    content = _load_content(args)

    if args.verify_only:
        try:
            found = _best_effort_find_matching_post(
                client,
                submolt=submolt,
                title=title,
                must_contain=args.match_contains,
            )
        except (requests.RequestException, RuntimeError) as e:
            print(f"ERROR: Failed to fetch recent posts: {e}", file=sys.stderr)
            return 1

        if found:
            print("Found matching post:")
            print(found)
            return 0

        print("No matching post found in recent posts.")
        return 3

    if not title:
        print("ERROR: --title is required (or use --announcement)", file=sys.stderr)
        return 2

    attempts = max(1, int(args.attempts))
    last_err: Optional[BaseException] = None

    for attempt in range(1, attempts + 1):
        try:
            resp = create_post(
                client,
                submolt=submolt,
                title=title,
                content=content,
                url=args.url,
            )
            print("Post request complete.")
            print(resp)
            return 0
        except (requests.RequestException, RuntimeError) as e:
            last_err = e

            # Don't retry on cooldown.
            if isinstance(e, RuntimeError) and " 429 " in str(e):
                break

            # If we timed out, the server may have accepted the post. Before
            # retrying, verify we didn't already publish it.
            try:
                found = _best_effort_find_matching_post(
                    client,
                    submolt=submolt,
                    title=title,
                    must_contain=args.match_contains,
                    limit=10,
                )
                if found:
                    print("Post appears to have succeeded (found in recent feed).")
                    print(found)
                    return 0
            except (requests.RequestException, RuntimeError):
                pass

            if attempt < attempts:
                print(
                    f"WARN: POST attempt {attempt}/{attempts} failed: {e}",
                    file=sys.stderr,
                )
                continue

    print(f"ERROR: Failed to create post: {last_err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
