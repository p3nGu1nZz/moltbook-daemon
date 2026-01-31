#!/usr/bin/env python3
"""
Moltbook Daemon - A daemon application for interacting with the Moltbook social network.

This daemon continuously monitors and interacts with the Moltbook API, using content
from a specified project directory as source material.
"""

import os
import sys
import time
import logging
import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from core.moltbook_client import MoltbookClient
from actions.create_post import create_post as action_create_post


# Best-effort: prefer UTF-8 on Windows consoles to avoid crashes when Moltbook
# responses include emoji (e.g. 🦞).
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('moltbook_daemon.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('moltbook-daemon')


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


class StateStore:
    """Small JSON state store for the daemon.

    This keeps the daemon safe and incremental across runs (last seen git head,
    last post time, etc.).
    """

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return {"version": 1, "projects": {}}
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except Exception as e:
            logger.warning(f"Failed to read state file {self.path}: {e}")
            return {"version": 1, "projects": {}}

    def save(self, state):
        try:
            self.path.write_text(
                json.dumps(state, indent=2, sort_keys=True),
                encoding='utf-8'
            )
        except Exception as e:
            logger.warning(f"Failed to write state file {self.path}: {e}")


def _project_key(project_dir):
    # Use the normalized absolute path as the key to avoid collisions.
    try:
        return str(Path(project_dir).resolve())
    except Exception:
        return str(project_dir)


def _truncate(text, max_chars):
    if max_chars is None:
        return text
    try:
        max_chars = int(max_chars)
    except Exception:
        return text
    if max_chars <= 0:
        return ""
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    suffix = "\n\n[truncated]"
    keep = max(0, max_chars - len(suffix))
    return text[:keep].rstrip() + suffix


class ProjectReader:
    """Read and process content from a local project directory."""
    
    def __init__(self, project_dir):
        """Initialize the project reader.
        
        Args:
            project_dir: Path to the project directory
        """
        self.project_dir = Path(project_dir)
        if not self.project_dir.exists():
            raise ValueError(f"Project directory does not exist: {project_dir}")
        logger.info(f"Initialized project reader for: {project_dir}")
    
    def get_readme_content(self):
        """Get content from README files in the project."""
        readme_files = list(self.project_dir.glob('README*'))
        if readme_files:
            try:
                content = readme_files[0].read_text(encoding='utf-8')
                logger.info(f"Read README from {readme_files[0]}")
                return content
            except Exception as e:
                logger.error(f"Failed to read README: {e}")
        return None
    
    def get_file_list(self, pattern='*.md'):
        """Get list of files matching a pattern.
        
        Args:
            pattern: Glob pattern for files to find
            
        Returns:
            List of file paths
        """
        return list(self.project_dir.glob(f'**/{pattern}'))
    
    def get_summary(self):
        """Generate a summary of the project.
        
        Returns:
            Summary string
        """
        # Count only files (not directories) for efficiency
        file_count = sum(1 for f in self.project_dir.rglob('*') if f.is_file())
        md_files = len(self.get_file_list('*.md'))
        py_files = len(self.get_file_list('*.py'))
        
        summary = f"Project: {self.project_dir.name}\n"
        summary += f"Total files: {file_count}\n"
        summary += f"Markdown files: {md_files}\n"
        summary += f"Python files: {py_files}\n"
        
        readme = self.get_readme_content()
        if readme:
            # Get first few lines of README
            lines = readme.split('\n')[:5]
            summary += "\nREADME preview:\n" + '\n'.join(lines)
        
        return summary

    def _run_git(self, args):
        """Run a git command in the project directory."""
        try:
            result = subprocess.run(
                ['git'] + args,
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return None, "git not found"
        except Exception as e:
            return None, str(e)

        if result.returncode != 0:
            err = (result.stderr or result.stdout or '').strip()
            return None, err
        return (result.stdout or '').strip(), None

    def is_git_repo(self):
        out, err = self._run_git(['rev-parse', '--is-inside-work-tree'])
        if err:
            return False
        return out.strip().lower() == 'true'

    def get_git_head(self):
        out, err = self._run_git(['rev-parse', 'HEAD'])
        if err:
            return None
        return out.strip()

    def get_git_commits_since(self, since_commit=None, max_count=10):
        """Return a list of commits as strings (oneline)."""
        rev = 'HEAD'
        if since_commit:
            rev = f"{since_commit}..HEAD"
        out, err = self._run_git(['log', rev, '--oneline', f'--max-count={max_count}', '--no-decorate'])
        if err:
            return []
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        return lines

    def get_git_changed_files_since(self, since_commit=None, max_files=25):
        """Return changed files as name-status lines."""
        if not since_commit:
            return []
        out, err = self._run_git(['diff', '--name-status', f'{since_commit}..HEAD'])
        if err:
            return []
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        return lines[:max_files]

    def get_fs_changes_since(self, since_epoch, max_files=25):
        """Fallback: list changed files by mtime (non-git projects)."""
        changed = []
        try:
            for p in self.project_dir.rglob('*'):
                if not p.is_file():
                    continue
                try:
                    if p.stat().st_mtime > since_epoch:
                        changed.append(str(p.relative_to(self.project_dir)))
                except Exception:
                    continue
        except Exception:
            return []
        return changed[:max_files]

    def get_delta(self, last_seen=None, last_scan_epoch=None, max_commits=10, max_files=25):
        """Compute a minimal delta summary from git if available, else filesystem mtime."""
        if self.is_git_repo():
            head = self.get_git_head()
            if not last_seen:
                # First run: treat as baseline. We still return a few recent commits
                # for visibility, but don't consider it "changes".
                commits = self.get_git_commits_since(None, max_count=max_commits)
                return {
                    'mode': 'git',
                    'head': head,
                    'has_changes': False,
                    'initial_baseline': True,
                    'commits': commits,
                    'changed_files': [],
                    'scan_epoch': None,
                }

            commits = self.get_git_commits_since(last_seen, max_count=max_commits)
            files = self.get_git_changed_files_since(last_seen, max_files=max_files)
            has_changes = bool(commits) and (head != last_seen)
            return {
                'mode': 'git',
                'head': head,
                'has_changes': has_changes,
                'initial_baseline': False,
                'commits': commits,
                'changed_files': files,
                'scan_epoch': None,
            }

        # Filesystem fallback
        now_epoch = time.time()
        since_epoch = last_scan_epoch if last_scan_epoch is not None else (now_epoch - 24 * 3600)
        changed = self.get_fs_changes_since(since_epoch, max_files=max_files)
        return {
            'mode': 'fs',
            'head': None,
            'has_changes': bool(changed),
            'initial_baseline': last_scan_epoch is None,
            'commits': [],
            'changed_files': changed,
            'scan_epoch': now_epoch,
        }


class MoltbookDaemon:
    """Main daemon class for continuous Moltbook interaction."""
    
    def __init__(
        self,
        api_key,
        project_dir,
        interval=300,
        dry_run=False,
        once=False,
        post_enabled=False,
        force_post=False,
        submolt='general',
        max_content_chars=3500,
        max_commits=10,
        max_files=25,
        state_file=None,
        api_timeout_s=30,
        api_retries=2,
    ):
        """Initialize the daemon.
        
        Args:
            api_key: Moltbook API key
            project_dir: Path to project directory
            interval: Seconds between operations (default: 300)
        """
        self.client = MoltbookClient(
            api_key,
            dry_run=dry_run,
            timeout_s=api_timeout_s,
            retries=api_retries,
        )
        self.project_reader = ProjectReader(project_dir)
        self.interval = interval
        self.running = False
        self.once = once
        self.dry_run = dry_run
        self.post_enabled = post_enabled
        self.force_post = force_post
        self.submolt = submolt
        self.max_content_chars = max_content_chars
        self.max_commits = max_commits
        self.max_files = max_files

        state_path = (
            state_file
            or os.getenv('STATE_FILE')
            or (Path(__file__).resolve().parents[1] / '.moltbook_daemon_state.json')
        )
        self.state_store = StateStore(state_path)
        self.state = self.state_store.load()
        logger.info("Moltbook daemon initialized")

    def run_iteration(self, iteration):
        """Run a single daemon iteration."""
        logger.info(f"Daemon iteration {iteration}")

        # Get project information
        project_summary = self.project_reader.get_summary()
        logger.info(f"Project summary:\n{project_summary}")

        proj_key = _project_key(self.project_reader.project_dir)
        proj_state = self.state.get('projects', {}).get(proj_key, {})
        last_seen_head = proj_state.get('last_git_head')
        last_scan_epoch = proj_state.get('last_scan_epoch')

        delta = self.project_reader.get_delta(
            last_seen=last_seen_head,
            last_scan_epoch=last_scan_epoch,
            max_commits=self.max_commits,
            max_files=self.max_files,
        )

        logger.info(
            f"Delta mode={delta.get('mode')} has_changes={delta.get('has_changes')} "
            f"head={delta.get('head')}"
        )

        if delta.get('has_changes'):
            title, content = self._render_update_post(delta, project_summary)
            logger.info("Draft post title: " + title)
            logger.info("Draft post content preview:\n" + content[:800])

            if self.post_enabled:
                self._maybe_post_update(proj_key, proj_state, title, content)

        # Optional: force a post even when nothing changed (still cooldown-limited)
        if self.force_post and self.post_enabled and not delta.get('has_changes'):
            title, content = self._render_status_post(delta, project_summary)
            logger.info("Draft post title: " + title)
            logger.info("Draft post content preview:\n" + content[:800])
            self._maybe_post_update(proj_key, proj_state, title, content)

        # Lightweight heartbeat checks (safe/read-only)
        try:
            status = self.client.get_agent_status()
            logger.info(f"Agent status: {status}")
        except Exception as e:
            logger.warning(f"Failed to fetch agent status: {e}")

        try:
            dm = self.client.dm_check()
            if isinstance(dm, dict) and dm.get('has_activity'):
                logger.info(f"DM activity detected: {dm.get('summary')}")
            else:
                logger.info("No DM activity")
        except Exception as e:
            logger.warning(f"Failed to check DMs: {e}")

        # Persist state
        if 'projects' not in self.state:
            self.state['projects'] = {}
        proj_state['last_run_at'] = _utc_now_iso()
        if delta.get('mode') == 'git':
            proj_state['last_git_head'] = delta.get('head')
        if delta.get('mode') == 'fs':
            proj_state['last_scan_epoch'] = delta.get('scan_epoch')
        self.state['projects'][proj_key] = proj_state
        self.state_store.save(self.state)

    def _render_status_post(self, delta, project_summary):
        project_name = self.project_reader.project_dir.name
        now_local = time.strftime('%Y-%m-%d %H:%M')
        title = f"{project_name} status ({now_local})"

        lines = []
        lines.append(f"Project: {project_name}")
        lines.append("")
        lines.append("No new changes detected since the last run.")
        head = delta.get('head')
        if head:
            lines.append(f"Current git HEAD: `{head[:12]}`")
        lines.append("")
        if "README preview:" in project_summary:
            lines.append("README preview:")
            lines.append(project_summary.split("README preview:", 1)[1].strip())
        content = _truncate("\n".join(lines).strip(), self.max_content_chars)
        return title, content

    def _render_update_post(self, delta, project_summary):
        project_name = self.project_reader.project_dir.name
        now_local = time.strftime('%Y-%m-%d %H:%M')

        title = f"{project_name} update ({now_local})"

        lines = []
        lines.append(f"Project: {project_name}")
        lines.append("")

        if delta.get('mode') == 'git':
            commits = delta.get('commits') or []
            if commits:
                lines.append("Changes (git commits):")
                for c in commits[:10]:
                    lines.append(f"- {c}")
                lines.append("")

            changed = delta.get('changed_files') or []
            if changed:
                lines.append("Changed files:")
                for f in changed[:25]:
                    lines.append(f"- {f}")
                lines.append("")
        else:
            changed = delta.get('changed_files') or []
            lines.append("Changes (file scan):")
            for f in changed[:25]:
                lines.append(f"- {f}")
            lines.append("")

        # Include a short README preview from the summary
        if "README preview:" in project_summary:
            lines.append("README preview:")
            lines.append(project_summary.split("README preview:", 1)[1].strip())

        content = _truncate("\n".join(lines).strip(), self.max_content_chars)
        return title, content

    def _maybe_post_update(self, proj_key, proj_state, title, content):
        last_post_at = _parse_iso_dt(proj_state.get('last_post_at'))
        if last_post_at is not None:
            age_s = (datetime.now(timezone.utc) - last_post_at).total_seconds()
            if age_s < 30 * 60:
                logger.info(
                    "Skipping post due to Moltbook cooldown (30 min). "
                    f"Next post allowed in ~{int((30 * 60 - age_s) / 60)} min"
                )
                return False

        logger.info(f"Posting update to m/{self.submolt} ...")
        resp = action_create_post(
            self.client,
            submolt=self.submolt,
            title=title,
            content=content,
        )
        if isinstance(resp, dict) and resp.get('dry_run'):
            logger.info("DRY_RUN - post skipped")
            return False

        # Best-effort record the post
        proj_state['last_post_at'] = _utc_now_iso()
        if isinstance(resp, dict):
            proj_state['last_post_response'] = resp

        return True
    
    def start(self):
        """Start the daemon."""
        logger.info("Starting Moltbook daemon...")

        if self.dry_run:
            logger.info("DRY_RUN enabled - write operations will be skipped")
        
        # Test connection
        if not self.client.test_connection():
            logger.warning(
                "Could not verify connection to Moltbook API. Check MOLTBOOK_API_KEY and "
                "ensure the API base uses https://www.moltbook.com"
            )
        
        self.running = True
        iteration = 0
        
        try:
            while self.running:
                iteration += 1
                self.run_iteration(iteration)
                
                # Here you would implement your interaction logic
                # For example, posting updates about the project
                # self.client.post_message(f"Update from {self.project_reader.project_dir.name}...")

                if self.once:
                    logger.info("--once set; exiting after one iteration")
                    self.running = False
                    break
                
                logger.info(f"Sleeping for {self.interval} seconds...")
                time.sleep(self.interval)
                
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")
            self.running = False
        except Exception as e:
            logger.error(f"Daemon error: {e}")
            raise
    
    def stop(self):
        """Stop the daemon."""
        logger.info("Stopping daemon...")
        self.running = False


def main():
    """Main entry point for the daemon."""
    parser = argparse.ArgumentParser(description="Moltbook daemon (Windows-first)")
    parser.add_argument('--once', action='store_true', help='Run one iteration and exit')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Do not perform write operations (POST/PATCH/PUT/DELETE)'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=None,
        help='Seconds between iterations (overrides INTERVAL env var)'
    )
    parser.add_argument(
        '--post',
        action='store_true',
        help='Actually create Moltbook posts when changes are detected'
    )
    parser.add_argument(
        '--force-post',
        action='store_true',
        help='Create a status post even when no changes are detected (requires --post)'
    )
    parser.add_argument(
        '--submolt',
        default=None,
        help='Submolt/community to post to (default: MOLTBOOK_SUBMOLT or general)'
    )
    parser.add_argument(
        '--max-content-chars',
        type=int,
        default=None,
        help='Max characters for post content (default: MAX_CONTENT_CHARS env or 3500)'
    )
    parser.add_argument(
        '--max-commits',
        type=int,
        default=None,
        help='Max git commits to include in a post (default: MAX_COMMITS env or 10)'
    )
    parser.add_argument(
        '--max-files',
        type=int,
        default=None,
        help='Max changed files to include in a post (default: MAX_FILES env or 25)'
    )
    parser.add_argument(
        '--state-file',
        default=None,
        help='Path to state JSON file (default: .moltbook_daemon_state.json in repo root)'
    )
    args = parser.parse_args()

    # Load environment variables
    load_dotenv()
    
    # Get configuration from environment
    api_key = os.getenv('MOLTBOOK_API_KEY')
    project_dir = os.getenv('PROJECT_DIR')
    
    # Validate configuration
    if not api_key:
        logger.error("MOLTBOOK_API_KEY not set in .env file")
        sys.exit(1)
    
    if not project_dir:
        logger.error("PROJECT_DIR not set in .env file")
        sys.exit(1)
    
    # Get optional interval (default 5 minutes)
    interval = args.interval if args.interval is not None else int(os.getenv('INTERVAL', '300'))
    submolt = args.submolt or os.getenv('MOLTBOOK_SUBMOLT') or 'general'

    max_content_chars = (
        args.max_content_chars
        if args.max_content_chars is not None
        else int(os.getenv('MAX_CONTENT_CHARS', '3500'))
    )
    max_commits = (
        args.max_commits
        if args.max_commits is not None
        else int(os.getenv('MAX_COMMITS', '10'))
    )
    max_files = (
        args.max_files
        if args.max_files is not None
        else int(os.getenv('MAX_FILES', '25'))
    )

    api_timeout_s = int(os.getenv('MOLTBOOK_TIMEOUT_S', '300'))
    api_retries = int(os.getenv('MOLTBOOK_RETRIES', '2'))
    
    # Create and start daemon
    try:
        daemon = MoltbookDaemon(
            api_key,
            project_dir,
            interval,
            dry_run=args.dry_run,
            once=args.once,
            post_enabled=args.post,
            force_post=args.force_post,
            submolt=submolt,
            max_content_chars=max_content_chars,
            max_commits=max_commits,
            max_files=max_files,
            state_file=args.state_file,
            api_timeout_s=api_timeout_s,
            api_retries=api_retries,
        )
        daemon.start()
    except Exception as e:
        logger.error(f"Failed to start daemon: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
