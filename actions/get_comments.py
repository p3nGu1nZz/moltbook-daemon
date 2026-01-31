#!/usr/bin/env python3
"""Fetch comments for Moltbook posts and sync them into local state.

Primary use:
- pull comments for a specific post id (from actions.view_posts)
- optionally pull for all of your recent posts
- store results in .moltbook_daemon_state.json so we can track replies

Skill refs:
- GET /agents/me
- GET /agents/profile?name=...
- GET /posts/{POST_ID}/comments

State strategy (non-breaking):
- preserves existing top-level keys (e.g. "projects")
- adds/updates state["moltbook"]["posts"][POST_ID]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from core.moltbook_client import MoltbookClient


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch comments for Moltbook posts")
    p.add_argument("--post-id", default=None, help="Fetch comments for this post")
    p.add_argument(
        "--all",
        action="store_true",
        help="Fetch comments for all of your recent posts",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max comments to fetch per post (default: 100)",
    )
    p.add_argument(
        "--sort",
        default="new",
        help="Sort order for comments (default: new)",
    )
    p.add_argument(
        "--timeout-s",
        type=int,
        default=None,
        help="HTTP timeout seconds (default: env MOLTBOOK_TIMEOUT_S or 300)",
    )
    p.add_argument(
        "--retries",
        type=int,
        default=None,
        help="Retries for GET/HEAD (default: env MOLTBOOK_RETRIES or 2)",
    )
    p.add_argument(
        "--state-file",
        default=None,
        help="State JSON path (default: env STATE_FILE or .moltbook_daemon_state.json)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write state; just print results",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON payload for each post",
    )
    return p.parse_args(argv)


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "projects": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "projects": {}}


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _extract_posts_from_profile(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    posts = profile.get("recentPosts") or []
    if not isinstance(posts, list):
        return []
    return [p for p in posts if isinstance(p, dict) and p.get("id")]


def _extract_comments_list(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Best-effort normalize API response into a list[comment]."""
    if not isinstance(payload, dict):
        return []

    for key in ("comments", "data", "items"):
        v = payload.get(key)
        if isinstance(v, list):
            return [c for c in v if isinstance(c, dict)]
        if isinstance(v, dict):
            for subkey in ("comments", "items"):
                vv = v.get(subkey)
                if isinstance(vv, list):
                    return [c for c in vv if isinstance(c, dict)]

    # fallback: sometimes API returns {success:true, ...} with no obvious list
    return []


def _comment_author(c: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    a = c.get("author")
    if isinstance(a, dict):
        return a.get("id"), a.get("name")
    # occasionally APIs use agent
    a2 = c.get("agent")
    if isinstance(a2, dict):
        return a2.get("id"), a2.get("name")
    return None, None


def _compute_responded_to(
    *,
    comments: List[Dict[str, Any]],
    my_agent_id: Optional[str],
) -> List[str]:
    """Infer which comment IDs have already been replied to by us."""
    if not my_agent_id:
        return []

    responded: set[str] = set()
    for c in comments:
        author_id, _ = _comment_author(c)
        if author_id != my_agent_id:
            continue
        parent_id = c.get("parent_id") or c.get("parentId")
        if isinstance(parent_id, str) and parent_id:
            responded.add(parent_id)

    return sorted(responded)


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
        else int(os.getenv("MOLTBOOK_TIMEOUT_S", "300"))
    )
    retries = (
        int(args.retries)
        if args.retries is not None
        else int(os.getenv("MOLTBOOK_RETRIES", "2"))
    )

    state_path = Path(
        args.state_file
        or os.getenv("STATE_FILE")
        or (Path(__file__).resolve().parents[1] / ".moltbook_daemon_state.json")
    )

    client = MoltbookClient(api_key, timeout_s=timeout_s, retries=retries)

    # Identify ourselves
    try:
        me = client.get_me()
    except (requests.RequestException, RuntimeError) as e:
        print(f"ERROR: failed to auth: {e}", file=sys.stderr)
        return 1

    agent = (me.get("agent") or {}) if isinstance(me, dict) else {}
    my_agent_id = agent.get("id")
    my_agent_name = str(agent.get("name") or "").strip() or None

    posts: List[Dict[str, Any]] = []
    if args.post_id:
        posts = [{"id": args.post_id, "title": None}]
    elif args.all:
        if not my_agent_name:
            print("ERROR: could not determine agent name", file=sys.stderr)
            return 1
        try:
            profile = client.get_profile(str(my_agent_name))
        except RuntimeError as e:
            print(
                f"ERROR: failed to fetch profile for name={my_agent_name!r}: {e}",
                file=sys.stderr,
            )
            return 1
        posts = _extract_posts_from_profile(profile)
    else:
        print("ERROR: specify --post-id or --all", file=sys.stderr)
        return 2

    state = _load_state(state_path)
    mb = state.setdefault("moltbook", {})
    mb["agent"] = {"id": my_agent_id, "name": my_agent_name}
    mb_posts = mb.setdefault("posts", {})

    wrote_any = False

    for p in posts:
        post_id = str(p.get("id"))
        if not post_id:
            continue

        try:
            payload = client.get_post_comments(
                post_id,
                sort=str(args.sort),
                limit=max(1, int(args.limit)),
            )
        except (requests.RequestException, RuntimeError) as e:
            print(f"WARN: failed to fetch comments for post {post_id}: {e}")
            continue

        comments = _extract_comments_list(payload)
        responded_to = _compute_responded_to(comments=comments, my_agent_id=my_agent_id)

        if args.json:
            print(json.dumps({"post_id": post_id, "payload": payload}, indent=2))
        else:
            print(f"Post {post_id}: {len(comments)} comment(s)")

        entry = mb_posts.setdefault(post_id, {})
        entry.setdefault("responded_to_comment_ids", [])

        # keep any existing responded-to marks, then add inferred ones
        existing = set(
            c for c in entry.get("responded_to_comment_ids", []) if isinstance(c, str)
        )
        existing.update(responded_to)
        entry["responded_to_comment_ids"] = sorted(existing)

        # store a compact copy of comments
        compact: Dict[str, Any] = {}
        for c in comments:
            cid = c.get("id")
            if not isinstance(cid, str) or not cid:
                continue
            author_id, author_name = _comment_author(c)
            compact[cid] = {
                "id": cid,
                "created_at": c.get("created_at") or c.get("createdAt"),
                "content": c.get("content"),
                "parent_id": c.get("parent_id") or c.get("parentId"),
                "author": {"id": author_id, "name": author_name},
            }

        entry["comments"] = compact
        entry["last_comments_sync_at"] = _utc_now_iso()
        wrote_any = True

    if wrote_any and not args.dry_run:
        _save_state(state_path, state)
        print(f"State updated: {state_path}")

    if args.dry_run:
        print("DRY_RUN: state not written")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
