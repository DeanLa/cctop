# Hook Events API Reference

> How Claude Code hooks work: events, input fields, output format, and configuration.

## Common stdin Fields (all events)

Every hook receives a JSON object on stdin with at least:

| Field | Type | Description |
|---|---|---|
| `session_id` | string | UUID of the current session |
| `transcript_path` | string | Absolute path to the session's JSONL transcript file |
| `cwd` | string | Current working directory |
| `permission_mode` | string | Current permission mode (e.g. `"default"`) |
| `hook_event_name` | string | Name of this event (e.g. `"PostToolUse"`) |
| `agent_id` | string | Present when running inside a subagent |
| `agent_type` | string | Present when running inside a subagent |

## Event Table

| Event | Extra Input Fields | Matcher | Can Block? |
|---|---|---|---|
| `SessionStart` | `source` (`startup`/`resume`/`clear`/`compact`), `model` | source value | No |
| `UserPromptSubmit` | `prompt` | — | Yes (exit 2 or `decision:block`) |
| `PreToolUse` | `tool_name`, `tool_input`, `tool_use_id` | tool name | Yes (`permissionDecision: deny`) |
| `PermissionRequest` | `tool_name`, `tool_input`, `permission_suggestions` | tool name | Yes |
| `PostToolUse` | `tool_name`, `tool_input`, `tool_response`, `tool_use_id` | tool name | No (feedback only) |
| `PostToolUseFailure` | `tool_name`, `tool_input`, `tool_use_id`, `error`, `is_interrupt` | tool name | No |
| `Notification` | `message`, `title`, `notification_type` | notification_type | No |
| `SubagentStart` | `agent_id`, `agent_type` | agent type | No |
| `SubagentStop` | `stop_hook_active`, `agent_id`, `agent_type`, `agent_transcript_path`, `last_assistant_message` | agent type | Yes (`decision:block`) |
| `Stop` | `stop_hook_active`, `last_assistant_message` | — | Yes (`decision:block`) |
| `StopFailure` | `error` (rate_limit/auth_failed/billing_error/invalid_request/server_error/max_output_tokens/unknown), `error_details`, `last_assistant_message` | — | No |
| `TeammateIdle` | `teammate_name`, `team_name` | — | Yes (exit 2) |
| `TaskCompleted` | `task_id`, `task_subject`, `task_description`, `teammate_name`, `team_name` | — | Yes (exit 2) |
| `Notification` | `message`, `title`, `notification_type` (permission_prompt/idle_prompt/auth_success/elicitation_dialog) | notification_type | No |
| `InstructionsLoaded` | `file_path`, `memory_type`, `load_reason`, `globs`, `trigger_file_path`, `parent_file_path` | — | No |
| `ConfigChange` | (config source) | config source | Yes |
| `PreCompact` | (compaction trigger) | `manual`/`auto` | No |
| `PostCompact` | (compaction trigger) | `manual`/`auto` | No |
| `FileChanged` | `file_path`, `event` (change/add/unlink) | — | No |
| `CwdChanged` | `old_cwd`, `new_cwd` | — | No |
| `Elicitation` | MCP server name in matcher, form field requirements | MCP server name | No |
| `ElicitationResult` | MCP server name, user's response | MCP server name | No |
| `WorktreeCreate` | (worktree info) | — | Yes (non-zero exit) |
| `WorktreeRemove` | (worktree info) | — | No |
| `SessionEnd` | (end reason) | end reason | No |

## Hook Output (stdout JSON)

Hooks communicate back to Claude Code by printing JSON to stdout:

- **Blocking hooks** — print `{"decision": "block", "reason": "..."}` or `{"permissionDecision": "deny", "reason": "..."}` (for `PreToolUse`)
- **Feedback hooks** (e.g. `PostToolUse`) — print `{"message": "text to inject into conversation"}` to surface info to the model
- **Exit code 2** — on events that support it, causes Claude Code to abort/block the action
- **Exit code 0** with no stdout — hook ran successfully, no action needed

## Hook Configuration Format

In `~/.claude/settings.json` (user scope) or `<project>/.claude/settings.json` (project scope):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/script.sh",
            "timeout": 30000
          }
        ]
      }
    ]
  }
}
```

- `matcher` is a regex tested against the event's matcher field (tool name, source value, etc.)
- An empty `matcher` string matches ALL values
- `timeout` is in milliseconds (default varies by event)
- Multiple hook entries per event are supported; they run in order

In a plugin's `hooks/hooks.json`, the format is the same but wrapped:

```json
{
  "description": "Hook descriptions",
  "hooks": {
    "PostToolUse": [...]
  }
}
```

Use `${CLAUDE_PLUGIN_ROOT}` in command paths to reference files within the installed plugin cache.
