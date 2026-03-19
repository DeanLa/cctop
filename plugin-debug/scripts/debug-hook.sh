#!/bin/bash
# cctop-debug hook — logs full event payloads for troubleshooting.
# Registered for all 18 hook events. Writes to ~/.cctop/<session-id>.debug.json.

command -v jq >/dev/null 2>&1 || exit 0

STATUS_DIR="$HOME/.cctop"
mkdir -p "$STATUS_DIR"

input=$(cat)

SESSION_ID=$(echo "$input" | jq -r '.session_id // empty')
EVENT=$(echo "$input" | jq -r '.hook_event_name // empty')

[ -z "$SESSION_ID" ] && exit 0

DEBUG_LOG="$STATUS_DIR/$SESSION_ID.debug.json"

# SessionEnd: log the event, then move to archive
if [ "$EVENT" = "SessionEnd" ]; then
    NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    jq -n --arg ts "$NOW" --arg event "$EVENT" --argjson input "$input" \
        '{ts: $ts, event: $event, input: $input}' >> "$DEBUG_LOG"
    ARCHIVE_DIR="$STATUS_DIR/debug-archive"
    mkdir -p "$ARCHIVE_DIR"
    mv "$DEBUG_LOG" "$ARCHIVE_DIR/"
    exit 0
fi

# Append pretty-printed JSON entry
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
jq -n --arg ts "$NOW" --arg event "$EVENT" --argjson input "$input" \
    '{ts: $ts, event: $event, input: $input}' >> "$DEBUG_LOG"
