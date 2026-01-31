# Comment replies workflow

This folder holds **per-comment** reply drafts as Markdown files.

## Why

- Replies should be **unique** per commenter and reflect what they said.
- We keep a local record for auditability and debugging.

## Structure

Drafts are stored under:

- `comments/replies/<post_id>/<comment_id>.md`

## Draft format

Each file contains **only the reply text**.

No YAML, no frontmatter, no metadata.

## Typical flow

1) Sync comments (optional but recommended):
- `python -m actions.get_comments --all`

2) Reply (fully automated):
- `python -m actions.comment_post --all`

This will:
- generate a tone-matched, unique reply per pending top-level comment
- write the reply text to `comments/replies/<post_id>/<comment_id>.md`
- post the reply immediately (unless you opt into a non-posting mode)

Non-posting modes:
- `--dry-run` (prints what it would send)
- `--draft-only` (writes reply files but does not post)

## Safety notes

- Don’t quote or copy/paste abusive content into replies.
- Avoid sharing secrets, local paths, or personal data.
- Keep replies short and factual; link to a public doc/repo when possible.
