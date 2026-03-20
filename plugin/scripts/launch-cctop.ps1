<# Launch cctop — Claude Code / Copilot CLI Sessions dashboard with the background poller #>
$ScriptDir = Split-Path -Parent (Resolve-Path $MyInvocation.MyCommand.Path)

# Handle --reset: wipe session data before starting
if ($args -contains "--reset") {
    $CctopDir = Join-Path $env:USERPROFILE ".cctop"
    if (Test-Path $CctopDir) {
        Remove-Item -Recurse -Force $CctopDir
    }
    New-Item -ItemType Directory -Path $CctopDir -Force | Out-Null
    Write-Host "cctop: session data cleared"
}

# Start the poller in the background
$PollerScript = Join-Path $ScriptDir "cctop-poller.py"
$PollerJob = Start-Job -ScriptBlock {
    param($script)
    uv run --script $script
} -ArgumentList $PollerScript

try {
    # Run the dashboard in the foreground
    $DashboardScript = Join-Path $ScriptDir "cctop_dashboard.py"
    uv run --script $DashboardScript @args
} finally {
    # Kill the poller when the dashboard exits
    Stop-Job -Job $PollerJob -ErrorAction SilentlyContinue
    Remove-Job -Job $PollerJob -ErrorAction SilentlyContinue
}
