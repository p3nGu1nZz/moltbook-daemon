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
```bash
cp .env.example .env
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

### Optional Variables

- `INTERVAL`: Time in seconds between daemon iterations (default: 300)

## Usage

### Running the Daemon

Start the daemon using the startup script (recommended):
```bash
./start_daemon.sh
```

Or run it directly with Python:
```bash
python moltbook_daemon.py
```

The daemon will:
1. Load configuration from `.env`
2. Validate the API key and project directory
3. Start continuous operation
4. Log activities to `moltbook_daemon.log` and stdout

### Stopping the Daemon

Press `Ctrl+C` to gracefully stop the daemon.

### Running in Background

To run the daemon in the background on Unix-like systems:
```bash
nohup python moltbook_daemon.py > output.log 2>&1 &
```

To stop a background daemon:
```bash
pkill -f moltbook_daemon.py
```

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
├── moltbook_daemon.py    # Main daemon application
├── start_daemon.sh       # Startup script with config checks
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
