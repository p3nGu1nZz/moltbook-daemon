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
    
    def __init__(self, api_key):
        """Initialize the Moltbook client.
        
        Args:
            api_key: API key for Moltbook authentication
        """
        self.api_key = api_key
        self.base_url = "https://api.moltbook.com"
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        })
    
    def test_connection(self):
        """Test the connection to the Moltbook API."""
        try:
            # This is a placeholder - adjust based on actual Moltbook API endpoints
            response = self.session.get(f'{self.base_url}/health')
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Connection test failed: {e}")
            return False
    
    def post_message(self, message):
        """Post a message to Moltbook.
        
        Args:
            message: The message content to post
            
        Returns:
            Response from the API
        """
        try:
            # Placeholder - adjust based on actual Moltbook API
            response = self.session.post(
                f'{self.base_url}/posts',
                json={'content': message}
            )
            response.raise_for_status()
            logger.info(f"Posted message successfully: {message[:50]}...")
            return response.json()
        except requests.RequestException as e:
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
        file_count = len(list(self.project_dir.glob('**/*')))
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
            summary += f"\nREADME preview:\n" + '\n'.join(lines)
        
        return summary


class MoltbookDaemon:
    """Main daemon class for continuous Moltbook interaction."""
    
    def __init__(self, api_key, project_dir, interval=300):
        """Initialize the daemon.
        
        Args:
            api_key: Moltbook API key
            project_dir: Path to project directory
            interval: Seconds between operations (default: 300)
        """
        self.client = MoltbookClient(api_key)
        self.project_reader = ProjectReader(project_dir)
        self.interval = interval
        self.running = False
        logger.info("Moltbook daemon initialized")
    
    def start(self):
        """Start the daemon."""
        logger.info("Starting Moltbook daemon...")
        
        # Test connection
        if not self.client.test_connection():
            logger.warning("Could not verify connection to Moltbook API")
        
        self.running = True
        iteration = 0
        
        try:
            while self.running:
                iteration += 1
                logger.info(f"Daemon iteration {iteration}")
                
                # Get project information
                project_summary = self.project_reader.get_summary()
                logger.info(f"Project summary:\n{project_summary}")
                
                # Here you would implement your interaction logic
                # For example, posting updates about the project
                # self.client.post_message(f"Update from {self.project_reader.project_dir.name}...")
                
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
    interval = int(os.getenv('INTERVAL', '300'))
    
    # Create and start daemon
    try:
        daemon = MoltbookDaemon(api_key, project_dir, interval)
        daemon.start()
    except Exception as e:
        logger.error(f"Failed to start daemon: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
