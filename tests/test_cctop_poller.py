# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest>=8.0"]
# ///
"""Tests for the cctop poller — parse_new_lines(), resolve_git_branch(), and detect_worktree()."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

_scripts = Path(__file__).resolve().parent.parent / "plugin" / "scripts"
_spec = importlib.util.spec_from_file_location("cctop_poller", _scripts / "cctop-poller.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_new_lines = _mod.parse_new_lines
parse_copilot_events = _mod.parse_copilot_events
parse_simple_yaml = _mod.parse_simple_yaml
discover_copilot_sessions = _mod.discover_copilot_sessions
resolve_git_branch = _mod.resolve_git_branch
detect_worktree = _mod.detect_worktree


# --- Helpers ---

def _user_line(content: str) -> str:
    """Build a JSONL line for a user message."""
    return json.dumps({"type": "user", "message": {"content": content}})


def _system_line(content: str) -> str:
    """Build a JSONL line for a system-injected user message (starts with <)."""
    return json.dumps({"type": "user", "message": {"content": content}})


def _assistant_line(text: str = "Sure!", model: str = "claude-sonnet-4-6") -> str:
    """Build a JSONL line for an assistant message."""
    return json.dumps({
        "type": "assistant",
        "message": {
            "model": model,
            "content": [{"type": "text", "text": text}],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    })


# --- Turn counting tests ---

class TestTurnCounting:
    """Verify that only genuine user messages count as turns."""

    def test_genuine_user_message_increments_turns(self):
        lines = [_user_line("Fix the bug in auth.py")]
        result = parse_new_lines(lines)
        assert result["_delta_turns"] == 1
        assert result["last_user_msg"] == "Fix the bug in auth.py"

    def test_system_reminder_does_not_increment_turns(self):
        lines = [_system_line("<system-reminder>Today is 2026-03-16.</system-reminder>")]
        result = parse_new_lines(lines)
        assert result["_delta_turns"] == 0
        assert "last_user_msg" not in result

    def test_task_notification_does_not_increment_turns(self):
        lines = [_system_line("<task-notification>Agent completed.</task-notification>")]
        result = parse_new_lines(lines)
        assert result["_delta_turns"] == 0

    def test_other_xml_tag_does_not_increment_turns(self):
        lines = [_system_line("<context>some injected context</context>")]
        result = parse_new_lines(lines)
        assert result["_delta_turns"] == 0

    def test_mixed_real_and_system_messages(self):
        lines = [
            _system_line("<system-reminder>hook output</system-reminder>"),
            _user_line("Hello, help me refactor"),
            _system_line("<task-notification>done</task-notification>"),
            _assistant_line("Sure, I can help!"),
            _user_line("Now add tests"),
            _system_line("<system-reminder>another reminder</system-reminder>"),
        ]
        result = parse_new_lines(lines)
        assert result["_delta_turns"] == 2
        assert result["last_user_msg"] == "Now add tests"

    def test_empty_lines_no_turns(self):
        lines = ["", "  ", "\n"]
        result = parse_new_lines(lines)
        assert result["_delta_turns"] == 0

    def test_no_lines_no_turns(self):
        result = parse_new_lines([])
        assert result["_delta_turns"] == 0

    def test_empty_content_does_not_count(self):
        lines = [_user_line("")]
        result = parse_new_lines(lines)
        assert result["_delta_turns"] == 0

    def test_non_string_content_does_not_count(self):
        """Content that's a list (tool results) should not count as a turn."""
        line = json.dumps({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "ok"}]},
        })
        result = parse_new_lines([line])
        assert result["_delta_turns"] == 0

    def test_multiple_genuine_messages(self):
        lines = [
            _user_line("First question"),
            _assistant_line("First answer"),
            _user_line("Second question"),
            _assistant_line("Second answer"),
            _user_line("Third question"),
        ]
        result = parse_new_lines(lines)
        assert result["_delta_turns"] == 3
        assert result["last_user_msg"] == "Third question"


# --- resolve_git_branch tests ---


def _mock_run(outputs: dict[tuple[str, ...], tuple[int, str]]):
    """Create a side_effect for subprocess.run that maps command tuples to (returncode, stdout)."""
    def side_effect(cmd, **kwargs):
        key = tuple(cmd)
        if key in outputs:
            rc, out = outputs[key]
            return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fatal")
    return side_effect


