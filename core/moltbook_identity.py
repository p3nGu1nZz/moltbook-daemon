#!/usr/bin/env python3
"""Helpers for "Sign in with Moltbook" (identity token verification).

This module is meant for *your app/server*, not for the daemon itself.

Moltbook identity flow (per https://moltbook.com/developers.md):
- Bots generate a short-lived identity token (JWT-like) using their Moltbook
    API key.
- Your service verifies that token using Moltbook's open verify endpoint.

Key property: verify is **free + no API key required**.

You can use this module in any Python web framework by:
1) extracting the `X-Moltbook-Identity` header (case-insensitive)
2) calling `verify_identity_token()`
3) attaching the returned agent dict to your request context

We intentionally keep this dependency-light (requests only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

import requests


DEFAULT_IDENTITY_HEADER = "X-Moltbook-Identity"
DEFAULT_VERIFY_URL = "https://moltbook.com/api/v1/agents/verify-identity"


@dataclass
class MoltbookIdentityError(RuntimeError):
    """Raised when identity verification fails."""

    error: str
    status_code: int = 401
    hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"error": self.error}
        if self.hint:
            data["hint"] = self.hint
        return data


def _get_header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Case-insensitive header lookup for plain dict-like headers."""

    name_l = name.lower()
    for k, v in headers.items():
        if k.lower() == name_l:
            return v
    return None


def extract_identity_token(
    headers: Mapping[str, str],
    *,
    header_name: str = DEFAULT_IDENTITY_HEADER,
) -> Optional[str]:
    """Extract the Moltbook identity token from request headers.

    Returns the raw token string or None if missing/empty.
    """

    token = _get_header(headers, header_name)
    if token is None:
        return None

    token_s = str(token).strip()
    return token_s or None


def verify_identity_token(
    token: str,
    *,
    timeout_s: int = 30,
    verify_url: str = DEFAULT_VERIFY_URL,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """Verify a Moltbook identity token and return the verified agent profile.

    Expected response (developers.md):
    - valid: true/false
    - agent: { id, name, karma, avatar_url, is_claimed,
               owner: { x_handle, x_verified }, ... }
    - error: "identity_token_expired" | "invalid_token" | ...

    Raises:
        MoltbookIdentityError: if invalid/expired/etc.
        requests.RequestException: on network problems.
    """

    if not token or not str(token).strip():
        raise MoltbookIdentityError(
            error="missing_identity_token",
            status_code=401,
        )

    sess = session or requests.Session()
    resp = sess.post(
        verify_url,
        json={"token": token},
        headers={"Content-Type": "application/json"},
        timeout=timeout_s,
        allow_redirects=True,
    )

    data: Any
    try:
        data = resp.json()
    except ValueError:
        data = None

    # Some failures are represented by status codes (table in developers.md).
    if not resp.ok:
        err = None
        hint = None
        if isinstance(data, dict):
            err = data.get("error") or data.get("message")
            hint = data.get("hint")
        raise MoltbookIdentityError(
            error=str(err or "verify_identity_failed"),
            status_code=int(resp.status_code),
            hint=str(hint) if hint else None,
        )

    if not isinstance(data, dict):
        raise MoltbookIdentityError(
            error="unexpected_verify_response",
            status_code=500,
        )

    if not data.get("valid"):
        # Developers.md suggests 401 for invalid/expired.
        err = data.get("error") or "invalid_token"
        hint = data.get("hint")
        raise MoltbookIdentityError(
            error=str(err),
            status_code=401,
            hint=str(hint) if hint else None,
        )

    agent = data.get("agent")
    if not isinstance(agent, dict):
        raise MoltbookIdentityError(
            error="missing_agent_profile",
            status_code=500,
        )

    return agent


def authenticate_headers(
    headers: Mapping[str, str],
    *,
    header_name: str = DEFAULT_IDENTITY_HEADER,
    timeout_s: int = 30,
    verify_url: str = DEFAULT_VERIFY_URL,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """Convenience: extract + verify, returning agent dict.

    This is the simplest building block to use in any framework.

    Example (pseudo):
        agent = authenticate_headers(request.headers)
        request.state.moltbook_agent = agent
    """

    token = extract_identity_token(headers, header_name=header_name)
    if not token:
        raise MoltbookIdentityError(
            error="missing_identity_token",
            status_code=401,
        )

    return verify_identity_token(
        token,
        timeout_s=timeout_s,
        verify_url=verify_url,
        session=session,
    )


def attach_agent_to_request(
    request: Any,
    agent: Dict[str, Any],
    *,
    attribute: str = "moltbook_agent",
) -> None:
    """Attach the verified agent profile onto a request context object.

    This is framework-agnostic, but supports common patterns:
    - Starlette/FastAPI: request.state.<attribute>
    - Anything else: request.<attribute>
    """

    state = getattr(request, "state", None)
    if state is not None:
        try:
            setattr(state, attribute, agent)
            return
        except (AttributeError, TypeError):
            # Fall back to setting it directly on the request.
            pass

    setattr(request, attribute, agent)


def get_agent_from_request(
    request: Any,
    *,
    attribute: str = "moltbook_agent",
) -> Optional[Dict[str, Any]]:
    """Read a previously-attached verified agent profile from a request."""

    state = getattr(request, "state", None)
    if state is not None and hasattr(state, attribute):
        try:
            agent = getattr(state, attribute)
            return agent if isinstance(agent, dict) else None
        except (AttributeError, TypeError):
            return None

    if hasattr(request, attribute):
        try:
            agent = getattr(request, attribute)
            return agent if isinstance(agent, dict) else None
        except (AttributeError, TypeError):
            return None

    return None


__all__ = [
    "DEFAULT_IDENTITY_HEADER",
    "DEFAULT_VERIFY_URL",
    "MoltbookIdentityError",
    "extract_identity_token",
    "verify_identity_token",
    "authenticate_headers",
    "attach_agent_to_request",
    "get_agent_from_request",
]
