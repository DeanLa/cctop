# JSONL Transcript Format

> Structure of session transcript files and project directory encoding.

## File Location

`~/.claude/projects/<project-dir-encoded>/<session-id>.jsonl`

Each session's transcript is a newline-delimited JSON file (one JSON object per line).

## Project Directory Encoding

Claude Code encodes project paths for use as directory names by replacing each `/` with `-`. All other characters (including underscores) are preserved as-is.

```
/home/user/my__project/src
 ^    ^    ^           ^    (slashes become hyphens)
-home-user-my__project-src
```

Double underscores are preserved as-is. Only `/` characters become `-`.

## Entry Types

### `type: "user"` — Human turn

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": "the user's prompt text (plain string)"
  },
  "slug": "session-slug",
  "gitBranch": "main",
  "cwd": "/path/to/project",
  "timestamp": "2026-03-11T10:00:00.000Z"
}
```

- `.message.content` is a **string** (not an array)
- `.slug` is an auto-generated short label
- `.gitBranch` and `.cwd` reflect state at the time of the message

### `type: "assistant"` — Model turn

```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": [
      { "type": "text", "text": "response text..." },
      { "type": "tool_use", "id": "toolu_...", "name": "Read", "input": {...} }
    ],
    "model": "claude-opus-4-6-v1",
    "usage": {
      "input_tokens": 12345,
      "output_tokens": 678,
      "cache_creation_input_tokens": 5000,
      "cache_read_input_tokens": 8000
    },
    "stop_reason": "end_turn"
  },
  "timestamp": "2026-03-11T10:00:05.000Z"
}
```

- `.message.content` is an **array** of content blocks (text, tool_use, tool_result)
- `.message.usage` contains token counts for cost/performance tracking
- `cache_creation_input_tokens` and `cache_read_input_tokens` relate to prompt caching

### `type: "custom-title"` — Session rename

```json
{
  "type": "custom-title",
  "customTitle": "user-chosen name",
  "sessionId": "uuid-of-session"
}
```

Written when the user renames a session via `/name`. This is the **authoritative source** for custom titles during an active session (the sessions-index may not update until later).

### Other Entry Types

| Type | Purpose |
|---|---|
| `progress` | Hook execution and tool progress indicators (UI internal) |
| `system` | System messages: `/clear`, `/compact`, config changes, notifications |
| `file-history-snapshot` | File state snapshots before edits (for undo/restore) |

## Parsing Notes

- Each line is an independent JSON object; parse line-by-line
- Lines can be large (especially assistant turns with tool results)
- Not all lines have a `timestamp` field — some older entry types omit it
- Tool results appear as content blocks within assistant messages, not as separate top-level entries