class TestResolveGitBranch:
    """Verify resolve_git_branch() tries tag, branch, then short SHA."""

    def test_returns_tag_with_emoji_prefix(self, tmp_path):
        with patch.object(_mod, "subprocess") as mock_sp:
            mock_sp.run.side_effect = _mock_run({
                ("git", "describe", "--tags", "--exact-match", "HEAD"): (0, "v1.2.3\n"),
            })
            mock_sp.TimeoutExpired = subprocess.TimeoutExpired
            assert resolve_git_branch(str(tmp_path)) == "\U0001f3f7\ufe0f v1.2.3"

    def test_falls_back_to_symbolic_ref(self, tmp_path):
        with patch.object(_mod, "subprocess") as mock_sp:
            mock_sp.run.side_effect = _mock_run({
                ("git", "describe", "--tags", "--exact-match", "HEAD"): (128, ""),
                ("git", "symbolic-ref", "--short", "HEAD"): (0, "main\n"),
            })
            mock_sp.TimeoutExpired = subprocess.TimeoutExpired
            assert resolve_git_branch(str(tmp_path)) == "main"

    def test_falls_back_to_short_sha_with_emoji_prefix(self, tmp_path):
        with patch.object(_mod, "subprocess") as mock_sp:
            mock_sp.run.side_effect = _mock_run({
                ("git", "describe", "--tags", "--exact-match", "HEAD"): (128, ""),
                ("git", "symbolic-ref", "--short", "HEAD"): (128, ""),
                ("git", "rev-parse", "--short", "HEAD"): (0, "abc1234\n"),
            })
            mock_sp.TimeoutExpired = subprocess.TimeoutExpired
            assert resolve_git_branch(str(tmp_path)) == "\U0001f500 abc1234"

    def test_returns_none_when_all_fail(self, tmp_path):
        with patch.object(_mod, "subprocess") as mock_sp:
            mock_sp.run.side_effect = _mock_run({})
            mock_sp.TimeoutExpired = subprocess.TimeoutExpired
            assert resolve_git_branch(str(tmp_path)) is None

    def test_returns_none_on_timeout(self, tmp_path):
        with patch.object(_mod, "subprocess") as mock_sp:
            mock_sp.run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=2)
            mock_sp.TimeoutExpired = subprocess.TimeoutExpired
            assert resolve_git_branch(str(tmp_path)) is None

    def test_returns_none_for_empty_cwd(self):
        assert resolve_git_branch("") is None

    def test_returns_none_for_nonexistent_dir(self):
        assert resolve_git_branch("/nonexistent/path/xyz") is None


# --- detect_worktree tests ---


def _mock_git_dirs(git_dir: str, common_dir: str):
    """Create a side_effect for subprocess.run that simulates git-dir / git-common-dir."""
    def side_effect(cmd, **kwargs):
        key = tuple(cmd)
        if key == ("git", "rev-parse", "--git-dir"):
            return subprocess.CompletedProcess(cmd, 0, stdout=git_dir + "\n", stderr="")
        if key == ("git", "rev-parse", "--git-common-dir"):
            return subprocess.CompletedProcess(cmd, 0, stdout=common_dir + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fatal")
    return side_effect


class TestDetectWorktree:
    """Verify worktree detection returns original repo name or None."""

    def test_worktree_returns_repo_name(self, tmp_path):
        with patch.object(_mod, "subprocess") as mock_sp:
            mock_sp.run.side_effect = _mock_git_dirs(
                "/path/to/cctop/.git/worktrees/my-wt", "/path/to/cctop/.git"
            )
            mock_sp.TimeoutExpired = subprocess.TimeoutExpired
            assert detect_worktree(str(tmp_path)) == "cctop"

    def test_main_tree_returns_none(self, tmp_path):
        with patch.object(_mod, "subprocess") as mock_sp:
            mock_sp.run.side_effect = _mock_git_dirs("/repo/.git", "/repo/.git")
            mock_sp.TimeoutExpired = subprocess.TimeoutExpired
            assert detect_worktree(str(tmp_path)) is None

    def test_not_a_repo_returns_none(self, tmp_path):
        with patch.object(_mod, "subprocess") as mock_sp:
            mock_sp.run.return_value = subprocess.CompletedProcess(
                [], 128, stdout="", stderr="fatal: not a git repo"
            )
            mock_sp.TimeoutExpired = subprocess.TimeoutExpired
            assert detect_worktree(str(tmp_path)) is None

    def test_returns_none_for_empty_cwd(self):
        assert detect_worktree("") is None

    def test_returns_none_on_timeout(self, tmp_path):
        with patch.object(_mod, "subprocess") as mock_sp:
            mock_sp.run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=2)
            mock_sp.TimeoutExpired = subprocess.TimeoutExpired
            assert detect_worktree(str(tmp_path)) is None


# --- Copilot CLI events.jsonl parsing tests ---


def _copilot_event(event_type: str, data: dict | None = None, timestamp: str = "2026-03-17T06:00:00.000Z") -> str:
    """Build a Copilot events.jsonl line."""
    obj = {
        "type": event_type,
        "data": data or {},
        "id": "test-id",
        "timestamp": timestamp,
        "parentId": None,
    }
    return json.dumps(obj)


