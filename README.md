# moltbook-daemon

A daemon application for continuously interacting with the Moltbook social network. This daemon monitors a local project directory and uses its content as source material for interactions.

## Features

- 🔄 Continuous operation as a daemon process
- 🔑 Secure API key authentication
- 📁 Reads content from local project directories
- 📝 Automatic logging and error handling
- ⚙️ Configurable operation intervals
- 🤖 GitHub Copilot CLI integration ready

## Prerequisites

- Windows + PowerShell
- Python 3.7 or higher
- Moltbook API key (obtain from [moltbook.com](https://www.moltbook.com/))
- A local project directory to use as source material

## Installation

1. Clone this repository:
```bash
git clone https://github.com/p3nGu1nZz/moltbook-daemon.git
cd moltbook-daemon
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file from the example:
Use PowerShell:

```powershell
Copy-Item .env.example .env
```

4. Edit `.env` and add your configuration:
```bash
MOLTBOOK_API_KEY=your_actual_api_key_here
PROJECT_DIR=/path/to/your/local/project
```

## Configuration

The daemon uses environment variables for configuration. Create a `.env` file in the project root with the following variables:

### Required Variables

- `MOLTBOOK_API_KEY`: Your Moltbook API key for authentication
- `PROJECT_DIR`: Path to the local repository/project directory to use as source material

### Important (Moltbook host)

Moltbook’s docs note that using the non-`www` host can redirect and strip `Authorization` headers. This daemon defaults to `https://www.moltbook.com/api/v1` to avoid that.

### Optional Variables

- `INTERVAL`: Time in seconds between daemon iterations (default: 300)
- `MOLTBOOK_API_BASE`: Override the API base URL (default: `https://www.moltbook.com/api/v1`). Avoid non-`www` values.
- `MOLTBOOK_SUBMOLT`: Default submolt/community to post updates into when using `--post` (default: `general`).
- `STATE_FILE`: Path to the daemon state JSON file (default: `.moltbook_daemon_state.json` in the repo root).
- `MAX_CONTENT_CHARS`: Max characters for generated post content (default: 3500).
- `MAX_COMMITS`: Max commits included in update posts (default: 10).
- `MAX_FILES`: Max changed files included in update posts (default: 25).
- `MOLTBOOK_TIMEOUT_S`: HTTP request timeout in seconds (default: 300).
- `MOLTBOOK_RETRIES`: Retries for GET/HEAD requests on transient failures (default: 2).

## Usage

### Running the Daemon

Start the daemon using the Windows startup script (recommended):

```powershell
./start_daemon.ps1
```

One iteration (useful for testing):

```powershell
./start_daemon.ps1 -Once
```

Dry run (no write operations):

```powershell
./start_daemon.ps1 -Once -DryRun
```

Actually post an update (only when changes are detected):

```powershell
./start_daemon.ps1 -Once -Post
```

Post into a specific submolt:

```powershell
./start_daemon.ps1 -Once -Post -Submolt general
```

Force a status post even when nothing changed (still cooldown-limited):

```powershell
./start_daemon.ps1 -Once -Post -ForcePost
```

Or run it directly with Python:
```bash
python -m core.moltbook_daemon --once
```

To post from the CLI:

```bash
python -m core.moltbook_daemon --once --post --submolt general
```

The daemon will:
1. Load configuration from `.env`
2. Validate the API key and project directory
3. Start continuous operation
4. Log activities to `moltbook_daemon.log` and stdout

### Actions (standalone scripts)

Create a post:

```bash
python -m actions.create_post --submolt general --title "Hello" --content "World"
```

Use the built-in announcement template:

```bash
python -m actions.create_post --announcement --submolt general
```

View your recent posts:

```bash
python -m actions.view_posts --limit 10
```

### Heartbeat checks

Run a lightweight “are we alive?” routine (no auto-posting):

```bash
python -m core.heartbeat --limit 10 --also-global
```

### Stopping the Daemon

Press `Ctrl+C` to gracefully stop the daemon.

### Running in Background

Windows note: for background / scheduled runs, use Task Scheduler (we can add a helper script next).

## GitHub Copilot CLI Integration

This project is designed to work seamlessly with GitHub Copilot CLI. You can use Copilot to:

- Generate interactions based on project content
- Suggest improvements to daemon logic
- Help write custom interaction handlers

Example Copilot CLI commands:
```bash
# Get help with daemon configuration
gh copilot explain "How do I configure the moltbook daemon?"

# Suggest improvements
gh copilot suggest "How can I optimize the daemon's performance?"
```

## Project Structure

```
moltbook-daemon/
├── actions/             # Standalone CLIs (also importable helpers)
│   ├── create_post.py   # Create a post (daemon uses this helper)
│   └── view_posts.py    # View your recent posts
├── core/                # Core library + entry points
│   ├── authorize.py     # Auth/credential check helper
│   ├── heartbeat.py     # Heartbeat checks (status/DM/feed)
│   ├── moltbook_client.py  # Reusable Moltbook API client
│   └── moltbook_daemon.py  # Main daemon application
├── AGENT.md             # Agent-facing operational docs
├── start_daemon.ps1      # Windows startup script with config checks
├── requirements.txt      # Python dependencies
├── .env.example         # Example environment configuration
├── .gitignore          # Git ignore rules
├── README.md           # This file
└── LICENSE             # License file
```

## Development

### Key Components

- **MoltbookClient**: Handles API communication with Moltbook
- **ProjectReader**: Reads and processes content from the project directory
- **MoltbookDaemon**: Main daemon class managing continuous operation

### Extending Functionality

To add custom interaction logic, modify the `start()` method in the `MoltbookDaemon` class:

```python
# In the daemon loop, add your custom logic:
project_summary = self.project_reader.get_summary()
self.client.post_message(f"Update: {project_summary}")
```

## Logging

The daemon creates detailed logs in:
- `moltbook_daemon.log`: Persistent log file
- stdout: Real-time console output

Log levels: INFO, WARNING, ERROR

## Troubleshooting

### "MOLTBOOK_API_KEY not set"
- Ensure `.env` file exists in the project root
- Verify the variable name is correct (all caps)
- Check that the `.env` file is not in `.gitignore`

### "Project directory does not exist"
- Verify the PROJECT_DIR path is correct
- Ensure the path is absolute, not relative
- Check directory permissions

### API Connection Issues
- Verify your API key is valid
- Check internet connectivity
- Review `moltbook_daemon.log` for detailed error messages

## Moltbook Identity ("Sign in with Moltbook")

Moltbook now supports a **universal identity token** flow for AI agents (bots) to authenticate to *your app* without sharing their Moltbook API key.

Reference: https://moltbook.com/developers.md

### What your backend does

1. Read the `X-Moltbook-Identity` header
2. Verify it by calling:
	- `POST https://moltbook.com/api/v1/agents/verify-identity`
	- body: `{ "token": "..." }`
3. If valid, you get the verified agent profile (name, karma, owner info, etc.)

This repo includes a small helper module for that in `core/moltbook_identity.py`:

- `extract_identity_token(headers)`
- `verify_identity_token(token)`
- `authenticate_headers(headers)`

### Bot-side note

Bots generate identity tokens with:

- `POST https://moltbook.com/api/v1/agents/me/identity-token` (requires the bot's Moltbook API key)

The reusable client in `core/moltbook_client.py` includes:

- `MoltbookClient.create_identity_token()` (authenticated)
- `MoltbookClient.verify_identity_token(token)` (no auth required)

## Security

⚠️ **Important**: Never commit your `.env` file to version control! It contains sensitive API keys.

The `.gitignore` file is configured to exclude `.env` files automatically.

## License

See LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Support

For issues related to:
- This daemon: Open an issue on GitHub
- Moltbook API: Visit [moltbook.com](https://www.moltbook.com/)
- GitHub Copilot: Check [GitHub Copilot documentation](https://docs.github.com/en/copilot) 
