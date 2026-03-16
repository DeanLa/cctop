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
parse_codex_new_lines = _mod.parse_codex_new_lines
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


class TestCodexParsing:
    """Verify Codex transcript parsing maps into normalized fields."""

    def test_codex_user_tool_and_completion(self):
        lines = [
            json.dumps({
                "type": "session_meta",
                "payload": {
                    "cwd": "D:\\repo",
                    "timestamp": "2026-03-16T20:00:00Z",
                    "model_provider": "openai",
                },
            }),
            json.dumps({
                "type": "turn_context",
                "payload": {"cwd": "D:\\repo", "model": "gpt-5.4"},
            }),
            json.dumps({
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "check the bug"},
            }),
            json.dumps({
                "type": "response_item",
                "payload": {"type": "function_call", "name": "shell_command"},
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": "Exit code: 1\nOutput:\nboom",
                },
            }),
            json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 1200,
                            "cached_input_tokens": 300,
                            "output_tokens": 90,
                            "reasoning_output_tokens": 10,
                        },
                        "last_token_usage": {
                            "input_tokens": 400,
                            "cached_input_tokens": 50,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 5,
                        },
                        "model_context_window": 258400,
                    },
                },
            }),
            json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_complete"},
            }),
        ]

        result = parse_codex_new_lines(lines)

        assert result["cwd"] == "D:\\repo"
        assert result["project_name"] == "repo"
        assert result["model"] == "gpt-5.4"
        assert result["last_user_msg"] == "check the bug"
        assert result["input_tokens"] == 450
        assert result["output_tokens"] == 25
        assert result["cumulative_input_tokens"] == 1200
        assert result["cumulative_cache_read_tokens"] == 300
        assert result["_delta_turns"] == 1
        assert result["_delta_tool_count"] == 1
        assert result["_delta_error_count"] == 1
        assert result["_status"] == "idle"


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