class TestParseCopilotEvents:
    """Tests for parse_copilot_events() — the Copilot CLI events.jsonl parser."""

    def test_session_start_extracts_metadata(self):
        line = _copilot_event("session.start", {
            "sessionId": "abc-123",
            "startTime": "2026-03-17T06:00:00.000Z",
            "selectedModel": "claude-sonnet-4.5",
            "context": {"cwd": "/home/user/project", "branch": "main"},
            "copilotVersion": "1.0.6",
            "version": 1,
            "producer": "copilot-agent",
        })
        result = parse_copilot_events([line])
        assert result.get("cwd") == "/home/user/project"
        assert result.get("git_branch") == "main"
        assert result.get("model") == "claude-sonnet-4.5"
        assert result.get("started_at") == "2026-03-17T06:00:00.000Z"
        assert result.get("status") == "started"

    def test_user_message_counts_turns(self):
        lines = [
            _copilot_event("user.message", {"content": "Fix the bug"}),
            _copilot_event("user.message", {"content": "Now add tests"}),
        ]
        result = parse_copilot_events(lines)
        assert result["_delta_turns"] == 2
        assert result.get("last_user_msg") == "Now add tests"

    def test_slash_commands_not_counted_as_turns(self):
        lines = [
            _copilot_event("user.message", {"content": "/model"}),
            _copilot_event("user.message", {"content": "/help"}),
        ]
        result = parse_copilot_events(lines)
        assert result["_delta_turns"] == 0

    def test_system_messages_not_counted(self):
        lines = [
            _copilot_event("user.message", {"content": "<system>internal stuff</system>"}),
        ]
        result = parse_copilot_events(lines)
        assert result["_delta_turns"] == 0

    def test_assistant_message_extracts_content(self):
        line = _copilot_event("assistant.message", {
            "content": "Here's the fix for the bug.",
            "messageId": "msg-1",
            "toolRequests": [],
        })
        result = parse_copilot_events([line])
        assert result.get("last_assistant_msg") == "Here's the fix for the bug."

    def test_assistant_message_counts_tool_requests(self):
        line = _copilot_event("assistant.message", {
            "content": "",
            "messageId": "msg-1",
            "toolRequests": [
                {"toolCallId": "tc1", "name": "view", "arguments": {"path": "/a.py"}, "type": "function"},
                {"toolCallId": "tc2", "name": "edit", "arguments": {"path": "/b.py"}, "type": "function"},
                {"toolCallId": "tc3", "name": "bash", "arguments": {"command": "ls"}, "type": "function"},
            ],
        })
        result = parse_copilot_events([line])
        assert result["_delta_tool_count"] == 3
        assert "/b.py" in result["_delta_files_edited"]

    def test_assistant_message_extracts_output_tokens(self):
        """Copilot CLI embeds outputTokens in assistant.message (no assistant.usage events)."""
        lines = [
            _copilot_event("assistant.message", {
                "content": "First response",
                "messageId": "msg-1",
                "outputTokens": 150,
                "toolRequests": [],
            }),
            _copilot_event("assistant.message", {
                "content": "Second response",
                "messageId": "msg-2",
                "outputTokens": 200,
                "toolRequests": [],
            }),
        ]
        result = parse_copilot_events(lines)
        assert result.get("output_tokens") == 200  # latest message
        assert result["_delta_cumulative_output"] == 350  # sum of all

    def test_assistant_usage_extracts_tokens(self):
        line = _copilot_event("assistant.usage", {
            "inputTokens": 5000,
            "outputTokens": 1000,
            "cacheReadTokens": 2000,
            "cacheWriteTokens": 500,
            "model": "claude-opus-4.6-1m",
            "cost": 0.05,
        })
        result = parse_copilot_events([line])
        assert result.get("input_tokens") == 5000 + 2000 + 500
        assert result.get("output_tokens") == 1000
        assert result.get("model") == "claude-opus-4.6-1m"
        assert result["_delta_cumulative_input"] == 5000
        assert result["_delta_cumulative_output"] == 1000
        assert result["_delta_cumulative_cache_read"] == 2000
        assert result["_delta_cumulative_cache_creation"] == 500

    def test_tool_execution_tracks_status(self):
        lines = [
            _copilot_event("tool.execution_start", {"toolName": "bash", "toolCallId": "tc1"}),
            _copilot_event("tool.execution_complete", {"toolCallId": "tc1", "success": True}),
        ]
        result = parse_copilot_events(lines)
        assert result.get("status") == "thinking"

    def test_tool_execution_failure_counts_errors(self):
        lines = [
            _copilot_event("tool.execution_complete", {"toolCallId": "tc1", "success": False, "error": "oops"}),
        ]
        result = parse_copilot_events(lines)
        assert result["_delta_error_count"] == 1

    def test_subagent_tracking(self):
        lines = [
            _copilot_event("subagent.started", {"agentName": "explore", "toolCallId": "tc1"}),
            _copilot_event("subagent.started", {"agentName": "task", "toolCallId": "tc2"}),
            _copilot_event("subagent.completed", {"agentName": "explore", "toolCallId": "tc1"}),
        ]
        result = parse_copilot_events(lines)
        assert result["_delta_subagent_count"] == 2
        assert result.get("_running_agents_delta") == 1

    def test_session_idle_sets_status(self):
        line = _copilot_event("session.idle", {})
        result = parse_copilot_events([line])
        assert result.get("status") == "idle"

    def test_assistant_intent_sets_slug(self):
        line = _copilot_event("assistant.intent", {"intent": "Fixing homepage CSS"})
        result = parse_copilot_events([line])
        assert result.get("slug") == "Fixing homepage CSS"

    def test_model_change_updates_model(self):
        line = _copilot_event("session.model_change", {
            "previousModel": "claude-sonnet-4.5",
            "newModel": "gpt-5.4",
        })
        result = parse_copilot_events([line])
        assert result.get("model") == "gpt-5.4"

    def test_empty_lines_produce_empty_result(self):
        result = parse_copilot_events(["", "  ", "\n"])
        assert result["_delta_turns"] == 0
        assert result["_delta_tool_count"] == 0

    def test_last_activity_tracks_timestamps(self):
        lines = [
            _copilot_event("session.idle", {}, timestamp="2026-03-17T06:01:00.000Z"),
            _copilot_event("user.message", {"content": "hello"}, timestamp="2026-03-17T06:02:00.000Z"),
        ]
        result = parse_copilot_events(lines)
        assert result.get("last_activity") == "2026-03-17T06:02:00.000Z"

    def test_session_usage_info_extracts_token_limit(self):
        line = _copilot_event("session.usage_info", {
            "currentTokens": 50000,
            "messagesLength": 20,
            "tokenLimit": 1000000,
        })
        result = parse_copilot_events([line])
        assert result.get("token_limit") == 1000000
        assert result.get("input_tokens") == 50000


