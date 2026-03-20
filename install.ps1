<#
.SYNOPSIS
    Install cctop — Claude Code / Copilot CLI sessions dashboard (Windows).
.DESCRIPTION
    Installs the cctop plugin for Claude Code and/or Copilot CLI,
    and creates a cctop command in the user's PATH.
.PARAMETER Mode
    "prod" (default) installs from GitHub. "dev" symlinks to the local repo.
#>
param(
    [ValidateSet("dev", "prod")]
    [string]$Mode = "prod"
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/DeanLa/cctop"
$ScriptDir = Split-Path -Parent (Resolve-Path $MyInvocation.MyCommand.Path)
$BinDir = Join-Path $env:USERPROFILE ".local\bin"

# --- Ensure bin dir exists and is in PATH ---
if (-not (Test-Path $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
}
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$BinDir", "User")
    Write-Host "Added $BinDir to user PATH (restart terminal to take effect)"
}

# --- Install Claude Code plugin (if claude is available) ---
$HasClaude = Get-Command claude -ErrorAction SilentlyContinue
if ($HasClaude) {
    Write-Host "Installing cctop plugin for Claude Code..."
    claude plugin marketplace remove cctop 2>$null
    if ($Mode -eq "dev" -and (Test-Path (Join-Path $ScriptDir ".claude-plugin\marketplace.json"))) {
        claude plugin marketplace add $ScriptDir
    } else {
        claude plugin marketplace add $RepoUrl
    }
    claude plugin install cctop@cctop --scope user
    Write-Host "Claude Code plugin installed."
} else {
    Write-Host "Claude Code not found, skipping Claude plugin install."
}

# --- Install Copilot CLI plugin (if copilot is available) ---
$HasCopilot = Get-Command copilot -ErrorAction SilentlyContinue
if ($HasCopilot) {
    Write-Host "Installing cctop plugin for Copilot CLI..."
    if ($Mode -eq "dev") {
        copilot plugin install $ScriptDir --plugin-dir (Join-Path $ScriptDir "plugin") 2>$null
    }
    Write-Host "Copilot CLI plugin configured."
} else {
    Write-Host "Copilot CLI not found, skipping Copilot plugin install."
}

# --- Create cctop CLI entry point ---
$BinFile = Join-Path $BinDir "cctop.cmd"
if ($Mode -eq "dev") {
    $LaunchScript = Join-Path $ScriptDir "plugin\scripts\launch-cctop.ps1"
    Set-Content -Path $BinFile -Value "@powershell -NoProfile -ExecutionPolicy Bypass -File `"$LaunchScript`" %*"
    Write-Host "Linked $BinFile -> local repo (dev mode)"
} else {
    $LaunchScript = Join-Path $BinDir "launch-cctop.ps1"
    # Copy the launcher to bin dir
    Copy-Item (Join-Path $ScriptDir "plugin\scripts\launch-cctop.ps1") $LaunchScript -Force
    Set-Content -Path $BinFile -Value "@powershell -NoProfile -ExecutionPolicy Bypass -File `"$LaunchScript`" %*"
    Write-Host "Installed cctop CLI to $BinFile"
}

# --- Check for uv ---
$HasUv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $HasUv) {
    Write-Host ""
    Write-Host "WARNING: uv is not installed. cctop requires uv to run Python scripts."
    Write-Host "Install it with: pip install uv"
}

Write-Host ""
Write-Host "Done ($Mode)! Run 'cctop' in a separate terminal to launch the dashboard."
