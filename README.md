# cctop

<p align="center">
  <img src="logo-wide.png" alt="cctop" width="600">
</p>

A live terminal dashboard for monitoring all your Claude Code sessions at a glance. Like `htop`, but for Claude Code.

<!-- TODO: Add demo video/screenshot -->

## The Problem

Power users run multiple Claude Code sessions simultaneously — one refactoring a module, another writing tests, a third researching an API. You end up tab-switching between terminals just to check "is it done yet?" or "is it stuck waiting for me?" There's no central place to see what's happening across sessions.

## What It Does

A Claude Code plugin that installs a lightweight hook to track session activity in real time. A companion TUI dashboard displays all active sessions in a single live-updating table.

### At a Glance

| Column | What it shows |
|--------|---------------|
| **Slug** | Session nickname (custom title or first prompt) |
| **Project** | Working directory name |
| **Branch** | Git branch (truncated to 12 chars) |
| **Status** | idle, thinking, editing, running cmd, searching web, subagent, stale, ended |
| **Model** | opus / sonnet / haiku |
| **Ctx%** | Context window usage percentage |
| **Tokens** | Total tokens consumed (e.g. "145k") |
| **Tools** | Tool call count |
| **Turns** | Conversation turn count |
| **Duration** | Elapsed time since session start (e.g. "1h23m") |
| **Activity** | Time since last event (e.g. "2m ago") |

Highlight any row to see a detail panel with the full working directory, git branch, token breakdown, the last user prompt, and Claude's last response.

Sessions that go quiet for 5+ minutes are marked stale. Sessions that end clean up after themselves.

### Keybindings

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Force refresh |
| `s` | Open sort picker (activity, name, status, duration, turns, tokens, tools, errors) |

## Install

Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [uv](https://docs.astral.sh/uv/).

```bash
curl -fsSL https://raw.githubusercontent.com/DeanLa/cctop/main/install.sh | bash
```

This registers the plugin, installs it into Claude Code, and adds the `cctop` CLI to `~/.local/bin/`.

After installing, all new Claude Code sessions will automatically have the hook active.

## Usage

Launch the dashboard from any terminal:

```bash
cctop
```

You can also invoke it from within Claude Code via the slash command `/cctop`.

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, architecture, and testing.

## License

[MIT](LICENSE)
