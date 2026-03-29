# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=3.0.0", "pytest>=8.0", "pytest-asyncio>=0.23"]
# ///
"""Tests for the cctop dashboard TUI."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console as RichConsole, Group
from textual.widgets import DataTable, Static

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "scripts"))

from cctop_dashboard import (
    SessionsDashboard,
    SessionInfo,
    ColumnPicker,
    HealthStatus,
    _render_message,
    format_tokens,
    format_relative_time,
    friendly_model_name,
    format_start_time,
    format_stop_reason,
    get_claude_pids,
    check_session_health,
    load_sessions,
    purge_dead_sessions,
    styled_status,
    load_config,
    save_config,
    _reset_session_data,
    CONFIG_PATH,
    STATUS_DIR,
)


# --- Helpers ---

def _render_table_text(table) -> str:
    """Render a Rich Table to plain text for assertions."""
    from rich.console import Console
    from io import StringIO
    buf = StringIO()
    Console(file=buf, width=120, no_color=True).print(table)
    return buf.getvalue()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago_iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


async def _wait_for_rows(pilot, app, *, expected: int = 1, retries: int = 20):
    """Wait for the worker thread to deliver results to the table.

    For expected > 0, waits until row_count >= expected.
    For expected == 0, waits until row_count == 0 (after being non-zero).
    """
    table = app.query_one(DataTable)
    for _ in range(retries):
        await pilot.pause()
        if expected == 0 and table.row_count == 0:
            return
        if expected > 0 and table.row_count >= expected:
            return
    # Final pause to settle
    await pilot.pause()


def write_fake_session(tmpdir: Path, sid: str, *,
                       cwd: str = "/tmp/proj",
                       status: str = "idle",
                       model: str = "claude-sonnet-4-6-20260301",
                       turns: int = 5,
                       tool_count: int = 10,
                       cum_input: int = 50000,
                       cum_output: int = 20000,
                       last_user_msg: str = "hello",
                       last_assistant_msg: str = "world",
                       error_count: int = 0,
                       subagent_count: int = 0,
                       files_edited: list[str] | None = None,
                       running_agents: int = 0,
                       git_branch: str = "main",
                       slug: str = "",
                       custom_title: str = "",
                       pid: int | None = None,
                       last_activity: str | None = None,
                       started_at: str | None = None,
                       planning_mode: bool = False,
                       last_tool: str = "",
                       active_subagent_type: str = "",
                       error_type: str = "",
                       error_details: str = "",
                       tool_failures: int = 0,
                       tmux_session: str = "",
                       tmux_window: str = "") -> None:
    """Write a pair of hook + poller JSON files into tmpdir."""
    hook = {
        "session_id": sid,
        "cwd": cwd,
        "status": status,
        "last_activity": last_activity or _now_iso(),
        "started_at": started_at or _ago_iso(30),
        "model": model,
        "tool_count": 0,
        "running_agents": running_agents,
        "planning_mode": planning_mode,
        "last_tool": last_tool,
        "active_subagent_type": active_subagent_type,
        "error_type": error_type,
        "error_details": error_details,
        "tool_failures": tool_failures,
        "tmux_session": tmux_session,
        "tmux_window": tmux_window,
    }
    if pid is not None:
        hook["pid"] = pid
    (tmpdir / f"{sid}.json").write_text(json.dumps(hook))
    poller = {
        "slug": slug or f"proj-{sid[:4]}",
        "git_branch": git_branch,
        "model": model,
        "last_user_msg": last_user_msg,
        "last_assistant_msg": last_assistant_msg,
        "input_tokens": 50000,
        "output_tokens": 20000,
        "tool_count": tool_count,
        "turns": turns,
        "custom_title": custom_title,
        "cumulative_input_tokens": cum_input,
        "cumulative_output_tokens": cum_output,
        "subagent_input_tokens": 0,
        "subagent_output_tokens": 0,
        "error_count": error_count,
        "subagent_count": subagent_count,
        "files_edited": files_edited,
        "stop_reason": "",
    }
    (tmpdir / f"{sid}.poller.json").write_text(json.dumps(poller))


def _render_static_text(detail: Static) -> str:
    """Render a Static widget's content to plain text for assertions."""
    content = detail.content
    if not content:
        return ""
    buf = StringIO()
    RichConsole(file=buf, force_terminal=False, width=200).print(content)
    return buf.getvalue()


# --- Unit tests for helpers ---

def test_format_tokens_zero():
    assert format_tokens(0) == ""


def test_format_tokens_small():
    assert format_tokens(700) == "700"


def test_format_tokens_thousands():
    assert format_tokens(145000) == "145k"


def test_format_tokens_large():
    assert format_tokens(110000) == "110k"



def test_format_relative_time_empty():
    assert format_relative_time("") == ""


def test_format_relative_time_recent():
    assert format_relative_time(_now_iso()) == "now"


def test_format_relative_time_minutes():
    result = format_relative_time(_ago_iso(5))
    assert "m ago" in result


# --- friendly_model_name tests ---

def test_friendly_model_name_sonnet():
    assert friendly_model_name("claude-sonnet-4-6-20260301") == "sonnet 4.6"


def test_friendly_model_name_opus():
    assert friendly_model_name("claude-opus-4-6-v1[1m]") == "opus 4.6"


def test_friendly_model_name_haiku():
    assert friendly_model_name("claude-haiku-4-5-20251001") == "haiku 4.5"


def test_friendly_model_name_unknown():
    assert friendly_model_name("gpt-4o-mini") == "gpt-4o-mini"


def test_friendly_model_name_empty():
    assert friendly_model_name("") == ""


# --- format_start_time tests ---

def test_format_start_time_empty():
    assert format_start_time("") == ""


def test_format_start_time_today():
    """A timestamp from today should show just HH:MM."""
    now = datetime.now(timezone.utc)
    result = format_start_time(now.isoformat())
    assert ":" in result
    # Should be short (just time, no date)
    assert len(result) <= 5


def test_format_start_time_other_day():
    """A timestamp from a different day should include month and day."""
    other_day = datetime.now(timezone.utc) - timedelta(days=2)
    result = format_start_time(other_day.isoformat())
    assert ":" in result
    # Should be longer (includes date)
    assert len(result) > 5


