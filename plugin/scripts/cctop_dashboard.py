# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=3.0.0"]
# ///
"""cctop — Claude Code Sessions TUI dashboard.

Read-only frontend. All data comes from session-status JSON files
written by the hook (cctop-hook.sh) and the poller (cctop-poller.py).
"""

from __future__ import annotations

# --- Imports ---
import json
import os
import re
import shutil
import subprocess
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from dataclasses import dataclass, field

from rich.console import Group
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

# --- Constants ---

STATUS_DIR = Path.home() / ".cctop"
CONTEXT_WINDOW = 200_000
STALE_SECONDS = 60 * 60
HEALTH_CHECK_INTERVAL = 10.0  # seconds between ps-based health checks

def _plural(n: int, word: str) -> str:
    """Return e.g. '3 files' or '1 file'."""
    return f"{n} {word}{'s' if n != 1 else ''}"


def _clean_user_msg(msg: str) -> str:
    """Filter out system-injected messages (task notifications, reminders, etc.)."""
    if msg.startswith("<"):
        return ""
    return msg


def _render_message(label: str, text: str | None, max_chars: int = 500) -> list[Text | RichMarkdown]:
    """Render a labeled message as markdown. Returns list of Rich renderables."""
    text = (text or "").strip()
    if not text:
        return [Text.from_markup(f"[dim]{label}:[/dim] —")]
    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    return [
        Text.from_markup(f"[dim]{label}:[/dim]"),
        RichMarkdown(text),
    ]


STATUS_STYLE_MAP: dict[str, tuple[str, str]] = {
    "idle": ("green", "idle"),
    "thinking": ("yellow", "thinking"),
    "started": ("blue", "started"),
    "resumed": ("#5fd7ff", "resumed"),
    "tool:Bash": ("green", "running cmd"),
    "tool:WebSearch": ("magenta", "searching web"),
    "tool:WebFetch": ("magenta", "searching web"),
    "tool:Agent": ("#af87ff", "subagent"),
    "tool:Read": ("cyan", "reading"),
    "tool:Edit": ("#ff8700", "editing"),
    "tool:Write": ("#ff8700", "editing"),
    "tool:Glob": ("cyan", "searching"),
    "tool:Grep": ("cyan", "searching"),
    "ended": ("dim", "ended"),
}

def friendly_model_name(model: str) -> str:
    """Human-friendly short name for the table column.

    E.g. "claude-sonnet-4-6-20260301" → "sonnet 4.6",
         "claude-opus-4-6-v1[1m]" → "opus 4.6",
         "claude-haiku-4-5-20251001" → "haiku 4.5".
    Falls back to first 12 chars for unknown models.
    """
    m = re.match(r"claude-(\w+)-(\d+)-(\d+)", model)
    if m:
        family = m.group(1)
        major = m.group(2)
        minor = m.group(3)
        return f"{family} {major}.{minor}"
    return model[:12] if model else ""


def format_start_time(iso_str: str) -> str:
    """Convert ISO UTC timestamp to local time display.

    Returns "14:30" for today, "Mar 15 14:30" for other days.
    """
    if not iso_str:
        return ""
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local = ts.astimezone()
        now_local = datetime.now().astimezone()
        if local.date() == now_local.date():
            return local.strftime("%H:%M")
        return local.strftime("%b %d %H:%M")
    except (ValueError, TypeError):
        return ""


def format_stop_reason(reason: str) -> str:
    """Map API stop reasons to short labels."""
    if not reason:
        return ""
    mapping = {
        "end_turn": "done",
        "tool_use": "tool",
        "max_tokens": "limit",
    }
    return mapping.get(reason, reason)


def _parse_age_seconds(iso_str: str) -> float | None:
    """Parse ISO timestamp and return seconds since then, or None on failure."""
    if not iso_str:
        return None
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except (ValueError, TypeError):
        return None


