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

**Poller** (`plugin/scripts/cctop-poller.py`) — background process that incrementally reads JSONL transcripts using byte offsets. Extracts custom title, slug, model, git branch, token usage, messages, turns, files edited, subagent count, errors, and stop reason. Also aggregates subagent transcript tokens.

**Dashboard** (`plugin/scripts/cctop_dashboard.py`) — Textual TUI. Reads `~/.cctop/` JSON files only, no JSONL parsing. Refreshes every 500ms. Read-only except for renaming: pressing `F2` shells out to `cc-send` to retitle a live session.

**cc-send** (`plugin/scripts/cc-send`) — standalone, stdlib-only tool that speaks Claude Code's per-session messaging protocol over the Unix domain socket each session binds (2.1.224+). Resolves a target session from the roster in `~/.claude/sessions/`, then either sends a message or a `control`/`rename` frame. The dashboard uses `--rename`; a socket rename makes Claude append a `custom-title` entry to the transcript, which the poller then surfaces back into the Name column.

The `~/.cctop/` directory is the API contract between the hook, poller, and dashboard.

## Project Structure

```
plugin/                        # Distribution files — only this directory gets installed
  scripts/
    cctop-hook.sh              # Hook handler — writes per-session JSON to ~/.cctop/
    cctop-poller.py            # Background poller — incremental JSONL reader
    cctop_dashboard.py         # Textual TUI dashboard
    cc-send                    # Peer messaging + live rename over session sockets
    launch-cctop.sh            # Convenience launcher (poller + dashboard)
  hooks/
    hooks.json                 # Registers the hook for 7 events
  .claude-plugin/
    plugin.json                # Plugin manifest
bin/
  cctop                        # CLI entry point
tests/
  test_cctop_dashboard.py      # Smoke tests (unit + headless TUI)
  test_cctop_poller.py         # Poller unit tests (JSONL parsing, git helpers)
  test_cc_send.py              # cc-send unit tests (frames, resolution, delivery)
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
