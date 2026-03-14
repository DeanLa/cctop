# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=3.0.0", "pytest>=8.0", "pytest-asyncio>=0.23"]
# ///
"""Tests for the cctop dashboard TUI."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import DataTable, Static

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin" / "scripts"))

from cctop_dashboard import (
    SessionsDashboard,
    SortPicker,
    format_tokens,
    format_relative_time,
    estimate_cost,
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
                       custom_title: str = "") -> None:
    """Write a pair of hook + poller JSON files into tmpdir."""
    hook = {
        "session_id": sid,
        "cwd": cwd,
        "status": status,
        "last_activity": _now_iso(),
        "started_at": _ago_iso(30),
        "model": model,
        "tool_count": 0,
    }
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
    """Empty status dir → 12 columns, 0 rows."""
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
        content = str(detail.content)
        assert "/home/user/myproject" in content
