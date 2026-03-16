# cctop, Claude Code Sessions Dashboard

A live terminal dashboard for monitoring all your Claude Code sessions at a glance. Like `htop`, but for Claude Code.

## Why

Power users run multiple Claude Code sessions simultaneously, one refactoring a module, another writing tests, a third researching an API. You end up tab-switching between terminals just to check "is it done yet?" or "is it stuck waiting for me?" There's no central place to see what's happening across sessions.

## What It Does

Installs a lightweight hook into Claude Code that tracks session activity in real time. A companion TUI dashboard (`cctop`) displays all active sessions in a single live-updating table:

- **Status**, see at a glance whether each session is idle (waiting for you), thinking, editing files, running commands, searching the web, or spawning subagents
- **Project & branch**, know which codebase and branch each session is working in
- **Context usage**, monitor how much of the context window has been consumed, so you can wrap up or start fresh before hitting limits
- **Tool count**, track how many tool calls a session has made
- **Model**, which Claude model each session is using
- **Last messages**, peek at the most recent user prompt and Claude response without switching terminals

Sessions that go quiet for 1+ hour are marked stale. Sessions that end clean up after themselves automatically.

## Who It's For

Anyone running more than one Claude Code session at a time, or anyone who wants a quick overview of what's happening without context-switching into each terminal.

## Project Structure

- `plugin/`, distribution files (only this directory gets installed)
  - `plugin/scripts/cctop-hook.sh`, hook handler, writes `~/.cctop/<session-id>.json`
  - `plugin/scripts/cctop_dashboard.py`, Textual TUI app (run with `uv run --script`)
  - `plugin/scripts/cctop-poller.py`, background transcript poller
  - `plugin/scripts/launch-cctop.sh`, convenience launcher
  - `plugin/hooks/hooks.json`, registers the hook for 7 events
  - `plugin/.claude-plugin/plugin.json`, plugin manifest
- `.claude-plugin/marketplace.json`, local marketplace manifest (points to `./plugin/`)
- `tests/test_cctop_dashboard.py`, TUI tests
- `install.sh`, reinstalls plugin into Claude's cache
- `plans/`, gitignored, PRDs and design docs (never commit these)
- `BACKLOG.md`, numbered feature backlog with completion tracking

## Reference Docs

The `reference/` directory contains Claude Code internals documentation, split by topic. **Read these on-demand**, don't load them all upfront, just read the one relevant to your current task:

| File | When to read |
|---|---|
| `reference/hooks-api.md` | Writing or debugging hooks, events, stdin fields, output format |
| `reference/transcript-format.md` | Parsing JSONL transcripts, entry types, field shapes, path encoding |
| `reference/sessions-index.md` | Reading the sessions index, schema, customTitle timing |
| `reference/plugin-system.md` | Plugin install/dev workflow, manifests, cache, gotchas |
| `reference/session-data-files.md` | Tool counts and session-status JSON files |

## Installing After Changes

The plugin runs from a **copy** in `~/.claude/plugins/cache/`, not from this directory.
After editing any file under `plugin/`, you **must** reinstall:

```bash
./install.sh --dev
```

**Always run `./install.sh --dev` after modifying any plugin file** (hooks, scripts, manifests). New Claude sessions will pick up the changes; existing sessions keep the old version.

## Writing Style

- Use commas instead of emdashes (—) in prose

## Security

Before committing, run a basic security audit on staged changes:
- No hardcoded secrets, API keys, tokens, or passwords
- No personal information (real names, private emails, internal hostnames, private IPs)
- No SentinelOne internal references (GHE URLs, internal tooling, team names)
- TruffleHog runs as a pre-commit hook, but also manually sanity-check diffs for anything it might miss

## Branching

- **ALWAYS use a worktree when starting work on a new branch.** Use the `EnterWorktree` tool to create an isolated worktree before making any changes. Do NOT just create a branch with `git checkout -b` or `git switch -c` in the main working directory.
- This keeps the main working directory clean on `main` and avoids conflicts with other sessions.
- When the work is done and merged, exit the worktree with `ExitWorktree`.

## PR Groups Workflow (MANDATORY)

This project uses a structured PR-groups workflow defined in `plans/pr-groups.md`. **This workflow is not optional.** When asked to work on a PR group or backlog items:

1. Read `plans/pr-groups.md` first to understand the grouping and dependencies
2. Follow the workflow steps exactly as written in that file (branch → plan → implement → test → push & PR)
3. Use `EnterWorktree` to create the worktree, do not skip this step
4. Enter plan mode before implementing, get user approval before writing code
5. Make granular commits (one per logical change)
6. After merge, update both `BACKLOG.md` (mark items done) and `plans/pr-groups.md` (check off the PR group)

## Commits

- Split uncommitted changes into logical, self-contained commits (e.g. separate feature code, tests, docs, backlog updates)
- When moving or renaming files, update all references in other files (BACKLOG.md links, CLAUDE.md structure, README, etc.) in the same or immediately following commit

## Docs Hygiene

When making changes that affect user-visible behavior (new features, changed columns, new keybindings, install steps, usage), always check that `README.md`, `BACKLOG.md`, and `CONTRIBUTING.md` are updated to match.
