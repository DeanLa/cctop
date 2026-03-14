---
description: Show how to launch the cctop sessions dashboard
---

# cctop — Claude Code Sessions Dashboard

cctop is a live TUI that shows all active Claude Code sessions.

To launch it, run this in a **separate terminal**:

```
cctop
```

You can also invoke it from within Claude Code via the slash command `/cctop`.

## What it shows

- **Slug** — session nickname
- **Project** — working directory name
- **Branch** — git branch
- **Status** — idle, thinking, tool in use, stale
- **Model** — opus/sonnet/haiku
- **Ctx%** — context window usage
- **Tools** — tool call count

## Keybindings

- `q` — quit
- `r` — force refresh
- `s` — cycle sort (activity / slug / status)

Highlight a row to see the last user and Claude messages in the detail panel below the table.
