#!/bin/bash
# Moltbook Daemon Startup Script
# This script helps start the daemon with proper configuration checks

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  Moltbook Daemon Startup"
echo "========================================"
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  Error: .env file not found!"
    echo ""
    echo "Please create a .env file from the example:"
    echo "  cp .env.example .env"
    echo ""
    echo "Then edit .env and add your configuration:"
    echo "  - MOLTBOOK_API_KEY=your_api_key"
    echo "  - PROJECT_DIR=/path/to/your/project"
    echo ""
    exit 1
fi

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "⚠️  Error: python3 not found!"
    echo "Please install Python 3.7 or higher"
    exit 1
fi

# Check if dependencies are installed
if ! python3 -c "import dotenv, requests" 2>/dev/null; then
    echo "📦 Installing dependencies..."
    pip install -r requirements.txt
    echo ""
fi

echo "✅ Configuration checks passed"
echo ""
echo "Starting Moltbook daemon..."
echo "Press Ctrl+C to stop"
echo ""
echo "========================================"
echo ""

# Start the daemon
python3 moltbook_daemon.py
