#!/bin/bash
# cctop hook — writes status JSON for the cctop dashboard.
# Registered for hook events in both Claude Code and Copilot CLI. Must be fast (<50ms).
#
# Handles both field naming conventions:
#   Claude Code: snake_case (session_id, hook_event_name, tool_name, transcript_path)
#   Copilot CLI: camelCase (sessionId, hookType, toolName)
#
# Writes ONLY hook-owned fields to <id>.json. The poller writes its own
# fields to <id>.poller.json. The dashboard merges both. No shared-file races.

command -v jq >/dev/null 2>&1 || exit 0

STATUS_DIR="$HOME/.cctop"
mkdir -p "$STATUS_DIR"

# Read stdin JSON once
input=$(cat)

# Extract fields — try snake_case first (Claude Code), fall back to camelCase (Copilot CLI)
eval "$(echo "$input" | jq -r '
  @sh "SESSION_ID=\(.session_id // .sessionId // "")",
  @sh "CWD=\(.cwd // "")",
  @sh "EVENT=\(.hook_event_name // "")",
  @sh "HOOK_TYPE=\(.hookType // "")",
  @sh "TOOL=\(.tool_name // .toolName // "")",
  @sh "TRANSCRIPT_PATH=\(.transcript_path // "")",
  @sh "MODEL=\(.model // "")",
  @sh "SOURCE=\(.source // "")"
' 2>/dev/null)"

[ -z "$SESSION_ID" ] && exit 0

# Normalize event name: Copilot CLI uses hookType (camelCase), Claude Code uses hook_event_name (PascalCase)
if [ -z "$EVENT" ] && [ -n "$HOOK_TYPE" ]; then
    case "$HOOK_TYPE" in
        preToolUse)    EVENT="PreToolUse" ;;
        postToolUse)   EVENT="PostToolUse" ;;
        stop)          EVENT="Stop" ;;
        sessionStart)  EVENT="SessionStart" ;;
        sessionEnd)    EVENT="SessionEnd" ;;
        subagentStop)  EVENT="SubagentStop" ;;
        userPromptSubmit) EVENT="UserPromptSubmit" ;;
        *)             EVENT="$HOOK_TYPE" ;;
    esac
fi

# Detect client type
CLIENT=""
if [ -n "$HOOK_TYPE" ]; then
    CLIENT="copilot"
fi

STATUS_FILE="$STATUS_DIR/$SESSION_ID.json"
POLLER_FILE="$STATUS_DIR/$SESSION_ID.poller.json"

# SessionEnd: clean up both files and exit
if [ "$EVENT" = "SessionEnd" ]; then
    rm -f "$STATUS_FILE" "$POLLER_FILE"
    exit 0
fi

# Determine status from event
case "$EVENT" in
    SessionStart)
        if [ "$SOURCE" = "resume" ]; then
            STATUS="resumed"
        else
            STATUS="started"
        fi
        TOOL="" ;;
    UserPromptSubmit)   STATUS="thinking"; TOOL="" ;;
    PreToolUse)         STATUS="tool:$TOOL" ;;
    PostToolUse)        STATUS="thinking"; TOOL="" ;;
    Stop)               STATUS="idle"; TOOL="" ;;
    SubagentStop)       STATUS="thinking"; TOOL="" ;;
    *)                  STATUS="unknown"; TOOL="" ;;
esac

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Compute running_agents update:
#   Reset to 0 on SessionStart/Stop (no agents run when idle or freshly started)
#   +1 on PreToolUse for Agent/task (subagent launching)
#   -1 on SubagentStop (subagent finished), floor at 0
AGENT_DELTA=0
AGENT_RESET="false"
case "$EVENT" in
    SessionStart|Stop) AGENT_RESET="true" ;;
    PreToolUse)
        case "$TOOL" in
            Agent|task) AGENT_DELTA=1 ;;
        esac ;;
    SubagentStop) AGENT_DELTA=-1 ;;
esac

# Read existing to preserve started_at and tool_count
EXISTING=$(cat "$STATUS_FILE" 2>/dev/null || echo '{}')

# Atomic write — hook-owned fields only
TMPFILE=$(mktemp "$STATUS_DIR/.tmp.XXXXXX")
echo "$EXISTING" | jq \
    --arg sid "$SESSION_ID" \
    --arg cwd "$CWD" \
    --arg status "$STATUS" \
    --arg tool "$TOOL" \
    --arg event "$EVENT" \
    --arg now "$NOW" \
    --arg tp "$TRANSCRIPT_PATH" \
    --arg model "$MODEL" \
    --arg client "$CLIENT" \
    --argjson ppid "${PPID:-0}" \
    --argjson agent_delta "$AGENT_DELTA" \
    --argjson agent_reset "$AGENT_RESET" \
    '{
        session_id: $sid,
        cwd: $cwd,
        status: $status,
        current_tool: $tool,
        last_event: $event,
        last_activity: $now,
        started_at: (.started_at // $now),
        pid: (if $ppid > 0 then $ppid else (.pid // null) end),
        transcript_path: (if $tp != "" then $tp else (.transcript_path // "") end),
        model: (if $model != "" then $model else (.model // "") end),
        tool_count: (if $event == "PostToolUse" then ((.tool_count // 0) + 1) else (.tool_count // 0) end),
        running_agents: (if $agent_reset then 0 else [(.running_agents // 0) + $agent_delta, 0] | max end),
        client: (if $client != "" then $client else (.client // "") end)
    }' > "$TMPFILE" && mv "$TMPFILE" "$STATUS_FILE"