def format_duration(started_at: str) -> str:
    """Format elapsed time since an ISO timestamp. E.g. '1h23m', '5m'."""
    elapsed = _parse_age_seconds(started_at)
    if elapsed is None or elapsed < 0:
        return ""
    minutes = int(elapsed) // 60
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m"


def format_relative_time(iso_str: str) -> str:
    """Format an ISO timestamp as relative time. E.g. '2m ago', '1h ago'."""
    age = _parse_age_seconds(iso_str)
    if age is None:
        return ""
    if age < 60:
        return "now"
    minutes = int(age) // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def format_tokens(total: int) -> str:
    """Format a token count compactly. E.g. '145k'."""
    if total == 0:
        return ""
    if total < 1000:
        return str(total)
    return f"{total // 1000}k"


# --- Data structures ---


@dataclass
class SessionInfo:
    """Aggregated info for one Claude Code session."""

    session_id: str = ""
    cwd: str = ""
    status: str = ""
    last_activity: str = ""
    started_at: str = ""
    slug: str = ""
    git_branch: str = ""
    project_name: str = ""
    model: str = ""
    last_user_msg: str = ""
    last_assistant_msg: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    custom_title: str = ""
    tool_count: int = 0
    # Phase 2 fields
    turns: int = 0
    files_edited: list[str] | None = None
    subagent_count: int = 0
    error_count: int = 0
    stop_reason: str = ""
    pid: int | None = None
    running_agents: int = 0
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    cumulative_cache_read_tokens: int = 0
    cumulative_cache_creation_tokens: int = 0
    subagent_input_tokens: int = 0
    subagent_output_tokens: int = 0
    subagent_cache_read_tokens: int = 0
    subagent_cache_creation_tokens: int = 0

    @property
    def context_tokens(self) -> int:
        """Current context window usage (input tokens from latest turn)."""
        return self.input_tokens



# --- Helper functions ---


def styled_status(raw: str, last_activity: str) -> Text:
    """Return a Rich Text object with colour-coded status."""
    age = _parse_age_seconds(last_activity)
    if age is not None and age > STALE_SECONDS:
        return Text("stale", style="dim")

    if raw in STATUS_STYLE_MAP:
        colour, label = STATUS_STYLE_MAP[raw]
        return Text(label, style=colour)

    # tool:* catch-all
    if raw.startswith("tool:"):
        tool_name = raw.split(":", 1)[1]
        return Text(tool_name, style="cyan")

    return Text(raw or "?", style="dim")


# --- Column definitions (single source of truth) ---


@dataclass(frozen=True)
class ColumnDef:
    """Definition for one table column: header, cell renderer, and optional sort config."""

    key: str                          # internal identifier, e.g. "slug"
    cell: Callable[[SessionInfo], object]  # renders a SessionInfo into a cell value
    header: str = ""                  # empty = derive from key
    sort_label: str = ""              # non-empty = appears in sort picker
    sort_key: Callable[[SessionInfo], object] | None = None  # extracts comparable value for sorting
    reverse_sort: bool = False        # True = largest/newest first
    sort_position: int = 0            # order in sort picker; 0 = not sortable

    def __post_init__(self) -> None:
        if not self.header:
            object.__setattr__(self, "header", self.key.replace("_", " ").title())


