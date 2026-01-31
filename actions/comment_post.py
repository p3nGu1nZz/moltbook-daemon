#!/usr/bin/env python3
"""Reply to comments on your Moltbook posts (safely + uniquely).

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

Workflow (fully automated):
- For each pending top-level comment, generate a unique reply that matches tone
    (helpful for questions, calm for nasty comments, etc.).
- Save the reply text to a markdown file for auditability.
- Post the reply (unless --dry-run or --draft-only).

Draft files contain ONLY the reply text (no metadata/frontmatter).
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
from core.comment_reply_policy import (
    CommentContext,
    generate_reply_text,
    get_project_dir_from_env,
    load_persona,
    reply_hash,
)


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
        help=(
            "Do not create comments; show what would be sent. "
            "Reply files may still be written locally."
        ),
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
        "--draft-only",
        action="store_true",
        help="Only write reply markdown files; do not post replies",
    )
    p.add_argument(
        "--draft-dir",
        default=None,
        help=(
            "Draft output dir (default: comments/replies in repo root). "
            "Drafts are stored as <draft-dir>/<post_id>/<comment_id>.md"
        ),
    )
    p.add_argument(
        "--persona-file",
        default=None,
        help=(
            "Persona markdown to guide drafting (default: comments/PERSONA.md)"
        ),
    )
    p.add_argument(
        "--project-dir",
        default=None,
        help=(
            "Local project dir to search for answers "
            "(default: env PROJECT_DIR)"
        ),
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: List[str]) -> int:
    repo_root = _repo_root()
    load_dotenv(dotenv_path=repo_root / ".env")

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

    # Writing reply files does not require API key. Posting replies does.
    will_post = (not args.dry_run) and (not args.draft_only)
    if will_post and not api_key:
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

    draft_root = Path(args.draft_dir) if args.draft_dir else (
        repo_root / "comments" / "replies"
    )
    persona_path = Path(args.persona_file) if args.persona_file else (
        repo_root / "comments" / "PERSONA.md"
    )
    persona_text = load_persona(persona_path)
    project_dir = (
        str(args.project_dir).strip()
        if args.project_dir is not None
        else get_project_dir_from_env()
    )
    if project_dir:
        project_dir = os.path.expandvars(project_dir)
        project_dir = str(Path(project_dir).expanduser())
    if project_dir:
        print(f"PROJECT_DIR={project_dir}")
    else:
        print("PROJECT_DIR is not set; replies will be less specific")

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
    mb.setdefault("sent_reply_hashes", [])
    mb.setdefault("sent_reply_hashes_by_author", {})

    prior_hashes_global = set(
        h
        for h in mb.get("sent_reply_hashes", [])
        if isinstance(h, str) and h
    )
    run_hashes_global: set[str] = set()
    run_hashes_by_author: Dict[str, set[str]] = {}

    replies_sent = 0
    would_reply_count = 0
    reply_files_written = 0
    replies_duplicate_avoided = 0
    max_replies = max(0, int(args.max_replies))
    sleep_s = max(0.0, float(args.sleep_s))

    for p in posts:
        post_id = str(p.get("id"))
        if not post_id:
            continue

        entry = mb_posts.setdefault(post_id, {})
        entry.setdefault("responded_to_comment_ids", [])
        entry.setdefault("sent_reply_hashes", [])
        entry.setdefault("reply_files", {})
        entry.setdefault("sent_replies", {})
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

        # Enforce per-post uniqueness: track hashes we already sent.
        prior_hashes = set(
            h
            for h in entry.get("sent_reply_hashes", [])
            if isinstance(h, str) and h
        )
        run_hashes: set[str] = set()

        for c in pending:
            if max_replies:
                if args.dry_run and would_reply_count >= max_replies:
                    break
                if not args.dry_run and replies_sent >= max_replies:
                    break

            cid = str(c.get("id"))
            author_id, author_name = _comment_author(c)
            author_name = author_name or "there"
            author_key = (
                str(author_id).strip()
                if isinstance(author_id, str) and author_id.strip()
                else str(author_name).strip().lower()
            )
            prior_hashes_author = set(
                h
                for h in (
                    mb.get("sent_reply_hashes_by_author", {}).get(
                        author_key,
                        [],
                    )
                )
                if isinstance(h, str) and h
            )
            run_hashes_by_author.setdefault(author_key, set())

            comment_text = _comment_content(c)
            created_at = c.get("created_at") or c.get("createdAt")

            draft_path = draft_root / post_id / f"{cid}.md"
            draft_path.parent.mkdir(parents=True, exist_ok=True)

            ctx = CommentContext(
                post_id=post_id,
                comment_id=cid,
                author_name=author_name,
                comment_text=comment_text,
                created_at=str(created_at) if created_at else None,
            )
            reply_text = generate_reply_text(
                ctx,
                persona_text=persona_text,
                project_dir=project_dir,
            ).strip()

            # Enforce uniqueness automatically (per-post, per-author, global).
            h = reply_hash(reply_text)
            if (
                h in prior_hashes
                or h in run_hashes
                or h in prior_hashes_author
                or h in run_hashes_by_author[author_key]
                or h in prior_hashes_global
                or h in run_hashes_global
            ):
                # Try a few small variations without echoing comment text.
                suffixes = [
                    " Quick question: what outcome were you expecting?",
                    " Quick check: what platform/engine are you on?",
                    " If you can share your exact steps, I can verify it.",
                    " If you can share a minimal repro, I’ll chase it down.",
                    " If you can share what you expected vs what happened, I can fix it.",
                ]
                for attempt in range(1, 11):
                    suffix = suffixes[(attempt - 1) % len(suffixes)]
                    candidate = (reply_text + suffix).strip()
                    h2 = reply_hash(candidate)
                    if (
                        h2 not in prior_hashes
                        and h2 not in run_hashes
                        and h2 not in prior_hashes_author
                        and h2 not in run_hashes_by_author[author_key]
                        and h2 not in prior_hashes_global
                        and h2 not in run_hashes_global
                    ):
                        reply_text = candidate
                        h = h2
                        replies_duplicate_avoided += 1
                        break

            if (
                h in prior_hashes
                or h in run_hashes
                or h in prior_hashes_author
                or h in run_hashes_by_author[author_key]
                or h in prior_hashes_global
                or h in run_hashes_global
            ):
                # Last resort: add a tiny deterministic tag to guarantee
                # uniqueness without including any of the original comment.
                tag = reply_hash(cid)[:6]
                candidate = (reply_text + f" (ref {tag})").strip()
                h2 = reply_hash(candidate)
                if (
                    h2 not in prior_hashes
                    and h2 not in run_hashes
                    and h2 not in prior_hashes_author
                    and h2 not in run_hashes_by_author[author_key]
                    and h2 not in prior_hashes_global
                    and h2 not in run_hashes_global
                ):
                    reply_text = candidate
                    h = h2
                    replies_duplicate_avoided += 1

            if (
                h in prior_hashes
                or h in run_hashes
                or h in prior_hashes_author
                or h in run_hashes_by_author[author_key]
                or h in prior_hashes_global
                or h in run_hashes_global
            ):
                print(
                    "WARN: could not generate a unique reply for post "
                    f"{post_id} comment {cid}; skipping"
                )
                continue

            run_hashes.add(h)
            run_hashes_global.add(h)
            run_hashes_by_author[author_key].add(h)

            # Write audit file (text only, no metadata).
            try:
                draft_path.write_text(reply_text + "\n", encoding="utf-8")
            except OSError as e:
                print(f"WARN: failed to write reply file {draft_path}: {e}")
                continue

            entry["reply_files"][cid] = {
                "path": str(draft_path),
                "hash": h,
                "generated_at": _utc_now_iso(),
                "project_dir": project_dir,
            }
            reply_files_written += 1

            if args.dry_run:
                print(
                    f"DRY_RUN: would reply to comment {cid} "
                    f"on post {post_id} (file {draft_path})"
                )
                print(f"  -> {reply_text}")
                would_reply_count += 1
                continue

            if args.draft_only:
                # Audit-only mode.
                continue

            try:
                resp = client.create_comment(
                    post_id,
                    content=reply_text,
                    parent_id=cid,
                )
            except (requests.RequestException, RuntimeError) as e:
                msg = str(e)

                # Moltbook comment posting has occasionally behaved
                # inconsistently across deployments. If we see an auth failure,
                # retry once with a fresh session to rule out a bad client
                # state, then abort the run to avoid spamming.
                is_auth_failure = (
                    " 401 " in msg
                    or " 403 " in msg
                    or "Authentication required" in msg
                )
                if is_auth_failure:
                    try:
                        retry_client = MoltbookClient(
                            api_key=api_key,
                            timeout_s=timeout_s,
                            retries=retries,
                        )
                        resp = retry_client.create_comment(
                            post_id,
                            content=reply_text,
                            parent_id=cid,
                        )
                        client = retry_client
                    except (requests.RequestException, RuntimeError) as e2:
                        print(
                            "ERROR: Moltbook rejected comment posting "
                            "(auth failure). Your reply files were written, "
                            "but posting is currently blocked.\n"
                            f"First error: {msg}\n"
                            f"Retry error: {e2}",
                            file=sys.stderr,
                        )
                        return 1

                else:
                    print(f"WARN: failed to reply to comment {cid}: {msg}")
                    continue

            if isinstance(resp, dict) and resp.get("dry_run"):
                print("DRY_RUN - reply skipped")
                continue

            print(f"Replied to comment {cid} on post {post_id}")
            replies_sent += 1
            responded.add(cid)
            replied_this_post += 1
            prior_hashes.add(h)
            prior_hashes_global.add(h)
            prior_hashes_author.add(h)
            entry["sent_reply_hashes"] = sorted(prior_hashes)
            mb["sent_reply_hashes"] = sorted(prior_hashes_global)
            mb.setdefault("sent_reply_hashes_by_author", {})[author_key] = (
                sorted(prior_hashes_author)
            )
            entry["sent_replies"][cid] = {
                "hash": h,
                "path": str(draft_path),
                "sent_at": _utc_now_iso(),
            }

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

    if reply_files_written:
        print(f"Reply files written: {reply_files_written}")
    if replies_duplicate_avoided:
        print(f"Duplicates auto-avoided: {replies_duplicate_avoided}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
