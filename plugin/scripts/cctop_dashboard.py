# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=3.0.0"]
# ///
"""cctop — Claude Code Sessions TUI dashboard.

Read-only frontend. All data comes from session-status JSON files
written by the hook (cctop-hook.sh) and the poller (cctop-poller.py).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time as _time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from dataclasses import dataclass, field

from rich.console import Group
from rich.markdown import Markdown as RichMarkdown
from rich.table import Table as RichTable
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.geometry import Region
from textual.widgets import DataTable, Header, OptionList, Static
from textual.widgets.option_list import Option

# --- Constants ---

STATUS_DIR = Path.home() / ".cctop"
CONFIG_PATH = STATUS_DIR / "config.toml"
_CONTEXT_WINDOW_DEFAULT = 200_000
STALE_SECONDS = 60 * 60
HEALTH_CHECK_INTERVAL = 10.0  # seconds between ps-based health checks

_CONFIG_DEFAULTS: dict = {
    "ui": {"theme": "textual-dark"},
    "sort": {"column": "activity", "reverse": True},
    "columns": {"hidden": ["errors", "started", "stop_reason", "tokens", "effort", "cost"]},
    "group": {"by": ""},
    "activity": {"visible": False, "width": 40},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def load_config() -> dict:
    """Read ~/.cctop/config.toml, returning defaults for missing keys."""
    if CONFIG_PATH.is_file():
        try:
            with CONFIG_PATH.open("rb") as f:
                user = tomllib.load(f)
            return _deep_merge(_CONFIG_DEFAULTS, user)
        except Exception:
            pass
    return dict(_CONFIG_DEFAULTS)


def save_config(updates: dict) -> None:
    """Merge *updates* into the existing config and write back to disk."""
    current = load_config()
    merged = _deep_merge(current, updates)
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for section, values in merged.items():
        if isinstance(values, dict):
            lines.append(f"[{section}]")
            for k, v in values.items():
                lines.append(f'{k} = {_toml_value(v)}')
            lines.append("")
        else:
            lines.append(f'{section} = {_toml_value(values)}')
    CONFIG_PATH.write_text("\n".join(lines) + "\n")


def _toml_value(v: object) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(i) for i in v) + "]"
    return f'"{v}"'


def _reset_session_data() -> None:
    """Delete session data files but preserve config."""
    if not STATUS_DIR.is_dir():
        return
    for pattern in ("*.json", "*.poller.json", "*.debug.jsonl"):
        for f in STATUS_DIR.glob(pattern):
            f.unlink(missing_ok=True)


def _plural(n: int, word: str) -> str:
    """Return e.g. '3 files' or '1 file'."""
    return f"{n} {word}{'s' if n != 1 else ''}"


def _clean_user_msg(msg: str) -> str:
    """Filter out system-injected messages (task notifications, reminders, etc.)."""
    if msg.startswith("<"):
        return ""
    return msg


def _format_event_time(iso_str: str) -> str:
    """Convert ISO timestamp to short local time (HH:MM)."""
    if not iso_str:
        return "     "
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return ts.astimezone().strftime("%H:%M")
    except (ValueError, TypeError):
        return "     "


def _shorten_path(path: str) -> str:
    """Show last 2 path components for file paths, or the string as-is."""
    if "/" not in path:
        return path
    parts = Path(path).parts
    return str(Path(*parts[-2:])) if len(parts) > 2 else path


def _truncate(text: str, limit: int = 60) -> str:
    """Truncate text at a word boundary."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut + "…"


def _render_message(
    label: str, text: str | None, max_chars: int = 500
) -> list[Text | RichMarkdown]:
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
    # Idle variants
    "idle": ("green", "idle"),
    "idle:awaiting_plan": ("blue", "awaiting plan"),
    "idle:needs_input": ("#ff8700", "needs input"),
    # Active
    "thinking": ("yellow", "thinking"),
    "started": ("blue", "started"),
    "resumed": ("#5fd7ff", "resumed"),
    # Permission / input waiting
    "awaiting_permission": ("#ff8700", "awaiting permission"),
    "awaiting_input": ("#ff5f5f", "awaiting input"),
    "awaiting_mcp_input": ("#ff8700", "awaiting mcp input"),
    # Tools
    "tool:Bash": ("green", "running cmd"),
    "tool:WebSearch": ("magenta", "searching web"),
    "tool:WebFetch": ("magenta", "searching web"),
    "tool:Agent": ("#af87ff", "subagent"),
    "tool:Read": ("cyan", "reading"),
    "tool:Edit": ("#ff8700", "editing"),
    "tool:Write": ("#ff8700", "editing"),
    "tool:NotebookEdit": ("#ff8700", "editing"),
    "tool:Glob": ("cyan", "searching"),
    "tool:Grep": ("cyan", "searching"),
    "tool:EnterPlanMode": ("blue", "entering plan"),
    "tool:ExitPlanMode": ("blue", "exiting plan"),
    "tool:AskUserQuestion": ("#ff5f5f", "asking user"),
    "tool:EnterWorktree": ("blue", "entering worktree"),
    "tool:ExitWorktree": ("blue", "exiting worktree"),
    "tool:TaskCreate": ("#af87ff", "creating task"),
    "tool:TaskUpdate": ("#af87ff", "updating task"),
    "tool:TaskList": ("cyan", "listing tasks"),
    "tool:TaskGet": ("cyan", "reading task"),
    "tool:SendMessage": ("#af87ff", "messaging"),
    "tool:TeamCreate": ("#af87ff", "creating team"),
    "tool:Skill": ("#af87ff", "running skill"),
    "ended": ("dim", "ended"),
}

