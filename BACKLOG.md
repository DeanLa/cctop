# cctop — Backlog

Items tagged with `PR-X` are assigned to a PR group, see [plans/pr-groups.md](plans/pr-groups.md) for details.
When a PR merges, mark its items `[x]` and append ` — PR-X`.

## Table & Columns
- [ ] **6.** Group/collapse sessions by project directory `PR-M`
- [ ] **49.** Add `Effort` column showing the model's reasoning effort level (the setting controlled by `/effort` in Claude Code, e.g. low/medium/high) `PR-O`
- [ ] **50.** Add `Cost` column showing estimated session cost, replicate the token-based cost calculation from `claude-cost` natively within cctop (don't shell out to the script) `PR-O`
- [x] **41.** Rename `slug` column to `Name`, the internal field is called `slug` (short directory name) but the user-facing column label should be `Name` since it shows the session's display name (custom title or slug) — [#18](https://github.com/DeanLa/cctop/pull/18)

## Detail Panel
- [ ] **14.** Parse system-injected user messages (e.g. `<task-notification>`) and display them nicely, show "Subagent completed: <summary>" instead of hiding them entirely `PR-H`
- [ ] **34.** Recent activity log: timestamped feed of recent events (tool calls, messages) for the selected session `PR-N`
- [ ] **35.** Session metadata section: display relevant metadata from the session-status JSON `PR-N`
- [ ] **70.** Expanded status context in the detail panel: show the full story behind the current status label. E.g. `editing` → file path being edited, `needs input` → the question text, `awaiting plan` → plan summary, `running cmd` → the command, `error: rate_limit` → error details, `searching web` → query. Pull from hook JSON fields (`tool_input`, `last_assistant_message`, `error_details`, `message`) `PR-N`
- [ ] **51.** Show full session ID in the detail panel, not displayed anywhere in the UI currently `PR-N`
- [ ] **52.** Show full (untruncated) model name in the detail panel, the table column truncates long model identifiers but the detail view should show the complete string `PR-N`

## Session Actions
- [ ] **18.** Add rename session action from the dashboard, see [`plans/rename-session-externally.md`](plans/rename-session-externally.md) — blocked: running sessions don't pick up external title changes *(was PR-F, deferred)*
- [ ] **46.** Jump to PyCharm: keybinding (`p`) to open/focus the PyCharm project window for the selected session's `cwd` (uses `open -a "PyCharm Professional" <cwd>` on macOS, no hook changes needed)
- [ ] **47.** Tmux attach: add env var gate (`os.environ.get("TMUX")`) to `check_action` so the `a` binding is hidden entirely when cctop isn't in tmux, keep per-session `tmux_session` check as second layer

## Session Lifecycle
- [ ] **22.** Session history, persist ended session stats (tokens, cost, turns, duration) for later querying `PR-I`

## UI & Theming
- [ ] **24.** Persist selected theme to disk so it survives restarts (config file) `PR-J`
- [ ] **36.** Config file in `~/.cctop/` for settings like stale threshold, theme, sort behavior, etc. `PR-J`
- [ ] **40.** Group-by view: group rows by a column (e.g. project, model, status) with collapsible section headers
- [ ] **42.** Configurable column display order: allow users to reorder columns via config (depends on #36)
- [ ] **43.** Default sort column in config: set which column the table sorts by on startup (depends on #36)
- [ ] **44.** Persist hidden columns in config: save column visibility state across restarts so hidden columns stay hidden (depends on #36)
- [ ] **45.** Default visible columns in config: define which columns are shown by default, so new columns start hidden unless opted in (depends on #36)

## Activity & Status Detection
- [ ] **48.** Detect and display granular activity statuses based on tool names, expand the STATUS_STYLE_MAP with distinct labels/colors for tools that currently fall through to the generic catch-all `PR-P`
- [ ] **54.** Show `planning` status when the session is in plan mode. Requires a mode flag in the hook JSON (set on EnterPlanMode, cleared on Stop/UserPromptSubmit) since the current last-event-wins model would immediately overwrite it with the next tool call `PR-P`
- [ ] **55.** Detect MCP tool usage, tool names matching `tool:mcp__<server>__<action>` should display as `mcp:<server>` with a distinct color instead of falling through to the generic cyan catch-all `PR-P`
- [ ] **56.** Show `reviewing` status when a code-review or PR-review subagent is active, detectable from Agent tool's `subagent_type` field `PR-P`
- [ ] **57.** Show `researching` status when an explore/research subagent is spawned, distinguish from generic `subagent` label `PR-P`
- [ ] **60.** Idle sub-statuses: track the last significant tool before Stop to distinguish different idle states. Hook sets a `last_tool` field on each PreToolUse, then on Stop maps it to an idle variant `PR-P`
- [ ] **61.** `awaiting plan` idle status — last tool was ExitPlanMode, session is waiting for user to approve or reject the plan `PR-P`
- [ ] **62.** `needs input` idle status — last tool was AskUserQuestion, session is blocked on a clarification question from the user `PR-P`
- [ ] **63.** `awaiting permission` idle status — session is waiting for tool approval. Best detected via Notification hook with `notification_type: permission_prompt` (see #65), fallback to last-tool inference `PR-P`
- [ ] **64.** Color-code idle variants by urgency: `needs input`/`awaiting permission` in orange (action needed now), `awaiting plan` in blue (review when ready), plain `idle` in green (no action needed) `PR-P`
- [ ] **65.** Register for `Notification` hook event. Use `notification_type` to reliably detect idle sub-statuses: `permission_prompt` → awaiting permission (#63), `elicitation_dialog` → awaiting MCP input. Much cleaner than inferring from last tool name `PR-P`
- [ ] **66.** Register for `StopFailure` hook event. Show error status with sub-type (`rate_limit`, `auth_failed`, `billing_error`, `server_error`, `max_output_tokens`) in red, so users can see at a glance when a session hit a wall `PR-P`
- [ ] **67.** Register for `PostToolUseFailure` hook event. Track tool failure count per session and optionally show transient `tool error` indicator `PR-P`
- [ ] **68.** Register for `SubagentStart` hook event for more accurate subagent lifecycle tracking (currently only SubagentStop is registered) `PR-P`
- [ ] **69.** Register for `CwdChanged` hook event to update session cwd in real time instead of only capturing it at session start `PR-P`
- [ ] **53.** Classify Bash commands into sub-statuses by inspecting the command string: `testing` (pytest, jest, npm test, go test, cargo test, make test), `building` (npm build, tsc, webpack, cargo build, make, go build), `installing` (pip install, npm install, brew install, cargo add), `linting` (eslint, ruff, black, prettier, mypy), `git op` (git commit/push/pull/rebase/merge), `creating PR` (gh pr create/merge) `PR-Q`
- [ ] **58.** Detect repeated test→edit cycles and show `debugging` status (e.g. if the last N tool calls alternate between Bash-test and Edit, the session is likely in a fix loop) `PR-Q`
- [ ] **59.** Show `deploying` status for infrastructure commands (docker, kubectl, terraform, aws, gcloud) `PR-Q`

## Health Check & Teammates
- [ ] **31.** Teammate detection & grouping: parse `--parent-session-id` from tmux-spawned teammate processes, group under parent session, add `Team` column showing teammate count. Exclude teammates from health check counts. See [`plans/prd-session-health-check.md`](plans/prd-session-health-check.md) `PR-K`
- [ ] **32.** Teammate drill-down: expandable rows for sessions with teammates, show agent name, color, status per teammate in indented sub-rows. See [`plans/prd-session-health-check.md`](plans/prd-session-health-check.md) `PR-K`

## New Frontiers
- [ ] **33.** Web frontend (Flask/FastAPI serving session-status JSON) *(standalone)*

## Done
- [x] **1.** Add `Files` column (count of files edited) `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [x] **2.** Add `Agents` column (subagent count) `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [x] **3.** Add `Errors` column (error count) `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [x] **4.** Add `StopRsn` column ("done", "tool", "limit") `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [x] **5.** ~~Sparkline per session showing tool activity over time~~ — scrapped, not useful
- [x] **7.** Show full model name in `Model` column (e.g. `claude-sonnet-4-6` instead of truncated) `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [x] **8.** Add `Started` column showing session start time `PR-A` — [#4](https://github.com/DeanLa/cctop/pull/4)
- [x] **9.** Fix `Turns` count, count user messages instead of tool calls (a turn is a user-assistant exchange, not every tool invocation) `PR-C` — [#1](https://github.com/DeanLa/cctop/pull/1)
- [x] **10.** The user message is not formatted markdown like the assistant message, make them consistent `PR-B` — [#2](https://github.com/DeanLa/cctop/pull/2)
- [x] **11.** DRY message rendering: both user and assistant messages go through shared `_render_message` helper `PR-B` — [#2](https://github.com/DeanLa/cctop/pull/2)
- [x] **12.** Increase assistant message lines from 4 to 5
- [x] **13.** Wrap detail panel text at window width (with margin) instead of fixed 100 chars
- [x] **15.** Bug: detail panel still shows last selected session after all sessions end, should clear when no active sessions remain `PR-B` — [#2](https://github.com/DeanLa/cctop/pull/2)
- [x] **16.** Debug mode, log all hook events with full JSON stdin to a file for troubleshooting `PR-D` — [#12](https://github.com/DeanLa/cctop/pull/12)
- [x] **17.** Investigate: can we kill a session from the dashboard? `PR-F` — [#14](https://github.com/DeanLa/cctop/pull/14)
- [x] **19.** Strong refresh: keybinding (e.g. Shift+R) and CLI flag (`cctop --reset`) that wipes `~/.cctop/`, re-scans all sessions from scratch
- [x] **20.** Increase stale threshold beyond 5 minutes — PR-E (#3)
- [x] **21.** Stale session cleanup via PID check instead of timeout heuristics
- [x] **23.** Fix branch showing "HEAD" for sessions in detached HEAD state, resolve to a meaningful name (tag, short SHA, or parent branch) — PR-E (#3)
- [x] **25.** Rename package to `cctop`, plugin name, slash command, and callable as `cctop` from terminal
- [x] **26.** Create `~/bin/cctop` CLI entry point
- [x] **27.** Create a git repo and push to GitHub
- [x] **29.** `/register` slash command, if the current session is not tracked by cctop (e.g. plugin was installed after the session started, or a bug), manually register it by writing the session JSON into `~/.cctop/` so the poller picks it up. `PR-G`
- [x] **30.** Session health check warning: periodically compare `ps`-based Claude process count against cctop tracked sessions, show orange warning bar on mismatch. `cctop > grep` = stale (crashed) sessions, `grep > cctop` = undetected sessions. Validate PIDs to distinguish. See [`plans/prd-session-health-check.md`](plans/prd-session-health-check.md) `PR-K` — [#6](https://github.com/DeanLa/cctop/pull/6)
- [x] **37.** Column selection & sort/hide (left/right to select column, s to sort, h to hide, c for column picker, C to show all) — [#17](https://github.com/DeanLa/cctop/pull/17)
- [x] **38.** Support versioning: plugin and CLI app should share the same version, tag releases in git
- [x] **39.** Maintain a `CHANGELOG.md` with entries for each release, generated from commit messages or PR descriptions, maintained by Claude automatically