COLUMNS: tuple[ColumnDef, ...] = (
    ColumnDef("slug",
              cell=lambda s: (
                  Text.assemble(("● ", "#e0af68"), s.custom_title) if s.custom_title
                  else Text.assemble(("○ ", "dim"), s.session_id[:8])
              ),
              sort_label="Name", sort_position=2,
              sort_key=lambda s: (s.custom_title or s.slug or s.session_id).lower()),
    ColumnDef("project",
              cell=lambda s: s.project_name or (os.path.basename(s.cwd) if s.cwd else "")),
    ColumnDef("branch",
              cell=lambda s: s.git_branch[:20]),
    ColumnDef("status",
              cell=lambda s: styled_status(s.status, s.last_activity),
              sort_label="Status", sort_position=3,
              sort_key=lambda s: s.status.lower()),
    ColumnDef("model",
              cell=lambda s: friendly_model_name(s.model)),
    ColumnDef("ctx_pct", header="Ctx%",
              cell=lambda s: f"{s.context_tokens * 100 // CONTEXT_WINDOW}%" if s.context_tokens else ""),
    ColumnDef("tokens",
              cell=lambda s: format_tokens(s.context_tokens),
              sort_label="Tokens", sort_position=6, reverse_sort=True,
              sort_key=lambda s: s.context_tokens),
    ColumnDef("tools",
              cell=lambda s: str(s.tool_count) if s.tool_count else "",
              sort_label="Tool Count", sort_position=7, reverse_sort=True,
              sort_key=lambda s: s.tool_count),
    ColumnDef("files",
              cell=lambda s: str(len(s.files_edited)) if s.files_edited else "",
              sort_label="Files Edited", sort_position=8, reverse_sort=True,
              sort_key=lambda s: len(s.files_edited) if s.files_edited else 0),
    ColumnDef("agents",
              cell=lambda s: str(s.running_agents) if s.running_agents else "",
              sort_label="Running Agents", sort_position=9, reverse_sort=True,
              sort_key=lambda s: s.running_agents),
    ColumnDef("errors",
              cell=lambda s: Text(str(s.error_count), style="red") if s.error_count else "",
              sort_label="Errors", sort_position=10, reverse_sort=True,
              sort_key=lambda s: s.error_count),
    ColumnDef("turns",
              cell=lambda s: str(s.turns) if s.turns else "",
              sort_label="Turns", sort_position=5, reverse_sort=True,
              sort_key=lambda s: s.turns),
    ColumnDef("stop_reason", header="StopRsn",
              cell=lambda s: format_stop_reason(s.stop_reason)),
    ColumnDef("duration",
              cell=lambda s: format_duration(s.started_at),
              sort_label="Duration", sort_position=4, reverse_sort=True,
              sort_key=lambda s: s.started_at or ""),
    ColumnDef("started",
              cell=lambda s: format_start_time(s.started_at)),
    ColumnDef("activity",
              cell=lambda s: format_relative_time(s.last_activity),
              sort_label="Last Activity", sort_position=1, reverse_sort=True,
              sort_key=lambda s: s.last_activity or ""),
)

# Derived from COLUMNS — used by SortPicker and the dashboard
SORT_OPTIONS: list[tuple[str, str]] = [
    (c.key, c.sort_label)
    for c in sorted(
        (c for c in COLUMNS if c.sort_position > 0),
        key=lambda c: c.sort_position,
    )
]
_COLUMN_BY_KEY: dict[str, ColumnDef] = {c.key: c for c in COLUMNS}
_COLUMN_HEADERS: tuple[str, ...] = tuple(c.header for c in COLUMNS)


def _row_cells(s: SessionInfo) -> tuple:
    """Compute all cell values for one session row."""
    return tuple(c.cell(s) for c in COLUMNS)