# --- format_stop_reason tests ---

def test_format_stop_reason_empty():
    assert format_stop_reason("") == ""


def test_format_stop_reason_end_turn():
    assert format_stop_reason("end_turn") == "done"


def test_format_stop_reason_tool_use():
    assert format_stop_reason("tool_use") == "tool"


def test_format_stop_reason_max_tokens():
    assert format_stop_reason("max_tokens") == "limit"


def test_format_stop_reason_unknown():
    assert format_stop_reason("something_else") == "something_else"


# --- format_start_time edge cases ---

def test_format_start_time_z_suffix():
    """Z-suffixed timestamps (the hook's format) should parse correctly."""
    assert format_start_time("2026-03-16T10:30:00Z") != ""


def test_format_start_time_malformed():
    """Malformed input should return empty string, not raise."""
    assert format_start_time("garbage") == ""


# --- TUI integration tests ---

@pytest.fixture
def fake_status_dir(tmp_path):
    """Create a temp dir and monkeypatch STATUS_DIR and CONFIG_PATH to point there."""
    with patch("cctop_dashboard.STATUS_DIR", tmp_path), \
         patch("cctop_dashboard.CONFIG_PATH", tmp_path / "config.toml"):
        yield tmp_path


@pytest.mark.asyncio
async def test_app_starts_empty(fake_status_dir):
    """Empty status dir → 12 visible columns (4 hidden by default), 0 rows."""
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        table = app.query_one(DataTable)
        assert len(table.columns) == 12
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_sessions_render(fake_status_dir):
    """Two fake sessions should appear as rows."""
    write_fake_session(fake_status_dir, "aaaa-1111", turns=3)
    write_fake_session(fake_status_dir, "bbbb-2222", turns=7)
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app, expected=2)
        table = app.query_one(DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_column_move_left_right(fake_status_dir):
    """Left/right should move the selected column on the table."""
    write_fake_session(fake_status_dir, "aaaa-1111")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        table = app.query_one(DataTable)
        assert table.selected_column == 0
        await pilot.press("right")
        await pilot.pause()
        assert table.selected_column == 1
        await pilot.press("right")
        await pilot.pause()
        assert table.selected_column == 2
        await pilot.press("left")
        await pilot.pause()
        assert table.selected_column == 1


@pytest.mark.asyncio
async def test_sort_by_active_column(fake_status_dir):
    """Pressing 's' should sort by the active column."""
    write_fake_session(fake_status_dir, "aaaa-1111")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        # Move to the "status" column (index 3) and sort
        for _ in range(3):
            await pilot.press("right")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert app.sort_mode == "status"


@pytest.mark.asyncio
async def test_hide_column(fake_status_dir):
    """Pressing 'h' should hide the active column."""
    write_fake_session(fake_status_dir, "aaaa-1111")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        table = app.query_one(DataTable)
        assert len(table.columns) == 12
        await pilot.press("h")
        await pilot.pause()
        assert len(table.columns) == 11


@pytest.mark.asyncio
async def test_show_all_columns(fake_status_dir):
    """Pressing 'C' should restore all hidden columns."""
    write_fake_session(fake_status_dir, "aaaa-1111")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        table = app.query_one(DataTable)
        await pilot.press("h")
        await pilot.pause()
        assert len(table.columns) == 11
        await pilot.press("C")
        await pilot.pause()
        assert len(table.columns) == 16


@pytest.mark.asyncio
async def test_cannot_hide_last_column(fake_status_dir):
    """Should not be able to hide the last visible column."""
    write_fake_session(fake_status_dir, "aaaa-1111")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        table = app.query_one(DataTable)
        # Hide all but one column (start with 12 visible)
        for _ in range(12):
            await pilot.press("h")
            await pilot.pause()
        assert len(table.columns) == 1
        # Try to hide the last one
        await pilot.press("h")
        await pilot.pause()
        assert len(table.columns) == 1


@pytest.mark.asyncio
async def test_sort_resets_when_sorted_column_hidden(fake_status_dir):
    """Sort should reset when the sorted column is hidden."""
    write_fake_session(fake_status_dir, "aaaa-1111")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        # Move to status (index 3) and sort by it
        for _ in range(3):
            await pilot.press("right")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert app.sort_mode == "status"
        # Now hide it
        await pilot.press("h")
        await pilot.pause()
        assert app.sort_mode != "status"


@pytest.mark.asyncio
async def test_detail_panel_updates(fake_status_dir):
    """Detail panel should show cwd from test data when row is highlighted."""
    write_fake_session(fake_status_dir, "aaaa-1111", cwd="/home/user/myproject")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        status = app.query_one("#status-left", Static)
        # The first row should auto-highlight and populate status bar
        rendered = _render_static_text(status)
        assert "/home/user/myproject" in rendered


@pytest.mark.asyncio
async def test_running_agents_column(fake_status_dir):
    """Sessions with running_agents should show the count in the Agents column."""
    write_fake_session(fake_status_dir, "agent-1111", running_agents=3)
    write_fake_session(fake_status_dir, "agent-2222", running_agents=0)
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app, expected=2)
        table = app.query_one(DataTable)
        assert table.row_count == 2
        # Verify the running_agents field was loaded from hook JSON
        s = next(s for s in app._sessions if s.session_id == "agent-1111")
        assert s.running_agents == 3


@pytest.mark.asyncio
async def test_sort_by_files(fake_status_dir):
    """Sorting by files should order by file count descending."""
    write_fake_session(fake_status_dir, "few-files", files_edited=["a.py"])
    write_fake_session(fake_status_dir, "many-files", files_edited=["a.py", "b.py", "c.py"])
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app, expected=2)
        app.sort_mode = "files"
        table = app.query_one(DataTable)
        # First row should be the session with more files
        first_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        assert str(first_key.value) == "many-files"


# --- load_sessions unit tests ---


