---
description: Register the current session with cctop when the hook missed it
allowed-tools: Bash, Read, Write, Glob
---

Register this Claude Code session with cctop so the dashboard can track it.

Follow these steps exactly. Do not prompt the user for input at any step.

## Step 1: Find the project directory

Encode the current working directory by replacing every `/` with `-`.
For example, `/Users/dean/code/project` becomes `-Users-dean-code-project`.

Construct the path: `~/.claude/projects/<encoded>/`

If that directory does not exist, tell the user:
> Could not find a Claude Code project directory for this session's working directory. This session may not have a transcript yet.

Then stop.

## Step 2: Find the current session's transcript

List all `*.jsonl` files in that project directory, sorted by modification time (most recent first). Use `ls -t`.

The most recently modified `.jsonl` file is the current session's transcript (Claude is actively writing to it right now).

Extract the session ID from the filename (it is the stem, without the `.jsonl` extension). It will be a UUID.

If no `.jsonl` files exist, tell the user:
> No transcript files found. This session may not have started recording yet.

Then stop.

## Step 3: Check if already tracked

Check whether `~/.cctop/<session-id>.json` already exists.

If it does, tell the user:
> This session is already tracked by cctop (found `~/.cctop/<session-id>.json`). No action needed.

Then stop.

## Step 4: Write the session JSON

First, ensure `~/.cctop/` exists (create it if not).

Get the transcript file's modification time as an ISO 8601 UTC timestamp for `started_at`.
Get the current time as an ISO 8601 UTC timestamp for `last_activity`.

Write `~/.cctop/<session-id>.json` with this exact structure:

```json
{
  "session_id": "<uuid from filename>",
  "cwd": "<current working directory>",
  "status": "idle",
  "current_tool": "",
  "last_event": "manual_register",
  "last_activity": "<current time, ISO 8601 UTC>",
  "started_at": "<transcript file mtime, ISO 8601 UTC>",
  "pid": <$PPID>,
  "transcript_path": "<absolute path to the .jsonl file>",
  "model": "",
  "tool_count": 0,
  "running_agents": 0
}
```

The `transcript_path` field MUST be a non-empty absolute path. The poller skips sessions without it.

## Step 5: Report success

Tell the user:
> Registered session `<session-id>` with cctop. The dashboard will pick it up within a few seconds.