def _read_json(path: Path) -> dict:
    """Read a JSON file, returning an empty dict on any error."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _iter_hook_files():
    """Yield (filepath, hook_dict) for each valid hook file in STATUS_DIR."""
    if not STATUS_DIR.is_dir():
        return
    for fp in STATUS_DIR.glob("*.json"):
        if fp.name.endswith(".poller.json"):
            continue
        hook = _read_json(fp)
        if hook:
            yield fp, hook


def _build_session_info(sid: str, hook: dict, poller: dict) -> SessionInfo:
    """Combine hook and poller data into a single SessionInfo."""
    raw_pid = hook.get("pid")
    return SessionInfo(
        session_id=sid,
        cwd=hook.get("cwd", ""),
        status=hook.get("status", ""),
        last_activity=hook.get("last_activity", ""),
        started_at=hook.get("started_at", ""),
        pid=raw_pid if isinstance(raw_pid, int) else None,
        # Hook-only fields
        running_agents=hook.get("running_agents", 0),
        # Poller-only fields
        slug=poller.get("slug", ""),
        git_branch=poller.get("git_branch", ""),
        project_name=poller.get("project_name", ""),
        last_user_msg=_clean_user_msg(poller.get("last_user_msg", "")),
        last_assistant_msg=poller.get("last_assistant_msg", ""),
        input_tokens=poller.get("input_tokens", 0),
        output_tokens=poller.get("output_tokens", 0),
        custom_title=poller.get("custom_title", ""),
        turns=poller.get("turns", 0),
        files_edited=poller.get("files_edited"),
        subagent_count=poller.get("subagent_count", 0),
        error_count=poller.get("error_count", 0),
        stop_reason=poller.get("stop_reason", ""),
        cumulative_input_tokens=poller.get("cumulative_input_tokens", 0),
        cumulative_output_tokens=poller.get("cumulative_output_tokens", 0),
        cumulative_cache_read_tokens=poller.get("cumulative_cache_read_tokens", 0),
        cumulative_cache_creation_tokens=poller.get("cumulative_cache_creation_tokens", 0),
        subagent_input_tokens=poller.get("subagent_input_tokens", 0),
        subagent_output_tokens=poller.get("subagent_output_tokens", 0),
        subagent_cache_read_tokens=poller.get("subagent_cache_read_tokens", 0),
        subagent_cache_creation_tokens=poller.get("subagent_cache_creation_tokens", 0),
        # Poller preferred, hook fallback
        tool_count=poller.get("tool_count", 0) or hook.get("tool_count", 0),
        model=poller.get("model", "") or hook.get("model", ""),
    )


def load_sessions() -> list[SessionInfo]:
    """Read all session status files and return a list of SessionInfo."""
    sessions: list[SessionInfo] = []
    for fp, hook in _iter_hook_files():
        sid = hook.get("session_id", fp.stem)
        poller = _read_json(STATUS_DIR / f"{sid}.poller.json")
        sessions.append(_build_session_info(sid, hook, poller))
    return sessions


def _is_process_dead(pid: int) -> bool:
    """Check if a process has exited. Returns False if still running or we lack permission."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # process exists, we just can't signal it
    except OSError:
        return True
    return False


def _is_session_dead(hook: dict) -> bool:
    """Determine if a session is dead via PID check or staleness fallback."""
    pid = hook.get("pid")
    if isinstance(pid, int) and pid > 0:
        return _is_process_dead(pid)
    # No PID available, fall back to staleness heuristic
    age = _parse_age_seconds(hook.get("last_activity", ""))
    return age is not None and age > STALE_SECONDS