def test_load_sessions_basic(fake_status_dir):
    """Basic hook + poller pair should produce a correct SessionInfo."""
    write_fake_session(fake_status_dir, "sess-1111", turns=7, tool_count=15,
                       model="claude-opus-4-6-v1[1m]", git_branch="feature/x",
                       running_agents=2, error_count=3, files_edited=["a.py", "b.py"])
    sessions = load_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s.session_id == "sess-1111"
    assert s.turns == 7
    assert s.tool_count == 15
    assert s.model == "claude-opus-4-6-v1[1m]"
    assert s.git_branch == "feature/x"
    assert s.running_agents == 2
    assert s.error_count == 3
    assert s.files_edited == ["a.py", "b.py"]


def test_load_sessions_poller_wins_over_hook(fake_status_dir):
    """Poller values should override hook values for shared keys like model."""
    hook = {"session_id": "merge-test", "cwd": "/tmp", "status": "idle",
            "last_activity": _now_iso(), "started_at": _ago_iso(10),
            "model": "hook-model", "tool_count": 5, "running_agents": 0}
    poller = {"model": "poller-model", "tool_count": 20, "slug": "test-slug",
              "git_branch": "main", "last_user_msg": "hi", "last_assistant_msg": "hello",
              "input_tokens": 1000, "output_tokens": 500, "turns": 3, "custom_title": "",
              "cumulative_input_tokens": 0, "cumulative_output_tokens": 0,
              "subagent_input_tokens": 0, "subagent_output_tokens": 0,
              "error_count": 0, "subagent_count": 0, "files_edited": None, "stop_reason": ""}
    (fake_status_dir / "merge-test.json").write_text(json.dumps(hook))
    (fake_status_dir / "merge-test.poller.json").write_text(json.dumps(poller))
    sessions = load_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    # model: poller wins (both non-empty, poller takes priority)
    assert s.model == "poller-model"
    # tool_count: poller wins via or-fallback (poller non-zero)
    assert s.tool_count == 20
    # running_agents: always from hook
    assert s.running_agents == 0


def test_load_sessions_model_fallback_to_hook(fake_status_dir):
    """If poller model is empty, should fall back to hook model."""
    hook = {"session_id": "fb-test", "cwd": "/tmp", "status": "idle",
            "last_activity": _now_iso(), "started_at": _ago_iso(5),
            "model": "hook-model", "tool_count": 0, "running_agents": 0}
    poller = {"model": "", "slug": "test", "git_branch": "", "last_user_msg": "",
              "last_assistant_msg": "", "input_tokens": 0, "output_tokens": 0,
              "tool_count": 0, "turns": 0, "custom_title": "",
              "cumulative_input_tokens": 0, "cumulative_output_tokens": 0,
              "subagent_input_tokens": 0, "subagent_output_tokens": 0,
              "error_count": 0, "subagent_count": 0, "files_edited": None, "stop_reason": ""}
    (fake_status_dir / "fb-test.json").write_text(json.dumps(hook))
    (fake_status_dir / "fb-test.poller.json").write_text(json.dumps(poller))
    sessions = load_sessions()
    assert sessions[0].model == "hook-model"


def test_load_sessions_tool_count_fallback(fake_status_dir):
    """If poller tool_count is 0, should fall back to hook tool_count."""
    hook = {"session_id": "tc-test", "cwd": "/tmp", "status": "idle",
            "last_activity": _now_iso(), "started_at": _ago_iso(5),
            "model": "", "tool_count": 8, "running_agents": 0}
    poller = {"model": "", "slug": "test", "git_branch": "", "last_user_msg": "",
              "last_assistant_msg": "", "input_tokens": 0, "output_tokens": 0,
              "tool_count": 0, "turns": 0, "custom_title": "",
              "cumulative_input_tokens": 0, "cumulative_output_tokens": 0,
              "subagent_input_tokens": 0, "subagent_output_tokens": 0,
              "error_count": 0, "subagent_count": 0, "files_edited": None, "stop_reason": ""}
    (fake_status_dir / "tc-test.json").write_text(json.dumps(hook))
    (fake_status_dir / "tc-test.poller.json").write_text(json.dumps(poller))
    sessions = load_sessions()
    assert sessions[0].tool_count == 8


def test_load_sessions_pid_type_check(fake_status_dir):
    """Non-int pid values should become None."""
    hook = {"session_id": "pid-test", "cwd": "/tmp", "status": "idle",
            "last_activity": _now_iso(), "started_at": _ago_iso(5),
            "model": "", "tool_count": 0, "running_agents": 0, "pid": "not-an-int"}
    (fake_status_dir / "pid-test.json").write_text(json.dumps(hook))
    sessions = load_sessions()
    assert sessions[0].pid is None


def test_load_sessions_pid_int(fake_status_dir):
    """Int pid should be preserved."""
    hook = {"session_id": "pid-ok", "cwd": "/tmp", "status": "idle",
            "last_activity": _now_iso(), "started_at": _ago_iso(5),
            "model": "", "tool_count": 0, "running_agents": 0, "pid": 12345}
    (fake_status_dir / "pid-ok.json").write_text(json.dumps(hook))
    sessions = load_sessions()
    assert sessions[0].pid == 12345


def test_load_sessions_cleans_user_msg(fake_status_dir):
    """System-injected messages (starting with <) should be cleaned."""
    write_fake_session(fake_status_dir, "msg-test", last_user_msg="<system-reminder>stuff</system-reminder>")
    sessions = load_sessions()
    assert sessions[0].last_user_msg == ""


def test_load_sessions_extra_json_keys_ignored(fake_status_dir):
    """Extra keys in hook/poller JSON that don't map to SessionInfo fields should be ignored."""
    hook = {"session_id": "extra-test", "cwd": "/tmp", "status": "idle",
            "last_activity": _now_iso(), "started_at": _ago_iso(5),
            "model": "", "tool_count": 0, "running_agents": 0,
            "unknown_field": "should be ignored", "another_extra": 42}
    (fake_status_dir / "extra-test.json").write_text(json.dumps(hook))
    sessions = load_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == "extra-test"


