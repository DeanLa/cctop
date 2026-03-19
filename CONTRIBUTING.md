# Contributing to cctop

## Dev Setup

```bash
git clone https://github.com/DeanLa/cctop.git
cd cctop
./install.sh --dev          # macOS/Linux
# or
.\install.ps1 -Mode dev     # Windows PowerShell
```

`--dev` symlinks to your local repo so changes take effect immediately (after reinstalling the plugin). `--prod` copies files into the plugin cache from the GitHub repo.

After editing any file under `plugin/`, reinstall:

```bash
./install.sh --dev
```

New Claude Code sessions pick up the changes; existing sessions keep the old version. Copilot CLI sessions are discovered automatically by the poller.

## Architecture

Four components, cleanly separated:

```
Claude Code Hook (event-driven) ──► ~/.cctop/<id>.json
                                         │
Copilot CLI Scanner ──────────────►      │  ◄── Poller (1s loop)
  (scans ~/.copilot/session-state/)      │           │
                                   <id>.json    <id>.poller.json
                                         │           │
                                  Dashboard (read-only, merges both)
```

**Claude Code Hook** (`plugin/scripts/cctop-hook.sh` / `.ps1`) — fires on 7 Claude Code events (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop, SessionEnd). Writes status, current tool, timestamps, tool count, and transcript path. Stays fast (<50ms). Bash on Unix, PowerShell on Windows.

**Copilot CLI Scanner** (built into the poller) — discovers Copilot CLI sessions by scanning `~/.copilot/session-state/` for `inuse.*.lock` files. Parses `events.jsonl` (typed events: `session.start`, `assistant.usage`, `tool.execution_*`, `subagent.*`) and `workspace.yaml` for metadata.

**Poller** (`plugin/scripts/cctop-poller.py`) — background process that handles both Claude Code (JSONL transcripts) and Copilot CLI (events.jsonl) incrementally. Extracts slug, model, git branch, token usage, messages, turns, files edited, subagent count, errors, and stop reason.

**Dashboard** (`plugin/scripts/cctop_dashboard.py`) — pure read-only Textual TUI. Reads `~/.cctop/` JSON files only. Shows a "Client" column (CC for Claude Code, GH for Copilot CLI). Refreshes every 500ms.

The `~/.cctop/` directory is the API contract between all components. Each session has a `client` field ("copilot" for Copilot CLI, empty/absent for Claude Code).

## Project Structure

```
plugin/                        # Distribution files — only this directory gets installed
  scripts/
    cctop-hook.sh              # Claude Code hook (bash, Unix)
    cctop-hook.ps1             # Claude Code hook (PowerShell, Windows)
    cctop-poller.py            # Background poller — Claude Code + Copilot CLI
    cctop_dashboard.py         # Textual TUI dashboard (read-only)
    launch-cctop.sh            # Convenience launcher (bash, Unix)
    launch-cctop.ps1           # Convenience launcher (PowerShell, Windows)
  hooks/
    hooks.json                 # Registers the hook for 7 Claude Code events
  .claude-plugin/
    plugin.json                # Plugin manifest
bin/
  cctop                        # CLI entry point (Unix)
tests/
  test_cctop_dashboard.py      # Dashboard tests (unit + headless TUI)
  test_cctop_poller.py         # Poller tests (Claude + Copilot parsing)
install.sh                     # Install script (bash, macOS/Linux)
install.ps1                    # Install script (PowerShell, Windows)
```

## Testing

```bash
PYTHONPATH=plugin/scripts uv run --with textual --with pytest --with pytest-asyncio -- python -m pytest tests/ -v
```

Tests cover: Claude Code JSONL parsing, Copilot CLI events.jsonl parsing, session discovery, model name formatting (Claude/GPT/Gemini), cross-platform PID detection, and headless TUI integration.

## Reference Docs

The `reference/` directory contains Claude Code internals documentation, split by topic. Read these on-demand — just the one relevant to your current task:

| File | When to read |
|---|---|
| `reference/hooks-api.md` | Writing or debugging hooks — events, stdin fields, output format |
| `reference/transcript-format.md` | Parsing JSONL transcripts — entry types, field shapes, path encoding |
| `reference/sessions-index.md` | Reading the sessions index — schema, customTitle timing |
| `reference/plugin-system.md` | Plugin install/dev workflow — manifests, cache, gotchas |
| `reference/session-data-files.md` | Tool counts and session-status JSON files |
