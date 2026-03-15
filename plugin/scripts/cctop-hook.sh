#!/bin/bash
# cctop hook — writes status JSON for the cctop dashboard.
# Registered for all 7 hook events. Must be fast (<50ms).
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
  @sh "SOURCE=\(.source // "")"
' 2>/dev/null)"

[ -z "$SESSION_ID" ] && exit 0

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
    --argjson ppid "${PPID:-0}" \
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
        tool_count: (if $event == "PostToolUse" then ((.tool_count // 0) + 1) else (.tool_count // 0) end)
    }' > "$TMPFILE" && mv "$TMPFILE" "$STATUS_FILE"
