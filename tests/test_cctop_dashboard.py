# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=3.0.0", "pytest>=8.0", "pytest-asyncio>=0.23"]
# ///
"""Tests for the cctop dashboard TUI."""
from __future__ import annotations

import json
import os
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
    SortPicker,
    _render_message,
    format_tokens,
    format_relative_time,
    estimate_cost,
    purge_dead_sessions,
    STATUS_DIR,
)


# --- Helpers ---

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago_iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


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


def test_estimate_cost_sonnet():
    # 100k base input + 50k output, no cache, no subagent
    cost = estimate_cost("claude-sonnet-4-6-20260301", 100000, 50000, 0, 0, 0, 0, 0, 0)
    assert cost.startswith("$")


def test_estimate_cost_cache_read_much_cheaper():
    # Same total tokens but as cache reads should be ~10x cheaper
    base_cost = estimate_cost("claude-opus-4-6-v1", 1000000, 0, 0, 0, 0, 0, 0, 0)
    cache_cost = estimate_cost("claude-opus-4-6-v1", 0, 0, 1000000, 0, 0, 0, 0, 0)
    # base_cost = $5.00, cache_cost = $0.50
    base_val = float(base_cost.strip("$"))
    cache_val = float(cache_cost.strip("$"))
    assert base_val == 5.00
    assert cache_val == 0.50


def test_format_relative_time_empty():
    assert format_relative_time("") == ""


def test_format_relative_time_recent():
    assert format_relative_time(_now_iso()) == "now"


def test_format_relative_time_minutes():
    result = format_relative_time(_ago_iso(5))
    assert "m ago" in result


# --- TUI integration tests ---

@pytest.fixture
def fake_status_dir(tmp_path):
    """Create a temp dir and monkeypatch STATUS_DIR to point there."""
    with patch("cctop_dashboard.STATUS_DIR", tmp_path):
        yield tmp_path


@pytest.mark.asyncio
async def test_app_starts_empty(fake_status_dir):
    """Empty status dir → 11 columns, 0 rows."""
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        table = app.query_one(DataTable)
        assert len(table.columns) == 11
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_sessions_render(fake_status_dir):
    """Two fake sessions should appear as rows."""
    write_fake_session(fake_status_dir, "aaaa-1111", turns=3)
    write_fake_session(fake_status_dir, "bbbb-2222", turns=7)
    app = SessionsDashboard()
    async with app.run_test() as pilot:
        await pilot.pause()
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
        await pilot.pause()
        detail = app.query_one("#detail", Static)
        # The first row should auto-highlight and populate detail
        rendered = _render_static_text(detail)
        assert "/home/user/myproject" in rendered


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
    """Session without pid field and last_activity 10min ago should be removed."""
    write_fake_session(
        fake_status_dir, "stale-3333",
        last_activity=_ago_iso(10),
        started_at=_ago_iso(30),
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
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 1
        await pilot.press("R")
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
        detail = app.query_one("#detail", Static)
        # Detail should have content from the highlighted session
        assert isinstance(detail.content, Group)
        # Purge the dead session
        await pilot.press("R")
        await pilot.pause()
        # Detail should now be empty
        assert str(detail.content) == ""
