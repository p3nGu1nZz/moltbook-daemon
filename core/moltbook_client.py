#!/usr/bin/env python3
"""Moltbook API client.

This module intentionally contains only Moltbook API communication logic so it
can be reused by the daemon and standalone scripts under `actions/`.

Important:
- Always use https://www.moltbook.com (with `www`). Moltbook warns that
  redirects from non-www hosts can strip Authorization headers.
- Moltbook enforces a post cooldown (currently 1 post per 30 minutes).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import requests


logger = logging.getLogger("moltbook-daemon")


class MoltbookClient:
    """Client for interacting with the Moltbook API."""

    def __init__(
        self,
        api_key: str,
        api_base: Optional[str] = None,
        timeout_s: int = 300,
        dry_run: bool = False,
        retries: int = 2,
        retry_backoff_s: float = 1.0,
    ):
        """Initialize the Moltbook client.

        Args:
            api_key: API key for Moltbook authentication
            api_base: Base URL for the Moltbook API
            timeout_s: Default request timeout in seconds
            dry_run: If True, do not perform write operations
                (POST/PATCH/PUT/DELETE)
            retries: Number of retries for idempotent requests (GET/HEAD)
            retry_backoff_s: Base backoff in seconds between retries
        """
        self.api_key = api_key
        self.api_base = (
            api_base
            or os.getenv("MOLTBOOK_API_BASE")
            or "https://www.moltbook.com/api/v1"
        ).rstrip("/")
        self.timeout_s = timeout_s
        self.dry_run = dry_run
        self.retries = max(0, int(retries))
        self.retry_backoff_s = float(retry_backoff_s)

        # Moltbook explicitly warns that using the non-www host can redirect and
        # strip Authorization headers. Keep users out of that foot-gun.
        if not self.api_base.startswith("https://www.moltbook.com"):
            logger.warning(
                "MOLTBOOK_API_BASE should start with https://www.moltbook.com "
                "to avoid redirects stripping Authorization headers. Current: %s",
                self.api_base,
            )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Internal request helper.

        Moltbook warns that redirects can strip the Authorization header. We
        disable redirects to fail fast with a clear message instead of silently
        making unauthenticated calls.
        """
        method_u = method.upper()
        if self.dry_run and method_u in {"POST", "PUT", "PATCH", "DELETE"}:
            url = f"{self.api_base}/{path.lstrip('/')}"
            logger.info("DRY_RUN - skipping %s %s", method_u, url)
            return {
                "success": True,
                "dry_run": True,
                "skipped": True,
                "method": method_u,
                "path": path,
            }

        url = f"{self.api_base}/{path.lstrip('/')}"
        kwargs.setdefault("timeout", self.timeout_s)
        kwargs.setdefault("allow_redirects", False)

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.session.request(method_u, url, **kwargs)
                break
            except requests.RequestException as e:
                can_retry = (
                    method_u in {"GET", "HEAD"}
                    and attempt < (1 + self.retries)
                )
                if can_retry:
                    sleep_s = self.retry_backoff_s * (2 ** (attempt - 1))
                    logger.warning(
                        "Request failed (%s %s) attempt %s/%s: %s; retrying in %.1fs",
                        method_u,
                        url,
                        attempt,
                        (1 + self.retries),
                        e,
                        sleep_s,
                    )
                    time.sleep(sleep_s)
                    continue

                logger.error("Request failed (%s %s): %s", method_u, url, e)
                raise

        if response.is_redirect:
            location = response.headers.get("Location")
            raise RuntimeError(
                "Moltbook API request was redirected (likely non-www host). "
                "Redirects can strip Authorization headers; refusing to follow. "
                f"URL={url} Location={location}"
            )

        data: Optional[Dict[str, Any]]
        try:
            data = response.json()
        except ValueError:
            data = None

        if response.status_code == 429:
            retry_after_minutes = None
            if isinstance(data, dict):
                retry_after_minutes = data.get("retry_after_minutes")
            msg = f"Rate limited (429) calling {method_u} {url}"
            if retry_after_minutes is not None:
                msg += f"; retry_after_minutes={retry_after_minutes}"
            logger.warning(msg)

        if not response.ok:
            err = None
            if isinstance(data, dict):
                err = data.get("error") or data.get("message")
            raise RuntimeError(
                f"Moltbook API error {response.status_code} for {method_u} {url}: "
                f"{err or response.text}"
            )

        if isinstance(data, dict):
            return data
        return {"success": True, "data": data}

    def get_me(self) -> Dict[str, Any]:
        """Get the current agent's profile (authenticated)."""
        return self._request("GET", "/agents/me")

    def get_profile(self, name: str) -> Dict[str, Any]:
        """Get a public agent profile and recent posts by agent name."""
        return self._request("GET", "/agents/profile", params={"name": name})

    def test_connection(self) -> bool:
        """Test the connection to the Moltbook API."""
        try:
            self._request("GET", "/agents/me")
            return True
        except (requests.RequestException, RuntimeError) as e:
            logger.error("Connection test failed: %s", e)
            return False

    def get_agent_status(self) -> Dict[str, Any]:
        """Check claim status."""
        return self._request("GET", "/agents/status")

    def get_feed(self, sort: str = "new", limit: int = 15) -> Dict[str, Any]:
        """Get personalized feed (subscribed submolts + followed agents)."""
        params = {"sort": sort, "limit": limit}
        return self._request("GET", "/feed", params=params)

    def list_posts(
        self,
        sort: str = "new",
        limit: int = 15,
        submolt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List posts globally or for a specific submolt."""
        params: Dict[str, Any] = {"sort": sort, "limit": limit}
        if submolt:
            params["submolt"] = submolt
        return self._request("GET", "/posts", params=params)

    def create_post(
        self,
        submolt: str,
        title: str,
        content: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a post."""
        payload: Dict[str, Any] = {"submolt": submolt, "title": title}
        if content is not None:
            payload["content"] = content
        if url is not None:
            payload["url"] = url
        return self._request("POST", "/posts", json=payload)

    def dm_check(self) -> Dict[str, Any]:
        """Quick poll for DM activity (for heartbeat)."""
        return self._request("GET", "/agents/dm/check")

    def post_message(self, message: str) -> Optional[Dict[str, Any]]:
        """Backwards-compatible helper that posts to m/general."""
        try:
            title = f"Update from {time.strftime('%Y-%m-%d %H:%M')}"
            resp = self.create_post(
                submolt="general",
                title=title,
                content=message,
            )
            logger.info("Posted message successfully")
            return resp
        except (requests.RequestException, RuntimeError) as e:
            logger.error("Failed to post message: %s", e)
            return None


__all__ = ["MoltbookClient"]
