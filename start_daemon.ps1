<#
Moltbook Daemon Startup Script (Windows)

- Validates .env exists
- Ensures dependencies are installed
- Starts the daemon

Usage examples:
  .\start_daemon.ps1
  .\start_daemon.ps1 -Once
  .\start_daemon.ps1 -Once -DryRun
#>

[CmdletBinding()]
param(
    [switch]$Once,
    [switch]$DryRun,
    [switch]$Post,
    [string]$Submolt
)

$ErrorActionPreference = 'Stop'

# Prefer UTF-8 output so emoji in Moltbook responses don't crash logging.
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch {
    # ignore
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "========================================"
Write-Host "  Moltbook Daemon Startup (Windows)"
Write-Host "========================================"
Write-Host ""

if (-not (Test-Path -LiteralPath ".env")) {
    Write-Host "Error: .env file not found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Create one from the example:" 
    Write-Host "  Copy-Item .env.example .env"
    Write-Host ""
    exit 1
}

$Python = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
}

if (-not $Python) {
    Write-Host "Error: Python not found. Install Python 3.7+ and/or create .venv." -ForegroundColor Red
    exit 1
}

Write-Host "Using Python: $Python"

# Install dependencies (idempotent)
Write-Host "Checking dependencies..."
& $Python -c "import dotenv, requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies..."
    & $Python -m pip install -r requirements.txt
}

$argsList = @("moltbook_daemon.py")
if ($Once) { $argsList += "--once" }
if ($DryRun) { $argsList += "--dry-run" }
if ($Post) { $argsList += "--post" }
if ($Submolt) { $argsList += @("--submolt", $Submolt) }

Write-Host ""
Write-Host "Starting Moltbook daemon... (Ctrl+C to stop)"
Write-Host "========================================"
Write-Host ""

& $Python @argsList
