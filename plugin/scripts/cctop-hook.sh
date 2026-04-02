#!/bin/bash
# cctop hook — writes status JSON for the cctop dashboard.
# Registered for 12 hook events. Must be fast (<50ms).
#
# Writes ONLY hook-owned fields to <id>.json. The poller writes its own
# fields to <id>.poller.json. The dashboard merges both. No shared-file races.

command -v jq >/dev/null 2>&1 || exit 0

STATUS_DIR="$HOME/.cctop"
mkdir -p "$STATUS_DIR"

# Read stdin JSON once
input=$(cat)

# Extract fields in a single jq call
eval "$(echo "$input" | jq -r '
  @sh "SESSION_ID=\(.session_id // "")",
  @sh "CWD=\(.cwd // "")",
  @sh "EVENT=\(.hook_event_name // "")",
  @sh "TOOL=\(.tool_name // "")",
  @sh "TRANSCRIPT_PATH=\(.transcript_path // "")",
  @sh "MODEL=\(.model // "")",
  @sh "SOURCE=\(.source // "")",
  @sh "NOTIFICATION_TYPE=\(.notification_type // "")",
  @sh "ERROR=\(.error // "")",
  @sh "ERROR_DETAILS=\(.error_details // "")",
  @sh "AGENT_TYPE=\(.agent_type // "")",
  @sh "NEW_CWD=\(.new_cwd // "")"
' 2>/dev/null)"

# Detect tmux session and window on SessionStart
TMUX_SESSION=""
TMUX_WINDOW=""
if [ "$EVENT" = "SessionStart" ] && [ -n "$TMUX" ]; then
    TMUX_SESSION=$(tmux display-message -p '#S' 2>/dev/null || echo "")
    TMUX_WINDOW=$(tmux display-message -p '#I' 2>/dev/null || echo "")
fi

# Don't trust transcript_path if the file doesn't exist (happens after
# EnterWorktree — Claude reports a path based on the worktree cwd, but
# the actual transcript stays at the original project path).
[ -n "$TRANSCRIPT_PATH" ] && [ ! -f "$TRANSCRIPT_PATH" ] && TRANSCRIPT_PATH=""

[ -z "$SESSION_ID" ] && exit 0

STATUS_FILE="$STATUS_DIR/$SESSION_ID.json"
POLLER_FILE="$STATUS_DIR/$SESSION_ID.poller.json"

# SessionEnd: clean up both files and exit
if [ "$EVENT" = "SessionEnd" ]; then
    rm -f "$STATUS_FILE" "$POLLER_FILE"
    exit 0
fi

# Read existing file early — needed for last_tool on Stop, status preservation
# on CwdChanged/SubagentStart/PostToolUseFailure, and planning_mode state.
EXISTING=$(cat "$STATUS_FILE" 2>/dev/null || echo '{}')

# Preserve last_tool from existing (overridden in PreToolUse case)
LAST_TOOL=$(echo "$EXISTING" | jq -r '.last_tool // ""' 2>/dev/null)

# Planning mode: read current state, updated per event below
PLANNING_MODE=$(echo "$EXISTING" | jq -r '.planning_mode // false' 2>/dev/null)

# Extract subagent_type and status_context from tool_input (PreToolUse only)
SUBAGENT_TYPE=""
STATUS_CONTEXT=""
if [ "$EVENT" = "PreToolUse" ]; then
    STATUS_CONTEXT=$(echo "$input" | jq -r --arg tool "$TOOL" '
        if $tool == "Edit" or $tool == "Write" or $tool == "Read" or $tool == "NotebookEdit" then
            (.tool_input.file_path // "")
        elif $tool == "Bash" then
            ((.tool_input.description // (.tool_input.command // ""))[:120])
        elif $tool == "WebSearch" then
            (.tool_input.query // "")
        elif $tool == "WebFetch" then
            (.tool_input.url // "")
        elif $tool == "Grep" or $tool == "Glob" then
            (.tool_input.pattern // "")
        elif $tool == "Agent" then
            (.tool_input.description // "")
        elif $tool == "AskUserQuestion" then
            ((.tool_input.questions[0].question // "")[:120])
        elif $tool == "SendMessage" then
            (.tool_input.to // "")
        elif $tool == "LSP" then
            (.tool_input.operation // "")
        elif $tool == "Skill" then
            (.tool_input.skill // "")
        else
            ""
        end
    ' 2>/dev/null)
    if [ "$TOOL" = "Agent" ]; then
        SUBAGENT_TYPE=$(echo "$input" | jq -r '.tool_input.subagent_type // ""' 2>/dev/null)
    fi
fi

# Determine status from event
case "$EVENT" in
    SessionStart)
        if [ "$SOURCE" = "resume" ]; then
            STATUS="resumed"
        else
            STATUS="started"
        fi
        TOOL=""
        PLANNING_MODE="false" ;;
    UserPromptSubmit)
        STATUS="thinking"
        TOOL=""
        PLANNING_MODE="false" ;;
    PreToolUse)
        STATUS="tool:$TOOL"
        LAST_TOOL="$TOOL"
        if [ "$TOOL" = "EnterPlanMode" ]; then
            PLANNING_MODE="true"
        fi ;;
    PostToolUse)
        STATUS="thinking"
        TOOL="" ;;
    Stop)
        # Idle sub-status based on last tool before Stop
        case "$LAST_TOOL" in
            ExitPlanMode)     STATUS="idle:awaiting_plan" ;;
            AskUserQuestion)  STATUS="idle:needs_input" ;;
            *)                STATUS="idle" ;;
        esac
        TOOL=""
        PLANNING_MODE="false" ;;
    SubagentStop)
        STATUS="thinking"
        TOOL="" ;;
    SubagentStart)
        # Preserve current status, just update active_subagent_type
        STATUS=$(echo "$EXISTING" | jq -r '.status // "thinking"' 2>/dev/null)
        TOOL=$(echo "$EXISTING" | jq -r '.current_tool // ""' 2>/dev/null)
        SUBAGENT_TYPE="$AGENT_TYPE" ;;
    Notification)
        case "$NOTIFICATION_TYPE" in
            permission_prompt)  STATUS="awaiting_permission" ;;
            elicitation_dialog) STATUS="awaiting_mcp_input" ;;
            *)                  exit 0 ;;  # Ignore other notification types
        esac
        TOOL=""
        STATUS_CONTEXT=$(echo "$input" | jq -r '.message // ""' 2>/dev/null) ;;
    StopFailure)
        STATUS="error:${ERROR:-unknown}"
        TOOL="" ;;
    PostToolUseFailure)
        # Preserve status, only increment tool_failures counter
        STATUS=$(echo "$EXISTING" | jq -r '.status // "thinking"' 2>/dev/null)
        TOOL=$(echo "$EXISTING" | jq -r '.current_tool // ""' 2>/dev/null) ;;
    CwdChanged)
        # Update cwd, preserve status
        CWD="$NEW_CWD"
        STATUS=$(echo "$EXISTING" | jq -r '.status // "unknown"' 2>/dev/null)
        TOOL=$(echo "$EXISTING" | jq -r '.current_tool // ""' 2>/dev/null) ;;
    *)
        STATUS="unknown"
        TOOL="" ;;
