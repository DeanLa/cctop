# cctop — Backlog

Items tagged with `PR-X` are assigned to a PR group, see [plans/pr-groups.md](plans/pr-groups.md) for details.
When a PR merges, mark its items `[x]` and append ` — PR-X`.

## Table & Columns
- [x] **1.** Add `Files` column (count of files edited) `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [x] **2.** Add `Agents` column (subagent count) `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [x] **3.** Add `Errors` column (error count) `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [x] **4.** Add `StopRsn` column ("done", "tool", "limit") `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [ ] **5.** Sparkline per session showing tool activity over time `PR-L`
- [ ] **6.** Group/collapse sessions by project directory `PR-M`
- [x] **7.** Show full model name in `Model` column (e.g. `claude-sonnet-4-6` instead of truncated) `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [x] **8.** Add `Started` column showing session start time `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [ ] **9.** Fix `Turns` count, count user messages instead of tool calls (a turn is a user-assistant exchange, not every tool invocation) `PR-C`

## Detail Panel
- [x] **10.** The user message is not formatted markdown like the assistant message, make them consistent `PR-B`
- [x] **11.** DRY message rendering: both user and assistant messages go through shared `_render_message` helper `PR-B`
- [x] **12.** Increase assistant message lines from 4 to 5
- [x] **13.** Wrap detail panel text at window width (with margin) instead of fixed 100 chars
- [ ] **14.** Parse system-injected user messages (e.g. `<task-notification>`) and display them nicely, show "Subagent completed: <summary>" instead of hiding them entirely `PR-H`
- [x] **15.** Bug: detail panel still shows last selected session after all sessions end, should clear when no active sessions remain `PR-B`

## Debugging
- [ ] **16.** Debug mode, log all hook events with full JSON stdin to a file for troubleshooting `PR-D`

## Session Actions
- [ ] **17.** Investigate: can we kill a session from the dashboard? `PR-F`
- [ ] **18.** Add rename session action from the dashboard `PR-F`
- [x] **19.** Strong refresh: keybinding (e.g. Shift+R) and CLI flag (`cctop --reset`) that wipes `~/.cctop/`, re-scans all sessions from scratch

## Session Lifecycle
- [x] **20.** Increase stale threshold beyond 5 minutes — PR-E (#3)
- [x] **21.** Stale session cleanup via PID check instead of timeout heuristics
- [ ] **22.** Session history, persist ended session stats (tokens, cost, turns, duration) for later querying `PR-I`
- [x] **23.** Fix branch showing "HEAD" for sessions in detached HEAD state, resolve to a meaningful name (tag, short SHA, or parent branch) — PR-E (#3)

## UI & Theming
- [ ] **24.** Persist selected theme to disk so it survives restarts (config file) `PR-J`

## Packaging & Distribution
- [x] **25.** Rename package to `cctop`, plugin name, slash command, and callable as `cctop` from terminal
- [x] **26.** Create `~/bin/cctop` CLI entry point
- [x] **27.** Create a git repo and push to GitHub
- [ ] **28.** Create a demo/promo video with Remotion *(standalone)*

## Commands
- [ ] **29.** `/register` slash command, if the current session is not tracked by cctop (e.g. plugin was installed after the session started, or a bug), manually register it by writing the session JSON into `~/.cctop/` so the poller picks it up. **Approach:** encode the current CWD to derive the project directory (`~/.claude/projects/<encoded>/`), scan it for `.jsonl` transcript files, cross-reference against existing `~/.cctop/*.json` to find untracked sessions, then identify the active one by matching `claude` process CWDs (via `ps` or `lsof`). No CLI command to list sessions exists, and Claude doesn't keep transcript files open as handles, so process CWD matching is the way to link PID → session. `PR-G`

## Health Check & Teammates
- [x] **30.** Session health check warning: periodically compare `ps`-based Claude process count against cctop tracked sessions, show orange warning bar on mismatch. `cctop > grep` = stale (crashed) sessions, `grep > cctop` = undetected sessions. Validate PIDs to distinguish. See [`plans/prd-session-health-check.md`](plans/prd-session-health-check.md) `PR-K`
- [ ] **31.** Teammate detection & grouping: parse `--parent-session-id` from tmux-spawned teammate processes, group under parent session, add `Team` column showing teammate count. Exclude teammates from health check counts. See [`plans/prd-session-health-check.md`](plans/prd-session-health-check.md) `PR-K`
- [ ] **32.** Teammate drill-down: expandable rows for sessions with teammates, show agent name, color, status per teammate in indented sub-rows. See [`plans/prd-session-health-check.md`](plans/prd-session-health-check.md) `PR-K`

## New Frontiers
- [ ] **33.** Web frontend (Flask/FastAPI serving session-status JSON) *(standalone)*
