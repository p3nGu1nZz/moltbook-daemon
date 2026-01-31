#!/usr/bin/env python3
"""Debug helper: inspect Moltbook post HTML for embedded JSON (comments, etc.).

This is intentionally a standalone helper so we can quickly adapt when the API
surface changes.

Usage:
  python tools/debug_post_page.py --post-id <POST_ID>

Notes:
- Moltbook post pages appear to live at: https://www.moltbook.com/post/<POST_ID>
- We do not require an API key for this.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any, Dict, Iterable, List, Tuple

import requests


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--post-id", required=True)
    p.add_argument("--timeout-s", type=int, default=60)
    return p.parse_args()


def _iter_paths(obj: Any, *, max_nodes: int = 20000) -> Iterable[Tuple[str, Any]]:
    # Breadth-first traversal with a node cap to avoid runaway.
    queue: List[Tuple[str, Any]] = [("$", obj)]
    seen = 0
    while queue and seen < max_nodes:
        path, cur = queue.pop(0)
        yield path, cur
        seen += 1

        if isinstance(cur, dict):
            for k, v in cur.items():
                queue.append((f"{path}.{k}", v))
        elif isinstance(cur, list):
            for i, v in enumerate(cur):
                queue.append((f"{path}[{i}]", v))


def main() -> int:
    args = _parse_args()
    url = f"https://www.moltbook.com/post/{args.post_id}"
    html = requests.get(url, timeout=args.timeout_s).text

    # Quick string probes
    for needle in ("api/v1", "comments", "_next/data", "__next_f", "self.__next_f"):
        print(f"contains {needle!r}: {needle in html}")

    # Next.js style (pages router)
    m = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL,
    )

    if not m:
        print("No __NEXT_DATA__ script tag found.")

        # Next.js app-router flight data: self.__next_f.push([...])
        pushes = re.findall(r"self\.__next_f\.push\((\[.*?\])\)", html, flags=re.DOTALL)
        print(f"flight pushes found: {len(pushes)}")
        parsed: List[Any] = []
        for i, raw in enumerate(pushes[:200]):
            try:
                parsed.append(json.loads(raw))
            except Exception:
                continue

        # Heuristics: search string chunks for interesting markers
        str_chunks: List[str] = []
        for item in parsed:
            if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], str):
                str_chunks.append(item[1])

        joined = "\n".join(str_chunks)
        print("flight string chunk bytes:", len(joined))
        for needle in ("comment", "comments", "parent_id", "author", "created_at"):
            print(f"flight contains {needle!r}:", needle in joined)

        # Show a few nearby snippets if present
        for needle in ("comment", "comments"):
            idx = joined.find(needle)
            if idx != -1:
                lo = max(0, idx - 200)
                hi = min(len(joined), idx + 200)
                print(f"--- snippet around {needle!r} ---")
                print(joined[lo:hi])
                break

        # Try to surface likely JSON endpoints from the HTML.
        api_hits = sorted(set(re.findall(r"https?://[^\s\"']+/api/v1/[^\s\"']+", html)))
        if api_hits:
            print("Found api/v1 URLs in HTML:")
            for u in api_hits[:20]:
                print("-", u)

        next_hits = sorted(set(re.findall(r"/_next/data/[^\s\"']+", html)))
        if next_hits:
            print("Found _next/data URLs in HTML:")
            for u in next_hits[:20]:
                print("-", u)

        # Print a small tail as well (sometimes scripts appear later)
        print("HTML head (first 800):")
        print(html[:800])
        print("HTML tail (last 800):")
        print(html[-800:])
        return 1

    raw = m.group(1)
    data = json.loads(raw)
    print("Found __NEXT_DATA__")
    print("Top-level keys:", sorted(list(data.keys())))

    # Look for comment-shaped things.
    hits = []
    for path, val in _iter_paths(data):
        if isinstance(val, dict) and "comments" in val:
            hits.append((path, val.get("comments")))

    print(f"comment-containing nodes: {len(hits)}")
    for path, val in hits[:10]:
        t = type(val).__name__
        size = len(val) if isinstance(val, (list, dict)) else None
        print(f"- {path}.comments -> {t} size={size}")

    # Also search for arrays of objects with id/content/author.
    shaped = []
    for path, val in _iter_paths(data):
        if isinstance(val, list) and val and all(isinstance(x, dict) for x in val[:3]):
            # Heuristic: looks like comments
            sample = val[0]
            if "content" in sample and ("author" in sample or "agent" in sample):
                shaped.append((path, len(val), sorted(list(sample.keys()))))

    print(f"comment-like arrays: {len(shaped)}")
    for path, n, keys in shaped[:10]:
        print(f"- {path} -> list[{n}] keys={keys[:12]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
