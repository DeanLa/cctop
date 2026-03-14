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
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass

from rich.console import Group
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

# --- Constants ---

STATUS_DIR = Path.home() / ".cctop"
CONTEXT_WINDOW = 200_000
STALE_SECONDS = 5 * 60
SORT_OPTIONS: list[tuple[str, str]] = [
    ("activity", "Last Activity"),
    ("slug", "Name"),
    ("status", "Status"),
    ("duration", "Duration"),
    ("turns", "Turns"),
    ("tokens", "Tokens"),
    ("tools", "Tool Count"),
    ("errors", "Errors"),
]

def _clean_user_msg(msg: str) -> str:
    """Filter out system-injected messages (task notifications, reminders, etc.)."""
    if msg.startswith("<"):
        return ""
    return msg


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

MODEL_SHORT: dict[str, str] = {
    "claude-opus-4-6": "opus",
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4-5": "haiku",
}


def shorten_model(model: str) -> str:
    """Shorten a model identifier for display."""
    for key, short in MODEL_SHORT.items():
        if key in model:
            return short
    return model[:12] if model else ""


# Per-model rates: (input_per_1M, output_per_1M, cache_read_per_1M, cache_creation_per_1M)
MODEL_RATES: dict[str, tuple[float, float, float, float]] = {
    "opus": (5.0, 25.0, 0.50, 6.25),
    "sonnet": (3.0, 15.0, 0.30, 3.75),
    "haiku": (1.0, 5.0, 0.10, 1.25),
}


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


def estimate_cost(model: str, cum_input: int, cum_output: int,
                  cum_cache_read: int, cum_cache_creation: int,
                  sub_input: int, sub_output: int,
                  sub_cache_read: int, sub_cache_creation: int) -> str:
    """Estimate session cost from cumulative tokens. Returns e.g. '$1.23'.

    Applies correct per-type rates: base input, output, cache read (0.1x),
    and cache creation (1.25x).
    """
    short = shorten_model(model)
    rates = MODEL_RATES.get(short)
    if not rates:
        return ""
    total_in = cum_input + sub_input
    total_out = cum_output + sub_output
    total_cache_read = cum_cache_read + sub_cache_read
    total_cache_creation = cum_cache_creation + sub_cache_creation
    if not total_in and not total_out and not total_cache_read and not total_cache_creation:
        return ""
    cost = (
        total_in * rates[0]
        + total_out * rates[1]
        + total_cache_read * rates[2]
        + total_cache_creation * rates[3]
    ) / 1_000_000
    if cost < 0.01:
        return "<$0.01"
    return f"${cost:.2f}"


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

    @property
    def estimated_cost(self) -> str:
        """Estimate session cost from cumulative tokens."""
        return estimate_cost(
            self.model,
            self.cumulative_input_tokens, self.cumulative_output_tokens,
            self.cumulative_cache_read_tokens, self.cumulative_cache_creation_tokens,
            self.subagent_input_tokens, self.subagent_output_tokens,
            self.subagent_cache_read_tokens, self.subagent_cache_creation_tokens,
        )


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
        info = SessionInfo(
            session_id=sid,
            cwd=hook.get("cwd", ""),
            status=hook.get("status", ""),
            last_activity=hook.get("last_activity", ""),
            started_at=hook.get("started_at", ""),
            tool_count=poller.get("tool_count", 0) or hook.get("tool_count", 0),
            slug=poller.get("slug", ""),
            git_branch=poller.get("git_branch", ""),
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
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True, system=True),
        Binding("q", "quit", "Quit"),
        Binding("r", "force_refresh", "Refresh"),
        Binding("s", "open_sort", "Sort"),
    ]

    sort_mode: str = "activity"
    _sessions: list[SessionInfo] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="table")
        with VerticalScroll(id="detail-scroll"):
            yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Slug", "Project", "Branch", "Status", "Model", "Ctx%", "Tokens", "Tools", "Turns", "Duration", "Activity")
        self.refresh_data()
        self.set_interval(0.5, self.refresh_data)

    # --- Actions ---------------------------------------------------------

    def action_force_refresh(self) -> None:
        """Force reload all session data."""
        self.refresh_data()

    def action_open_sort(self) -> None:
        """Open the sort picker popup."""
        def _on_dismiss(result: str) -> None:
            if result:
                self.sort_mode = result
                self._repopulate_table()
        self.push_screen(SortPicker(), callback=_on_dismiss)

    # --- Data loading ----------------------------------------------------

    def refresh_data(self) -> None:
        """Poll session status files and update the table."""
        self._sessions = load_sessions()
        self._repopulate_table()
        count = len(self._sessions)
        self.sub_title = f"{count} session{'s' if count != 1 else ''} · sorted by {self.sort_mode}"

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
        if self.sort_mode == "errors":
            return s.error_count
        # activity — most recent first
        return s.last_activity or ""

    def _repopulate_table(self) -> None:
        table = self.query_one(DataTable)
        # Preserve the currently highlighted row across refreshes
        saved_key = None
        if table.row_count > 0:
            try:
                saved_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            except Exception:
                pass
        table.clear()
        # Numeric/time sorts: largest first; alphabetical sorts: A-Z
        reverse = self.sort_mode in ("activity", "duration", "turns", "tokens", "tools", "errors")
        ordered = sorted(self._sessions, key=self._sort_key, reverse=reverse)
        for s in ordered:
            project = os.path.basename(s.cwd) if s.cwd else ""
            ctx = s.context_tokens
            ctx_pct = f"{ctx * 100 // CONTEXT_WINDOW}%" if ctx else ""
            tokens = format_tokens(ctx)
            table.add_row(
                Text.assemble(("● ", "#e0af68"), s.custom_title) if s.custom_title else Text.assemble(("○ ", "dim"), s.session_id[:8]),
                project,
                s.git_branch[:12],
                styled_status(s.status, s.last_activity),
                shorten_model(s.model),
                ctx_pct,
                tokens,
                str(s.tool_count) if s.tool_count else "",
                str(s.turns) if s.turns else "",
                format_duration(s.started_at),
                format_relative_time(s.last_activity),
                key=s.session_id,
            )
        # Restore cursor to the previously highlighted row
        if saved_key is not None and table.row_count > 0:
            try:
                row_idx = table.get_row_index(saved_key)
                table.move_cursor(row=row_idx)
            except Exception:
                pass  # Row no longer exists (session ended)

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
        user_text = (session.last_user_msg or "—").replace("\n", " ").strip()
        if len(user_text) > 300:
            user_text = user_text[:300] + "…"

        asst_text = (session.last_assistant_msg or "").strip()
        if len(asst_text) > 800:
            asst_text = asst_text[:800] + "…"

        parts: list = [
            Text.from_markup(header_line),
            Text(""),
            Text.from_markup(f"[dim]User:[/dim]  {user_text}"),
        ]

        if asst_text:
            parts.append(Text.from_markup("[dim]Claude:[/dim]"))
            parts.append(RichMarkdown(asst_text))
        else:
            parts.append(Text.from_markup("[dim]Claude:[/dim] —"))

        if meta_parts:
            parts.append(Text.from_markup(f"[dim]Info:[/dim]   {'  '.join(meta_parts)}"))

        detail.update(Group(*parts))

if __name__ == "__main__":
    app = SessionsDashboard()
    app.run()
