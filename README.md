# cctop

![cctop](images/logo-small.png)

Like `htop`, but for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). A live terminal dashboard that shows all your sessions at a glance — status, context usage, tokens, and latest messages.

<!-- TODO: Add demo video/screenshot -->

## Install

Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [uv](https://docs.astral.sh/uv/).

```bash
curl -fsSL https://raw.githubusercontent.com/DeanLa/cctop/main/install.sh | bash
```

Then launch from any terminal:

```bash
cctop
```

Or use the `/cctop` slash command from within Claude Code.

## Why

If you're past the "one session at a time" stage but not running a fleet of headless agents, you're in the middle ground where most tools don't help. You have 4–20 sessions open across multiple projects — refactoring one repo while tests run in another, firing off a prompt in a third while waiting for a fourth to finish. You context-switch constantly, lose track of which tab is blocked on you, and forget what that session in the background was even doing.

cctop gives you one screen to see all of them.

## What You See

### Keybindings

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Force refresh |
| `s` | Open sort picker (activity, name, status, duration, turns, tokens, tools, errors) |

### Columns

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

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, architecture, and testing.

## License

[MIT](LICENSE)
