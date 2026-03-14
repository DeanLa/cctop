# cctop — Backlog

## Table & Columns
- [ ] Add `Files` column (count of files edited)
- [ ] Add `Agents` column (subagent count)
- [ ] Add `Errors` column (error count)
- [ ] Add `StopRsn` column ("done", "tool", "limit")
- [ ] Sparkline per session showing tool activity over time
- [ ] Group/collapse sessions by project directory

## Detail Panel
- [ ] The user message is not formatted markdown like the assistant message — make them consistent
- [ ] Replace `Static` with `RichLog`/`Markdown` widget for markdown rendering
- [ ] Increase assistant message lines from 4 to 5
- [ ] Wrap detail panel text at window width (with margin) instead of fixed 100 chars
- [ ] Parse system-injected user messages (e.g. `<task-notification>`) and display them nicely — show "Subagent completed: <summary>" instead of hiding them entirely

## Debugging
- [ ] Debug mode — log all hook events with full JSON stdin to a file for troubleshooting

## Session Actions
- [ ] Investigate: can we kill a session from the dashboard?
- [ ] Add rename session action from the dashboard

## Session Lifecycle
- [ ] Increase stale threshold beyond 5 minutes
- [ ] Stale session cleanup via PID check instead of timeout heuristics
- [ ] Session history — persist ended session stats (tokens, cost, turns, duration) for later querying

## Packaging & Distribution
- [x] Rename package to `cctop` — plugin name, slash command, and callable as `cctop` from terminal
- [x] Create `~/bin/cctop` CLI entry point
- [ ] Create a git repo and push to GitHub
- [ ] Create a demo/promo video with Remotion

## New Frontends
- [ ] Web frontend (Flask/FastAPI serving session-status JSON)
