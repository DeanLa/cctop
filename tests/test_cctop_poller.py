# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest>=8.0"]
# ///
"""Tests for the cctop poller — parse_new_lines() turn counting."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_scripts = Path(__file__).resolve().parent.parent / "plugin" / "scripts"
_spec = importlib.util.spec_from_file_location("cctop_poller", _scripts / "cctop-poller.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_new_lines = _mod.parse_new_lines


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