class TestParseSimpleYaml:
    """Tests for parse_simple_yaml() — flat YAML parser."""

    def test_parses_workspace_yaml(self, tmp_path):
        yaml_file = tmp_path / "workspace.yaml"
        yaml_file.write_text(
            "id: abc-123\n"
            "cwd: /home/user/project\n"
            "branch: main\n"
            "summary: Fix bugs\n"
        )
        result = parse_simple_yaml(yaml_file)
        assert result["id"] == "abc-123"
        assert result["cwd"] == "/home/user/project"
        assert result["branch"] == "main"
        assert result["summary"] == "Fix bugs"

    def test_handles_missing_file(self, tmp_path):
        result = parse_simple_yaml(tmp_path / "missing.yaml")
        assert result == {}

    def test_skips_comments_and_empty_lines(self, tmp_path):
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("# comment\n\nkey: value\n")
        result = parse_simple_yaml(yaml_file)
        assert result == {"key": "value"}


class TestDiscoverCopilotSessions:
    """Tests for discover_copilot_sessions()."""

    def test_finds_session_with_lock_file(self, tmp_path):
        session_dir = tmp_path / "abc-123"
        session_dir.mkdir()
        (session_dir / "events.jsonl").write_text("")
        (session_dir / "inuse.12345.lock").write_text("12345")
        with patch.object(_mod, "COPILOT_SESSION_DIR", tmp_path):
            sessions = discover_copilot_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "abc-123"
        assert sessions[0]["pid"] == 12345

    def test_skips_session_without_lock(self, tmp_path):
        session_dir = tmp_path / "abc-123"
        session_dir.mkdir()
        (session_dir / "events.jsonl").write_text("")
        with patch.object(_mod, "COPILOT_SESSION_DIR", tmp_path):
            sessions = discover_copilot_sessions()
        assert len(sessions) == 0

    def test_skips_session_without_events(self, tmp_path):
        session_dir = tmp_path / "abc-123"
        session_dir.mkdir()
        (session_dir / "inuse.12345.lock").write_text("12345")
        with patch.object(_mod, "COPILOT_SESSION_DIR", tmp_path):
            sessions = discover_copilot_sessions()
        assert len(sessions) == 0

    def test_handles_empty_dir(self, tmp_path):
        with patch.object(_mod, "COPILOT_SESSION_DIR", tmp_path):
            sessions = discover_copilot_sessions()
        assert sessions == []

    def test_handles_nonexistent_dir(self, tmp_path):
        with patch.object(_mod, "COPILOT_SESSION_DIR", tmp_path / "nope"):
            sessions = discover_copilot_sessions()
        assert sessions == []
