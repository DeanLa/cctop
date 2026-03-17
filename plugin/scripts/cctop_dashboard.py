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
SORT_OPTIONS: list[tuple[str, str]] = [
    ("activity", "Last Activity"),
    ("slug", "Name"),
    ("status", "Status"),
    ("duration", "Duration"),
    ("turns", "Turns"),
    ("tokens", "Tokens"),
    ("tools", "Tool Count"),
    ("files", "Files Edited"),
    ("agents", "Running Agents"),
    ("errors", "Errors"),
]

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


def load_sessions() -> list[SessionInfo]:
    """Read hook JSON + poller JSON per session and merge them."""
    sessions: list[SessionInfo] = []
    if not STATUS_DIR.is_dir():
        return sessions

    # First pass: collect all sessions
    for fp in STATUS_DIR.glob("*.json"):
        if fp.name.endswith(".poller.json"):
            continue
        try:
            hook = json.loads(fp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        sid = hook.get("session_id", fp.stem)
        poller_fp = STATUS_DIR / f"{sid}.poller.json"
        try:
            poller = json.loads(poller_fp.read_text())
        except (OSError, json.JSONDecodeError):
            poller = {}
        raw_pid = hook.get("pid")
        info = SessionInfo(
            session_id=sid,
            cwd=hook.get("cwd", ""),
            status=hook.get("status", ""),
            last_activity=hook.get("last_activity", ""),
            started_at=hook.get("started_at", ""),
            pid=raw_pid if isinstance(raw_pid, int) else None,
            tool_count=poller.get("tool_count", 0) or hook.get("tool_count", 0),
            slug=poller.get("slug", ""),
            git_branch=poller.get("git_branch", ""),
            project_name=poller.get("project_name", ""),
            model=poller.get("model", "") or hook.get("model", ""),
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
            running_agents=hook.get("running_agents", 0),
            cumulative_input_tokens=poller.get("cumulative_input_tokens", 0),
            cumulative_output_tokens=poller.get("cumulative_output_tokens", 0),
            cumulative_cache_read_tokens=poller.get("cumulative_cache_read_tokens", 0),
            cumulative_cache_creation_tokens=poller.get("cumulative_cache_creation_tokens", 0),
            subagent_input_tokens=poller.get("subagent_input_tokens", 0),
            subagent_output_tokens=poller.get("subagent_output_tokens", 0),
            subagent_cache_read_tokens=poller.get("subagent_cache_read_tokens", 0),
            subagent_cache_creation_tokens=poller.get("subagent_cache_creation_tokens", 0),
        )
        sessions.append(info)

    return sessions


def purge_dead_sessions() -> int:
    """Remove session files whose owning Claude process has exited.

    Uses PID check when available, falls back to staleness heuristic.
    Returns the number of sessions cleaned up.
    """
    if not STATUS_DIR.is_dir():
        return 0

    removed = 0
    for fp in STATUS_DIR.glob("*.json"):
        if fp.name.endswith(".poller.json"):
            continue
        try:
            hook = json.loads(fp.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        sid = hook.get("session_id", fp.stem)
        pid = hook.get("pid")
        is_dead = False

        if pid is not None and isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                is_dead = True
            except PermissionError:
                pass  # process exists
            except OSError:
                is_dead = True
        else:
            # Staleness fallback for pre-PID files
            age = _parse_age_seconds(hook.get("last_activity", ""))
            if age is not None and age > STALE_SECONDS:
                is_dead = True

        if is_dead:
            try:
                fp.unlink(missing_ok=True)
            except OSError:
                pass
            poller_fp = STATUS_DIR / f"{sid}.poller.json"
            try:
                poller_fp.unlink(missing_ok=True)
            except OSError:
                pass
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
            parts.append(f"{n} stale session{'s' if n != 1 else ''} detected, press R to purge")
        if self.untracked_count > 0:
            n = self.untracked_count
            parts.append(
                f"{n} session{'s' if n != 1 else ''} not tracked, "
                "if they started before cctop was installed, this is expected"
            )
        return "\n".join(parts)


def get_claude_pids() -> set[int]:
    """Return PIDs of real Claude CLI sessions from ``ps``.

    Excludes the Desktop app, MCP servers, teammate subagents, and
    other non-interactive processes.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,command"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()

    pids: set[int] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        pid_str, cmd = parts
        # The command portion must reference "claude"
        if "claude" not in cmd.lower():
            continue
        # Apply exclusion filters
        if any(pat in cmd for pat in _PS_EXCLUDE_PATTERNS):
            continue
        # Include only bare `claude` or `claude -r` style invocations
        # The executable basename should be "claude"
        cmd_parts = cmd.split()
        basename = os.path.basename(cmd_parts[0]) if cmd_parts else ""
        if basename != "claude":
            continue
        try:
            pids.add(int(pid_str))
        except ValueError:
            continue
    return pids


def check_session_health(sessions: list[SessionInfo], claude_pids: set[int]) -> HealthStatus:
    """Compare tracked sessions against live Claude processes."""
    tracked_count = len(sessions)
    stale_ids: list[str] = []

    for s in sessions:
        if s.pid is not None and s.pid > 0:
            if s.pid not in claude_pids:
                stale_ids.append(s.session_id)

    live_tracked = tracked_count - len(stale_ids)
    process_count = len(claude_pids)
    untracked_count = max(0, process_count - live_tracked)

    return HealthStatus(
        tracked_count=tracked_count,
        process_count=process_count,
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
        self._sessions: list[SessionInfo] = []
        self._last_health_check: float = 0.0
        self._last_health: HealthStatus | None = None
        self._last_row_keys: list[str] = []
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        self._column_keys = table.add_columns("Slug", "Project", "Branch", "Status", "Model", "Ctx%", "Tokens", "Tools", "Files", "Agents", "Errors", "Turns", "StopRsn", "Duration", "Started", "Activity")
        self._schedule_refresh()
        self.set_interval(0.5, self._schedule_refresh)

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
        now = _time.monotonic()
        check_health = (now - self._last_health_check) >= HEALTH_CHECK_INTERVAL
        if check_health:
            self._last_health_check = now
        self._do_refresh(check_health)

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
        self._repopulate_table()
        count = len(self._sessions)
        self.sub_title = f"{count} session{'s' if count != 1 else ''} · sorted by {self.sort_mode}"
        if health is not None:
            self._last_health = health
        bar = self.query_one("#health-bar", Static)
        current_health = self._last_health
        if current_health and current_health.has_mismatch:
            bar.update(current_health.message)
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

    def _sort_key(self, s: SessionInfo):
        if self.sort_mode == "slug":
            return (s.custom_title or s.slug or s.session_id).lower()
        if self.sort_mode == "status":
            return s.status.lower()
        if self.sort_mode == "duration":
            return s.started_at or ""
        if self.sort_mode == "turns":
            return s.turns
        if self.sort_mode == "tokens":
            return s.context_tokens
        if self.sort_mode == "tools":
            return s.tool_count
        if self.sort_mode == "files":
            return len(s.files_edited) if s.files_edited else 0
        if self.sort_mode == "agents":
            return s.running_agents
        if self.sort_mode == "errors":
            return s.error_count
        # activity — most recent first
        return s.last_activity or ""

    @staticmethod
    def _build_row_cells(s: SessionInfo) -> tuple:
        """Compute the cell values for a single session row."""
        project = s.project_name or (os.path.basename(s.cwd) if s.cwd else "")
        ctx = s.context_tokens
        ctx_pct = f"{ctx * 100 // CONTEXT_WINDOW}%" if ctx else ""
        tokens = format_tokens(ctx)
        errors_cell = Text(str(s.error_count), style="red") if s.error_count else ""
        slug_cell = (
            Text.assemble(("● ", "#e0af68"), s.custom_title)
            if s.custom_title
            else Text.assemble(("○ ", "dim"), s.session_id[:8])
        )
        return (
            slug_cell,
            project,
            s.git_branch[:20],
            styled_status(s.status, s.last_activity),
            friendly_model_name(s.model),
            ctx_pct,
            tokens,
            str(s.tool_count) if s.tool_count else "",
            str(len(s.files_edited)) if s.files_edited else "",
            str(s.running_agents) if s.running_agents else "",
            errors_cell,
            str(s.turns) if s.turns else "",
            format_stop_reason(s.stop_reason),
            format_duration(s.started_at),
            format_start_time(s.started_at),
            format_relative_time(s.last_activity),
        )

    def _repopulate_table(self) -> None:
        table = self.query_one(DataTable)
        # Numeric/time sorts: largest first; alphabetical sorts: A-Z
        reverse = self.sort_mode in ("activity", "duration", "turns", "tokens", "tools", "files", "agents", "errors")
        ordered = sorted(self._sessions, key=self._sort_key, reverse=reverse)
        new_keys = [s.session_id for s in ordered]

        if new_keys == self._last_row_keys:
            # Structure unchanged, patch cells in place
            for s in ordered:
                cells = self._build_row_cells(s)
                for col_key, value in zip(self._column_keys, cells):
                    table.update_cell(s.session_id, col_key, value)
        else:
            # Structural change: full rebuild
            saved_key = None
            if table.row_count > 0:
                try:
                    saved_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
                except Exception:
                    pass
            table.clear()
            for s in ordered:
                table.add_row(*self._build_row_cells(s), key=s.session_id)
            self._last_row_keys = new_keys
            # Restore cursor to the previously highlighted row
            if saved_key is not None and table.row_count > 0:
                try:
                    row_idx = table.get_row_index(saved_key)
                    table.move_cursor(row=row_idx)
                except Exception:
                    pass  # Row no longer exists (session ended)

        # Clear detail panel when table is empty (no sessions left)
        if table.row_count == 0:
            self.query_one("#detail", Static).update("")

    # --- Detail panel ----------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show detail for the highlighted row."""
        detail = self.query_one("#detail", Static)
        if event.row_key is None:
            detail.update("")
            return
        sid = str(event.row_key.value)
        session = next((s for s in self._sessions if s.session_id == sid), None)
        if session is None:
            detail.update("")
            return

        # Line 1: path, branch, tokens
        tokens = format_tokens(session.context_tokens)
        header_parts = [f"[bold]{session.cwd or '?'}[/bold]"]
        if session.git_branch:
            header_parts.append(f"  [cyan]{session.git_branch}[/cyan]")
        if tokens:
            header_parts.append(f"  [dim]Tokens: {tokens}[/dim]")
        header_line = "".join(header_parts)

        # Info metadata
        meta_parts: list[str] = []
        if session.model:
            meta_parts.append(f"Model: {session.model}")
        if session.files_edited:
            n = len(session.files_edited)
            meta_parts.append(f"{n} file{'s' if n != 1 else ''} edited")
        if session.subagent_count:
            meta_parts.append(f"{session.subagent_count} subagent{'s' if session.subagent_count != 1 else ''}")
        if session.error_count:
            meta_parts.append(f"[red]{session.error_count} error{'s' if session.error_count != 1 else ''}[/red]")
        if session.stop_reason and session.stop_reason != "end_turn":
            meta_parts.append(f"stop: {session.stop_reason}")

        # Build detail as Rich renderables
        parts: list = [
            Text.from_markup(header_line),
            Text(""),
        ]
        parts.extend(_render_message("User", session.last_user_msg, 300))
        parts.extend(_render_message("Claude", session.last_assistant_msg, 800))

        if meta_parts:
            parts.append(Text.from_markup(f"[dim]Info:[/dim]   {'  '.join(meta_parts)}"))

        detail.update(Group(*parts))

if __name__ == "__main__":
    if "--reset" in sys.argv:
        if STATUS_DIR.is_dir():
            shutil.rmtree(STATUS_DIR)
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        print("cctop: session data cleared")
    app = SessionsDashboard()
    app.run()