# Activity feed: icon + color per event type, single source of truth.
ACTIVITY_STYLE: dict[str, tuple[str, str]] = {
    "user": ("▸", "green"),
    "assistant": ("◂", "yellow"),
    "system": ("→", "dim italic"),
    "tool": ("⚙", "cyan"),
    "tool:AskUserQuestion": ("⚙", "#ff5f5f"),
    "slash_cmd": ("", "#af87ff bold"),
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


def get_context_window(model: str) -> int:
    """Return the context window size for a model string."""
    return 1_000_000 if "[1m]" in model else _CONTEXT_WINDOW_DEFAULT


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


# Pricing per 1M tokens (from Anthropic published rates)
_PRICING: dict[str, dict[str, float]] = {
    "opus-4-6":   {"input": 5.0,   "output": 25.0,  "cache_write": 6.25,  "cache_read": 0.50},
    "opus-4-5":   {"input": 5.0,   "output": 25.0,  "cache_write": 6.25,  "cache_read": 0.50},
    "opus-4-1":   {"input": 15.0,  "output": 75.0,  "cache_write": 18.75, "cache_read": 1.50},
    "sonnet-4-6": {"input": 3.0,   "output": 15.0,  "cache_write": 3.75,  "cache_read": 0.30},
    "sonnet-4-5": {"input": 3.0,   "output": 15.0,  "cache_write": 3.75,  "cache_read": 0.30},
    "haiku-4-5":  {"input": 1.0,   "output": 5.0,   "cache_write": 1.25,  "cache_read": 0.10},
}

_PRICING_FALLBACK = {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}


def _get_pricing(model: str) -> dict[str, float]:
    """Match a model string like 'claude-sonnet-4-6-20260301' to a pricing tier."""
    m = re.match(r"claude-(\w+-\d+-\d+)", model)
    key = m.group(1) if m else ""
    return _PRICING.get(key, _PRICING_FALLBACK)


def _calc_cost(s: SessionInfo) -> float:
    """Compute estimated session cost in dollars (main + subagent tokens)."""
    p = _get_pricing(s.model)
    main = (
        s.cumulative_input_tokens * p["input"]
        + s.cumulative_output_tokens * p["output"]
        + s.cumulative_cache_creation_tokens * p["cache_write"]
        + s.cumulative_cache_read_tokens * p["cache_read"]
    ) / 1e6
    sub = (
        s.subagent_input_tokens * p["input"]
        + s.subagent_output_tokens * p["output"]
        + s.subagent_cache_creation_tokens * p["cache_write"]
        + s.subagent_cache_read_tokens * p["cache_read"]
    ) / 1e6
    return main + sub


def format_cost(cost: float) -> str:
    """Format a cost value as $X.XX, or empty for zero."""
    if cost < 0.005:
        return ""
    return f"${cost:.2f}"


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
    last_system_msg: str = ""
    last_assistant_msg: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    custom_title: str = ""
    tool_count: int = 0
    turns: int = 0
    files_edited: list[str] | None = None
    subagent_count: int = 0
    error_count: int = 0
    stop_reason: str = ""
    pid: int | None = None
    transcript_path: str = ""
    running_agents: int = 0
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    cumulative_cache_read_tokens: int = 0
    cumulative_cache_creation_tokens: int = 0
    subagent_input_tokens: int = 0
    subagent_output_tokens: int = 0
    subagent_cache_read_tokens: int = 0
    subagent_cache_creation_tokens: int = 0
    tmux_session: str = ""
    tmux_window: str = ""
    planning_mode: bool = False
    last_tool: str = ""
    active_subagent_type: str = ""
    error_type: str = ""
    error_details: str = ""
    tool_failures: int = 0
    effort_level: str = ""
    status_context: str = ""
    recent_events: list = field(default_factory=list)

    @property
    def context_tokens(self) -> int:
        """Current context window usage (input tokens from latest turn)."""
        return self.input_tokens


# --- Helper functions ---


def styled_status(session: SessionInfo) -> Text:
    """Return a Rich Text object with colour-coded status."""
    raw = session.status
    if _is_stale(session):
        return Text("stale", style="dim")

    # Error states (error:rate_limit, error:auth_failed, etc.)
    if raw.startswith("error:"):
        label = raw.split(":", 1)[1].replace("_", " ")
        return Text(f"error: {label}", style="red bold")

    # Planning mode overrides tool-specific statuses
    if session.planning_mode and raw.startswith("tool:"):
        return Text("planning", style="blue")

    # Subagent type overrides for tool:Agent
    if raw == "tool:Agent" and session.active_subagent_type:
        st = session.active_subagent_type.lower()
        if any(k in st for k in ("review", "pr-review", "code-review")):
            return Text("reviewing", style="#af87ff")
        if any(k in st for k in ("explore", "research")):
            return Text("researching", style="#af87ff")

    # MCP tool detection (tool:mcp__server__action → mcp:server)
    if raw.startswith("tool:mcp__"):
        parts = raw.split("__", 2)
        server = parts[1] if len(parts) >= 2 else "mcp"
        return Text(f"mcp:{server}", style="magenta")

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

    key: str  # internal identifier, e.g. "slug"
    cell: Callable[[SessionInfo], object]  # renders a SessionInfo into a cell value
    header: str = ""  # empty = derive from key
    sort_key: Callable[[SessionInfo], object] | None = (
        None  # extracts comparable value for sorting
    )
    reverse_sort: bool = False  # True = largest/newest first

    def __post_init__(self) -> None:
        if not self.header:
            object.__setattr__(self, "header", self.key.replace("_", " ").title())


COLUMNS: tuple[ColumnDef, ...] = (
    ColumnDef(
        "slug",
        header="Name",
        cell=lambda s: (
            Text.assemble(("● ", "#e0af68"), s.custom_title)
            if s.custom_title
            else Text.assemble(("○ ", "dim"), s.session_id[:8])
        ),
        sort_key=lambda s: (s.custom_title or s.slug or s.session_id).lower(),
    ),
    ColumnDef(
        "project",
        cell=lambda s: s.project_name or (os.path.basename(s.cwd) if s.cwd else ""),
        sort_key=lambda s: (
            s.project_name or os.path.basename(s.cwd) if s.cwd else ""
        ).lower(),
    ),
    ColumnDef(
        "branch",
        cell=lambda s: s.git_branch[:20],
        sort_key=lambda s: s.git_branch.lower(),
    ),
    ColumnDef(
        "status",
        cell=lambda s: styled_status(s),
        sort_key=lambda s: s.status.lower(),
    ),
    ColumnDef(
        "model",
        cell=lambda s: friendly_model_name(s.model),
        sort_key=lambda s: s.model.lower(),
    ),
    ColumnDef(
        "ctx_pct",
        header="Ctx%",
        cell=lambda s: f"{s.context_tokens * 100 // get_context_window(s.model)}%"
        if s.context_tokens
        else "",
        reverse_sort=True,
        sort_key=lambda s: s.context_tokens,
    ),
    ColumnDef(
        "tokens",
        cell=lambda s: format_tokens(s.context_tokens),
        reverse_sort=True,
        sort_key=lambda s: s.context_tokens,
    ),
    ColumnDef(
        "tools",
        cell=lambda s: str(s.tool_count) if s.tool_count else "",
        reverse_sort=True,
        sort_key=lambda s: s.tool_count,
    ),
    ColumnDef(
        "files",
        cell=lambda s: str(len(s.files_edited)) if s.files_edited else "",
        reverse_sort=True,
        sort_key=lambda s: len(s.files_edited) if s.files_edited else 0,
    ),
    ColumnDef(
        "agents",
        cell=lambda s: str(s.running_agents) if s.running_agents else "",
        reverse_sort=True,
        sort_key=lambda s: s.running_agents,
    ),
    ColumnDef(
        "errors",
        cell=lambda s: Text(str(s.error_count + s.tool_failures), style="red") if (s.error_count + s.tool_failures) else "",
        reverse_sort=True,
        sort_key=lambda s: s.error_count + s.tool_failures,
    ),
    ColumnDef(
        "turns",
        cell=lambda s: str(s.turns) if s.turns else "",
        reverse_sort=True,
        sort_key=lambda s: s.turns,
    ),
    ColumnDef(
        "effort",
        cell=lambda s: s.effort_level or "",
        sort_key=lambda s: s.effort_level.lower(),
    ),
    ColumnDef(
        "cost",
        cell=lambda s: format_cost(_calc_cost(s)),
        reverse_sort=True,
        sort_key=lambda s: _calc_cost(s),
    ),
    ColumnDef(
        "stop_reason",
        header="StopRsn",
        cell=lambda s: format_stop_reason(s.stop_reason),
        sort_key=lambda s: s.stop_reason.lower(),
    ),
    ColumnDef(
        "duration",
        cell=lambda s: format_duration(s.started_at),
        reverse_sort=True,
        sort_key=lambda s: s.started_at or "",
    ),
    ColumnDef(
        "started",
        cell=lambda s: format_start_time(s.started_at),
        reverse_sort=True,
        sort_key=lambda s: s.started_at or "",
    ),
    ColumnDef(
        "activity",
        cell=lambda s: format_relative_time(s.last_activity),
        reverse_sort=True,
        sort_key=lambda s: s.last_activity or "",
    ),
)

_COLUMN_BY_KEY: dict[str, ColumnDef] = {c.key: c for c in COLUMNS}


# --- Group-by definitions (single source of truth) ---

_GROUP_ROW_PREFIX = "__g:"


@dataclass(frozen=True)
class GroupDef:
    """Definition for one group-by option."""

    key: str  # internal identifier, e.g. "project"
    label: str  # display name in picker/subtitle
    group_fn: Callable[[SessionInfo], str]  # extracts group label from session
    order: tuple[str, ...] | None = None  # fixed display order for binary groups


def _is_stale(s: SessionInfo) -> bool:
    """Check whether a session is stale (idle > STALE_SECONDS)."""
    age = _parse_age_seconds(s.last_activity)
    return age is not None and age > STALE_SECONDS


GROUP_DEFS: dict[str, GroupDef] = {
    "project": GroupDef(
        "project",
        "Project",
        group_fn=lambda s: s.project_name
        or (os.path.basename(s.cwd) if s.cwd else "unknown"),
    ),
    "model": GroupDef(
        "model",
        "Model",
        group_fn=lambda s: friendly_model_name(s.model) if s.model else "unknown",
    ),
    "stale": GroupDef(
        "stale",
        "Active / Stale",
        group_fn=lambda s: "Stale" if _is_stale(s) else "Active",
        order=("Active", "Stale"),
    ),
    "renamed": GroupDef(
        "renamed",
        "Named / Unnamed",
        group_fn=lambda s: "Named" if s.custom_title else "Unnamed",
        order=("Named", "Unnamed"),
    ),
}


def _group_sessions(
    sessions: list[SessionInfo], group_def: GroupDef
) -> list[tuple[str, list[SessionInfo]]]:
    """Partition sorted sessions into ordered groups.

    Returns (group_name, sessions) pairs. Fixed-order groups use GroupDef.order;
    dynamic groups are sorted alphabetically. Empty groups are omitted.
    """
    buckets: dict[str, list[SessionInfo]] = {}
    for s in sessions:
        buckets.setdefault(group_def.group_fn(s), []).append(s)
    if group_def.order:
        return [(k, buckets[k]) for k in group_def.order if k in buckets]
    return sorted(buckets.items(), key=lambda x: x[0].lower())


def _row_cells(s: SessionInfo, columns: tuple[ColumnDef, ...]) -> tuple:
    """Compute all cell values for one session row."""
    return tuple(c.cell(s) for c in columns)


def _group_header_cells(
    name: str, count: int, collapsed: bool, num_cols: int
) -> tuple:
    """Build cell values for a group separator row."""
    indicator = "\u25b6" if collapsed else "\u25bc"
    label = Text.assemble(
        (f"{indicator} ", "dim"),
        (name, "dim"),
        (f" ({count})", "dim"),
    )
    return (label,) + ("",) * (num_cols - 1)


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
        transcript_path=hook.get("transcript_path", ""),
        # Hook-only fields
        running_agents=hook.get("running_agents", 0),
        tmux_session=hook.get("tmux_session", ""),
        tmux_window=hook.get("tmux_window", ""),
        planning_mode=hook.get("planning_mode", False),
        last_tool=hook.get("last_tool", ""),
        active_subagent_type=hook.get("active_subagent_type", ""),
        error_type=hook.get("error_type", ""),
        error_details=hook.get("error_details", ""),
        tool_failures=hook.get("tool_failures", 0),
        status_context=hook.get("status_context", ""),
        # Poller-only fields
        slug=poller.get("slug", ""),
        git_branch=poller.get("git_branch", ""),
        project_name=poller.get("project_name", ""),
        last_user_msg=_clean_user_msg(poller.get("last_user_msg", "")),
        last_system_msg=poller.get("last_system_msg", ""),
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
        cumulative_cache_creation_tokens=poller.get(
            "cumulative_cache_creation_tokens", 0
        ),
        subagent_input_tokens=poller.get("subagent_input_tokens", 0),
        subagent_output_tokens=poller.get("subagent_output_tokens", 0),
        subagent_cache_read_tokens=poller.get("subagent_cache_read_tokens", 0),
        subagent_cache_creation_tokens=poller.get("subagent_cache_creation_tokens", 0),
        effort_level=poller.get("effort_level", ""),
        recent_events=poller.get("recent_events", []),
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

# Basenames (lowercase) of known terminal/editor apps for parent-process detection
_KNOWN_TERMINAL_APPS: set[str] = {
    "iterm2", "terminal", "tmux", "code", "cursor", "pycharm",
    "warp", "alacritty", "ghostty", "wezterm", "wezterm-gui",
    "kitty", "screen", "hyper", "tabby",
}


@dataclass
class HealthStatus:
    """Result of comparing cctop tracked sessions against real processes."""

    tracked_count: int = 0
    process_count: int = 0
    stale_ids: list[str] = field(default_factory=list)
    untracked_pids: set[int] = field(default_factory=set)

    @property
    def untracked_count(self) -> int:
        return len(self.untracked_pids)

    @property
    def has_mismatch(self) -> bool:
        return bool(self.stale_ids) or bool(self.untracked_pids)

    @property
    def message(self) -> str:
        parts: list[str] = []
        if self.stale_ids:
            n = len(self.stale_ids)
            parts.append(f"{_plural(n, 'stale session')} detected")
        if self.untracked_pids:
            parts.append(
                f"{_plural(self.untracked_count, 'session')} not tracked"
            )
        return " · ".join(parts)


@dataclass
class UntrackedProcessInfo:
    """Details about an untracked Claude CLI process."""

    pid: int
    cwd: str = ""
    parent_app: str = ""
    args: str = ""
    started: str = ""
    uptime: str = ""
    version: str = ""
    tty: str = ""
    children: list[str] = field(default_factory=list)


def _run_ps() -> str | None:
    """Run ``ps -eo pid,command`` and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,command"],
            capture_output=True,
            text=True,
            timeout=5,
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


def _get_pid_cwd(pid: int) -> str:
    """Get the working directory of a process via lsof."""
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if line.startswith("n/"):
                return line[1:]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _get_pid_parent_app(pid: int) -> str:
    """Walk the process tree upward to find a recognizable terminal/app name."""
    visited: set[int] = set()
    current = pid
    while current > 1 and current not in visited:
        visited.add(current)
        try:
            result = subprocess.run(
                ["ps", "-o", "ppid=,comm=", "-p", str(current)],
                capture_output=True, text=True, timeout=3,
            )
            line = result.stdout.strip()
            if not line:
                break
            parts = line.split(None, 1)
            if len(parts) < 2:
                break
            ppid, comm = int(parts[0]), parts[1].strip()
            basename = os.path.basename(comm).lower()
            if basename in _KNOWN_TERMINAL_APPS:
                return os.path.basename(comm)  # preserve original case
            current = ppid
        except (OSError, subprocess.TimeoutExpired, ValueError):
            break
    return ""


def _get_pid_version(pid: int) -> str:
    """Extract Claude version from the lsof txt entries (binary path)."""
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "txt", "-Fn"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "claude/versions/" in line:
                return line.rsplit("/", 1)[-1]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _get_pid_children(pid: int) -> list[str]:
    """Return command-line summaries of direct child processes."""
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True, text=True, timeout=3,
        )
        child_pids = result.stdout.strip().splitlines()
        if not child_pids:
            return []
        result = subprocess.run(
            ["ps", "-o", "args=", "-p", ",".join(child_pids)],
            capture_output=True, text=True, timeout=3,
        )
        return [ln.strip() for ln in result.stdout.strip().splitlines() if ln.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return []


def _gather_untracked_info(pids: set[int]) -> list[UntrackedProcessInfo]:
    """Gather detailed process info for a set of untracked PIDs."""
    # Batch-fetch ps fields for all PIDs in one call
    ps_data: dict[int, dict[str, str]] = {}
    pid_list = sorted(pids)
    if pid_list:
        try:
            result = subprocess.run(
                ["ps", "-o", "pid=,lstart=,etime=,tty=,args=",
                 "-p", ",".join(str(p) for p in pid_list)],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                # Format: PID  DOW MON DD HH:MM:SS YYYY  ELAPSED  TTY  ARGS...
                parts = line.split()
                if len(parts) < 8:
                    continue
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                # lstart is 5 fields: DOW MON DD HH:MM:SS YYYY
                started = " ".join(parts[1:6])
                etime = parts[6]
                tty = parts[7] if parts[7] != "??" else ""
                args = " ".join(parts[8:]) if len(parts) > 8 else ""
                ps_data[pid] = {
                    "started": started, "etime": etime,
                    "tty": tty, "args": args,
                }
        except (OSError, subprocess.TimeoutExpired):
            pass

    results: list[UntrackedProcessInfo] = []
    for pid in pid_list:
        ps = ps_data.get(pid, {})
        results.append(UntrackedProcessInfo(
            pid=pid,
            cwd=_get_pid_cwd(pid),
            parent_app=_get_pid_parent_app(pid),
            args=ps.get("args", ""),
            started=ps.get("started", ""),
            uptime=ps.get("etime", ""),
            version=_get_pid_version(pid),
            tty=ps.get("tty", ""),
            children=_get_pid_children(pid),
        ))
    return results


def _find_stale_session_ids(
    sessions: list[SessionInfo], live_pids: set[int]
) -> list[str]:
    """Return session IDs whose PID is known but no longer running."""
    return [
        s.session_id
        for s in sessions
        if s.pid is not None and s.pid > 0 and s.pid not in live_pids
    ]


def check_session_health(
    sessions: list[SessionInfo], claude_pids: set[int]
) -> HealthStatus:
    """Compare tracked sessions against live Claude processes."""
    stale_ids = _find_stale_session_ids(sessions, claude_pids)
    stale_set = set(stale_ids)
    tracked_pids = {
        s.pid
        for s in sessions
        if s.pid is not None and s.pid > 0 and s.session_id not in stale_set
    }
    return HealthStatus(
        tracked_count=len(sessions),
        process_count=len(claude_pids),
        stale_ids=stale_ids,
        untracked_pids=claude_pids - tracked_pids,
    )


# --- Column Picker Modal ---


class ColumnPicker(ModalScreen[set]):
    """Modal for toggling column visibility."""

    CSS = """
    ColumnPicker {
        align: center middle;
    }
    #column-list {
        width: 50;
        height: auto;
        max-height: 20;
        background: $surface;
        border: tall $accent;
        padding: 0 1;
    }
    #column-keys {
        width: 50;
        height: 1;
        background: $surface;
        color: $text-muted;
        content-align: center middle;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel_picker", "Cancel", show=False),
        Binding("space", "toggle_highlighted", "Toggle", show=False),
        Binding("enter", "dismiss_picker", "Apply", show=False),
        Binding("c", "dismiss_picker", "Apply", show=False),
    ]

    def __init__(self, all_columns: tuple[ColumnDef, ...], hidden: set[str]) -> None:
        super().__init__()
        self._all_columns = all_columns
        self._hidden = set(hidden)
        self._original_hidden = set(hidden)

    def _build_options(self) -> list[Option]:
        return [
            Option(f"{'○' if c.key in self._hidden else '●'} {c.header}", id=c.key)
            for c in self._all_columns
        ]

    def compose(self) -> ComposeResult:
        yield OptionList(*self._build_options(), id="column-list")
        yield Static("space toggle · enter/c apply · esc cancel", id="column-keys")

    def _toggle(self, key: str) -> None:
        """Toggle a column's visibility."""
        if key in self._hidden:
            self._hidden.discard(key)
        elif len(self._all_columns) - len(self._hidden) > 1:
            self._hidden.add(key)
        else:
            return
        ol = self.query_one("#column-list", OptionList)
        highlighted = ol.highlighted
        ol.clear_options()
        for opt in self._build_options():
            ol.add_option(opt)
        if highlighted is not None:
            ol.highlighted = highlighted

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # enter triggers OptionList select — dismiss the picker instead of toggling
        self.action_dismiss_picker()

    def action_toggle_highlighted(self) -> None:
        ol = self.query_one("#column-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            return
        self._toggle(self._all_columns[idx].key)

    def action_dismiss_picker(self) -> None:
        self.dismiss(self._hidden)

    def action_cancel_picker(self) -> None:
        self.dismiss(self._original_hidden)


# --- Group Picker Modal ---


class GroupPicker(ModalScreen[str]):
    """Modal for selecting a group-by column."""

    CSS = """
    GroupPicker {
        align: center middle;
    }
    #group-list {
        width: 40;
        height: auto;
        max-height: 12;
        background: $surface;
        border: tall $accent;
        padding: 0 1;
    }
    #group-keys {
        width: 40;
        height: 1;
        background: $surface;
        color: $text-muted;
        content-align: center middle;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel_picker", "Cancel", show=False),
        Binding("g", "cancel_picker", "Cancel", show=False),
    ]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def _build_options(self) -> list[Option]:
        opts = [
            Option(
                f"{'●' if self._current == '' else '○'} None (flat view)", id="__none__"
            ),
        ]
        for gd in GROUP_DEFS.values():
            marker = "●" if self._current == gd.key else "○"
            opts.append(Option(f"{marker} {gd.label}", id=gd.key))
        return opts

    def compose(self) -> ComposeResult:
        yield OptionList(*self._build_options(), id="group-list")
        yield Static("enter select · esc cancel", id="group-keys")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option_id
        self.dismiss("" if oid == "__none__" else oid)

    def action_cancel_picker(self) -> None:
        self.dismiss(self._current)


# --- Confirm Kill Modal ---


class ConfirmKillScreen(ModalScreen[bool]):
    """Modal confirmation for killing a session."""

    CSS = """
    ConfirmKillScreen {
        align: center middle;
    }
    #kill-dialog {
        width: 50;
        height: auto;
        background: $surface;
        border: tall $error;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, session_name: str) -> None:
        super().__init__()
        self._session_name = session_name

    def compose(self) -> ComposeResult:
        yield Static(
            f"Kill session [bold]{self._session_name}[/bold]?\n\n"
            "[dim](y) yes  (n/esc) cancel[/dim]",
            id="kill-dialog",
        )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class UntrackedDetailsScreen(ModalScreen[None]):
    """Modal showing details about untracked Claude CLI processes."""

    CSS = """
    UntrackedDetailsScreen {
        align: center middle;
    }
    #untracked-panel {
        width: 80;
        height: auto;
        max-height: 32;
        background: $surface;
        border: tall $warning;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
        Binding("d", "dismiss_modal", "Close", show=False),
        Binding("q", "dismiss_modal", "Close", show=False),
    ]

    def __init__(self, pids: set[int]) -> None:
        super().__init__()
        self._pids = pids

    def compose(self) -> ComposeResult:
        n = len(self._pids)
        yield VerticalScroll(
            Static(
                f"[bold]{_plural(n, 'Untracked Session')}[/bold]\n\n"
                "[dim]Loading process details...[/dim]",
                id="untracked-content",
            ),
            id="untracked-panel",
        )

    def on_mount(self) -> None:
        self._load_details()

    @work(thread=True, exclusive=True, group="untracked-details")
    def _load_details(self) -> None:
        info = _gather_untracked_info(self._pids)
        self.app.call_from_thread(self._apply_details, info)

    @staticmethod
    def _format_entry(p: UntrackedProcessInfo) -> list[str]:
        """Format a single untracked process as markup lines."""
        dim = "[dim]unknown[/dim]"
        lines = [f"[bold]PID {p.pid}[/bold]  {p.args or ''}"]
        lines.append(f"  Dir:     {p.cwd or dim}")
        lines.append(f"  App:     {p.parent_app or dim}")
        if p.version:
            lines.append(f"  Version: {p.version}")
        if p.started or p.uptime:
            time_parts = []
            if p.started:
                time_parts.append(p.started)
            if p.uptime:
                time_parts.append(f"(up {p.uptime})")
            lines.append(f"  Started: {' '.join(time_parts)}")
        if p.tty:
            lines.append(f"  TTY:     {p.tty}")
        if p.children:
            lines.append(f"  Children:")
            for child in p.children:
                lines.append(f"    - {child}")
        return lines

    def _apply_details(self, info: list[UntrackedProcessInfo]) -> None:
        n = len(info)
        lines: list[str] = [
            f"[bold]{_plural(n, 'Untracked Session')}[/bold]",
            "[dim]No cctop tracking file - started before the plugin was installed.[/dim]",
            "",
        ]
        for i, p in enumerate(info):
            lines.extend(self._format_entry(p))
            if i < n - 1:
                lines.append("")
        lines.append("")
        lines.append("[dim]Press d or esc to close[/dim]")
        self.query_one("#untracked-content", Static).update("\n".join(lines))

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


# --- Help overlay ---

_HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Navigation", [
        ("\u2191/\u2193", "Select session (wraps around)"),
        ("\u2190/\u2192", "Move column"),
        ("Enter", "Toggle group collapse"),
    ]),
    ("View", [
        ("s", "Sort by active column"),
        ("g", "Group by picker"),
        ("G", "Remove grouping"),
        ("x", "Collapse/expand group"),
        ("v", "Toggle activity panel"),
        ("V", "Wide activity panel"),
    ]),
    ("Columns", [
        ("c", "Column picker"),
        ("C", "Show all columns"),
        ("h", "Hide active column"),
    ]),
    ("Actions", [
        ("k", "Kill session"),
        ("a", "Tmux attach"),
        ("R", "Purge dead sessions"),
        ("D", "Untracked session details"),
        ("r", "Force refresh"),
    ]),
    ("General", [
        ("t", "Change theme"),
        ("?", "This help"),
        ("q", "Quit"),
    ]),
]


class HelpOverlay(ModalScreen[None]):
    CSS = """
    HelpOverlay {
        align: center middle;
    }
    #help-panel {
        width: 52;
        height: auto;
        max-height: 28;
        background: $surface;
        border: tall $accent;
        padding: 1 2;
    }
    """
    BINDINGS = [
        Binding("question_mark", "dismiss_help", "Close", show=False),
        Binding("escape", "dismiss_help", "Close", show=False),
        Binding("q", "dismiss_help", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        lines: list[str] = ["[bold]Keyboard Shortcuts[/bold]\n"]
        for section, keys in _HELP_SECTIONS:
            lines.append(f"[bold]{section}[/bold]")
            for key, desc in keys:
                lines.append(f"  [reverse] {key:>5} [/reverse]  {desc}")
            lines.append("")
        lines.append("[dim]Press ? or esc to close[/dim]")
        yield Static("\n".join(lines), id="help-panel")

    def action_dismiss_help(self) -> None:
        self.dismiss(None)


# --- DataTable subclass for column indicator ---


class _CctopTable(DataTable):
    """DataTable with a column-selection indicator rendered at native speed.

    Overrides DataTable's left/right actions so the key path is identical to
    the native row cursor: widget binding → action → reactive set → watcher
    → targeted region refresh.  No App-level priority bindings needed.
    """

    selected_column: reactive[int] = reactive(0, init=False)

    def action_cursor_left(self) -> None:
        n = len(self.columns)
        if n:
            self.selected_column = (self.selected_column - 1) % n

    def action_cursor_right(self) -> None:
        n = len(self.columns)
        if n:
            self.selected_column = (self.selected_column + 1) % n

    def action_cursor_up(self) -> None:
        if self.row_count == 0:
            return
        cur = self.cursor_coordinate.row
        self.move_cursor(row=(cur - 1) % self.row_count)

    def action_cursor_down(self) -> None:
        if self.row_count == 0:
            return
        cur = self.cursor_coordinate.row
        self.move_cursor(row=(cur + 1) % self.row_count)

    def _should_highlight(self, cursor, target_cell, type_of_cursor):
        if super()._should_highlight(cursor, target_cell, type_of_cursor):
            return True
        cell_row, cell_col = target_cell
        return cell_row == -1 and cell_col == self.selected_column

    def _render_line_in_row(
        self, row_key, line_no, base_style, cursor_location, hover_location
    ):
        if row_key is self._header_row_key:
            cursor_location = Coordinate(cursor_location[0], self.selected_column)
        return super()._render_line_in_row(
            row_key,
            line_no,
            base_style,
            cursor_location,
            hover_location,
        )

    def watch_selected_column(self, old_value: int, new_value: int) -> None:
        header_height = self.header_height if self.show_header else 0
        if header_height == 0:
            return
        for col_idx in (old_value, new_value):
            if not self.is_valid_column_index(col_idx):
                continue
            region = self._get_column_region(col_idx)
            self._refresh_region(Region(region.x, 0, region.width, header_height))


# --- Textual App ---


class SessionsDashboard(App):
    """TUI dashboard for monitoring Claude Code sessions."""

    TITLE = "Claude Sessions"

    CSS = """
    #main-area {
        height: 1fr;
    }
    #main-left {
        width: 1fr;
    }
    #status-bar {
        height: 1;
        background: $surface;
    }
    #status-left, #status-right {
        width: 1fr;
        padding: 0 1;
    }
    #detail-panels {
        height: 12;
    }
    #detail-activity-scroll {
        width: 40;
        padding: 0 1;
        color: $text-muted;
        border-left: solid $surface-lighten-2;
    }
    #detail-chat-scroll, #detail-info-scroll {
        width: 1fr;
        padding: 0 1;
        color: $text-muted;
    }
    #detail-activity, #detail-chat, #detail-info {
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
    #action-bar {
        height: auto;
        padding: 0 1;
        background: $primary;
        color: $text;
        text-style: bold;
        text-align: right;
        display: none;
    }
    #action-bar.visible {
        display: block;
    }
    #footer-bar {
        dock: bottom;
        height: 1;
        background: $accent;
        color: $text;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True, system=True),
        Binding("q", "quit", "Quit", show=False),
        Binding("r", "force_refresh", "Refresh", show=False),
        Binding("R", "purge_dead", "Purge dead", show=False),
        Binding("D", "show_untracked_details", "Untracked details", show=False),
        Binding("s", "sort_by_column", "Sort col", show=False),
        Binding("h", "hide_column", "Hide col", show=False),
        Binding("c", "show_columns", "Columns", show=False),
        Binding("C", "show_all_columns", "Show all", show=False),
        Binding("k", "kill_session", "Kill", show=False),
        Binding("a", "tmux_attach", "Tmux Attach", show=False),
        Binding("g", "group_by_picker", "Group", show=False),
        Binding("G", "clear_group_by", "Ungroup", show=False),
        Binding("x", "toggle_group_collapse", "Collapse", show=False),
        Binding("v", "toggle_activity", "Activity", show=False),
        Binding("V", "expand_activity", "Activity++", show=False),
        Binding("question_mark", "show_help", "Help", show=False),
        Binding("t", "change_theme", "Theme", show=False),
    ]

    sort_mode: reactive[str] = reactive("activity", init=False)
    sort_reverse: reactive[bool] = reactive(True, init=False)
    group_by: reactive[str] = reactive("", init=False)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-area"):
            with Vertical(id="main-left"):
                yield _CctopTable(id="table")
                yield Static("", id="health-bar")
                yield Static("", id="action-bar")
                with Horizontal(id="status-bar"):
                    yield Static("", id="status-left")
                    yield Static("", id="status-right")
                with Horizontal(id="detail-panels"):
                    with VerticalScroll(id="detail-chat-scroll"):
                        yield Static("", id="detail-chat")
                    with VerticalScroll(id="detail-info-scroll"):
                        yield Static("", id="detail-info")
            with VerticalScroll(id="detail-activity-scroll"):
                yield Static("", id="detail-activity")
        yield Static("", id="footer-bar")

    def on_mount(self) -> None:
        self._config_loaded = False
        cfg = load_config()
        self.theme = cfg.get("ui", {}).get("theme", "textual-dark")
        hidden = cfg.get("columns", {}).get("hidden", [])
        self._init_state(hidden_columns=set(hidden))
        sort_cfg = cfg.get("sort", {})
        self.sort_mode = sort_cfg.get("column", "activity")
        self.sort_reverse = sort_cfg.get("reverse", True)
        self.group_by = cfg.get("group", {}).get("by", "")
        activity_cfg = cfg.get("activity", {})
        panel = self.query_one("#detail-activity-scroll")
        panel.display = activity_cfg.get("visible", False)
        w = activity_cfg.get("width", 40)
        panel.styles.width = "50%" if str(w) in ("50%", "50w") else 40
        self._setup_table()
        self._config_loaded = True
        self._update_footer()
        self._schedule_refresh()
        self.set_interval(0.5, self._schedule_refresh)

    def watch_theme(self, new_theme: str) -> None:
        """Persist theme choice to config whenever it changes."""
        if getattr(self, "_config_loaded", False):
            save_config({"ui": {"theme": new_theme}})

    def _init_state(self, hidden_columns: set[str] | None = None) -> None:
        """Initialize per-instance mutable state."""
        self._sessions: list[SessionInfo] = []
        self._last_health_check: float = 0.0
        self._last_health: HealthStatus | None = None
        self._last_row_keys: list[str] = []
        self._hidden_columns: set[str] = hidden_columns or set()
        self._collapsed_groups: set[str] = set()

    def _setup_table(self) -> None:
        """Configure the DataTable with columns from COLUMNS definitions."""
        table = self.query_one(_CctopTable)
        table.cursor_type = "row"
        vis = self._visible_columns()
        self._column_keys = table.add_columns(*self._column_headers(vis))
        self._update_column_indicator()

    def _visible_columns(self) -> tuple[ColumnDef, ...]:
        """Return COLUMNS filtered by _hidden_columns."""
        return tuple(c for c in COLUMNS if c.key not in self._hidden_columns)

    def _column_headers(self, vis: tuple[ColumnDef, ...]) -> list[str]:
        """Build header labels with sort arrow on the sorted column, space placeholder on others."""
        headers = []
        for c in vis:
            if c.key == self.sort_mode:
                arrow = "▼" if self.sort_reverse else "▲"
                headers.append(f"{c.header} {arrow}")
            else:
                headers.append(f"{c.header}  ")
        return headers

    def _update_sort_headers(self) -> None:
        """Update column header labels to reflect current sort column and direction."""
        table = self.query_one(_CctopTable)
        vis = self._visible_columns()
        headers = self._column_headers(vis)
        for col_key, label in zip(self._column_keys, headers):
            table.columns[col_key].label = Text(label)
        table._update_count += 1
        table.refresh()

    def _rebuild_columns(self) -> None:
        """Clear all columns and rows, re-add visible ones."""
        table = self.query_one(_CctopTable)
        saved_key = self._save_cursor(table)
        table.clear(columns=True)
        vis = self._visible_columns()
        self._column_keys = table.add_columns(*self._column_headers(vis))
        rows = self._build_table_rows(vis)
        for key, cells in rows:
            table.add_row(*cells, key=key)
        self._last_row_keys = [k for k, _ in rows]
        self._restore_cursor(table, saved_key)

        self._update_column_indicator()

    def _update_column_indicator(self) -> None:
        """Clamp the column indicator after column rebuild."""
        table = self.query_one(_CctopTable)
        max_col = max(0, len(table.columns) - 1)
        if table.selected_column > max_col:
            table.selected_column = max_col

    # --- Actions ---------------------------------------------------------

    def action_force_refresh(self) -> None:
        """Force reload all session data."""
        self._schedule_refresh()

    def action_purge_dead(self) -> None:
        """Remove dead session files and refresh."""
        self._do_purge()

    def action_show_untracked_details(self) -> None:
        """Show details about untracked Claude sessions."""
        if not self._last_health or not self._last_health.untracked_pids:
            return
        self.push_screen(UntrackedDetailsScreen(self._last_health.untracked_pids))

    def action_sort_by_column(self) -> None:
        """Sort by the currently active column. Press again to toggle direction."""
        vis = self._visible_columns()
        col_idx = self.query_one(_CctopTable).selected_column
        if not vis or col_idx >= len(vis):
            return
        col = vis[col_idx]
        if col.sort_key is None:
            self.notify(f"'{col.header}' is not sortable", severity="warning")
            return
        if self.sort_mode == col.key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_mode = col.key
            self.sort_reverse = col.reverse_sort

    def _apply_column_visibility(self) -> None:
        """Rebuild columns after visibility changes, reset sort if needed."""
        self._rebuild_columns()
        if self.sort_mode in self._hidden_columns:
            fallback = self._visible_columns()[0]
            self.sort_mode = fallback.key
            self.sort_reverse = fallback.reverse_sort
        else:
            self._update_subtitle()
        self._persist_columns()

    def _persist_columns(self) -> None:
        """Save current hidden columns to config."""
        if getattr(self, "_config_loaded", False):
            save_config({"columns": {"hidden": sorted(self._hidden_columns)}})

    def action_hide_column(self) -> None:
        """Hide the currently active column."""
        vis = self._visible_columns()
        if len(vis) <= 1:
            self.notify("Cannot hide the last column", severity="warning")
            return
        col = vis[self.query_one(_CctopTable).selected_column]
        self._hidden_columns.add(col.key)
        self._apply_column_visibility()

    def action_show_columns(self) -> None:
        """Open the column picker to toggle column visibility."""

        def _on_dismiss(result: set | None) -> None:
            if result is not None:
                self._hidden_columns = result
                self._apply_column_visibility()

        self.push_screen(
            ColumnPicker(COLUMNS, self._hidden_columns), callback=_on_dismiss
        )

    def action_show_all_columns(self) -> None:
        """Show all columns (reset hidden set)."""
        if not self._hidden_columns:
            self.notify("All columns already visible", severity="information")
            return
        self._hidden_columns.clear()
        self._apply_column_visibility()

    def action_group_by_picker(self) -> None:
        """Open the group-by picker modal."""

        def _on_dismiss(result: str | None) -> None:
            if result is not None and result != self.group_by:
                self.group_by = result

        self.push_screen(GroupPicker(self.group_by), callback=_on_dismiss)

    def _save_activity_config(self, panel) -> None:
        """Persist activity panel state to config."""
        # Textual represents 50% as "50w" internally; normalize to our format
        raw = str(panel.styles.width) if panel.display else "40"
        w = "50%" if raw in ("50%", "50w") else 40
        save_config({"activity": {"visible": panel.display, "width": w}})

    def _is_activity_wide(self) -> bool:
        panel = self.query_one("#detail-activity-scroll")
        return panel.display and str(panel.styles.width) in ("50%", "50w")

    def action_toggle_activity(self) -> None:
        """Toggle narrow activity panel, or shrink from wide."""
        panel = self.query_one("#detail-activity-scroll")
        if self._is_activity_wide():
            panel.styles.width = 40
        elif panel.display:
            panel.display = False
        else:
            panel.styles.width = 40
            panel.display = True
        self._save_activity_config(panel)

    def action_expand_activity(self) -> None:
        """Toggle wide activity panel, or expand from narrow."""
        panel = self.query_one("#detail-activity-scroll")
        if self._is_activity_wide():
            panel.display = False
        elif panel.display:
            panel.styles.width = "50%"
        else:
            panel.styles.width = "50%"
            panel.display = True
        self._save_activity_config(panel)

    def action_show_help(self) -> None:
        """Open the keybinding help overlay."""
        self.push_screen(HelpOverlay())

    def action_clear_group_by(self) -> None:
        """Remove grouping and return to flat view."""
        if not self.group_by:
            self.notify("Not grouped", severity="information")
            return
        self.group_by = ""

    def action_toggle_group_collapse(self) -> None:
        """Collapse/expand the group that the current row belongs to."""
        if not self.group_by:
            return
        table = self.query_one(_CctopTable)
        if table.row_count == 0:
            return
        row = table.cursor_coordinate.row
        # Walk upward from current row to find the parent group header
        for idx in range(row, -1, -1):
            try:
                cell_key = table.coordinate_to_cell_key(Coordinate(idx, 0))
                key = str(cell_key.row_key.value)
            except Exception:
                continue
            if key.startswith(_GROUP_ROW_PREFIX):
                group_name = key[len(_GROUP_ROW_PREFIX):]
                self._collapsed_groups.symmetric_difference_update({group_name})
                self._repopulate_table()
                # Restore cursor to the group header row
                try:
                    table.move_cursor(
                        row=table.get_row_index(key)
                    )
                except Exception:
                    pass
                return

    def action_kill_session(self) -> None:
        """Kill the highlighted session's process."""
        table = self.query_one(_CctopTable)
        row_key = self._save_cursor(table)
        session = self._find_session(row_key)
        if session is None:
            self.notify("No session selected", severity="warning")
            return
        if session.pid is None:
            self.notify("No PID available for this session", severity="warning")
            return
        name = session.custom_title or session.session_id[:8]

        def _on_confirm(confirmed: bool) -> None:
            if confirmed:
                self._do_kill(session.pid, name)

        self.push_screen(ConfirmKillScreen(name), callback=_on_confirm)

    @work(thread=True, exclusive=True, group="kill")
    def _do_kill(self, pid: int, name: str) -> None:
        """Send SIGINT to the session process in a worker thread."""
        try:
            os.kill(pid, signal.SIGTERM)
            self.call_from_thread(self.notify, f"Sent SIGTERM to {name} (pid {pid})")
        except ProcessLookupError:
            self.call_from_thread(
                self.notify, f"Process {pid} already exited", severity="warning"
            )
        except PermissionError:
            self.call_from_thread(
                self.notify, f"Permission denied killing pid {pid}", severity="error"
            )
        except OSError as exc:
            self.call_from_thread(self.notify, f"Kill failed: {exc}", severity="error")
        self.call_from_thread(self._schedule_refresh)

    def _find_tmux_target_by_pid(self, pid: int) -> str | None:
        """Find tmux target (session:window) by process PID.

        Uses 'tmux list-panes -a' to find which session:window contains this PID.
        Returns None if not found or tmux command fails.
        """
        try:
            result = subprocess.run(
                [
                    "tmux",
                    "list-panes",
                    "-a",
                    "-F",
                    "#{pane_pid} #{session_name}:#{window_index}",
                ],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode != 0:
                return None

            for line in result.stdout.splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2:
                    try:
                        pane_pid = int(parts[0])
                        if pane_pid == pid:
                            return parts[1]
                    except ValueError:
                        continue
            return None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Check if actions are available based on selected session."""
        if action == "tmux_attach":
            table = self.query_one(DataTable)
            if table.row_count == 0:
                return None
            try:
                row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
                session = self._find_session(row_key)
                if session is None:
                    return None
                # Show binding only if session has tmux metadata (non-empty string)
                return True if session.tmux_session != "" else None
            except Exception:
                return None
        return True

    def action_tmux_attach(self) -> None:
        """Attach to the tmux session & window where this Claude Code session is running."""
        table = self.query_one(DataTable)
        if table.row_count == 0:
            self.notify("No sessions available", severity="warning")
            return

        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            self.notify("No session selected", severity="warning")
            return

        session = self._find_session(row_key)
        if session is None:
            self.notify("No session selected", severity="warning")
            return

        if not session.tmux_session and not session.pid:
            self.notify("Session not running in tmux", severity="warning")
            return

        # Build cached target: "session:window" if window available, else just "session"
        target = None
        if session.tmux_session:
            target = session.tmux_session
            if session.tmux_window:
                target = f"{session.tmux_session}:{session.tmux_window}"

        # Try cached target first (fast path)
        if target:
            try:
                subprocess.run(
                    ["tmux", "switch-client", "-t", target],
                    timeout=2,
                    check=True,
                    capture_output=True,
                )
                return  # Success!
            except subprocess.CalledProcessError:
                pass  # Fall through to PID-based lookup

        # Fallback: PID-based lookup (handles renamed sessions/moved windows)
        if session.pid:
            target = self._find_tmux_target_by_pid(session.pid)
            if target:
                try:
                    subprocess.run(
                        ["tmux", "switch-client", "-t", target],
                        timeout=2,
                        check=True,
                        capture_output=True,
                    )
                    return  # Success!
                except subprocess.CalledProcessError:
                    pass  # Continue to error handling

        # Both methods failed
        self.notify("Tmux window not found", severity="warning")

    def watch_sort_mode(self, new_value: str) -> None:
        self._on_sort_changed()
        self._persist_sort()

    def watch_sort_reverse(self, new_value: bool) -> None:
        self._on_sort_changed()
        self._persist_sort()

    def watch_group_by(self, new_value: str) -> None:
        if not getattr(self, "_config_loaded", False):
            return
        self._collapsed_groups.clear()
        self._repopulate_table()
        self._update_subtitle()
        self._update_footer()
        self._persist_group()

    def _persist_group(self) -> None:
        """Save current group-by setting to config."""
        if getattr(self, "_config_loaded", False):
            save_config({"group": {"by": self.group_by}})

    def _persist_sort(self) -> None:
        """Save current sort settings to config."""
        if getattr(self, "_config_loaded", False):
            save_config({"sort": {"column": self.sort_mode, "reverse": self.sort_reverse}})

    def _on_sort_changed(self) -> None:
        """Re-sort table, update header arrows, refresh subtitle."""
        if not getattr(self, "_config_loaded", False):
            return
        self._repopulate_table()
        self._update_sort_headers()
        self._update_subtitle()

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

    def _apply_refresh(
        self, sessions: list[SessionInfo], health: HealthStatus | None
    ) -> None:
        """Main thread: update state and UI with results from the worker."""
        self._sessions = sessions
        if health is not None:
            self._last_health = health
        self._repopulate_table()
        self._update_subtitle()
        self._update_health_bar()
        # Refresh detail panels for the currently highlighted session
        table = self.query_one(DataTable)
        if table.cursor_row is not None and table.row_count > 0:
            row_key, _ = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
            session = self._find_session(row_key)
            if session:
                self._update_detail_panels(session)

    @staticmethod
    def _fkey(key: str) -> str:
        """Format a key for the footer bar (matches Textual Footer look)."""
        return f"[bold reverse] {key} [/bold reverse]"

    def _selected_session(self) -> SessionInfo | None:
        """Return the currently highlighted session, or None."""
        table = self.query_one(_CctopTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            return self._find_session(row_key)
        except Exception:
            return None

    def _update_footer(self) -> None:
        """Render the custom footer bar with grouped keybindings."""
        k = self._fkey
        sep = " "
        parts: list[str] = [
            f"{k('q')} Quit",
            f"{k('\u2191\u2193')} Session  {k('\u2190\u2192')} Col",
            f"{k('s')} Sort  {k('g')} Group  {k('c')} Cols",
        ]
        if self.group_by:
            parts[-1] += f"  {k('x')} Fold"
        parts.append(f"{k('v')} Activity")
        parts.append(f"{k('k')} Kill")
        parts.append(f"{k('?')} Help")
        self.query_one("#footer-bar", Static).update(
            Text.from_markup(sep.join(parts))
        )

    def _update_action_bar(self) -> None:
        """Show/hide the action bar based on selected session context."""
        bar = self.query_one("#action-bar", Static)
        session = self._selected_session()
        if session and session.tmux_session:
            k = self._fkey
            bar.update(Text.from_markup(
                f"{k('a')} Attach to tmux session '{session.tmux_session}'"
            ))
            bar.add_class("visible")
        else:
            bar.update("")
            bar.remove_class("visible")

    def _update_subtitle(self) -> None:
        """Update the header subtitle with session count, group, and sort info."""
        count = len(self._sessions)
        col_def = _COLUMN_BY_KEY.get(self.sort_mode)
        sort_label = col_def.header if col_def else self.sort_mode
        parts = [_plural(count, "session")]
        group_def = GROUP_DEFS.get(self.group_by)
        if group_def:
            parts.append(f"group: {group_def.label}")
        parts.append(f"sort: {sort_label}")
        self.sub_title = " · ".join(parts)

    def _update_health_bar(self) -> None:
        """Show or hide the health warning bar based on current health status."""
        bar = self.query_one("#health-bar", Static)
        h = self._last_health
        if h and h.has_mismatch:
            k = self._fkey
            parts: list[str] = []
            if h.stale_ids:
                parts.append(
                    f"{_plural(len(h.stale_ids), 'stale session')} detected"
                    f"  {k('R')} Purge"
                )
            if h.untracked_pids:
                parts.append(
                    f"{_plural(h.untracked_count, 'session')} not tracked"
                    f"  {k('D')} Details"
                )
            bar.update(Text.from_markup("  |  ".join(parts)))
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
        return sorted(self._sessions, key=sort_fn, reverse=self.sort_reverse)

    def _build_table_rows(
        self, vis: tuple[ColumnDef, ...]
    ) -> list[tuple[str, tuple]]:
        """Build (row_key, cells) list, interleaving group headers when grouped."""
        ordered = self._sorted_sessions()
        group_def = GROUP_DEFS.get(self.group_by) if self.group_by else None
        if not group_def:
            return [(s.session_id, _row_cells(s, vis)) for s in ordered]
        groups = _group_sessions(ordered, group_def)
        num_cols = len(vis)
        rows: list[tuple[str, tuple]] = []
        for name, sessions in groups:
            collapsed = name in self._collapsed_groups
            rows.append((
                f"{_GROUP_ROW_PREFIX}{name}",
                _group_header_cells(name, len(sessions), collapsed, num_cols),
            ))
            if not collapsed:
                for s in sessions:
                    cells = _row_cells(s, vis)
                    first = cells[0]
                    if isinstance(first, Text):
                        indented = Text("   ") + first
                    else:
                        indented = f"   {first}"
                    rows.append((s.session_id, (indented,) + cells[1:]))
        return rows

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
        vis = self._visible_columns()
        rows = self._build_table_rows(vis)
        new_keys = [k for k, _ in rows]

        if new_keys == self._last_row_keys:
            table = self.query_one(_CctopTable)
            for key, cells in rows:
                for col_key, value in zip(self._column_keys, cells):
                    table.update_cell(key, col_key, value)
        else:
            table = self.query_one(_CctopTable)
            saved_key = self._save_cursor(table)
            table.clear()
            for key, cells in rows:
                table.add_row(*cells, key=key)
            self._restore_cursor(table, saved_key)
    
            self._last_row_keys = new_keys

        if not self._sessions:
            self._clear_detail_panels()

    # --- Detail panel ----------------------------------------------------

    @staticmethod
    def _status_left(s: SessionInfo) -> str:
        """Markup for the left status bar: path and branch."""
        return "".join(
            p
            for p in (
                f"[bold]{s.cwd or '?'}[/bold]",
                f"  [cyan]{s.git_branch}[/cyan]" if s.git_branch else None,
            )
            if p is not None
        )

    @staticmethod
    def _status_right(s: SessionInfo) -> str:
        """Markup for the right status bar: model and session ID."""
        chips = [
            p
            for p in (
                f"[cyan]{s.model}[/cyan]" if s.model else None,
                f"[dim]{s.session_id}[/dim]",
            )
            if p
        ]
        return " [dim]·[/dim] ".join(chips)

    @staticmethod
    def _detail_session_info(s: SessionInfo) -> RichTable:
        """Build a key-value table for the info panel (right)."""
        tbl = RichTable(
            show_header=False, show_edge=False, box=None,
            pad_edge=False, padding=(0, 1),
        )
        tbl.add_column("key", style="dim", no_wrap=True)
        tbl.add_column("val", no_wrap=True)

        def _add(label: str, markup: str) -> None:
            tbl.add_row(label, Text.from_markup(markup))

        # Status with context
        status_text = styled_status(s)
        if s.status_context:
            ctx = s.status_context
            if len(ctx) > 60:
                ctx = ctx[:60] + "…"
            if "/" in ctx:
                ctx = _shorten_path(ctx)
            tbl.add_row("Status", Text.assemble(
                status_text, " ", Text(f"· {ctx}", style="dim"),
            ))
        else:
            tbl.add_row("Status", status_text)

        # Timing
        start = format_start_time(s.started_at) if s.started_at else ""
        dur = format_duration(s.started_at) if s.started_at else ""
        if start or dur:
            val = f"[green]{start}[/green]" if start else ""
            if dur:
                val += f" [green]({dur})[/green]" if val else f"[green]{dur}[/green]"
            _add("Started", val)

        # Activity
        if s.turns:
            _add("Turns", f"[yellow]{s.turns}[/yellow]")
        if s.tool_count:
            _add("Tools", f"[yellow]{s.tool_count}[/yellow]")
        n_files = len(s.files_edited) if s.files_edited else 0
        if n_files:
            _add("Files", f"[yellow]{n_files}[/yellow] edited")
        if s.subagent_count:
            _add("Agents", f"[#af87ff]{s.subagent_count}[/#af87ff]")

        # Tokens
        ctx = format_tokens(s.context_tokens)
        if ctx:
            window = format_tokens(get_context_window(s.model))
            _add("Tokens", f"[cyan]{ctx}[/cyan]/[dim]{window}[/dim] ctx")

        # Metrics
        if s.effort_level:
            _add("Effort", f"[yellow]{s.effort_level}[/yellow]")
        cost = _calc_cost(s)
        if cost >= 0.005:
            _add("Cost", f"[cyan]{format_cost(cost)}[/cyan]")

        # System
        if s.pid:
            _add("PID", f"[dim]{s.pid}[/dim]")
        if s.tmux_session:
            _add("Tmux", f"[dim]{s.tmux_session}:{s.tmux_window}[/dim]")

        # Errors (conditional)
        err_parts: list[str] = []
        if s.error_count:
            err_parts.append(f"[red bold]{_plural(s.error_count, 'error')}[/red bold]")
        if s.tool_failures:
            err_parts.append(f"[red bold]{_plural(s.tool_failures, 'failure')}[/red bold]")
        if s.error_details:
            err_parts.append(f"[red]{s.error_details}[/red]")
        if err_parts:
            _add("Errors", " [dim]·[/dim] ".join(err_parts))
        if s.stop_reason and s.stop_reason != "end_turn":
            _add("Stop", f"[dim]{s.stop_reason}[/dim]")

        return tbl

    def _find_session(self, row_key) -> SessionInfo | None:
        """Look up a session by its table row key."""
        if row_key is None:
            return None
        sid = str(row_key.value)
        return next((s for s in self._sessions if s.session_id == sid), None)

    @staticmethod
    def _build_chat(session: SessionInfo) -> Group:
        """Assemble the Rich renderable for the chat panel (center)."""
        parts: list = []
        parts.extend(_render_message("User", session.last_user_msg, 300))
        parts.extend(_render_message("Claude", session.last_assistant_msg, 800))
        if session.last_system_msg:
            msg = session.last_system_msg
            if msg.startswith("/"):
                cmd, _, args = msg.partition(" ")
                args_str = f" [dim italic]{args}[/]" if args else ""
                parts.append(Text.from_markup(
                    f"[dim italic]\u2192[/] [#af87ff bold]{cmd}[/]{args_str}"
                ))
            else:
                parts.append(Text.from_markup(
                    f"[dim italic]\u2192 {msg}[/dim italic]"
                ))
        return Group(*parts)

    @staticmethod
    def _build_activity(session: SessionInfo) -> Group:
        """Assemble the timestamped activity feed for the left panel."""
        if not session.recent_events:
            return Group(Text.from_markup("[dim]No recent activity[/dim]"))

        slash_c = ACTIVITY_STYLE["slash_cmd"][1]
        lines: list[Text] = []
        for ev in reversed(session.recent_events):
            ts = _format_event_time(ev.get("ts", ""))
            ev_type = ev.get("type", "")
            detail = ev.get("detail", "")

            if ev_type == "tool":
                name = ev.get("name", "?")
                icon, c = ACTIVITY_STYLE.get(f"tool:{name}", ACTIVITY_STYLE["tool"])
                detail = _shorten_path(detail) if "/" in detail else _truncate(detail)
                detail_str = f" [dim]{detail}[/dim]" if detail else ""
                lines.append(Text.from_markup(
                    f"[dim]{ts}[/dim] [{c}]{icon} {name}[/{c}]{detail_str}"
                ))
                continue

            icon, c = ACTIVITY_STYLE.get(ev_type, ("?", "dim"))
            if ev_type == "system" and detail.startswith("/") and "/" not in detail[1:].split(" ", 1)[0]:
                cmd, _, args = detail.partition(" ")
                args_str = f" [{c}]{args}[/]" if args else ""
                lines.append(Text.from_markup(
                    f"[dim]{ts}[/dim] [{c}]{icon}[/] [{slash_c}]{cmd}[/]{args_str}"
                ))
            else:
                lines.append(Text.from_markup(
                    f"[dim]{ts}[/dim] [{c}]{icon} {_truncate(detail)}[/]"
                ))
        return Group(*lines)

    @staticmethod
    def _build_info(session: SessionInfo) -> RichTable:
        """Assemble the Rich renderable for the session info panel (right)."""
        return SessionsDashboard._detail_session_info(session)

    def _clear_detail_panels(self) -> None:
        """Clear status bar and all detail panels."""
        self.query_one("#status-left", Static).update("")
        self.query_one("#status-right", Static).update("")
        self.query_one("#detail-activity", Static).update("")
        self.query_one("#detail-chat", Static).update("")
        self.query_one("#detail-info", Static).update("")
        bar = self.query_one("#action-bar", Static)
        bar.update("")
        bar.remove_class("visible")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Toggle collapse when Enter is pressed on a group header row."""
        if event.row_key is None:
            return
        key = str(event.row_key.value)
        if not key.startswith(_GROUP_ROW_PREFIX):
            return
        group_name = key[len(_GROUP_ROW_PREFIX):]
        self._collapsed_groups.symmetric_difference_update({group_name})
        self._repopulate_table()

    def _update_detail_panels(self, session: SessionInfo) -> None:
        """Refresh all detail panels for the given session."""
        self.query_one("#status-left", Static).update(
            Text.from_markup(self._status_left(session))
        )
        self.query_one("#status-right", Static).update(
            Text.from_markup(self._status_right(session))
        )
        self.query_one("#detail-activity", Static).update(self._build_activity(session))
        self.query_one("#detail-chat", Static).update(self._build_chat(session))
        self.query_one("#detail-info", Static).update(self._build_info(session))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show detail for the highlighted row."""
        session = self._find_session(event.row_key)
        if session is None:
            self._clear_detail_panels()
            return
        self._update_detail_panels(session)
        self._update_action_bar()


if __name__ == "__main__":
    if "--reset" in sys.argv:
        _reset_session_data()
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        print("cctop: session data cleared")
    app = SessionsDashboard()
    app.run()
