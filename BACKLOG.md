# cctop — Backlog

## Table & Columns
- [ ] **1.** Add `Files` column (count of files edited)
- [ ] **2.** Add `Agents` column (subagent count)
- [ ] **3.** Add `Errors` column (error count)
- [ ] **4.** Add `StopRsn` column ("done", "tool", "limit")
- [ ] **5.** Sparkline per session showing tool activity over time
- [ ] **6.** Group/collapse sessions by project directory
- [ ] **7.** Show full model name in `Model` column (e.g. `claude-sonnet-4-6` instead of truncated)
- [ ] **8.** Add `Started` column showing session start time
- [ ] **9.** Fix `Turns` count, count user messages instead of tool calls (a turn is a user-assistant exchange, not every tool invocation)

## Detail Panel
- [ ] **10.** The user message is not formatted markdown like the assistant message, make them consistent
- [ ] **11.** Replace `Static` with `RichLog`/`Markdown` widget for markdown rendering
- [x] **12.** Increase assistant message lines from 4 to 5
- [x] **13.** Wrap detail panel text at window width (with margin) instead of fixed 100 chars
- [ ] **14.** Parse system-injected user messages (e.g. `<task-notification>`) and display them nicely, show "Subagent completed: <summary>" instead of hiding them entirely
- [ ] **15.** Bug: detail panel still shows last selected session after all sessions end, should clear when no active sessions remain

## Debugging
- [ ] **16.** Debug mode, log all hook events with full JSON stdin to a file for troubleshooting

## Session Actions
- [ ] **17.** Investigate: can we kill a session from the dashboard?
- [ ] **18.** Add rename session action from the dashboard
- [x] **19.** Strong refresh: keybinding (e.g. Shift+R) and CLI flag (`cctop --reset`) that wipes `~/.cctop/`, re-scans all sessions from scratch

## Session Lifecycle
- [ ] **20.** Increase stale threshold beyond 5 minutes
- [x] **21.** Stale session cleanup via PID check instead of timeout heuristics
- [ ] **22.** Session history, persist ended session stats (tokens, cost, turns, duration) for later querying
- [ ] **23.** Fix branch showing "HEAD" for sessions in detached HEAD state, resolve to a meaningful name (tag, short SHA, or parent branch)

## UI & Theming
- [ ] **24.** Persist selected theme to disk so it survives restarts (config file)

## Packaging & Distribution
- [x] **25.** Rename package to `cctop`, plugin name, slash command, and callable as `cctop` from terminal
- [x] **26.** Create `~/bin/cctop` CLI entry point
- [x] **27.** Create a git repo and push to GitHub
- [ ] **28.** Create a demo/promo video with Remotion

## Commands
- [ ] **29.** `/register` slash command, if the current session is not tracked by cctop (e.g. plugin was installed after the session started, or a bug), manually register it by writing the session JSON into `~/.cctop/` so the poller picks it up. **Approach:** encode the current CWD to derive the project directory (`~/.claude/projects/<encoded>/`), scan it for `.jsonl` transcript files, cross-reference against existing `~/.cctop/*.json` to find untracked sessions, then identify the active one by matching `claude` process CWDs (via `ps` or `lsof`). No CLI command to list sessions exists, and Claude doesn't keep transcript files open as handles, so process CWD matching is the way to link PID → session.

## Health Check & Teammates
- [ ] **30.** Session health check warning: periodically compare `ps`-based Claude process count against cctop tracked sessions, show orange warning bar on mismatch. `cctop > grep` = stale (crashed) sessions, `grep > cctop` = undetected sessions. Validate PIDs to distinguish. See [`docs/prd-session-health-check.md`](docs/prd-session-health-check.md)
- [ ] **31.** Teammate detection & grouping: parse `--parent-session-id` from tmux-spawned teammate processes, group under parent session, add `Team` column showing teammate count. Exclude teammates from health check counts. See [`docs/prd-session-health-check.md`](docs/prd-session-health-check.md)
- [ ] **32.** Teammate drill-down: expandable rows for sessions with teammates, show agent name, color, status per teammate in indented sub-rows. See [`docs/prd-session-health-check.md`](docs/prd-session-health-check.md)

## New Frontiers
- [ ] **33.** Web frontend (Flask/FastAPI serving session-status JSON)
