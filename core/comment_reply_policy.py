#!/usr/bin/env python3
"""Generate unique, tone-aware comment replies.

Design goals:
- Every reply should be unique *per comment* and reflect what the commenter
    said.
- Avoid quoting untrusted comment text back verbatim (it may be unsafe).
- Replies are generated automatically by the agent (no human editing).
- We still write the reply text to a markdown file for auditability.

This module intentionally stays dependency-light (stdlib only).
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CommentContext:
    post_id: str
    comment_id: str
    author_name: str
    comment_text: str
    created_at: Optional[str] = None


_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{2,}")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _stable_pick(options: Sequence[str], seed: str) -> str:
    if not options:
        return ""
    h = hashlib.sha256((seed or "").encode("utf-8")).hexdigest()
    idx = int(h[:8], 16) % len(options)
    return str(options[idx])


def normalize_for_hash(text: str) -> str:
    """Normalize reply text for dedupe hashing."""
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def reply_hash(text: str) -> str:
    """Stable SHA256 hash for a reply body (normalized)."""
    n = normalize_for_hash(text)
    return hashlib.sha256(n.encode("utf-8")).hexdigest()


def redact_urls(text: str) -> str:
    return _URL_RE.sub("(link omitted)", text or "")


def extract_keywords(text: str, *, max_words: int = 10) -> List[str]:
    """Extract a small keyword set to guide drafting.

    This is not NLP; it’s a simple heuristic so we can:
    - avoid echoing the full comment
    - still tailor the reply to the topic
    """

    if not text:
        return []

    cleaned = redact_urls(text)
    words = [w.lower() for w in _WORD_RE.findall(cleaned)]

    # tiny stoplist to avoid noise
    stop = {
        "this",
        "that",
        "with",
        "from",
        "have",
        "your",
        "youre",
        "about",
        "what",
        "when",
        "where",
        "which",
        "would",
        "could",
        "should",
        "thanks",
        "thank",
        "nice",
        "cool",
        "good",
        "great",
        "bad",
        "lol",
        "lmao",
    }

    # Safety: don't echo common insults/profanity back as a "topic".
    banned = {
        "stupid",
        "idiot",
        "trash",
        "garbage",
        "worst",
        "hate",
        "dumb",
        "shut",
        "kys",
    }

    uniq: List[str] = []
    for w in words:
        if w in stop:
            continue
        if w in banned:
            continue
        if w.isdigit():
            continue
        if w in uniq:
            continue
        uniq.append(w)
        if len(uniq) >= max_words:
            break
    return uniq


def classify_intent(text: str) -> str:
    """Classify what kind of comment this is (very rough)."""

    t = (text or "").lower()

    if not t.strip():
        return "empty"

    # question-ish
    if "?" in t or any(
        x in t
        for x in [
            "how do",
            "how to",
            "what is",
            "why",
            "where",
            "help",
        ]
    ):
        return "question"

    if any(
        x in t
        for x in [
            "error",
            "exception",
            "crash",
            "broken",
            "doesn't work",
            "doesnt work",
            "bug",
        ]
    ):
        return "bug"

    # praise-ish
    if any(
        x in t
        for x in [
            "love",
            "awesome",
            "great",
            "cool",
            "nice",
            "sick",
            "amazing",
        ]
    ):
        return "praise"

    # hostile-ish (not exhaustive; just to steer tone)
    if any(
        x in t
        for x in ["stupid", "idiot", "trash", "garbage", "worst", "hate"]
    ):
        return "hostile"

    # feedback-ish
    if any(
        x in t
        for x in ["suggest", "maybe", "consider", "would be nice", "feature"]
    ):
        return "feedback"

    return "neutral"


def choose_tone(intent: str) -> str:
    """Pick a tone label used in draft metadata."""

    if intent in {"hostile"}:
        return "calm"
    if intent in {"bug"}:
        return "helpful"
    if intent in {"question"}:
        return "helpful"
    if intent in {"praise"}:
        return "warm"
    if intent in {"feedback"}:
        return "builder"
    if intent in {"empty"}:
        return "neutral"
    return "neutral"


def load_persona(persona_path: Path) -> str:
    try:
        return persona_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _candidate_files(root: Path) -> List[Path]:
    """Pick a prioritized set of files to scan first."""

    if not root.exists():
        return []

    candidates: List[Path] = []
    for name in [
        "README.md",
        "README.MD",
        "readme.md",
        "CHANGELOG.md",
        "docs/README.md",
        "docs/readme.md",
    ]:
        p = root / name
        if p.exists() and p.is_file():
            candidates.append(p)

    return candidates


def search_project_dir(
    project_dir: Optional[str],
    query_terms: Sequence[str],
    *,
    max_files: int = 80,
    max_hits: int = 8,
) -> List[Tuple[str, int, str]]:
    """Search a local project directory for lines matching query_terms.

    Returns a list of (relative_path, line_number, line_text) hits.

    NOTE: This runs on the user's machine at runtime. It may point outside this
    repo (e.g. PROJECT_DIR=C:\\Users\\...\\CatGame).
    """

    if not project_dir:
        return []

    root = Path(project_dir).expanduser()
    if not root.exists() or not root.is_dir():
        return []

    terms = [t.lower() for t in query_terms if t and str(t).strip()]
    if not terms:
        return []

    # First scan a few high-signal files.
    files: List[Path] = _candidate_files(root)

    # Then walk a limited subset.
    allowed_ext = {
        ".md",
        ".txt",
        ".py",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".cs",
        ".ts",
        ".js",
        ".lua",
    }
    ignored_dirs = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "Library",
        "Temp",
        "Build",
        "Logs",
        "obj",
        "bin",
    }

    if len(files) < max_files:
        for p in root.rglob("*"):
            if len(files) >= max_files:
                break
            if p.is_dir():
                # Skip ignored dirs early.
                if p.name in ignored_dirs:
                    continue
                continue
            if p.suffix.lower() not in allowed_ext:
                continue
            # Avoid huge files.
            try:
                if p.stat().st_size > 300_000:
                    continue
            except OSError:
                continue

            # Don't add duplicates.
            if p in files:
                continue
            files.append(p)

    hits: List[Tuple[str, int, str]] = []

    for fp in files:
        try:
            rel = str(fp.relative_to(root))
        except ValueError:
            rel = str(fp)

        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for i, line in enumerate(text.splitlines(), start=1):
            line_l = line.lower()
            if all(t in line_l for t in terms):
                snippet = line.strip()
                if snippet:
                    hits.append((rel, i, snippet))
                if len(hits) >= max_hits:
                    return hits

    return hits


def _project_name(project_dir: Optional[str]) -> str:
    if not project_dir:
        return "CatGame"
    try:
        p = Path(project_dir).expanduser()
        return p.name or "CatGame"
    except OSError:
        return "CatGame"


def _reference_hint(
    *,
    intent: str,
    project_dir: Optional[str],
    keywords: List[str],
) -> str:
    """Optional short hint grounded in repo content.

    We avoid quoting arbitrary lines; we only mention filenames as pointers.
    """

    if intent not in {"question", "bug", "feedback"}:
        return ""
    if not keywords:
        return ""

    hits = search_project_dir(project_dir, keywords[:2])
    if not hits:
        return ""

    files: List[Tuple[str, int]] = []
    for rel, ln, _snippet in hits:
        if any(rel == f for f, _ in files):
            continue
        files.append((rel, ln))
        if len(files) >= 2:
            break

    if not files:
        return ""

    if len(files) == 1:
        rel, ln = files[0]
        return f"(I found related bits in `{rel}` around line {ln}.)"

    (rel1, ln1), (rel2, ln2) = files[0], files[1]
    return (
        f"(I found related bits in `{rel1}` around line {ln1} "
        f"and `{rel2}` around line {ln2}.)"
    )


def _persona_allows_dry_humor(persona_text: str) -> bool:
    return "dry humor" in (persona_text or "").lower()


def generate_reply_text(
    ctx: CommentContext,
    *,
    persona_text: str,
    project_dir: Optional[str],
) -> str:
    """Generate a reply string.

    Note: The returned text is safe-ish by construction (no quoting the
    original comment). It still may require rate limiting and dedupe checks.
    """

    allow_dry_humor = _persona_allows_dry_humor(persona_text)

    project = _project_name(project_dir)
    intent = classify_intent(ctx.comment_text)
    tone = choose_tone(intent)
    keywords = extract_keywords(ctx.comment_text, max_words=8)

    seed = f"{ctx.comment_text}\n{ctx.author_name}\n{ctx.comment_id}"
    topic = keywords[0] if keywords else project
    hint = _reference_hint(
        intent=intent,
        project_dir=project_dir,
        keywords=keywords,
    )

    humor = ""
    if allow_dry_humor:
        # Deterministically include a small, non-snarky aside sometimes.
        if int(reply_hash(seed)[:2], 16) % 5 == 0:
            humor = _stable_pick(
                [
                    "I’ll poke at it and report back.",
                    "I’ll go spelunking in the repo for this.",
                    "I’ll sanity-check it end-to-end.",
                ],
                seed + "/humor",
            )

    openers = {
        "warm": [
            f"Thanks {ctx.author_name} — appreciate you checking out "
            f"{project}.",
            (
                f"Hey {ctx.author_name}, glad you’re following along with "
                f"{project}."
            ),
        ],
        "helpful": [
            f"Good callout, {ctx.author_name}.",
            f"Thanks {ctx.author_name} — that’s a solid question.",
        ],
        "builder": [
            f"Fair feedback, {ctx.author_name}.",
            f"That’s helpful input, {ctx.author_name}.",
        ],
        "calm": [
            f"I hear you, {ctx.author_name}.",
            f"Got it, {ctx.author_name}.",
        ],
        "neutral": [
            f"Thanks {ctx.author_name}.",
            f"Appreciate it, {ctx.author_name}.",
        ],
    }

    opener = _stable_pick(openers.get(tone, openers["neutral"]), seed)

    if intent == "praise":
        mid = _stable_pick(
            [
                "I’m iterating quickly and sharing progress as things land.",
                "I’m polishing the rough edges and posting updates as I go.",
            ],
            seed + "/mid",
        )
        ask = _stable_pick(
            [
                (
                    f"If there’s a feature you want next around `{topic}`, "
                    "tell me."
                ),
                "If you’ve got a wishlist item, toss it my way.",
            ],
            seed + "/ask",
        )
        parts = [opener, mid, ask, humor]
        if hint:
            parts.append(hint)
        return " ".join(p for p in parts if p).strip()

    if intent == "question":
        mid = _stable_pick(
            [
                (
                    f"On `{topic}`: I’ll double-check the repo and share the "
                    "precise steps/entry point."
                ),
                (
                    f"Re `{topic}`: I’ll double-check the latest CatGame "
                    "code/docs and answer precisely."
                ),
            ],
            seed + "/mid",
        )
        ask = _stable_pick(
            [
                "What’s the end goal you’re trying to achieve?",
                "What platform/tooling are you using (Unity/Godot/other)?",
            ],
            seed + "/ask",
        )
        parts = [opener, mid, ask, humor]
        if hint:
            parts.append(hint)
        return " ".join(p for p in parts if p).strip()

    if intent == "bug":
        mid = _stable_pick(
            [
                f"If you can share the minimal repro steps around "
                f"`{topic}`, I can chase it down.",
                "If you can share the exact steps + environment, I can reproduce and patch it.",
            ],
            seed + "/mid",
        )
        ask = _stable_pick(
            [
                "What were you doing right before it happened?",
                "Any error text (sanitized) or screenshot of the symptom?",
            ],
            seed + "/ask",
        )
        parts = [opener, mid, ask, humor]
        if hint:
            parts.append(hint)
        return " ".join(p for p in parts if p).strip()

    if intent == "feedback":
        mid = _stable_pick(
            [
                f"I can see why `{topic}` could feel rough right now.",
                "That’s a reasonable ask — the current behavior is a bit raw.",
            ],
            seed + "/mid",
        )
        ask = _stable_pick(
            [
                "If you tell me what ‘good’ looks like for your workflow, I’ll aim for that.",
                "If you have a preferred UX, describe it and I’ll match it.",
            ],
            seed + "/ask",
        )
        parts = [opener, mid, ask, humor]
        if hint:
            parts.append(hint)
        return " ".join(p for p in parts if p).strip()

    if intent == "hostile":
        mid = _stable_pick(
            [
                "If you’ve got specific technical feedback, I’m happy to "
                "address it.",
                "If you can make it concrete (what failed / what you expected), I can fix it.",
            ],
            seed + "/mid",
        )
        close = _stable_pick(
            [
                f"Either way, I’m going to keep building {project}.",
                "I’m going to keep iterating and posting updates.",
            ],
            seed + "/close",
        )
        return " ".join([opener, mid, close]).strip()

    if intent == "empty":
        ask = _stable_pick(
            [
                "What were you curious about?",
                "What part should I clarify — gameplay, tech, or roadmap?",
            ],
            seed + "/ask",
        )
        return " ".join([opener, ask]).strip()

    # neutral
    mid = _stable_pick(
        [
            f"If you meant `{topic}` specifically, tell me what you’re "
            "aiming for and I’ll respond with details.",
            "If there’s a specific edge case you care about, I’ll prioritize it.",
        ],
        seed + "/mid",
    )
    parts = [opener, mid, humor]
    if hint:
        parts.append(hint)
    return " ".join(p for p in parts if p).strip()


def get_project_dir_from_env() -> Optional[str]:
    v = os.getenv("PROJECT_DIR")
    if not v:
        return None
    return str(v).strip() or None


__all__ = [
    "CommentContext",
    "normalize_for_hash",
    "reply_hash",
    "extract_keywords",
    "classify_intent",
    "choose_tone",
    "load_persona",
    "search_project_dir",
    "generate_reply_text",
    "get_project_dir_from_env",
]
