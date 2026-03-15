# PRD: Session Health Check & Teammate Detection

## Problem

cctop tracks sessions via hook-written JSON files in `~/.cctop/`. But there's no way to know if the tracked sessions match reality. Sessions can crash without cleanup, or run without ever being detected. Users need confidence that what cctop shows is the full, accurate picture.

## Goals

1. Detect mismatches between cctop's tracked sessions and actual running Claude processes
2. Surface an orange warning line in the dashboard when counts don't match
3. Detect teammate (team agent) sessions and group them under their parent
4. Add a "Teammates" column to the parent session row

## Discovery Summary

Empirical testing on macOS revealed the following:

### Process Identification

| What | Process signature | Separate PID? | Separate cctop entry? |
|------|-------------------|---------------|----------------------|
| Interactive session | `claude` or `claude -r` in a TTY | Yes | Yes |
| Subagent (Agent tool) | Runs inside parent PID | No | No |
| Teammate (non-tmux) | Runs inside parent PID | No | No |
| Teammate (tmux) | Versioned binary with `--agent-name --parent-session-id` flags | Yes | Yes |
| Claude Desktop app | `/Applications/Claude.app/...` | Yes (many) | No |
| MCP servers | `uv tool uvx mcp-atlassian` etc. | Yes (child of session) | No |

### Key Findings

- **One `claude` PID = one user-facing session**, always true for non-tmux environments
- **Claude Code detects tmux** and spawns real separate-process teammates in new tmux panes; without tmux it falls back to in-process
- **Tmux teammates** run as the versioned binary (`~/.local/share/claude/versions/X.Y.Z`) with distinguishing flags:
  - `--agent-id <name>@<team>`
  - `--agent-name <name>`
  - `--team-name <team>`
  - `--parent-session-id <uuid>`
  - `--agent-color <color>`
  - `--model <model>`
- **Tmux teammates get their own TTY** (each in a tmux pane)
- **Tmux teammates fire hooks** and create their own `~/.cctop/<session-id>.json` files
- Subagents and non-tmux teammates are invisible to both `ps` and cctop, they are a non-issue

## Feature 1: Health Check Warning

### Counting Logic

**grep count** (ground truth of running processes):
```
ps aux | grep claude  →  filter to TTY-attached `claude` or `claude -r` commands
```
Exclude:
- Claude Desktop app processes (`/Applications/Claude.app/...`)
- MCP servers, caffeinate, shell children
- Tmux teammate processes (versioned binary with `--parent-session-id`)

**cctop count** (tracked sessions):
```
count of ~/.cctop/*.json files, excluding:
- *.poller.json files
- files where session is identified as a teammate (see Feature 2)
```

### Mismatch Cases

| Case | Meaning | Warning color | Message |
|------|---------|---------------|---------|
| cctop == grep | All good | None | — |
| cctop > grep | Stale sessions: process crashed, `SessionEnd` hook never fired, JSON lingers | Orange | `N stale session(s) detected` |
| grep > cctop | Undetected sessions: started before plugin install, resumed with `-r`, stale plugin cache | Orange | `N untracked session(s) detected` |

### Stale Session Resolution (cctop > grep)

For each cctop session file, check if its `pid` field corresponds to a running process:
- PID alive → session is real
- PID dead → session is stale, mark accordingly

This is more reliable than the current 5-minute timeout heuristic (backlog item #21, already checked off).

### Undetected Session Resolution (grep > cctop)

No automatic fix possible, you can't inject hooks into a running session. Options:
- Show warning: "N untracked session(s) detected"
- Reference `/register` slash command (backlog item #29) for manual registration

### Where to Run

The periodic check should run in the dashboard's refresh cycle (the Textual timer that already refreshes sessions). On each tick:
1. Read `~/.cctop/*.json` files (already done)
2. Run `ps` to get claude process list (new)
3. Compare counts (excluding teammates from both sides)
4. If mismatch, show orange warning bar at top or bottom of session table

### Performance

`ps aux` is cheap (~5ms). Running it every refresh cycle (1-2s) is fine.

## Feature 2: Teammate Detection & Grouping

### Detection via Process Args

Scan `ps` output for processes matching:
```
--parent-session-id <uuid>
```
Extract:
- `--parent-session-id` → links to parent session
- `--agent-name` → display name
- `--agent-color` → for future UI use
- `--team-name` → team identifier

### Detection via cctop Session Files

Teammate sessions can also be identified from their `~/.cctop/*.json` files by:
- Model field showing `<synthetic>` or the raw model without `[1m]` suffix
- Very low tool count (often 0)
- Same `cwd` as parent
- Started within seconds of each other

However, **process args are the reliable signal**. The session file fields are heuristic.

### Parent Grouping

For each teammate process:
1. Extract `--parent-session-id`
2. Match to a cctop session file by session_id
3. Count teammates per parent

### New Column: Teammates

Add a `Team` column to the dashboard table on the parent session row:
- Empty if no teammates
- Shows count: `5` or `3/5` (active/total)
- Color-coded if teammates are active

### Future: Drill-Down

Not in scope for this iteration. Future feature: pressing Enter/arrow on a session row with teammates expands to show individual teammate rows indented below the parent, with their agent name, status, and color.

## Implementation Phases

### Phase 1: Health Check Warning
1. Add `ps`-based process counting to the dashboard refresh
2. Filter out Desktop app, MCP, teammates from grep count
3. Filter out teammate sessions from cctop count
4. Compare and show orange warning line on mismatch
5. For cctop > grep: validate PIDs, identify stale sessions

### Phase 2: Teammate Detection
1. Parse `ps` output for `--parent-session-id` processes
2. Group teammates under parent session
3. Add `Team` column to the table
4. Exclude teammates from health check counts

### Phase 3 (Future): Teammate Drill-Down
1. Expandable rows for sessions with teammates
2. Show agent name, color, status per teammate
3. Teammate detail in the detail panel

## Edge Cases

- **Resumed sessions (`claude -r`)**: May or may not fire `SessionStart` hook. If not, grep > cctop.
- **Very short-lived sessions**: `claude --help`, `claude -p "quick question"`, may appear briefly in grep but never create cctop entries. Could cause false positives. Consider filtering by session uptime (ignore processes < 5 seconds old).
- **Multiple tmux servers**: Teammates could be in different tmux sessions. Doesn't affect detection since we use `ps`, not tmux commands.
- **PID reuse**: After a crash, a stale cctop file's PID could be reused by an unrelated process. Validate by checking if the process command is actually `claude`.
- **Non-tmux teammate sessions in cctop**: If Claude Code changes behavior and starts creating cctop entries for in-process teammates, we'd need to handle that. Currently non-tmux teammates are invisible.