def _remove_session_files(fp: Path, sid: str) -> None:
    """Delete hook and poller files for a session."""
    for path in (fp, STATUS_DIR / f"{sid}.poller.json"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def purge_dead_sessions() -> int:
    """Remove session files whose owning process has exited. Returns count removed."""
    removed = 0
    for fp, hook in _iter_hook_files():
        if _is_session_dead(hook):
            _remove_session_files(fp, hook.get("session_id", fp.stem))
            removed += 1

    return removed


# --- Health check ---

# Patterns to exclude from ps output when identifying real Claude sessions
_PS_EXCLUDE_PATTERNS = (
    "/Applications/Claude.app/",
    "--parent-session-id",
    "mcp-",
    "uvx",
    "caffeinate",
    "grep",
)


@dataclass
class HealthStatus:
    """Result of comparing cctop tracked sessions against real processes."""

    tracked_count: int = 0
    process_count: int = 0
    stale_ids: list[str] = field(default_factory=list)
    untracked_count: int = 0

    @property
    def has_mismatch(self) -> bool:
        return bool(self.stale_ids) or self.untracked_count > 0

    @property
    def message(self) -> str:
        parts: list[str] = []
        if self.stale_ids:
            n = len(self.stale_ids)
            parts.append(f"{_plural(n, 'stale session')} detected, press R to purge")
        if self.untracked_count > 0:
            parts.append(
                f"{_plural(self.untracked_count, 'session')} not tracked, "
                "if they started before cctop was installed, this is expected"
            )
        return "\n".join(parts)


def _run_ps() -> str | None:
    """Run ``ps -eo pid,command`` and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,command"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def _parse_ps_line(line: str) -> tuple[int, str] | None:
    """Parse a ps output line into (pid, command), or None if unparseable."""
    parts = line.strip().split(None, 1)
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), parts[1]
    except ValueError:
        return None


def _is_claude_cli_process(cmd: str) -> bool:
    """True if the command is a real Claude CLI session (not desktop app, MCP, etc.)."""
    if "claude" not in cmd.lower():
        return False
    if any(pat in cmd for pat in _PS_EXCLUDE_PATTERNS):
        return False
    basename = os.path.basename(cmd.split()[0]) if cmd.split() else ""
    return basename == "claude"


def get_claude_pids() -> set[int]:
    """Return PIDs of real Claude CLI sessions from ``ps``."""
    output = _run_ps()
    if output is None:
        return set()
    pids: set[int] = set()
    for line in output.splitlines():
        parsed = _parse_ps_line(line)
        if parsed and _is_claude_cli_process(parsed[1]):
            pids.add(parsed[0])
    return pids


def _find_stale_session_ids(sessions: list[SessionInfo], live_pids: set[int]) -> list[str]:
    """Return session IDs whose PID is known but no longer running."""
    return [
        s.session_id for s in sessions
        if s.pid is not None and s.pid > 0 and s.pid not in live_pids
    ]


def check_session_health(sessions: list[SessionInfo], claude_pids: set[int]) -> HealthStatus:
    """Compare tracked sessions against live Claude processes."""
    stale_ids = _find_stale_session_ids(sessions, claude_pids)
    live_tracked = len(sessions) - len(stale_ids)
    untracked_count = max(0, len(claude_pids) - live_tracked)
    return HealthStatus(
        tracked_count=len(sessions),
        process_count=len(claude_pids),
        stale_ids=stale_ids,
        untracked_count=untracked_count,
    )


# --- Sort Picker Modal ---


class SortPicker(ModalScreen[str]):
    """Modal popup for choosing a sort mode."""

    CSS = """
    SortPicker {
        align: center middle;
    }
    #sort-list {
        width: 30;
        height: auto;
        max-height: 14;
        background: $surface;
        border: tall $accent;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("s", "cancel", "Cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        options = [Option(label, id=key) for key, label in SORT_OPTIONS]
        yield OptionList(*options, id="sort-list")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss("")


# --- Textual App ---


class SessionsDashboard(App):
    """TUI dashboard for monitoring Claude Code sessions."""

    TITLE = "Claude Sessions"

    CSS = """
    #detail-scroll {
        height: 14;
        padding: 0 1;
        color: $text-muted;
    }
    #detail {
        height: auto;
    }
    DataTable {
        height: 1fr;
    }
    #health-bar {
        height: auto;
        padding: 0 1;
        background: #c46600;
        color: #1a1a1a;
        text-style: bold;
        text-align: right;
        display: none;
    }
    #health-bar.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True, system=True),
        Binding("q", "quit", "Quit"),
        Binding("r", "force_refresh", "Refresh"),
        Binding("R", "purge_dead", "Purge dead"),
        Binding("s", "open_sort", "Sort"),
    ]

    sort_mode: reactive[str] = reactive("activity", init=False)

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="table")
        yield Static("", id="health-bar")
        with VerticalScroll(id="detail-scroll"):
            yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self._init_state()
        self._setup_table()
        self._schedule_refresh()
        self.set_interval(0.5, self._schedule_refresh)

    def _init_state(self) -> None:
        """Initialize per-instance mutable state."""
        self._sessions: list[SessionInfo] = []
        self._last_health_check: float = 0.0
        self._last_health: HealthStatus | None = None
        self._last_row_keys: list[str] = []

    def _setup_table(self) -> None:
        """Configure the DataTable with columns from COLUMNS definitions."""
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        self._column_keys = table.add_columns(*_COLUMN_HEADERS)

    # --- Actions ---------------------------------------------------------

    def action_force_refresh(self) -> None:
        """Force reload all session data."""
        self._schedule_refresh()

    def action_purge_dead(self) -> None:
        """Remove dead session files and refresh."""
        self._do_purge()

    def action_open_sort(self) -> None:
        """Open the sort picker popup."""
        def _on_dismiss(result: str) -> None:
            if result:
                self.sort_mode = result
        self.push_screen(SortPicker(), callback=_on_dismiss)

    def watch_sort_mode(self, new_value: str) -> None:
        """Re-sort the table when sort_mode changes."""
        self._repopulate_table()

    # --- Data loading ----------------------------------------------------

    def _schedule_refresh(self) -> None:
        """Timer callback: decide if health check is due, launch worker."""
        self._do_refresh(self._is_health_check_due())

    def _is_health_check_due(self) -> bool:
        """Return True and reset timer if enough time has passed since last check."""
        now = _time.monotonic()
        if (now - self._last_health_check) >= HEALTH_CHECK_INTERVAL:
            self._last_health_check = now
            return True
        return False

    @work(thread=True, exclusive=True)
    def _do_refresh(self, check_health: bool) -> None:
        """Background thread: read session files and optionally run health check."""
        sessions = load_sessions()
        health: HealthStatus | None = None
        if check_health:
            pids = get_claude_pids()
            health = check_session_health(sessions, pids)
        self.call_from_thread(self._apply_refresh, sessions, health)

    def _apply_refresh(self, sessions: list[SessionInfo], health: HealthStatus | None) -> None:
        """Main thread: update state and UI with results from the worker."""
        self._sessions = sessions
        if health is not None:
            self._last_health = health
        self._repopulate_table()
        self._update_subtitle()
        self._update_health_bar()

    def _update_subtitle(self) -> None:
        """Update the header subtitle with session count and sort mode."""
        count = len(self._sessions)
        self.sub_title = f"{_plural(count, 'session')} · sorted by {self.sort_mode}"

    def _update_health_bar(self) -> None:
        """Show or hide the health warning bar based on current health status."""
        bar = self.query_one("#health-bar", Static)
        if self._last_health and self._last_health.has_mismatch:
            bar.update(self._last_health.message)
            bar.add_class("visible")
        else:
            bar.update("")
            bar.remove_class("visible")

    @work(thread=True, exclusive=True, group="purge")
    def _do_purge(self) -> None:
        """Background thread: purge dead sessions."""
        count = purge_dead_sessions()
        self.call_from_thread(self._apply_purge, count)

    def _apply_purge(self, count: int) -> None:
        """Main thread: notify user and refresh after purge."""
        if count:
            self.notify(f"Purged {count} dead session(s)")
        else:
            self.notify("No dead sessions found")
        self._schedule_refresh()

    def _sorted_sessions(self) -> list[SessionInfo]:
        """Return sessions sorted according to the current sort_mode."""
        col_def = _COLUMN_BY_KEY.get(self.sort_mode, _COLUMN_BY_KEY["activity"])
        sort_fn = col_def.sort_key or (lambda s: s.last_activity or "")
        return sorted(self._sessions, key=sort_fn, reverse=col_def.reverse_sort)

    def _patch_table_cells(self, ordered: list[SessionInfo]) -> None:
        """Update cell values in place without rebuilding the table."""
        table = self.query_one(DataTable)
        for s in ordered:
            cells = _row_cells(s)
            for col_key, value in zip(self._column_keys, cells):
                table.update_cell(s.session_id, col_key, value)

    def _rebuild_table(self, ordered: list[SessionInfo]) -> None:
        """Clear and rebuild all rows, preserving cursor position."""
        table = self.query_one(DataTable)
        saved_key = self._save_cursor(table)
        table.clear()
        for s in ordered:
            table.add_row(*_row_cells(s), key=s.session_id)
        self._restore_cursor(table, saved_key)

    @staticmethod
    def _save_cursor(table: DataTable):
        """Return the row key of the currently highlighted row, or None."""
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None

    @staticmethod
    def _restore_cursor(table: DataTable, saved_key) -> None:
        """Move the cursor back to a previously highlighted row."""
        if saved_key is not None and table.row_count > 0:
            try:
                table.move_cursor(row=table.get_row_index(saved_key))
            except Exception:
                pass  # Row no longer exists

    def _repopulate_table(self) -> None:
        """Sort sessions and update the table, using cell patches when possible."""
        ordered = self._sorted_sessions()
        new_keys = [s.session_id for s in ordered]

        if new_keys == self._last_row_keys:
            self._patch_table_cells(ordered)
        else:
            self._rebuild_table(ordered)
            self._last_row_keys = new_keys

        if not ordered:
            self.query_one("#detail", Static).update("")

    # --- Detail panel ----------------------------------------------------

    @staticmethod
    def _detail_header(s: SessionInfo) -> str:
        """Build the markup header line: path, branch, tokens."""
        tokens = format_tokens(s.context_tokens)
        return "".join(p for p in (
            f"[bold]{s.cwd or '?'}[/bold]",
            f"  [cyan]{s.git_branch}[/cyan]" if s.git_branch else None,
            f"  [dim]Tokens: {tokens}[/dim]" if tokens else None,
        ) if p is not None)

    @staticmethod
    def _detail_meta(s: SessionInfo) -> list[str]:
        """Build metadata chips for the detail panel."""
        n_files = len(s.files_edited) if s.files_edited else 0
        return [p for p in (
            f"Model: {s.model}" if s.model else None,
            f"{_plural(n_files, 'file')} edited" if n_files else None,
            _plural(s.subagent_count, "subagent") if s.subagent_count else None,
            f"[red]{_plural(s.error_count, 'error')}[/red]" if s.error_count else None,
            f"stop: {s.stop_reason}" if s.stop_reason and s.stop_reason != "end_turn" else None,
        ) if p is not None]

    def _find_session(self, row_key) -> SessionInfo | None:
        """Look up a session by its table row key."""
        if row_key is None:
            return None
        sid = str(row_key.value)
        return next((s for s in self._sessions if s.session_id == sid), None)

    def _build_detail(self, session: SessionInfo) -> Group:
        """Assemble the Rich renderable for the detail panel."""
        parts: list = [
            Text.from_markup(self._detail_header(session)),
            Text(""),
        ]
        parts.extend(_render_message("User", session.last_user_msg, 300))
        parts.extend(_render_message("Claude", session.last_assistant_msg, 800))
        meta = self._detail_meta(session)
        if meta:
            parts.append(Text.from_markup(f"[dim]Info:[/dim]   {'  '.join(meta)}"))
        return Group(*parts)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show detail for the highlighted row."""
        detail = self.query_one("#detail", Static)
        session = self._find_session(event.row_key)
        if session is None:
            detail.update("")
            return
        detail.update(self._build_detail(session))

if __name__ == "__main__":
    if "--reset" in sys.argv:
        if STATUS_DIR.is_dir():
            shutil.rmtree(STATUS_DIR)
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        print("cctop: session data cleared")
    app = SessionsDashboard()
    app.run()