def test_load_sessions_no_poller_file(fake_status_dir):
    """Missing poller file should still load from hook with defaults."""
    hook = {"session_id": "hook-only", "cwd": "/tmp/proj", "status": "thinking",
            "last_activity": _now_iso(), "started_at": _ago_iso(10),
            "model": "claude-sonnet-4-6-20260301", "tool_count": 3, "running_agents": 1}
    (fake_status_dir / "hook-only.json").write_text(json.dumps(hook))
    sessions = load_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s.status == "thinking"
    assert s.model == "claude-sonnet-4-6-20260301"
    assert s.tool_count == 3
    assert s.running_agents == 1
    assert s.turns == 0  # default


def test_load_sessions_empty_dir(fake_status_dir):
    """Empty status dir should return empty list."""
    sessions = load_sessions()
    assert sessions == []


# --- Purge dead sessions tests ---


def test_purge_dead_pid(fake_status_dir):
    """Session with a dead PID (99999) should be removed."""
    write_fake_session(fake_status_dir, "dead-1111", pid=99999)
    count = purge_dead_sessions()
    assert count == 1
    assert not (fake_status_dir / "dead-1111.json").exists()
    assert not (fake_status_dir / "dead-1111.poller.json").exists()


def test_purge_alive_pid(fake_status_dir):
    """Session with our own PID (alive) should be kept."""
    write_fake_session(fake_status_dir, "alive-2222", pid=os.getpid())
    count = purge_dead_sessions()
    assert count == 0
    assert (fake_status_dir / "alive-2222.json").exists()


def test_purge_stale_no_pid(fake_status_dir):
    """Session without pid field and last_activity 65min ago should be removed."""
    write_fake_session(
        fake_status_dir, "stale-3333",
        last_activity=_ago_iso(65),
        started_at=_ago_iso(120),
    )
    count = purge_dead_sessions()
    assert count == 1
    assert not (fake_status_dir / "stale-3333.json").exists()


def test_purge_recent_no_pid(fake_status_dir):
    """Session without pid field but recent activity should be kept."""
    write_fake_session(
        fake_status_dir, "recent-4444",
        last_activity=_now_iso(),
        started_at=_ago_iso(10),
    )
    count = purge_dead_sessions()
    assert count == 0
    assert (fake_status_dir / "recent-4444.json").exists()


@pytest.mark.asyncio
async def test_purge_keybinding(fake_status_dir):
    """Pressing R should purge dead sessions and remove them from the table."""
    write_fake_session(fake_status_dir, "dead-5555", pid=99999)
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        table = app.query_one(DataTable)
        assert table.row_count == 1
        await pilot.press("R")
        await _wait_for_rows(pilot, app, expected=0)
        assert table.row_count == 0


# --- _render_message unit tests ---


def test_render_message_empty_text():
    """Empty or None text should render as a dash placeholder."""
    result = _render_message("User", "")
    assert len(result) == 1
    assert "—" in str(result[0])


def test_render_message_none_text():
    """None text should render as a dash placeholder."""
    result = _render_message("Claude", None)
    assert len(result) == 1
    assert "—" in str(result[0])


def test_render_message_whitespace_only():
    """Whitespace-only text should render as a dash placeholder."""
    result = _render_message("User", "   \n  ")
    assert len(result) == 1
    assert "—" in str(result[0])


def test_render_message_normal_text():
    """Non-empty text should produce label + markdown renderable."""
    result = _render_message("User", "hello world")
    assert len(result) == 2
    assert "User" in str(result[0])


def test_render_message_truncation():
    """Text exceeding max_chars should be truncated with ellipsis."""
    long_text = "x" * 100
    result = _render_message("User", long_text, max_chars=50)
    assert len(result) == 2
    # Verify the markdown source was actually truncated with ellipsis
    assert result[1].markup.endswith("…")
    assert len(result[1].markup) == 51  # 50 chars + ellipsis


# --- Detail panel integration tests ---


@pytest.mark.asyncio
async def test_detail_panel_shows_user_and_assistant(fake_status_dir):
    """Detail panel should include both user and assistant message text."""
    write_fake_session(
        fake_status_dir, "detail-1111",
        last_user_msg="my user question",
        last_assistant_msg="my assistant answer",
    )
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        chat = app.query_one("#detail-chat", Static)
        rendered = _render_static_text(chat)
        assert "User" in rendered
        assert "Claude" in rendered
        assert "my user question" in rendered
        assert "my assistant answer" in rendered


@pytest.mark.asyncio
async def test_detail_panel_clears_when_sessions_removed(fake_status_dir):
    """Detail panel should clear when all sessions are purged."""
    write_fake_session(fake_status_dir, "temp-1111", pid=99999)
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        chat = app.query_one("#detail-chat", Static)
        # Detail should have content from the highlighted session
        assert isinstance(chat.content, Group)
        # Purge the dead session
        await pilot.press("R")
        await _wait_for_rows(pilot, app, expected=0)
        # Detail should now be empty
        assert str(chat.content) == ""


# --- get_claude_pids() unit tests ---


def test_get_claude_pids_basic():
    """Real claude sessions should be included."""
    ps_output = (
        "  PID COMMAND\n"
        " 1234 /usr/local/bin/claude\n"
        " 5678 /opt/homebrew/bin/claude -r\n"
    )
    with patch("cctop_dashboard.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=ps_output, stderr=""
        )
        pids = get_claude_pids()
    assert pids == {1234, 5678}


def test_get_claude_pids_excludes_desktop_app():
    """Claude.app processes should be excluded."""
    ps_output = (
        "  PID COMMAND\n"
        " 1234 /Applications/Claude.app/Contents/MacOS/Claude\n"
        " 5678 /usr/local/bin/claude\n"
    )
    with patch("cctop_dashboard.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=ps_output, stderr=""
        )
        pids = get_claude_pids()
    assert pids == {5678}


def test_get_claude_pids_excludes_teammates():
    """Teammate subagents (--parent-session-id) should be excluded."""
    ps_output = (
        "  PID COMMAND\n"
        " 1234 /usr/local/bin/claude --parent-session-id abc123\n"
        " 5678 /usr/local/bin/claude\n"
    )
    with patch("cctop_dashboard.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=ps_output, stderr=""
        )
        pids = get_claude_pids()
    assert pids == {5678}


