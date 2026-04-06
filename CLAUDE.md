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
- `plans/`, **gitignored**, PRDs and design docs (never commit these, never `git add` them)
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
| `reference/slash-commands.md` | Slash command categories, transcript formats (3 variants), hook invisibility, poller parsing pipeline |

## Textual TUI Development

When writing or modifying Textual-based code (the dashboard, widgets, tests), always use the `textual-tui-dev` skill first to load best practices for architecture, workers, reactive state, testing patterns, etc.

## Installing After Changes

The plugin runs from a **copy** in `~/.claude/plugins/cache/`, not from this directory.
After editing any file under `plugin/`, you **must** reinstall:

```bash
./install.sh --dev
```

**Always run `./install.sh --dev` after modifying any plugin file** (hooks, scripts, manifests). New Claude sessions will pick up the changes; existing sessions keep the old version. **Do this before telling the user to test.** Never say "restart cctop" without installing first. "DI" is shorthand for dev install.

## Releasing

Use `release.sh` for version bumps and tagging. The script handles the mechanical parts, you write the changelog.

1. `./release.sh bump <version>` — updates `plugin.json`, prints git log since last tag
2. Read the git log output and write a human-readable `CHANGELOG.md` entry. Prepend the new entry to the file. Use format: `## vX.Y.Z — YYYY-MM-DD`
   - Group features into thematic subgroups with punchy headers (e.g. "Status Detection, Know What Every Session Is Actually Doing")
   - Focus on what changed for the user, not implementation details (no config file paths, hook event names, or keybindings)
   - No self-praise or hype ("major upgrade", "significant improvement"), just describe what's new
   - Cross-reference `plans/pr-groups.md` and `BACKLOG.md` to make sure nothing is missed
3. `git add plugin/.claude-plugin/plugin.json CHANGELOG.md`
4. `./release.sh tag` — commits, tags, pushes

## Writing Style

- Never use emdashes (—) in any language. Use commas in prose, regular dashes (-) in lists.

### Hebrew Announcements

When writing Hebrew-facing text (release announcements, social posts, community messages):

- **Casual, grounded, anti-hype.** No self-praise. "כמה שדרוגים" not "שדרוג רציני". Relatable openers ("כמו כולם, גם אני הבנתי ש..."). Say what it's NOT to anchor expectations ("זה לא orchestrator, זה לא prompt manager. זה פשוט...").
- **Transliterate tech terms to Hebrew:** לייאאוט, מטאדאטה, סשנים, דשבורד, ת'מת, צ'אט, גרנולריות, פרסונליזציה. Keep actual code/UI terms in English (editing, session, permission, TMUX).
- **Structure:** short intro line, bold feature headers with dash then explanation, bullet lists for sub-features, casual sign-off ("יאללה, בואו לנסות").
- **No implementation details.** Focus on what changed for the user, not config paths or keybindings.
- **Credit contributors by name.**

## Security

Before committing, run a basic security audit on staged changes:
- No hardcoded secrets, API keys, tokens, or passwords
- No personal information (real names, private emails, internal hostnames, private IPs)
- No SentinelOne internal references (GHE URLs, internal tooling, team names)
- TruffleHog runs as a pre-commit hook, but also manually sanity-check diffs for anything it might miss

## Branching

- **ALWAYS use a worktree when starting work on a new branch.** Use the `EnterWorktree` tool to create an isolated worktree before making any changes. Do NOT just create a branch with `git checkout -b` or `git switch -c` in the main working directory.
- This keeps the main working directory clean on `main` and avoids conflicts with other sessions.
- When entering a worktree, rename the conversation to include the PR identifier, e.g. "PR-M: Group-by view".
- When the work is done and merged, exit the worktree with `ExitWorktree`.

## PR Reviews & Comments

- **NEVER post comments, reviews, or replies to GitHub PRs/issues without showing the exact text first and getting explicit approval.** GitHub doesn't allow deleting submitted reviews, so mistakes are permanent.
- Keep PR feedback concise and actionable.

### Reviewing External PRs

To review and locally test a PR from another contributor:

