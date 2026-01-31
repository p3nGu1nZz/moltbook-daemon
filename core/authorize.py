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
- MOLTBOOK_TIMEOUT_S (optional, default: 300)
- MOLTBOOK_API_BASE (optional, default: https://www.moltbook.com/api/v1)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

from core.moltbook_client import MoltbookClient


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "projects": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "projects": {}}


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


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
        "--attempts",
        type=int,
        default=10,
        help="How many times to try before giving up (default: 10)",
    )
    p.add_argument(
        "--sleep-s",
        type=float,
        default=5.0,
        help="Seconds to sleep between attempts (default: 5)",
    )
    p.add_argument(
        "--no-proxy",
        action="store_true",
        help=(
            "Ignore proxy env vars for requests "
            "(sets session.trust_env=false)"
        ),
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
    p.add_argument(
        "--state-file",
        default=None,
        help=(
            "State JSON path to update (default: env STATE_FILE or "
            ".moltbook_daemon_state.json in repo root)"
        ),
    )
    return p.parse_args(argv)


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

    retries = (
        int(args.retries)
        if args.retries is not None
        else int(os.getenv("MOLTBOOK_RETRIES", "2"))
    )

    client = MoltbookClient(api_key, timeout_s=timeout_s, retries=retries)
    if args.no_proxy:
        client.session.trust_env = False

    attempts = max(1, int(args.attempts))
    sleep_s = max(0.0, float(args.sleep_s))

    me: Dict[str, Any] | None = None
    last_err: BaseException | None = None

    for attempt in range(1, attempts + 1):
        attempt_msg = (
            f"Authorize attempt {attempt}/{attempts} "
            f"(timeout={timeout_s}s retries={retries} "
            f"no_proxy={bool(args.no_proxy)})..."
        )
        print(
            attempt_msg,
            file=sys.stderr,
            flush=True,
        )
        try:
            me = client.get_me()
            last_err = None
            break
        except (requests.RequestException, RuntimeError) as e:
            last_err = e

            # If credentials are actually invalid, don't spin forever.
            msg = str(e)
            if " 401 " in msg or " 403 " in msg:
                print(f"ERROR: authorization failed: {e}", file=sys.stderr)
                return 1

            if attempt < attempts:
                warn_msg = (
                    f"WARN: authorize attempt {attempt}/{attempts} "
                    f"failed: {e}"
                )
                print(
                    warn_msg,
                    file=sys.stderr,
                )
                if sleep_s > 0:
                    time.sleep(sleep_s)
                continue

    if me is None:
        print(f"ERROR: authorization failed: {last_err}", file=sys.stderr)
        return 1

    agent = (me.get("agent") or {}) if isinstance(me, dict) else {}
    name = (agent.get("name") or "").strip()
    agent_id = agent.get("id")

    if args.json:
        print(
            json.dumps(
                {"me": me},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )
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

    # Persist agent identity + a short-lived identity token into state.
    state_path = Path(
        args.state_file
        or os.getenv("STATE_FILE")
        or (
            Path(__file__).resolve().parents[1]
            / ".moltbook_daemon_state.json"
        )
    )

    state = _load_state(state_path)
    mb = state.setdefault("moltbook", {})
    mb["agent"] = {"id": agent_id, "name": name or None}
    mb["last_authorize_at"] = _utc_now_iso()

    # The identity-token flow is primarily for third-party services verifying
    # an agent. We still store it here because it's short-lived and useful for
    # integrations.
    try:
        token_resp = client.create_identity_token()
        identity_token = token_resp.get("identity_token")
        mb["identity"] = {
            "identity_token": identity_token,
            "expires_in": token_resp.get("expires_in"),
            "expires_at": token_resp.get("expires_at"),
            "fetched_at": _utc_now_iso(),
        }
    except (requests.RequestException, RuntimeError) as e:
        mb["identity"] = {
            "error": str(e),
            "fetched_at": _utc_now_iso(),
        }

    _save_state(state_path, state)
    if not args.json:
        print(f"State updated: {state_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
