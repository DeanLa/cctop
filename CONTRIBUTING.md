# Contributing to cctop

## Dev Setup

```bash
git clone https://github.com/DeanLa/cctop.git
cd cctop
./install.sh --dev
```

`--dev` symlinks to your local repo so changes take effect immediately (after reinstalling the plugin). `--prod` copies files into the plugin cache from the GitHub repo.

After editing any file under `plugin/`, reinstall:

```bash
./install.sh --dev
```

New Claude Code sessions pick up the changes; existing sessions keep the old version.

## Architecture

Three components, cleanly separated:

```
Hook (event-driven)  ──► ~/.cctop/<id>.json ◄── Poller (1s loop)
                                │
                         Dashboard (read-only)
```

**Hook** (`plugin/scripts/cctop-hook.sh`) — fires on 7 Claude Code events (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop, SessionEnd). Writes status, current tool, timestamps, tool count, and transcript path. Stays fast (<50ms).

**Poller** (`plugin/scripts/cctop-poller.py`) — background process that incrementally reads JSONL transcripts using byte offsets. For Claude, it enriches hook-written sessions with title, slug, model, git branch, token usage, messages, turns, files edited, subagent count, errors, and stop reason. For Codex, it auto-discovers recent sessions from `~/.codex/session_index.jsonl`, normalizes them into the same `~/.cctop/` contract, and parses token/message/tool data from Codex transcripts.

**Dashboard** (`plugin/scripts/cctop_dashboard.py`) — pure read-only Textual TUI. Reads `~/.cctop/` JSON files only. No JSONL parsing, no writes. Refreshes every 500ms.

The `~/.cctop/` directory is the API contract between all three components, regardless of provider.

## Project Structure

```
plugin/                        # Distribution files — only this directory gets installed
  scripts/
    cctop-hook.sh              # Hook handler — writes per-session JSON to ~/.cctop/
    cctop-poller.py            # Background poller — incremental JSONL reader
    cctop_dashboard.py         # Textual TUI dashboard (read-only)
    launch-cctop.sh            # Convenience launcher (poller + dashboard)
  hooks/
    hooks.json                 # Registers the hook for 7 events
  .claude-plugin/
    plugin.json                # Plugin manifest
bin/
  cctop                        # CLI entry point
tests/
  test_cctop_dashboard.py      # Smoke tests (unit + headless TUI)
install.sh                     # Install/reinstall into Claude's plugin cache
```

## Testing

```bash
PYTHONPATH=plugin/scripts uv run --with textual --with pytest --with pytest-asyncio -- python -m pytest tests/ -v
```

Runs unit tests for helper functions (token formatting, relative time) and headless TUI integration tests (empty state, session rendering, sort picker, detail panel).

## Reference Docs

The `reference/` directory contains Claude Code internals documentation, split by topic. Read these on-demand — just the one relevant to your current task:

| File | When to read |
|---|---|
| `reference/hooks-api.md` | Writing or debugging hooks — events, stdin fields, output format |
| `reference/transcript-format.md` | Parsing JSONL transcripts — entry types, field shapes, path encoding |
| `reference/sessions-index.md` | Reading the sessions index — schema, customTitle timing |
| `reference/plugin-system.md` | Plugin install/dev workflow — manifests, cache, gotchas |
| `reference/session-data-files.md` | Tool counts and session-status JSON files |