esac

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Compute running_agents update:
#   Reset to 0 on SessionStart/Stop (no agents run when idle or freshly started)
#   +1 on PreToolUse for Agent (subagent launching, counted before spawn completes)
#   -1 on SubagentStop (subagent finished), floor at 0
AGENT_DELTA=0
AGENT_RESET="false"
case "$EVENT" in
    SessionStart|Stop) AGENT_RESET="true" ;;
    PreToolUse)
        case "$TOOL" in
            Agent) AGENT_DELTA=1 ;;
        esac ;;
    SubagentStop) AGENT_DELTA=-1 ;;
esac

# Compute active_subagent_type
ACTIVE_SUBAGENT_TYPE=""
if [ -n "$SUBAGENT_TYPE" ]; then
    ACTIVE_SUBAGENT_TYPE="$SUBAGENT_TYPE"
elif [ "$EVENT" = "SubagentStop" ] || [ "$EVENT" = "Stop" ] || [ "$EVENT" = "SessionStart" ]; then
    ACTIVE_SUBAGENT_TYPE=""
else
    ACTIVE_SUBAGENT_TYPE=$(echo "$EXISTING" | jq -r '.active_subagent_type // ""' 2>/dev/null)
fi

# Tool failure counter
TOOL_FAILURE_DELTA=0
if [ "$EVENT" = "PostToolUseFailure" ]; then
    TOOL_FAILURE_DELTA=1
fi

# Status context lifecycle: clear on PostToolUse/UserPromptSubmit/SessionStart,
# preserve on Stop/SubagentStop/SubagentStart/CwdChanged/PostToolUseFailure/StopFailure
CLEAR_CONTEXT="false"
case "$EVENT" in
    PostToolUse|UserPromptSubmit|SessionStart) CLEAR_CONTEXT="true" ;;
esac

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
    --arg tmux_session "$TMUX_SESSION" \
    --arg tmux_window "$TMUX_WINDOW" \
    --argjson ppid "${PPID:-0}" \
    --argjson agent_delta "$AGENT_DELTA" \
    --argjson agent_reset "$AGENT_RESET" \
    --arg last_tool "$LAST_TOOL" \
    --argjson planning_mode "$PLANNING_MODE" \
    --arg active_subagent_type "$ACTIVE_SUBAGENT_TYPE" \
    --arg error_type "$ERROR" \
    --arg error_details "$ERROR_DETAILS" \
    --argjson tool_failure_delta "$TOOL_FAILURE_DELTA" \
    --arg status_context "$STATUS_CONTEXT" \
    --argjson clear_context "$CLEAR_CONTEXT" \
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
        tmux_session: (if $tmux_session != "" then $tmux_session else (.tmux_session // "") end),
        tmux_window: (if $tmux_window != "" then $tmux_window else (.tmux_window // "") end),
        last_tool: $last_tool,
        planning_mode: $planning_mode,
        active_subagent_type: $active_subagent_type,
        error_type: $error_type,
        error_details: $error_details,
        tool_failures: ((.tool_failures // 0) + $tool_failure_delta),
        status_context: (if $status_context != "" then $status_context
                         elif $clear_context then ""
                         else (.status_context // "") end)
    }' > "$TMPFILE" && mv "$TMPFILE" "$STATUS_FILE"
