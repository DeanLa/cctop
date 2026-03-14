# Session Data Files

> Per-session data files written by hooks: tool counts and session status.

## Tool Counts

**Location:** `~/.claude/tool-counts/<session-id>`

A plain text file containing a single integer, incremented by a `PostToolUse` hook in user-level settings. Tracks how many tool calls have occurred in a given session.

- One file per session, named by session UUID (no extension)
- Content is just the integer count as a string (e.g. `42`)
- Created on first tool use, incremented on each subsequent tool use

### User Settings Hook (for reference)

This hook in `~/.claude/settings.json` maintains tool counts:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "FILE=\"$HOME/.claude/tool-counts/$CLAUDE_SESSION_ID\"; COUNT=$(cat \"$FILE\" 2>/dev/null || echo 0); echo $((COUNT + 1)) > \"$FILE\""
          }
        ]
      }
    ]
  }
}
```

Note: The empty `matcher` string matches ALL tool names.

## Session Status Files (sessions-dashboard plugin)

**Location:** `~/.claude/session-status/<session-id>.json`

Written by the `session-status.sh` hook in the sessions-dashboard plugin. Tracks real-time state of active sessions.

### Format

```json
{
  "session_id": "uuid",
  "status": "active",
  "last_event": "PostToolUse",
  "last_tool": "Edit",
  "timestamp": "2026-03-11T10:15:00Z",
  "cwd": "/path/to/project"
}
```

### Notes

- One file per active session
- Updated on each hook event the plugin listens to
- Can be used to determine which sessions are currently active vs. idle
- Files persist after session ends (not automatically cleaned up)