def test_get_claude_pids_excludes_mcp_and_uvx():
    """MCP servers and uvx processes should be excluded."""
    ps_output = (
        "  PID COMMAND\n"
        " 1000 /usr/local/bin/claude\n"
        " 2000 mcp-server-claude --port 3000\n"
        " 3000 uvx claude-mcp\n"
        " 4000 caffeinate -w 1000\n"
    )
    with patch("cctop_dashboard.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=ps_output, stderr=""
        )
        pids = get_claude_pids()
    assert pids == {1000}


def test_get_claude_pids_handles_subprocess_error():
    """Should return empty set if ps fails."""
    with patch("cctop_dashboard.subprocess.run", side_effect=OSError("no ps")):
        pids = get_claude_pids()
    assert pids == set()


def test_get_claude_pids_excludes_non_claude_basename():
    """Processes where basename is not 'claude' should be excluded."""
    ps_output = (
        "  PID COMMAND\n"
        " 1000 /usr/local/bin/claude\n"
        " 2000 /usr/local/bin/claude-dev\n"
        " 3000 python claude_helper.py\n"
    )
    with patch("cctop_dashboard.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=ps_output, stderr=""
        )
        pids = get_claude_pids()
    assert pids == {1000}


# --- check_session_health() unit tests ---


def test_health_all_live():
    """All sessions have live PIDs, no warnings."""
    sessions = [
        SessionInfo(session_id="a", pid=100),
        SessionInfo(session_id="b", pid=200),
    ]
    pids = {100, 200}
    health = check_session_health(sessions, pids)
    assert not health.has_mismatch
    assert health.stale_ids == []
    assert health.untracked_count == 0
    assert health.message == ""


def test_health_stale_sessions():
    """Sessions with dead PIDs should appear in stale_ids."""
    sessions = [
        SessionInfo(session_id="a", pid=100),
        SessionInfo(session_id="b", pid=200),
    ]
    pids = {100}  # PID 200 is dead
    health = check_session_health(sessions, pids)
    assert health.has_mismatch
    assert health.stale_ids == ["b"]
    assert "1 stale session" in health.message


def test_health_untracked_processes():
    """More processes than tracked sessions, untracked_count > 0."""
    sessions = [
        SessionInfo(session_id="a", pid=100),
    ]
    pids = {100, 200, 300}  # 2 extra processes
    health = check_session_health(sessions, pids)
    assert health.has_mismatch
    assert health.untracked_count == 2
    assert "2 sessions not tracked" in health.message


def test_health_mixed_scenario():
    """Both stale sessions and untracked processes."""
    sessions = [
        SessionInfo(session_id="a", pid=100),
        SessionInfo(session_id="b", pid=200),  # dead
        SessionInfo(session_id="c", pid=300),  # dead
    ]
    pids = {100, 400, 500}  # 200 and 300 are gone, 400 and 500 are new
    health = check_session_health(sessions, pids)
    assert health.has_mismatch
    assert set(health.stale_ids) == {"b", "c"}
    # live_tracked = 3 - 2 = 1, untracked = 3 - 1 = 2
    assert health.untracked_count == 2
    msg = health.message
    assert "stale" in msg
    assert "not tracked" in msg


def test_health_no_pid_sessions_ignored():
    """Sessions without a PID field should not be flagged as stale."""
    sessions = [
        SessionInfo(session_id="a", pid=None),
        SessionInfo(session_id="b", pid=100),
    ]
    pids = {100}
    health = check_session_health(sessions, pids)
    assert health.stale_ids == []
    # live_tracked = 2 - 0 = 2, untracked = max(0, 1 - 2) = 0
    assert health.untracked_count == 0


def test_health_status_message_plural():
    """Plural form when multiple stale sessions."""
    sessions = [
        SessionInfo(session_id="a", pid=100),
        SessionInfo(session_id="b", pid=200),
    ]
    health = check_session_health(sessions, set())
    assert "2 stale sessions" in health.message


def test_health_status_message_singular():
    """Singular form with exactly one stale session."""
    sessions = [
        SessionInfo(session_id="a", pid=100),
    ]
    health = check_session_health(sessions, set())
    assert "1 stale session " in health.message


# --- Health bar TUI integration tests ---


@pytest.mark.asyncio
async def test_health_bar_shows_on_mismatch(fake_status_dir):
    """Health bar should be visible when there are stale sessions."""
    write_fake_session(fake_status_dir, "stale-session", pid=99999)
    app = SessionsDashboard()
    with patch("cctop_dashboard.get_claude_pids", return_value=set()):
        async with app.run_test() as pilot:
            await _wait_for_rows(pilot, app)
            bar = app.query_one("#health-bar", Static)
            assert "visible" in bar.classes
            rendered = _render_static_text(bar)
            assert "stale" in rendered


@pytest.mark.asyncio
async def test_health_bar_hidden_when_matching(fake_status_dir):
    """Health bar should be hidden when tracked sessions match processes."""
    my_pid = os.getpid()
    write_fake_session(fake_status_dir, "live-session", pid=my_pid)
    app = SessionsDashboard()
    with patch("cctop_dashboard.get_claude_pids", return_value={my_pid}):
        async with app.run_test() as pilot:
            await _wait_for_rows(pilot, app)
            bar = app.query_one("#health-bar", Static)
            assert "visible" not in bar.classes


@pytest.mark.asyncio
async def test_health_bar_shows_untracked(fake_status_dir):
    """Health bar should warn about untracked sessions."""
    my_pid = os.getpid()
    write_fake_session(fake_status_dir, "live-session", pid=my_pid)
    app = SessionsDashboard()
    # Return our PID plus two extras not in cctop
    with patch("cctop_dashboard.get_claude_pids", return_value={my_pid, 88888, 77777}):
        async with app.run_test() as pilot:
            await _wait_for_rows(pilot, app)
            bar = app.query_one("#health-bar", Static)
            assert "visible" in bar.classes
            rendered = _render_static_text(bar)
            assert "not tracked" in rendered


# --- tmux attach tests ---


