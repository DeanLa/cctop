# Sessions Index

> Pre-built index of all sessions in a project directory.

## File Location

`~/.claude/projects/<project-dir-encoded>/sessions-index.json`

Claude Code updates this file periodically and on session end.

## Schema

```json
{
  "version": 1,
  "entries": [
    {
      "sessionId": "uuid",
      "fullPath": "/absolute/path/to/transcript.jsonl",
      "fileMtime": 1768987191628,
      "firstPrompt": "first user message text...",
      "summary": "auto-generated summary...",
      "messageCount": 10,
      "created": "2026-03-11T10:00:00.000Z",
      "modified": "2026-03-11T10:30:00.000Z",
      "gitBranch": "main",
      "projectPath": "/path/to/project",
      "isSidechain": false,
      "customTitle": "user-chosen name"
    }
  ]
}
```

## Field Notes

| Field | Notes |
|---|---|
| `sessionId` | UUID, matches the JSONL filename (without extension) |
| `fullPath` | Absolute path to the `.jsonl` transcript file |
| `fileMtime` | File modification time as Unix epoch milliseconds |
| `firstPrompt` | Text of the first user message (may be truncated) |
| `summary` | Auto-generated summary of the session (may be empty) |
| `messageCount` | Total number of entries in the JSONL (not just user/assistant turns) |
| `created` | ISO 8601 timestamp of session creation |
| `modified` | ISO 8601 timestamp of last modification |
| `gitBranch` | Git branch at time of last index update |
| `projectPath` | Absolute path to the project root |
| `isSidechain` | Whether this session was created as a sidechain (subagent) |
| `customTitle` | User-chosen session name; **only present if renamed** |

## customTitle Timing

`customTitle` only appears in the sessions-index **after the session is indexed** (often after it ends or after a periodic re-index). For **active sessions**, read `custom-title` entries directly from the JSONL transcript file instead. The JSONL is the authoritative source during a session's lifetime.
