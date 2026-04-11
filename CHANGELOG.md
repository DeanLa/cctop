# Changelog

## v0.5.2 — 2026-04-11

### Keybinding UX Overhaul

- **Help overlay** (`?`): categorized keybinding reference, dismiss with `?`, `esc`, or `q`
- **Redesigned footer**: grouped key badges with reverse styling, context-sensitive keys (`x Fold` only visible when grouped)
- **Wrap-around navigation**: `↑`/`↓` wrap from last row to first and vice versa
- **Theme picker** (`t`): direct access to Textual's built-in theme picker

### Group View Fixes

- **Collapse from any row** (`x`): toggling collapse now works from any session row inside a group, not just the group header
- **Group headers are navigable**: collapsed groups can be selected and expanded via `Enter` or `x`

## v0.5.1 — 2026-04-06

### Bug Fixes

- **Worktree detection on resume**: resuming a session into a worktree (`claude -c`/`-r`) now correctly re-derives the git branch, worktree indicator, and project name. Previously these stayed stale after CwdChanged events.
- **Context window denominator**: the Ctx% column now uses 1M as the denominator for extended context sessions (`[1m]` models) instead of always dividing by 200k. The detail panel shows the fraction (e.g. `145k/1M ctx`).
- **AskUserQuestion status**: when Claude asks you a question via AskUserQuestion, the dashboard now shows "awaiting input" (red) instead of "awaiting permission" (orange), so you can tell it apart from actual tool-approval prompts.

## v0.5.0 — 2026-04-04

### Detail Panel, The Full Story Behind Every Status

- **Status context**: the detail panel now shows what's behind the status label, the file being edited, the command being run, the question waiting for your answer, the error details, all pulled from hook data so you don't have to switch terminals to find out
- **Activity log**: timestamped feed of recent events (tool calls, messages, status changes) for the selected session, see what happened without scrolling through the transcript
- **Live updates**: the detail panel now refreshes automatically when new poller data arrives, no more navigating away and back to see the latest

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