@pytest.mark.asyncio
async def test_tmux_attach_binding_visible_with_metadata(fake_status_dir):
    """Tmux attach binding should be visible for sessions with tmux metadata."""
    write_fake_session(fake_status_dir, "tmux-sess", pid=12345)
    hook_path = fake_status_dir / "tmux-sess.json"
    hook = json.loads(hook_path.read_text())
    hook["tmux_session"] = "my-session"
    hook["tmux_window"] = "0"
    hook_path.write_text(json.dumps(hook))

    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        result = app.check_action("tmux_attach", ())
        assert result is True


@pytest.mark.asyncio
async def test_tmux_attach_binding_hidden_without_metadata(fake_status_dir):
    """Tmux attach binding should be hidden for sessions without tmux metadata."""
    write_fake_session(fake_status_dir, "no-tmux", pid=12345)
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        result = app.check_action("tmux_attach", ())
        assert result is None


@pytest.mark.asyncio
async def test_tmux_attach_binding_updates_on_navigation(fake_status_dir):
    """Binding visibility should update when navigating between sessions."""
    # Session with tmux
    write_fake_session(fake_status_dir, "with-tmux", pid=12345)
    hook1 = json.loads((fake_status_dir / "with-tmux.json").read_text())
    hook1["tmux_session"] = "my-session"
    (fake_status_dir / "with-tmux.json").write_text(json.dumps(hook1))

    # Session without tmux
    write_fake_session(fake_status_dir, "without-tmux", pid=54321)

    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app, expected=2)

        # Check first row
        first_check = app.check_action("tmux_attach", ())

        # Move to second row
        await pilot.press("down")
        await pilot.pause()

        second_check = app.check_action("tmux_attach", ())

        # One should be True, one should be None (order may vary by sort)
        checks = {first_check, second_check}
        assert True in checks and None in checks


# --- styled_status unit tests ---


def test_styled_status_idle():
    s = SessionInfo(status="idle", last_activity=_now_iso())
    assert styled_status(s).plain == "idle"


def test_styled_status_idle_awaiting_plan():
    s = SessionInfo(status="idle:awaiting_plan", last_activity=_now_iso())
    assert styled_status(s).plain == "awaiting plan"


def test_styled_status_idle_needs_input():
    s = SessionInfo(status="idle:needs_input", last_activity=_now_iso())
    assert styled_status(s).plain == "needs input"


def test_styled_status_awaiting_permission():
    s = SessionInfo(status="awaiting_permission", last_activity=_now_iso())
    assert styled_status(s).plain == "awaiting permission"


def test_styled_status_awaiting_mcp_input():
    s = SessionInfo(status="awaiting_mcp_input", last_activity=_now_iso())
    assert styled_status(s).plain == "awaiting mcp input"


def test_styled_status_error_rate_limit():
    s = SessionInfo(status="error:rate_limit", last_activity=_now_iso())
    result = styled_status(s)
    assert "rate limit" in result.plain
    assert "red" in str(result.style)


def test_styled_status_error_auth_failed():
    s = SessionInfo(status="error:auth_failed", last_activity=_now_iso())
    assert "auth failed" in styled_status(s).plain


def test_styled_status_error_max_output_tokens():
    s = SessionInfo(status="error:max_output_tokens", last_activity=_now_iso())
    assert "max output tokens" in styled_status(s).plain


def test_styled_status_planning_mode_overrides_tool():
    s = SessionInfo(status="tool:Edit", last_activity=_now_iso(), planning_mode=True)
    assert styled_status(s).plain == "planning"


def test_styled_status_planning_mode_no_override_for_non_tool():
    s = SessionInfo(status="thinking", last_activity=_now_iso(), planning_mode=True)
    assert styled_status(s).plain == "thinking"


def test_styled_status_mcp_tool():
    s = SessionInfo(status="tool:mcp__atlassian__jira_search", last_activity=_now_iso())
    assert styled_status(s).plain == "mcp:atlassian"


def test_styled_status_mcp_tool_short_name():
    s = SessionInfo(status="tool:mcp__myserver__do_thing", last_activity=_now_iso())
    assert styled_status(s).plain == "mcp:myserver"


def test_styled_status_reviewing_subagent():
    s = SessionInfo(status="tool:Agent", last_activity=_now_iso(), active_subagent_type="code-reviewer")
    assert styled_status(s).plain == "reviewing"


def test_styled_status_reviewing_pr_review():
    s = SessionInfo(status="tool:Agent", last_activity=_now_iso(), active_subagent_type="pr-review-toolkit:code-reviewer")
    assert styled_status(s).plain == "reviewing"


def test_styled_status_researching_subagent():
    s = SessionInfo(status="tool:Agent", last_activity=_now_iso(), active_subagent_type="Explore")
    assert styled_status(s).plain == "researching"


def test_styled_status_researching_researcher():
    s = SessionInfo(status="tool:Agent", last_activity=_now_iso(), active_subagent_type="branch-researcher")
    assert styled_status(s).plain == "researching"


def test_styled_status_generic_subagent():
    s = SessionInfo(status="tool:Agent", last_activity=_now_iso(), active_subagent_type="general-purpose")
    # No match on review/explore/research, falls through to STATUS_STYLE_MAP["tool:Agent"]
    assert styled_status(s).plain == "subagent"


def test_styled_status_subagent_no_type():
    s = SessionInfo(status="tool:Agent", last_activity=_now_iso(), active_subagent_type="")
    assert styled_status(s).plain == "subagent"


def test_styled_status_stale():
    s = SessionInfo(status="idle", last_activity=_ago_iso(120))
    assert styled_status(s).plain == "stale"


def test_styled_status_unknown_tool_catchall():
    s = SessionInfo(status="tool:SomeNewTool", last_activity=_now_iso())
    assert styled_status(s).plain == "SomeNewTool"


def test_styled_status_known_tools():
    """Verify key tools from STATUS_STYLE_MAP render their labels."""
    cases = [
        ("tool:Bash", "running cmd"),
        ("tool:WebSearch", "searching web"),
        ("tool:Read", "reading"),
        ("tool:Edit", "editing"),
        ("tool:Glob", "searching"),
        ("tool:AskUserQuestion", "asking user"),
        ("tool:EnterPlanMode", "entering plan"),
        ("tool:SendMessage", "messaging"),
        ("tool:Skill", "running skill"),
    ]
    for status, expected in cases:
        s = SessionInfo(status=status, last_activity=_now_iso())
        assert styled_status(s).plain == expected, f"Failed for {status}"


