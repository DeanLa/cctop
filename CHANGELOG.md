# Changelog

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
