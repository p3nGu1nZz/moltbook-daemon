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
from pathlib import Path
from dotenv import load_dotenv
import requests


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('moltbook_daemon.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('moltbook-daemon')


class MoltbookClient:
    """Client for interacting with the Moltbook API."""
    
    def __init__(self, api_key, api_base=None, timeout_s=30, dry_run=False):
        """Initialize the Moltbook client.
        
        Args:
            api_key: API key for Moltbook authentication
            api_base: Base URL for the Moltbook API (defaults to https://www.moltbook.com/api/v1)
            timeout_s: Default request timeout in seconds
            dry_run: If True, do not perform write operations (POST/PATCH/PUT/DELETE)
        """
        self.api_key = api_key
        self.api_base = (
            api_base
            or os.getenv('MOLTBOOK_API_BASE')
            or "https://www.moltbook.com/api/v1"
        ).rstrip('/')
        self.timeout_s = timeout_s
        self.dry_run = dry_run

        # Moltbook explicitly warns that using the non-www host can redirect and strip
        # Authorization headers. Keep users out of that foot-gun.
        if not self.api_base.startswith("https://www.moltbook.com"):
            logger.warning(
                "MOLTBOOK_API_BASE should start with https://www.moltbook.com to avoid "
                "redirects stripping Authorization headers. "
                f"Current: {self.api_base}"
            )

        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        })

    def _request(self, method, path, **kwargs):
        """Internal request helper.

        Moltbook warns that redirects can strip the Authorization header. We disable
        redirects to fail fast with a clear message instead of silently making unauthenticated calls.
        """
        if self.dry_run and method.upper() in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            url = f"{self.api_base}/{path.lstrip('/')}"
            logger.info(f"DRY_RUN - skipping {method.upper()} {url}")
            return {
                "success": True,
                "dry_run": True,
                "skipped": True,
                "method": method.upper(),
                "path": path,
            }

        url = f"{self.api_base}/{path.lstrip('/')}"
        kwargs.setdefault('timeout', self.timeout_s)
        kwargs.setdefault('allow_redirects', False)

        try:
            response = self.session.request(method, url, **kwargs)
        except requests.RequestException as e:
            logger.error(f"Request failed ({method} {url}): {e}")
            raise

        if response.is_redirect:
            location = response.headers.get('Location')
            raise RuntimeError(
                "Moltbook API request was redirected (likely non-www host). "
                "Redirects can strip Authorization headers; refusing to follow. "
                f"URL={url} Location={location}"
            )

        # Try to parse JSON (most endpoints return JSON)
        data = None
        try:
            data = response.json()
        except ValueError:
            data = None

        if response.status_code == 429:
            retry_after_minutes = None
            if isinstance(data, dict):
                retry_after_minutes = data.get('retry_after_minutes')
            msg = f"Rate limited (429) calling {method} {url}"
            if retry_after_minutes is not None:
                msg += f"; retry_after_minutes={retry_after_minutes}"
            logger.warning(msg)

        if not response.ok:
            err = None
            if isinstance(data, dict):
                err = data.get('error') or data.get('message')
            raise RuntimeError(
                f"Moltbook API error {response.status_code} for {method} {url}: "
                f"{err or response.text}"
            )

        return data
    
    def test_connection(self):
        """Test the connection to the Moltbook API."""
        try:
            # Official endpoint per skill docs
            self._request('GET', '/agents/me')
            return True
        except requests.RequestException as e:
            logger.error(f"Connection test failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

    def get_agent_status(self):
        """Check claim status."""
        return self._request('GET', '/agents/status')

    def get_feed(self, sort='new', limit=15):
        """Get personalized feed (subscribed submolts + followed agents)."""
        params = {'sort': sort, 'limit': limit}
        return self._request('GET', '/feed', params=params)

    def list_posts(self, sort='new', limit=15, submolt=None):
        """List posts globally or for a specific submolt."""
        params = {'sort': sort, 'limit': limit}
        if submolt:
            params['submolt'] = submolt
        return self._request('GET', '/posts', params=params)

    def create_post(self, submolt, title, content=None, url=None):
        """Create a post."""
        payload = {'submolt': submolt, 'title': title}
        if content is not None:
            payload['content'] = content
        if url is not None:
            payload['url'] = url
        return self._request('POST', '/posts', json=payload)

    def dm_check(self):
        """Quick poll for DM activity (for heartbeat)."""
        return self._request('GET', '/agents/dm/check')
    
    def post_message(self, message):
        """Post a message to Moltbook.
        
        Args:
            message: The message content to post
            
        Returns:
            Response from the API
        """
        try:
            # Backwards-compatible helper: post to m/general with a generic title.
            # Prefer calling create_post(...) directly.
            title = f"Update from {time.strftime('%Y-%m-%d %H:%M')}"
            resp = self.create_post(submolt='general', title=title, content=message)
            logger.info("Posted message successfully")
            return resp
        except requests.RequestException as e:
            logger.error(f"Failed to post message: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to post message: {e}")
            return None


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


class MoltbookDaemon:
    """Main daemon class for continuous Moltbook interaction."""
    
    def __init__(self, api_key, project_dir, interval=300, dry_run=False, once=False):
        """Initialize the daemon.
        
        Args:
            api_key: Moltbook API key
            project_dir: Path to project directory
            interval: Seconds between operations (default: 300)
        """
        self.client = MoltbookClient(api_key, dry_run=dry_run)
        self.project_reader = ProjectReader(project_dir)
        self.interval = interval
        self.running = False
        self.once = once
        self.dry_run = dry_run
        logger.info("Moltbook daemon initialized")

    def run_iteration(self, iteration):
        """Run a single daemon iteration."""
        logger.info(f"Daemon iteration {iteration}")

        # Get project information
        project_summary = self.project_reader.get_summary()
        logger.info(f"Project summary:\n{project_summary}")

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
    
    # Create and start daemon
    try:
        daemon = MoltbookDaemon(
            api_key,
            project_dir,
            interval,
            dry_run=args.dry_run,
            once=args.once,
        )
        daemon.start()
    except Exception as e:
        logger.error(f"Failed to start daemon: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