# --- New field loading tests ---


def test_load_sessions_new_hook_fields(fake_status_dir):
    """New hook fields should be loaded into SessionInfo."""
    write_fake_session(fake_status_dir, "new-fields",
                       planning_mode=True, last_tool="ExitPlanMode",
                       active_subagent_type="Explore",
                       error_type="rate_limit", error_details="Try again later",
                       tool_failures=3)
    sessions = load_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s.planning_mode is True
    assert s.last_tool == "ExitPlanMode"
    assert s.active_subagent_type == "Explore"
    assert s.error_type == "rate_limit"
    assert s.error_details == "Try again later"
    assert s.tool_failures == 3


def test_load_sessions_new_fields_default(fake_status_dir):
    """New hook fields should default to zero/empty/false when absent."""
    write_fake_session(fake_status_dir, "defaults")
    sessions = load_sessions()
    s = sessions[0]
    assert s.planning_mode is False
    assert s.last_tool == ""
    assert s.active_subagent_type == ""
    assert s.error_type == ""
    assert s.error_details == ""
    assert s.tool_failures == 0


# --- Status rendering in table integration test ---


@pytest.mark.asyncio
async def test_idle_variant_renders(fake_status_dir):
    """Idle variants should render with correct labels in the table."""
    write_fake_session(fake_status_dir, "plan-wait", status="idle:awaiting_plan")
    write_fake_session(fake_status_dir, "input-wait", status="idle:needs_input")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app, expected=2)
        # Verify sessions loaded with correct statuses
        statuses = {s.status for s in app._sessions}
        assert "idle:awaiting_plan" in statuses
        assert "idle:needs_input" in statuses


# --- Config file tests ---


@pytest.fixture
def fake_config_dir(tmp_path):
    """Monkeypatch STATUS_DIR and CONFIG_PATH to a temp dir."""
    config_path = tmp_path / "config.toml"
    with patch("cctop_dashboard.STATUS_DIR", tmp_path), \
         patch("cctop_dashboard.CONFIG_PATH", config_path):
        yield tmp_path, config_path


def test_load_config_missing_file(fake_config_dir):
    """Missing config file returns defaults."""
    cfg = load_config()
    assert cfg["ui"]["theme"] == "textual-dark"
    assert cfg["sort"]["column"] == "activity"
    assert cfg["columns"]["hidden"] == ["errors", "started", "stop_reason", "tokens"]


def test_load_config_empty_file(fake_config_dir):
    _, config_path = fake_config_dir
    config_path.write_text("")
    cfg = load_config()
    assert cfg["ui"]["theme"] == "textual-dark"
    assert cfg["sort"]["column"] == "activity"
    assert cfg["columns"]["hidden"] == ["errors", "started", "stop_reason", "tokens"]


def test_load_config_partial(fake_config_dir):
    """Config with only some keys still gets defaults for the rest."""
    _, config_path = fake_config_dir
    config_path.write_text('[ui]\ntheme = "dracula"\n')
    cfg = load_config()
    assert cfg["ui"]["theme"] == "dracula"


def test_load_config_invalid_toml(fake_config_dir):
    """Malformed TOML falls back to defaults."""
    _, config_path = fake_config_dir
    config_path.write_text("this is not [valid toml")
    cfg = load_config()
    assert cfg["ui"]["theme"] == "textual-dark"
    assert cfg["sort"]["column"] == "activity"
    assert cfg["columns"]["hidden"] == ["errors", "started", "stop_reason", "tokens"]


def test_save_config_creates_file(fake_config_dir):
    _, config_path = fake_config_dir
    save_config({"ui": {"theme": "nord"}})
    assert config_path.exists()
    cfg = load_config()
    assert cfg["ui"]["theme"] == "nord"


def test_save_config_merges(fake_config_dir):
    """Saving a new section preserves existing sections."""
    save_config({"ui": {"theme": "dracula"}})
    save_config({"ui": {"theme": "monokai"}})
    cfg = load_config()
    assert cfg["ui"]["theme"] == "monokai"


def test_reset_preserves_config(fake_config_dir):
    """_reset_session_data should delete session files but keep config.toml."""
    tmp_dir, config_path = fake_config_dir
    # Write config and a session file
    save_config({"ui": {"theme": "nord"}})
    (tmp_dir / "sess-123.json").write_text("{}")
    (tmp_dir / "sess-123.poller.json").write_text("{}")
    (tmp_dir / "sess-123.debug.jsonl").write_text("")
    _reset_session_data()
    assert not (tmp_dir / "sess-123.json").exists()
    assert not (tmp_dir / "sess-123.poller.json").exists()
    assert not (tmp_dir / "sess-123.debug.jsonl").exists()
    assert config_path.exists()
    cfg = load_config()
    assert cfg["ui"]["theme"] == "nord"


@pytest.mark.asyncio
async def test_theme_persists_across_restart(fake_config_dir):
    """Theme set in one app run should be loaded by the next."""
    tmp_dir, _ = fake_config_dir
    save_config({"ui": {"theme": "dracula"}})
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        assert app.theme == "dracula"


def test_save_config_sort(fake_config_dir):
    """Sort settings round-trip through config."""
    save_config({"sort": {"column": "status", "reverse": False}})
    cfg = load_config()
    assert cfg["sort"]["column"] == "status"
    assert cfg["sort"]["reverse"] is False


def test_save_config_hidden_columns(fake_config_dir):
    """Hidden columns round-trip through config."""
    save_config({"columns": {"hidden": ["branch", "model"]}})
    cfg = load_config()
    assert cfg["columns"]["hidden"] == ["branch", "model"]


@pytest.mark.asyncio
async def test_sort_persists_across_restart(fake_config_dir):
    """Sort mode saved to config should be loaded on next startup."""
    save_config({"sort": {"column": "status", "reverse": False}})
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        assert app.sort_mode == "status"
        assert app.sort_reverse is False


