# Changelog

## v0.4.0 — 2026-04-04

### Group-By View, Organize Sessions Your Way
- Group sessions by any column (project, status, model, branch, etc.) with collapsible section headers
- Each group shows a count of sessions, collapse or expand groups to focus on what matters
- Toggle grouping via the `g` key, works alongside existing sort and column picker

### Cleaner Chat History
- System-injected messages (tool results, context reminders) are now parsed and displayed cleanly in the detail panel instead of showing as raw user messages

## v0.3.0 — 2026-04-02

### Effort & Cost, See What Each Session Is Spending
- **Effort column**: shows the active effort level per session, extracted from `/effort` commands in the transcript, with fallback to your global default
- **Cost column**: estimates session cost from cumulative token usage (main + subagent) using Anthropic's published pricing
- Both columns hidden by default, toggle them on via the column picker (`C`)
- Effort and cost also appear in the detail panel

### Smoother Upgrades
- Installing a new version no longer wipes running sessions from the dashboard
- Upgrades now use in-place plugin update instead of remove-and-reinstall

## v0.2.0 — 2026-03-30

### Status Detection, Know What Every Session Is Actually Doing
- Granular, tool-aware status labels: `editing`, `searching`, `browsing`, `running cmd`, `planning`, `reviewing`, `researching` instead of a generic "working" blob
- MCP tool detection, sessions using MCP servers show as `mcp:<server>`
- Subagent-type awareness: distinguish code-review, explore, and research agents
- Smart idle states: `awaiting plan`, `needs input`, `awaiting permission`, so you know exactly what each session is waiting for
- Urgency-based color coding: orange for "needs you now", blue for "waiting on plan", green for "all good"
- Error surface: rate limits, auth failures, and tool failures show in red immediately
- 5 new hook events registered: Notification, StopFailure, PostToolUseFailure, SubagentStart, CwdChanged

### Detail Panel, A Real Session Inspector
- Split layout: side-by-side chat view + session metadata panel with a dedicated status bar
- Key-value metadata table: session ID, full model name, project path, token breakdown
- Errors column now includes tool failures, not just transcript errors

### Column Picker, Your Dashboard, Your Layout
- Interactive column picker (`c`): show, hide, and reorder any of the 16 columns
- Sort by any column (`s`), hide columns you don't need (`h`), restore all with `C`
- Renamed "slug" column to the clearer "Name"

### Config File, Preferences That Stick
- New `~/.cctop/config.toml` persists preferences across restarts
- Saved state: theme, sort column, hidden columns, column order, column widths
- Sensible defaults: new columns start hidden so your layout doesn't shift on update

### Session Actions, Control Sessions Without Switching Terminals
- Kill session (`k`): terminate a running Claude session directly from the dashboard
- Tmux attach (`a`): jump straight into a session's tmux window
- Tmux tab title: launcher automatically sets the tab name to "cctop"

### Under the Hood
- Full async worker + reactive state refactor for smoother, more responsive updates
- Worktree sessions now resolve the correct project path and branch
- Debug plugin (`cctop-debug`) for hook event logging during development
- Updated demo GIF

## v0.1.0 — 2026-03-16

Initial public release.

### Dashboard
- Live-updating TUI with 16 columns: name, project, branch, status, model, context %, tokens, tools, files edited, running agents, errors, turns, stop reason, duration, start time, last activity
- Detail panel showing full path, token breakdown, last user prompt, and Claude's response
- Sort picker with 10 modes (activity, name, status, duration, turns, tokens, tools, files, agents, errors)
- Session health check bar comparing tracked sessions against running Claude processes

### Session Tracking
- Hook-based event capture (7 Claude Code events) via `jq` for fast JSON extraction
- Background poller with incremental JSONL transcript parsing (byte-offset seeking)
- Subagent token aggregation across agent transcripts
- Git worktree detection with branch resolution for detached HEAD states
- PID-based dead session cleanup with staleness fallback

### Install
- One-line install via `curl | bash`
- `cctop` CLI entry point in `~/.local/bin/`
- Claude Code plugin with automatic hook registration
