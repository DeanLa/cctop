<# cctop hook — writes status JSON for the cctop dashboard (Windows PowerShell).
   Registered for hook events in both Claude Code and Copilot CLI. Must be fast (<50ms).

   Handles both field naming conventions:
     Claude Code: snake_case (session_id, hook_event_name, tool_name, transcript_path)
     Copilot CLI: camelCase (sessionId, hookType, toolName)

   Writes ONLY hook-owned fields to <id>.json. The poller writes its own
   fields to <id>.poller.json. The dashboard merges both. No shared-file races.
#>
$ErrorActionPreference = "SilentlyContinue"

$StatusDir = Join-Path $env:USERPROFILE ".cctop"
if (-not (Test-Path $StatusDir)) {
    New-Item -ItemType Directory -Path $StatusDir -Force | Out-Null
}

# Read stdin JSON
$InputText = [Console]::In.ReadToEnd()
if (-not $InputText) { exit 0 }

try {
    $InputObj = $InputText | ConvertFrom-Json
} catch {
    exit 0
}

# Extract fields — try snake_case first (Claude Code), fall back to camelCase (Copilot CLI)
$SessionId = if ($InputObj.session_id) { $InputObj.session_id } elseif ($InputObj.sessionId) { $InputObj.sessionId } else { "" }
if (-not $SessionId) { exit 0 }

$Cwd = if ($InputObj.cwd) { $InputObj.cwd } else { "" }
$Event = if ($InputObj.hook_event_name) { $InputObj.hook_event_name } else { "" }
$HookType = if ($InputObj.hookType) { $InputObj.hookType } else { "" }
$Tool = if ($InputObj.tool_name) { $InputObj.tool_name } elseif ($InputObj.toolName) { $InputObj.toolName } else { "" }
$TranscriptPath = if ($InputObj.transcript_path) { $InputObj.transcript_path } else { "" }
$Model = if ($InputObj.model) { $InputObj.model } else { "" }
$Source = if ($InputObj.source) { $InputObj.source } else { "" }

# Normalize event name from Copilot CLI camelCase to PascalCase
$Client = ""
if (-not $Event -and $HookType) {
    $Client = "copilot"
    $Event = switch ($HookType) {
        "preToolUse"       { "PreToolUse" }
        "postToolUse"      { "PostToolUse" }
        "stop"             { "Stop" }
        "sessionStart"     { "SessionStart" }
        "sessionEnd"       { "SessionEnd" }
        "subagentStop"     { "SubagentStop" }
        "userPromptSubmit" { "UserPromptSubmit" }
        default            { $HookType }
    }
}

$StatusFile = Join-Path $StatusDir "$SessionId.json"
$PollerFile = Join-Path $StatusDir "$SessionId.poller.json"

# SessionEnd: clean up both files and exit
if ($Event -eq "SessionEnd") {
    Remove-Item -Path $StatusFile -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $PollerFile -Force -ErrorAction SilentlyContinue
    exit 0
}

# Determine status from event
$Status = "unknown"
switch ($Event) {
    "SessionStart" {
        $Status = if ($Source -eq "resume") { "resumed" } else { "started" }
        $Tool = ""
    }
    "UserPromptSubmit" { $Status = "thinking"; $Tool = "" }
    "PreToolUse"       { $Status = "tool:$Tool" }
    "PostToolUse"      { $Status = "thinking"; $Tool = "" }
    "Stop"             { $Status = "idle"; $Tool = "" }
    "SubagentStop"     { $Status = "thinking"; $Tool = "" }
    default            { $Status = "unknown"; $Tool = "" }
}

$Now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

# Compute running_agents
$AgentDelta = 0
$AgentReset = $false
switch ($Event) {
    { $_ -in "SessionStart", "Stop" } { $AgentReset = $true }
    "PreToolUse" { if ($Tool -in "Agent", "task") { $AgentDelta = 1 } }
    "SubagentStop" { $AgentDelta = -1 }
}

# Read existing to preserve started_at and tool_count
$Existing = @{}
if (Test-Path $StatusFile) {
    try {
        $Existing = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $ExistingHash = @{}
        $Existing.PSObject.Properties | ForEach-Object { $ExistingHash[$_.Name] = $_.Value }
        $Existing = $ExistingHash
    } catch {
        $Existing = @{}
    }
}

$Ppid = $PID
$ToolCount = if ($Existing.ContainsKey("tool_count")) { $Existing["tool_count"] } else { 0 }
if ($Event -eq "PostToolUse") { $ToolCount++ }

$RunningAgents = if ($Existing.ContainsKey("running_agents")) { $Existing["running_agents"] } else { 0 }
if ($AgentReset) {
    $RunningAgents = 0
} else {
    $RunningAgents = [Math]::Max(0, $RunningAgents + $AgentDelta)
}

$Result = @{
    session_id     = $SessionId
    cwd            = $Cwd
    status         = $Status
    current_tool   = $Tool
    last_event     = $Event
    last_activity  = $Now
    started_at     = if ($Existing.ContainsKey("started_at") -and $Existing["started_at"]) { $Existing["started_at"] } else { $Now }
    pid            = $Ppid
    transcript_path = if ($TranscriptPath) { $TranscriptPath } elseif ($Existing.ContainsKey("transcript_path")) { $Existing["transcript_path"] } else { "" }
    model          = if ($Model) { $Model } elseif ($Existing.ContainsKey("model")) { $Existing["model"] } else { "" }
    tool_count     = $ToolCount
    running_agents = $RunningAgents
    client         = if ($Client) { $Client } elseif ($Existing.ContainsKey("client")) { $Existing["client"] } else { "" }
}

# Atomic write via temp file
$TmpFile = Join-Path $StatusDir ".tmp.$([System.IO.Path]::GetRandomFileName())"
$Result | ConvertTo-Json -Compress | Set-Content -Path $TmpFile -Encoding UTF8 -NoNewline
Move-Item -Path $TmpFile -Destination $StatusFile -Force
