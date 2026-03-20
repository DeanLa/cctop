# Copilot Instructions for cctop

## What This Is

cctop is a live terminal dashboard for monitoring Claude Code and Copilot CLI sessions — like `htop` for AI coding agents. Works on Linux, macOS, and Windows.

## Architecture

```
Claude Code Hook (event-driven)  ──► ~/.cctop/<id>.json
                                          │
Copilot CLI Scanner (poller-based) ──►    │  ◄── Poller (1s loop)
  scans ~/.copilot/session-state/         │           │
                                    <id>.json    <id>.poller.json
                                          │           │
                                   Dashboard (read-only, merges both)
```

**Three data sources, one output format:**

- **Claude Code Hook** (`plugin/scripts/cctop-hook.sh` / `.ps1`) — Bash/PowerShell script fired on 7 Claude Code events. Writes status, current tool, timestamps, tool count. Must stay fast (<50ms). Requires `jq` on Unix.
- **Copilot CLI Scanner** (built into the poller) — Discovers sessions by scanning `~/.copilot/session-state/` for `inuse.*.lock` files. Parses `events.jsonl` and `workspace.yaml`. No plugin install needed.
- **Poller** (`plugin/scripts/cctop-poller.py`) — Background Python process. For Claude Code: incrementally reads JSONL transcripts. For Copilot CLI: handles discovery and events.jsonl parsing. Writes to `<id>.poller.json`.
- **Dashboard** (`plugin/scripts/cctop_dashboard.py`) — Read-only Textual TUI. Merges both JSON files per session. Shows a "Client" column (CC/GH).

The two-file split eliminates write races. The dashboard merges at read time.

## Build & Test

No build step. Python scripts use [uv](https://docs.astral.sh/uv/) inline script dependencies (`# /// script` headers).

**Run all tests:**
```bash
PYTHONPATH=plugin/scripts uv run --with textual --with pytest --with pytest-asyncio -- python -m pytest tests/ -v
```

**Run a single test file:**
```bash
PYTHONPATH=plugin/scripts uv run --with textual --with pytest --with pytest-asyncio -- python -m pytest tests/test_cctop_poller.py -v
```

**Run a single test:**
```bash
PYTHONPATH=plugin/scripts uv run --with textual --with pytest --with pytest-asyncio -- python -m pytest tests/test_cctop_poller.py::TestParseCopilotEvents::test_user_message_counts_turns -v
```

Tests cover: Claude Code JSONL parsing, Copilot CLI events.jsonl parsing, session discovery, model name formatting (Claude/GPT/Gemini), cross-platform PID detection, headless TUI integration.

## Installing After Changes

The plugin runs from a **copy** in `~/.claude/plugins/cache/`, not from this repo. After editing any file under `plugin/`, you must reinstall:

```bash
./install.sh --dev      # macOS/Linux
.\install.ps1 -Mode dev  # Windows PowerShell
```

New Claude Code sessions pick up changes; existing sessions keep the old version. Copilot CLI sessions are discovered by the poller automatically.

## Key Conventions

- **Python scripts use `uv run --script`** with inline PEP 723 dependency metadata — no `requirements.txt` or `pyproject.toml`
- **Cross-platform**: bash scripts have PowerShell equivalents. PID detection uses `os.kill` on Unix, `ctypes`/`tasklist` on Windows.
- **Reference docs are read on-demand** — the `reference/` directory documents Claude Code internals. Read only the one relevant to your current task.
- **Writing style**: use commas instead of emdashes (—) in prose
- **Security**: no hardcoded secrets, PII, or internal references. TruffleHog runs as a pre-commit hook.
- **`~/.cctop/` is the API contract** between all components — if you change the JSON schema, update hook, poller, and dashboard.
- **`client` field**: session JSON includes `"client": "copilot"` for Copilot CLI sessions. Missing/empty = Claude Code.

## Releasing

```bash
./release.sh bump <version>   # Updates plugin.json, prints git log
# Write CHANGELOG.md entry (format: ## vX.Y.Z — YYYY-MM-DD)
git add plugin/.claude-plugin/plugin.json CHANGELOG.md
./release.sh tag               # Commits, tags, pushes, creates GitHub Release
```

## GitHub CLI

This repo's remote is `github.com`, but the environment may have `GH_HOST` set to something else. Always prefix:
```bash
GH_HOST=github.com gh pr create ...
```