1. Fetch the PR: `git fetch origin pull/N/head`
2. Create a worktree: `EnterWorktree name=review-pr-N`
3. Checkout the fetched code: `git checkout FETCH_HEAD`
4. Dev install if needed: `./install.sh --dev`
5. Review, run tests, eyeball the UI
6. When done: `ExitWorktree` (remove, since it's read-only review work)

## GitHub CLI Gotchas

- This repo's remote is `github.com`, but `GH_HOST` may be set to GHE. Always prefix `gh` commands with `GH_HOST=github.com` (e.g. `GH_HOST=github.com gh pr create ...`).
- When merging PRs from a worktree, do NOT use `--delete-branch` on `gh pr merge`, it tries to checkout main locally which fails because main is already checked out in the main worktree. Just `gh pr merge N --squash`, then exit the worktree normally with `ExitWorktree` which handles branch cleanup.

## PR Groups Workflow (MANDATORY)

This project uses a structured PR-groups workflow defined in `plans/pr-groups.md`. **This workflow is not optional.**

**Shorthand commands:**
- **"Implement/work PR X"** — Look up PR group X in `plans/pr-groups.md`, implement all its items following the full workflow below
- **"Implement/work Task/T #"** — Implement only that single backlog item, but still follow the full PR workflow (worktree → plan → implement → test → push & PR)

**Workflow steps:**
1. Read `plans/pr-groups.md` first to understand the grouping and dependencies
2. Follow the workflow steps exactly as written in that file (branch → plan → implement → test → push & PR)
3. Use `EnterWorktree` to create the worktree, do not skip this step
4. Enter plan mode before implementing, get user approval before writing code
5. Make granular commits (one per logical change)
6. After user approves, follow this exact post-merge sequence:
   1. Merge the PR on remote (`gh pr merge`)
   2. Exit the worktree (`ExitWorktree`)
   3. Pull remote main (`git pull`)
   4. Update `BACKLOG.md` (mark items done) and `plans/pr-groups.md` (check off the PR group), but do NOT commit or push these changes, the user will review and commit manually

## Project-Level Files (BACKLOG.md, CLAUDE.md)

- **NEVER edit `BACKLOG.md` or `CLAUDE.md` in a worktree copy.** Edit them via the main repo path (e.g. `/Users/deanl/__me/code/cctop/CLAUDE.md`), even while working in a worktree. This avoids merge conflicts when the worktree branch is merged.
- **Do not commit changes to these files on your own.** Only update `BACKLOG.md` or `CLAUDE.md` when the user explicitly asks for it.

## Commits

- Split uncommitted changes into logical, self-contained commits (e.g. separate feature code, tests, docs, backlog updates)
- When moving or renaming files, update all references in other files (BACKLOG.md links, CLAUDE.md structure, README, etc.) in the same or immediately following commit

## Docs Hygiene

When making changes that affect user-visible behavior (new features, changed columns, new keybindings, install steps, usage), always check that `README.md` and `CONTRIBUTING.md` are updated to match.

## Code Quality

- **Simplify aggressively.** After implementing a feature, re-read the entire file looking for dead code, vestigial fields, duplicate patterns, unused defaults, and guards for impossible conditions. Budget a simplification pass after every feature.
- **DRY is a hard rule.** Extract shared patterns even for 2 call sites.
- **Code should read as stories.** Method ordering and naming should make the flow obvious top-to-bottom without jumping around.
- **Config-aware changes.** Any change that introduces user-configurable state (new columns, sort options, visibility toggles, thresholds) must be persisted in `~/.cctop/config.toml`. Update `_CONFIG_DEFAULTS`, load the value on startup, and save on change.

## UI Preferences

- **High contrast, not subtle.** Prefer bold, theme-native visual indicators (accent colors, `reverse`, built-in component classes) over custom muted styles.
- **Polish details matter.** Indicators should be where the user's eye already is. Navigation should wrap around. Modals should be wide enough for one-line instructions. Text should be centered when appropriate.
- **Performance must match native feel.** If a UI element feels slower than Textual's native equivalent, dig into the rendering pipeline (`_should_highlight`, `_render_line_in_row`, `_refresh_region`) and replicate the same pattern. Don't settle for "good enough."
