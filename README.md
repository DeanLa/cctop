# cctop

![cctop](media/logo-small.png)

Like `htop`, but for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and Codex. A live terminal dashboard that shows all your sessions at a glance, status, context usage, tokens, and latest messages.

![cctop demo](media/cctop-demo.gif)

## Install

Requires [uv](https://docs.astral.sh/uv/).

Optional:
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) for Claude session tracking via plugin hooks
- [jq](https://jqlang.github.io/jq/) for the Claude hook

```bash
curl -fsSL https://raw.githubusercontent.com/DeanLa/cctop/main/install.sh | bash
```

Then launch from any terminal:

```bash
cctop
```

## Why

If you're past the "one session at a time" stage but not running a fleet of headless agents, you're in the middle ground where most tools don't help. You have 4-20 sessions open across multiple projects, refactoring one repo while tests run in another, firing off a prompt in a third while waiting for a fourth to finish. You context-switch constantly, lose track of which tab is blocked on you, and forget what that session in the background was even doing.

cctop gives you one screen to see all of them.

Claude sessions are tracked through the plugin hooks. Codex sessions are discovered directly from local Codex session transcripts under `~/.codex/`, so they appear automatically once the dashboard is running.

If Claude is installed, `install.sh` also installs the Claude plugin automatically. If Claude is not installed, cctop still installs a standalone runtime and can monitor Codex sessions.

## What You See

### Keybindings

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Force refresh |
| `R` | Purge dead sessions (PID check + staleness fallback) |
| `s` | Open sort picker (activity, name, status, duration, started, turns, tokens, tools, files, agents, errors) |

### Columns

| Column | What it shows |
|--------|---------------|
| **Slug** | Session nickname (custom title or first prompt) |
| **Project** | Working directory name |
| **Branch** | Git branch (truncated to 20 chars) |
| **Status** | idle, thinking, editing, running cmd, searching web, subagent, stale, ended |
| **Model** | Model family and version (e.g. "sonnet 4.6", "opus 4.6") |
| **Ctx%** | Context window usage percentage |
| **Tokens** | Total tokens consumed (e.g. "145k") |
| **Tools** | Tool call count |
| **Files** | Number of files edited |
| **Agents** | Running subagents |
| **Errors** | Error count (highlighted in red) |
| **Turns** | Conversation turn count (user-assistant exchanges) |
| **StopRsn** | Last stop reason (done, tool, limit) |
| **Duration** | Elapsed time since session start (e.g. "1h23m") |
| **Started** | Session start time (e.g. "14:30") |
| **Activity** | Time since last event (e.g. "2m ago") |

For Codex sessions, live status and token usage are inferred from the Codex transcript stream. File edit counts and subagent metrics are currently Claude-only.

Highlight any row to see a detail panel with the full working directory, git branch, token breakdown, files edited, subagent and error counts, the last user prompt, and the latest assistant response.

### Session Lifecycle

Sessions that go quiet for 1+ hour are marked stale. Sessions that end clean up after themselves. Sessions whose Claude process has exited (e.g. Ctrl+C) are automatically removed by the background poller via PID checks. Press `R` to manually purge dead sessions, or run `cctop --reset` to wipe all session data and start fresh.

A health check bar may appear at the bottom of the dashboard when cctop detects a mismatch between tracked sessions and running Claude processes. This is normal if you had sessions running before installing cctop.

## Troubleshooting

**No sessions appear after install**
- For Claude sessions, make sure `jq` is installed (`jq --version`). The hook requires it and silently does nothing without it.
- Claude sessions started *before* installing the plugin won't appear until they are restarted.
- For Codex sessions, make sure `~/.codex/session_index.jsonl` and `~/.codex/sessions/` exist and contain recent sessions.
- Try running `cctop --reset` to clear stale data and start fresh.

**Orange warning bar at the bottom**
- "N sessions not tracked" means Claude processes are running that cctop doesn't know about. This is expected for sessions that started before cctop was installed.
- "N stale sessions detected" means tracked sessions whose process has exited. Press `R` to purge them.

## Uninstall

Remove the plugin, standalone runtime, and CLI entry point:

```bash
rm -rf ~/.claude/plugins/cache/cctop
rm -rf ~/.local/share/cctop
rm -f ~/.local/bin/cctop
rm -rf ~/.cctop
```

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, architecture, and testing.

## License

[MIT](LICENSE)