@pytest.mark.asyncio
async def test_hidden_columns_persist_across_restart(fake_config_dir):
    """Hidden columns saved to config should be loaded on next startup."""
    save_config({"columns": {"hidden": ["branch", "model"]}})
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        assert app._hidden_columns == {"branch", "model"}
        table = app.query_one(DataTable)
        assert len(table.columns) == 14  # 16 total - 2 hidden


@pytest.mark.asyncio
async def test_sort_change_persists_to_config(fake_config_dir):
    """Changing sort via keybinding should persist to config."""
    tmp_dir, config_path = fake_config_dir
    write_fake_session(tmp_dir, "aaaa-1111")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        # Move to status column (index 3) and sort
        for _ in range(3):
            await pilot.press("right")
        await pilot.press("s")
        await pilot.pause()
    cfg = load_config()
    assert cfg["sort"]["column"] == "status"


@pytest.mark.asyncio
async def test_hide_column_persists_to_config(fake_config_dir):
    """Hiding a column should persist to config."""
    tmp_dir, config_path = fake_config_dir
    write_fake_session(tmp_dir, "aaaa-1111")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        await pilot.press("h")
        await pilot.pause()
    cfg = load_config()
    assert len(cfg["columns"]["hidden"]) == 5  # 4 default + 1 newly hidden


# --- Detail session info tests ---


def test_status_bar_shows_session_id():
    """Status bar right should include the full session ID."""
    s = SessionInfo(
        session_id="abc12345-6789-0def-ghij-klmnopqrstuv",
        model="claude-opus-4-6",
    )
    markup = SessionsDashboard._status_right(s)
    assert "abc12345-6789-0def-ghij-klmnopqrstuv" in markup


def test_status_bar_shows_full_model():
    """Status bar right should include the full model name."""
    s = SessionInfo(
        session_id="test-1234",
        model="claude-opus-4-6",
    )
    markup = SessionsDashboard._status_right(s)
    assert "claude-opus-4-6" in markup


def test_status_bar_shows_path_and_branch():
    """Status bar left should include path and branch."""
    s = SessionInfo(
        cwd="/Users/me/project",
        git_branch="main",
    )
    markup = SessionsDashboard._status_left(s)
    assert "/Users/me/project" in markup
    assert "main" in markup


def test_detail_session_info_shows_timing():
    """Session info should include start time and duration."""
    s = SessionInfo(
        session_id="test-1234",
        last_activity=_now_iso(),
        started_at=_ago_iso(90),  # 90 minutes ago
        turns=12,
        tool_count=45,
    )
    tbl = SessionsDashboard._detail_session_info(s)
    text = _render_table_text(tbl)
    assert "1h30m" in text
    assert "12" in text     # Turns row
    assert "45" in text     # Tools row


def test_detail_session_info_shows_files_and_subagents():
    """Session info should show files edited and subagent count."""
    s = SessionInfo(
        session_id="test-1234",
        last_activity=_now_iso(),
        started_at=_ago_iso(10),
        files_edited=["/a.py", "/b.py", "/c.py"],
        subagent_count=2,
    )
    tbl = SessionsDashboard._detail_session_info(s)
    text = _render_table_text(tbl)
    assert "3" in text and "edited" in text
    assert "Agents" in text and "2" in text


def test_detail_session_info_shows_tokens():
    """Session info should show context tokens."""
    s = SessionInfo(
        session_id="test-1234",
        last_activity=_now_iso(),
        input_tokens=150000,
    )
    tbl = SessionsDashboard._detail_session_info(s)
    text = _render_table_text(tbl)
    assert "150k" in text and "ctx" in text


def test_detail_session_info_shows_pid():
    """Session info should show PID when available."""
    s = SessionInfo(
        session_id="test-1234",
        last_activity=_now_iso(),
        pid=84726,
    )
    tbl = SessionsDashboard._detail_session_info(s)
    text = _render_table_text(tbl)
    assert "PID" in text and "84726" in text


def test_detail_session_info_shows_tmux():
    """Session info should show tmux session:window when available."""
    s = SessionInfo(
        session_id="test-1234",
        last_activity=_now_iso(),
        tmux_session="local",
        tmux_window="6",
    )
    tbl = SessionsDashboard._detail_session_info(s)
    text = _render_table_text(tbl)
    assert "Tmux" in text and "local:6" in text


def test_detail_session_info_omits_tmux_when_empty():
    """Session info should not mention tmux when not available."""
    s = SessionInfo(
        session_id="test-1234",
        last_activity=_now_iso(),
    )
    tbl = SessionsDashboard._detail_session_info(s)
    text = _render_table_text(tbl)
    assert "Tmux" not in text


def test_detail_session_info_shows_errors():
    """Session info should show errors in red when present."""
    s = SessionInfo(
        session_id="test-1234",
        last_activity=_now_iso(),
        error_count=3,
        tool_failures=1,
        error_details="rate_limit",
    )
    tbl = SessionsDashboard._detail_session_info(s)
    text = _render_table_text(tbl)
    assert "3 errors" in text
    assert "1 failure" in text
    assert "rate_limit" in text


def test_detail_session_info_omits_errors_when_zero():
    """Session info should not show error line when no errors."""
    s = SessionInfo(
        session_id="test-1234",
        last_activity=_now_iso(),
        error_count=0,
        tool_failures=0,
    )
    tbl = SessionsDashboard._detail_session_info(s)
    text = _render_table_text(tbl)
    assert "Errors" not in text
    assert "failure" not in text


@pytest.mark.asyncio
async def test_detail_panel_includes_session_section(fake_status_dir):
    """Detail panel should render the Session info section."""
    write_fake_session(
        fake_status_dir, "info-1111",
        model="claude-sonnet-4-6-20260301",
        pid=12345,
        tmux_session="dev",
        tmux_window="3",
    )
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        # Status bar should show model + session ID
        status_r = app.query_one("#status-right", Static)
        status_text = _render_static_text(status_r)
        assert "info-1111" in status_text
        assert "claude-sonnet-4-6-20260301" in status_text
        # Info panel should show PID, tmux
        info = app.query_one("#detail-info", Static)
        rendered = _render_static_text(info)
        assert "12345" in rendered  # PID row
        assert "dev:3" in rendered  # Tmux row
