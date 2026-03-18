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
    SortPicker,
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
    STATUS_DIR,
)


# --- Helpers ---

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
                       started_at: str | None = None) -> None:
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
    """Create a temp dir and monkeypatch STATUS_DIR to point there."""
    with patch("cctop_dashboard.STATUS_DIR", tmp_path):
        yield tmp_path


@pytest.mark.asyncio
async def test_app_starts_empty(fake_status_dir):
    """Empty status dir → 16 columns, 0 rows."""
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        table = app.query_one(DataTable)
        assert len(table.columns) == 16
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
async def test_sort_popup_opens(fake_status_dir):
    """Pressing 's' should push SortPicker as active screen."""
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SortPicker)


@pytest.mark.asyncio
async def test_sort_changes(fake_status_dir):
    """Selecting 'turns' in sort picker updates sort_mode."""
    write_fake_session(fake_status_dir, "aaaa-1111", turns=3)
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        # Navigate to "Turns" (index 4) and select
        option_list = app.screen.query_one("#sort-list")
        option_list.highlighted = 4  # "Turns"
        await pilot.press("enter")
        await pilot.pause()
        assert app.sort_mode == "turns"


@pytest.mark.asyncio
async def test_detail_panel_updates(fake_status_dir):
    """Detail panel should show cwd from test data when row is highlighted."""
    write_fake_session(fake_status_dir, "aaaa-1111", cwd="/home/user/myproject")
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await _wait_for_rows(pilot, app)
        detail = app.query_one("#detail", Static)
        # The first row should auto-highlight and populate detail
        rendered = _render_static_text(detail)
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
        detail = app.query_one("#detail", Static)
        rendered = _render_static_text(detail)
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
        detail = app.query_one("#detail", Static)
        # Detail should have content from the highlighted session
        assert isinstance(detail.content, Group)
        # Purge the dead session
        await pilot.press("R")
        await _wait_for_rows(pilot, app, expected=0)
        # Detail should now be empty
        assert str(detail.content) == ""


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
