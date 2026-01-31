#!/usr/bin/env python3
"""Probe Moltbook endpoints for listing post comments.

Moltbook's deployed API has (at times) returned 405 for the documented:
  GET /api/v1/posts/{post_id}/comments

This helper tries a handful of plausible alternatives and prints a compact
report (status, redirect, allow header, content-type, and a short body preview)
for each.

Usage:
  python -m tools.probe_comments_endpoint --post-id <POST_ID>

Optional:
  - Provide MOLTBOOK_API_KEY to also probe with Authorization header.
  - Provide STATE_FILE (or default state file) to also probe with
    X-Moltbook-Identity if present.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Probe Moltbook endpoints for listing comments"
    )
    p.add_argument("--post-id", required=True, help="Post id to probe")
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
    return p.parse_args(argv)


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "projects": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "projects": {}}


def _preview_text(r: requests.Response, max_chars: int = 200) -> str:
    text = r.text
    text = " ".join(text.split())
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def _probe(
    *,
    session: requests.Session,
    method: str,
    url: str,
    timeout_s: int,
    headers: Dict[str, str],
) -> Tuple[int, str, str, str, str]:
    r = session.request(
        method,
        url,
        timeout=timeout_s,
        allow_redirects=False,
        headers=headers,
    )
    loc = r.headers.get("Location") or ""
    allow = r.headers.get("Allow") or ""
    ct = r.headers.get("Content-Type") or ""
    prev = _preview_text(r)
    redir = f"redirect->{loc}" if r.is_redirect else ""
    return r.status_code, redir, allow, ct, prev


def main(argv: List[str]) -> int:
    load_dotenv()

    args = _parse_args(argv)
    post_id = str(args.post_id)

    timeout_s = (
        int(args.timeout_s)
        if args.timeout_s is not None
        else int(os.getenv("MOLTBOOK_TIMEOUT_S", "30"))
    )

    api_key = os.getenv("MOLTBOOK_API_KEY")

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
    identity_token = None
    if isinstance(mb, dict):
        identity = mb.get("identity")
        if isinstance(identity, dict):
            tok = identity.get("identity_token")
            if isinstance(tok, str) and tok.strip():
                identity_token = tok.strip()

    header_sets: List[Tuple[str, Dict[str, str]]] = [("public", {})]
    if api_key:
        header_sets.append(("api_key", {"Authorization": f"Bearer {api_key}"}))
    if identity_token:
        header_sets.append(
            ("identity", {"X-Moltbook-Identity": identity_token})
        )
    if api_key and identity_token:
        header_sets.append(
            (
                "api_key+identity",
                {
                    "Authorization": f"Bearer {api_key}",
                    "X-Moltbook-Identity": identity_token,
                },
            )
        )

    bases = [
        "https://www.moltbook.com/api/v1",
        "https://moltbook.com/api/v1",
        "https://www.moltbook.com/api",
    ]

    paths = [
        f"/posts/{post_id}/comments",
        f"/posts/{post_id}/comments/",
        f"/posts/{post_id}/comments/list",
        f"/posts/{post_id}/comments/all",
        f"/comments?post_id={post_id}",
        f"/comments?postId={post_id}",
        f"/posts/{post_id}?include=comments",
        f"/posts/{post_id}?with_comments=true",
        f"/posts/{post_id}?includeComments=true",
    ]

    methods = ["GET", "OPTIONS", "HEAD"]

    session = requests.Session()

    print(f"timeout_s={timeout_s} state_file={state_path}")
    print(
        f"have_api_key={bool(api_key)} "
        f"have_identity_token={bool(identity_token)}"
    )

    for label, headers in header_sets:
        print("=")
        print(f"Headers: {label}")
        for base in bases:
            for path in paths:
                url = base.rstrip("/") + path
                for method in methods:
                    try:
                        code, redir, allow, ct, prev = _probe(
                            session=session,
                            method=method,
                            url=url,
                            timeout_s=timeout_s,
                            headers=headers,
                        )
                    except requests.RequestException as e:
                        print(f"{method:7} {url} -> ERR {e}")
                        continue

                    extras = " ".join(
                        x
                        for x in [
                            redir,
                            f"Allow={allow}" if allow else "",
                            ct,
                        ]
                        if x
                    )
                    print(f"{method:7} {url} -> {code} {extras} :: {prev}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
