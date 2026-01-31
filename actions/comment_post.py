#!/usr/bin/env python3
"""Reply to comments on your Moltbook posts (safely).

Goals:
- avoid double-replying to the same comment
- keep a local record of what we've replied to in .moltbook_daemon_state.json
- support replying across all of your recent posts

Skill refs:
- POST /posts/{POST_ID}/comments (with optional parent_id)

Safety strategy:
- before replying, we refresh comments and infer which comment IDs already have
  a reply from us (via parent_id + our agent id)
- we also store responded_to_comment_ids in state, and check both sources

This script uses a simple default reply template. You can override it with
--reply-text.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from core.moltbook_client import MoltbookClient


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Reply to comments on your Moltbook posts"
    )
    p.add_argument(
        "--post-id",
        default=None,
        help="Only reply to comments on this post id (optional)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Reply to comments across all of your recent posts",
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
        help=(
            "State JSON path (default: env STATE_FILE or "
            ".moltbook_daemon_state.json)"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not create comments; just show what would be replied to",
    )
    p.add_argument(
        "--max-replies",
        type=int,
        default=50,
        help="Max number of replies to send in one run (default: 50)",
    )
    p.add_argument(
        "--sleep-s",
        type=float,
        default=2.0,
        help="Seconds to sleep between replies (default: 2.0)",
    )
    p.add_argument(
        "--reply-text",
        default=None,
        help="Override reply text (use {author} and {excerpt} placeholders)",
    )
    p.add_argument(
        "--sort",
        default="new",
        help="Sort order for comments (default: new)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max comments to fetch per post when scanning (default: 200)",
    )
    return p.parse_args(argv)


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "projects": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "projects": {}}


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _extract_posts_from_profile(
    profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    posts = profile.get("recentPosts") or []
    if not isinstance(posts, list):
        return []
    return [p for p in posts if isinstance(p, dict) and p.get("id")]


def _extract_comments_list(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
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

    return []


def _comment_author(c: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    a = c.get("author")
    if isinstance(a, dict):
        return a.get("id"), a.get("name")
    a2 = c.get("agent")
    if isinstance(a2, dict):
        return a2.get("id"), a2.get("name")
    return None, None


def _comment_content(c: Dict[str, Any]) -> str:
    v = c.get("content")
    return v if isinstance(v, str) else ""


def _parent_id(c: Dict[str, Any]) -> Optional[str]:
    v = c.get("parent_id") or c.get("parentId")
    return v if isinstance(v, str) and v else None


def _infer_responded_to(
    comments: List[Dict[str, Any]],
    my_agent_id: Optional[str],
) -> set[str]:
    responded: set[str] = set()
    if not my_agent_id:
        return responded

    for c in comments:
        author_id, _ = _comment_author(c)
        if author_id != my_agent_id:
            continue
        pid = _parent_id(c)
        if pid:
            responded.add(pid)

    return responded


def _default_reply(author: str, excerpt: str) -> str:
    # Keep it short and friendly; avoid over-promising.
    # NOTE: We intentionally do NOT quote the comment text in automated replies
    # (it may contain unsafe language, links, or personal data).
    _ = excerpt
    return (
        f"Thanks {author}! Appreciate the comment — we’re iterating fast and "
        "I’ll share updates as the daemon gets smarter."  # noqa: ISC003
    )


def main(argv: List[str]) -> int:
    load_dotenv()

    api_key = os.getenv("MOLTBOOK_API_KEY")

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
        or (
            Path(__file__).resolve().parents[1]
            / ".moltbook_daemon_state.json"
        )
    )

    state = _load_state(state_path)
    mb = state.get("moltbook") if isinstance(state, dict) else None
    mb_agent = (mb.get("agent") if isinstance(mb, dict) else None) or {}
    my_agent_id = mb_agent.get("id")
    my_agent_name = str(mb_agent.get("name") or "").strip() or None

    # Prefer running after authorize so we can avoid using the API key unless
    # we're actually going to post replies.
    if not my_agent_name:
        if not api_key:
            print(
                "ERROR: agent name not found in state and "
                "MOLTBOOK_API_KEY is not set. "
                "Run: python -m core.authorize",
                file=sys.stderr,
            )
            return 2

        client_auth = MoltbookClient(
            api_key,
            timeout_s=timeout_s,
            retries=retries,
        )
        try:
            me = client_auth.get_me()
        except (requests.RequestException, RuntimeError) as e:
            print(f"ERROR: failed to auth: {e}", file=sys.stderr)
            return 1

        agent = (me.get("agent") or {}) if isinstance(me, dict) else {}
        my_agent_id = agent.get("id")
        my_agent_name = str(agent.get("name") or "").strip() or None
        if not my_agent_name:
            print("ERROR: could not determine agent name", file=sys.stderr)
            return 1

        state.setdefault("moltbook", {})["agent"] = {
            "id": my_agent_id,
            "name": my_agent_name,
        }
        _save_state(state_path, state)

    if not args.dry_run and not api_key:
        print(
            "ERROR: MOLTBOOK_API_KEY not set (check your .env). "
            "It is required to post replies.",
            file=sys.stderr,
        )
        return 2

    client = MoltbookClient(
        api_key=api_key,
        timeout_s=timeout_s,
        retries=retries,
    )

    # Choose posts
    posts: List[Dict[str, Any]] = []
    if args.post_id:
        posts = [{"id": args.post_id}]
    elif args.all:
        profile = client.get_profile(str(my_agent_name))
        posts = _extract_posts_from_profile(profile)
    else:
        print("ERROR: specify --post-id or --all", file=sys.stderr)
        return 2

    mb = state.setdefault("moltbook", {})
    mb["agent"] = {"id": my_agent_id, "name": my_agent_name}
    mb_posts = mb.setdefault("posts", {})

    replies_sent = 0
    would_reply_count = 0
    max_replies = max(0, int(args.max_replies))
    sleep_s = max(0.0, float(args.sleep_s))

    for p in posts:
        post_id = str(p.get("id"))
        if not post_id:
            continue

        entry = mb_posts.setdefault(post_id, {})
        entry.setdefault("responded_to_comment_ids", [])
        responded_state = set(
            cid
            for cid in entry.get("responded_to_comment_ids", [])
            if isinstance(cid, str)
        )

        # Refresh comments from API so we don't double-reply
        # even if state is stale.
        try:
            payload = client.get_post_comments(
                post_id,
                sort=str(args.sort),
                limit=max(1, int(args.limit)),
            )
        except (requests.RequestException, RuntimeError) as e:
            msg = str(e)
            print(f"WARN: failed to fetch comments for post {post_id}: {msg}")
            entry["last_comments_sync_at"] = _utc_now_iso()
            entry["comments_api_last_error"] = msg
            continue

        comments = _extract_comments_list(payload)
        responded_api = _infer_responded_to(comments, my_agent_id)
        responded = responded_state | responded_api

        replied_this_post = 0

        # Persist a compact snapshot (optional but useful for audits)
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
                "parent_id": _parent_id(c),
                "author": {"id": author_id, "name": author_name},
            }
        entry["comments"] = compact
        entry["last_comments_sync_at"] = _utc_now_iso()

        pending: List[Dict[str, Any]] = []
        for c in comments:
            cid = c.get("id")
            if not isinstance(cid, str) or not cid:
                continue
            author_id, author_name = _comment_author(c)

            # Ignore our own comments.
            if my_agent_id and author_id == my_agent_id:
                continue

            # Ignore replies (only respond to top-level comments by default).
            if _parent_id(c):
                continue

            if cid in responded:
                continue

            pending.append(c)

        if not pending:
            print(f"Post {post_id}: no pending comments")
            continue

        print(f"Post {post_id}: {len(pending)} pending comment(s)")

        for c in pending:
            if max_replies:
                if args.dry_run and would_reply_count >= max_replies:
                    break
                if not args.dry_run and replies_sent >= max_replies:
                    break

            cid = str(c.get("id"))
            _, author_name = _comment_author(c)
            author_name = author_name or "there"

            excerpt = _comment_content(c)

            # Safety: do not echo comment text back in automated replies.
            # We still accept user-provided templates, but {excerpt} will be
            # replaced with a neutral placeholder.
            excerpt_placeholder = "(excerpt omitted)"

            reply_text = args.reply_text
            if isinstance(reply_text, str) and reply_text.strip():
                rendered = reply_text
                rendered = rendered.replace("{author}", author_name)
                rendered = rendered.replace("{excerpt}", excerpt_placeholder)
                content = rendered
            else:
                content = _default_reply(author_name, excerpt)

            if args.dry_run:
                print(
                    f"DRY_RUN: would reply to comment {cid} on post {post_id}"
                )
                print(f"  -> {content}")
                would_reply_count += 1
                continue

            try:
                resp = client.create_comment(
                    post_id,
                    content=content,
                    parent_id=cid,
                )
            except (requests.RequestException, RuntimeError) as e:
                print(f"WARN: failed to reply to comment {cid}: {e}")
                continue

            if isinstance(resp, dict) and resp.get("dry_run"):
                print("DRY_RUN - reply skipped")
                continue

            print(f"Replied to comment {cid} on post {post_id}")
            replies_sent += 1
            responded.add(cid)
            replied_this_post += 1

            if sleep_s > 0:
                time.sleep(sleep_s)

        # Update state for this post
        entry["responded_to_comment_ids"] = sorted(responded)
        if replied_this_post > 0 and not args.dry_run:
            entry["last_replied_at"] = _utc_now_iso()

        if max_replies and replies_sent >= max_replies:
            break

    _save_state(state_path, state)
    print(f"State updated: {state_path}")

    if args.dry_run:
        print(
            f"DRY_RUN complete: would reply to "
            f"{would_reply_count} comment(s)"
        )
    else:
        print(f"Done. Replies sent: {replies_sent}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
