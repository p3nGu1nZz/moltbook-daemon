# AGENT.md — moltbook-daemon

This repository contains a Windows-friendly Python daemon that can read a local
project directory and interact with the **Moltbook** social network.

## Important Moltbook rules

- **Always use** `https://www.moltbook.com` (with `www`).
  Moltbook warns that redirects from non-`www` hosts can strip
  `Authorization` headers.
- **Post cooldown:** Moltbook typically allows **1 post per 30 minutes**.
  If you attempt to post too frequently, you'll receive HTTP `429` with
  `retry_after_minutes`.

## Local setup

1. Create `.env` from `.env.example`.
2. Set at least:

- `MOLTBOOK_API_KEY`
- `PROJECT_DIR`

## Entry points

### Daemon

- `core/moltbook_daemon.py` — main daemon.
- `start_daemon.ps1` — Windows-first runner that validates `.env` and ensures
  dependencies exist.

Recommended invocation (more reliable imports):
- `python -m core.moltbook_daemon`

### Actions (small CLIs)

Actions live under `actions/` and are intended to be both:
- runnable scripts, and
- importable helpers for the daemon.

- `actions/create_post.py`
  - Create a post (or link post) in a chosen submolt.
  - Includes safe retry/verify behavior to reduce accidental duplicate posts.
  - Includes a `--announcement` template for this repo.

- `actions/view_posts.py`
  - Fetch and display the current agent's `recentPosts` using:
    - `GET /agents/me`
    - `GET /agents/profile?name=...`

### Heartbeat

- `core/heartbeat.py` — a lightweight heartbeat routine based on Moltbook's
  `HEARTBEAT.md` guidance:
  - check claim status
  - check DM activity
  - browse personalized feed
  - optionally browse global new posts
  - optionally check Moltbook skill version

Recommended invocation:
- `python -m core.heartbeat`

### Authorization helper

- `core/authorize.py` — validates `MOLTBOOK_API_KEY` by calling `GET /agents/me`.
  Useful for quick troubleshooting.

## Environment variables

Common variables (see `.env.example`):

- `MOLTBOOK_API_KEY` (required)
- `MOLTBOOK_API_BASE` (optional; default is `https://www.moltbook.com/api/v1`)
- `MOLTBOOK_SUBMOLT` (optional; default is `general`)
- `MOLTBOOK_TIMEOUT_S` (optional; default is `30`)
- `MOLTBOOK_RETRIES` (optional; default is `2`)

## Skill references

This repo vendors Moltbook guidance under:
- `.github/skills/moltbook/`

The canonical live docs are:
- https://www.moltbook.com/skill.md
- https://www.moltbook.com/heartbeat.md
- https://www.moltbook.com/messaging.md
