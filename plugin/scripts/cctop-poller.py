# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""cctop poller — incremental JSONL reader for the cctop dashboard.

Runs as a background loop (~1s interval). For each active session, seeks to the
last-read byte offset in the JSONL transcript and parses only new lines.

Supports both Claude Code transcripts (from hook-created session files) and
Copilot CLI sessions (discovered by scanning ~/.copilot/session-state/).

Writes poller-owned fields to <id>.poller.json (separate from the hook's
<id>.json). The dashboard merges both files. This eliminates write races.

Poller-owned fields: slug, custom_title, git_branch, project_name, model, last_user_msg,
  last_assistant_msg, input_tokens, output_tokens, turns, files_edited,
  subagent_count, error_count, stop_reason, cumulative_input_tokens,
  cumulative_output_tokens, cumulative_cache_read_tokens,
  cumulative_cache_creation_tokens, subagent_input_tokens,
  subagent_output_tokens, subagent_cache_read_tokens,
  subagent_cache_creation_tokens
"""

from __future__ import annotations

import glob as _glob_mod
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STATUS_DIR = Path.home() / ".cctop"
COPILOT_SESSION_DIR = Path.home() / ".copilot" / "session-state"
POLL_INTERVAL = 1.0

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# --- JSONL parsing ---


def parse_new_lines(lines: list[str]) -> dict:
    """Parse JSONL lines and extract poller-owned fields.

    For token counts, we keep the **latest** assistant turn's values (not
    cumulative sums) since they represent current context window usage.

    Also returns incremental deltas (prefixed with _delta_) for fields that
    must be accumulated by poll_once():
      _delta_turns, _delta_files_edited (set), _delta_subagent_count,
      _delta_error_count, _delta_cumulative_input, _delta_cumulative_output,
      _delta_cumulative_cache_read, _delta_cumulative_cache_creation
    """
    updates: dict = {}
    latest_input = 0
    latest_output = 0

    # Incremental counters (accumulated by poll_once)
    turns_delta = 0
    tool_count_delta = 0
    files_edited_delta: set[str] = set()
    subagent_count_delta = 0
    error_count_delta = 0
    cumulative_input_delta = 0
    cumulative_output_delta = 0
    cumulative_cache_read_delta = 0
    cumulative_cache_creation_delta = 0

    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        if obj.get("slug"):
            updates["slug"] = obj["slug"]
        if obj.get("gitBranch"):
            updates["git_branch"] = obj["gitBranch"]

        msg_type = obj.get("type", "")

        if msg_type == "custom-title":
            updates["custom_title"] = obj.get("customTitle", "")
            continue

        message = obj.get("message") or {}

        if msg_type == "user":
            content = message.get("content", "")
            if isinstance(content, str) and content and not content.startswith("<"):
                turns_delta += 1
                updates["last_user_msg"] = content

        elif msg_type == "assistant":
            content = message.get("content")
            if isinstance(content, list):
                parts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")

                    if btype == "text":
                        parts.append(block.get("text", ""))

                    elif btype == "tool_use":
                        tool_count_delta += 1
                        name = block.get("name", "")
                        inp = block.get("input") or {}
                        # Track files edited
                        if name in ("Edit", "Write"):
                            fp = inp.get("file_path", "")
                            if fp:
                                files_edited_delta.add(fp)
                        # Track subagent spawns
                        elif name == "Agent":
                            subagent_count_delta += 1

                    elif btype == "tool_result":
                        if block.get("is_error"):
                            error_count_delta += 1

                text = " ".join(parts).strip()
                if text:
                    updates["last_assistant_msg"] = text

            model = message.get("model", "")
            if model:
                updates["model"] = model

            stop = message.get("stop_reason", "")
            if stop:
                updates["stop_reason"] = stop

            usage = message.get("usage")
            if isinstance(usage, dict):
                base_in = usage.get("input_tokens", 0)
                cache_create = usage.get("cache_creation_input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
                out_tokens = usage.get("output_tokens", 0)
                inp_tokens = base_in + cache_create + cache_read
                latest_input = inp_tokens
                latest_output = out_tokens
                cumulative_input_delta += base_in
                cumulative_output_delta += out_tokens
                cumulative_cache_read_delta += cache_read
                cumulative_cache_creation_delta += cache_create

    if latest_input:
        updates["input_tokens"] = latest_input
    if latest_output:
        updates["output_tokens"] = latest_output

    # Deltas for accumulation
    updates["_delta_turns"] = turns_delta
    updates["_delta_tool_count"] = tool_count_delta
    updates["_delta_files_edited"] = list(files_edited_delta)
    updates["_delta_subagent_count"] = subagent_count_delta
    updates["_delta_error_count"] = error_count_delta
    updates["_delta_cumulative_input"] = cumulative_input_delta
    updates["_delta_cumulative_output"] = cumulative_output_delta
    updates["_delta_cumulative_cache_read"] = cumulative_cache_read_delta
    updates["_delta_cumulative_cache_creation"] = cumulative_cache_creation_delta

    return updates


# --- Copilot CLI events.jsonl parsing ---


# Map Copilot CLI tool names to the same status strings the dashboard understands.
# Copilot uses lowercase tool names; the dashboard STATUS_STYLE_MAP handles both.
_COPILOT_EDIT_TOOLS = frozenset({"edit", "create", "Edit", "Write"})


def parse_copilot_events(lines: list[str]) -> dict:
    """Parse Copilot CLI events.jsonl lines and extract poller-owned fields.

    The Copilot events.jsonl format uses typed events (session.start,
    user.message, assistant.usage, tool.execution_start, etc.) instead of
    Claude Code's flat type:user/assistant/custom-title format.

    Returns the same shape as parse_new_lines() for compatibility with
    _accumulate_deltas() and the dashboard.
    """
    updates: dict = {}
    latest_input = 0
    latest_output = 0

    turns_delta = 0
    tool_count_delta = 0
    files_edited_delta: set[str] = set()
    subagent_count_delta = 0
    error_count_delta = 0
    cumulative_input_delta = 0
    cumulative_output_delta = 0
    cumulative_cache_read_delta = 0
    cumulative_cache_creation_delta = 0

    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        event_type = obj.get("type", "")
        data = obj.get("data") or {}
        timestamp = obj.get("timestamp", "")

        if event_type == "session.start":
            ctx = data.get("context") or {}
            if ctx.get("cwd"):
                updates["cwd"] = ctx["cwd"]
            if ctx.get("branch"):
                updates["git_branch"] = ctx["branch"]
            if data.get("selectedModel"):
                updates["model"] = data["selectedModel"]
            if data.get("startTime"):
                updates["started_at"] = data["startTime"]
            updates["status"] = "started"

        elif event_type == "session.resume":
            updates["status"] = "resumed"

        elif event_type == "user.message":
            content = data.get("content", "")
            if isinstance(content, str) and content:
                # Skip slash commands and system-injected messages
                if not content.startswith("<") and not content.startswith("/"):
                    turns_delta += 1
                    updates["last_user_msg"] = content
            updates["status"] = "thinking"

        elif event_type == "assistant.intent":
            intent = data.get("intent", "")
            if intent:
                updates["slug"] = intent

        elif event_type == "assistant.turn_start":
            updates["status"] = "thinking"

        elif event_type == "assistant.message":
            content = data.get("content", "")
            if isinstance(content, str) and content.strip():
                updates["last_assistant_msg"] = content.strip()
            # Count tool requests
            for tr in data.get("toolRequests", []):
                tool_count_delta += 1
                name = tr.get("name", "")
                inp = tr.get("arguments") or {}
                if name in _COPILOT_EDIT_TOOLS:
                    fp = inp.get("path", "") or inp.get("file_path", "")
                    if fp:
                        files_edited_delta.add(fp)
                elif name in ("task",):
                    subagent_count_delta += 1

        elif event_type == "assistant.usage":
            inp_tokens = data.get("inputTokens", 0)
            out_tokens = data.get("outputTokens", 0)
            cache_read = data.get("cacheReadTokens", 0)
            cache_write = data.get("cacheWriteTokens", 0)
            latest_input = inp_tokens + cache_read + cache_write
            latest_output = out_tokens
            cumulative_input_delta += inp_tokens
            cumulative_output_delta += out_tokens
            cumulative_cache_read_delta += cache_read
            cumulative_cache_creation_delta += cache_write
            if data.get("model"):
                updates["model"] = data["model"]

        elif event_type == "tool.execution_start":
            name = data.get("toolName", "")
            if name:
                updates["status"] = f"tool:{name}"

        elif event_type == "tool.execution_complete":
            if not data.get("success", True):
                error_count_delta += 1
            updates["status"] = "thinking"

        elif event_type == "subagent.started":
            subagent_count_delta += 1
            updates["_running_agents_delta"] = updates.get("_running_agents_delta", 0) + 1

        elif event_type == "subagent.completed":
            updates["_running_agents_delta"] = updates.get("_running_agents_delta", 0) - 1

        elif event_type == "subagent.failed":
            error_count_delta += 1
            updates["_running_agents_delta"] = updates.get("_running_agents_delta", 0) - 1

        elif event_type == "session.idle":
            updates["status"] = "idle"

        elif event_type == "session.usage_info":
            if data.get("tokenLimit"):
                updates["token_limit"] = data["tokenLimit"]
            if data.get("currentTokens"):
                latest_input = data["currentTokens"]

        elif event_type == "assistant.turn_end":
            # After a turn ends, if no subsequent event changes status, it's idle
            updates["status"] = "idle"

        elif event_type == "session.model_change":
            if data.get("newModel"):
                updates["model"] = data["newModel"]

        # Track last activity from any event with a timestamp
        if timestamp:
            updates["last_activity"] = timestamp

    if latest_input:
        updates["input_tokens"] = latest_input
    if latest_output:
        updates["output_tokens"] = latest_output

    updates["_delta_turns"] = turns_delta
    updates["_delta_tool_count"] = tool_count_delta
    updates["_delta_files_edited"] = list(files_edited_delta)
    updates["_delta_subagent_count"] = subagent_count_delta
    updates["_delta_error_count"] = error_count_delta
    updates["_delta_cumulative_input"] = cumulative_input_delta
    updates["_delta_cumulative_output"] = cumulative_output_delta
    updates["_delta_cumulative_cache_read"] = cumulative_cache_read_delta
    updates["_delta_cumulative_cache_creation"] = cumulative_cache_creation_delta

    return updates


def parse_simple_yaml(path: Path) -> dict:
    """Parse a flat YAML file (key: value per line) without PyYAML dependency."""
    result: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            colon = line.find(":")
            if colon < 0:
                continue
            key = line[:colon].strip()
            value = line[colon + 1:].strip()
            result[key] = value
    except OSError:
        pass
    return result


def discover_copilot_sessions() -> list[dict]:
    """Find active Copilot CLI sessions by scanning for lock files.

    Returns list of dicts: {session_id, pid, session_dir, events_path}.
    """
    sessions: list[dict] = []
    if not COPILOT_SESSION_DIR.is_dir():
        return sessions

    for session_dir in COPILOT_SESSION_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        sid = session_dir.name
        events_path = session_dir / "events.jsonl"
        if not events_path.exists():
            continue

        # Find lock file: inuse.<pid>.lock
        pid = None
        for lock_fp in session_dir.glob("inuse.*.lock"):
            # Extract PID from filename
            parts = lock_fp.stem.split(".")  # "inuse.<pid>"
            if len(parts) >= 2:
                try:
                    pid = int(parts[1])
                except ValueError:
                    pass
            # Also check file content for PID
            if pid is None:
                try:
                    content = lock_fp.read_text().strip()
                    if content.isdigit():
                        pid = int(content)
                except OSError:
                    pass
            break  # only one lock file expected

        if pid is None:
            continue  # no active lock, session not running

        sessions.append({
            "session_id": sid,
            "pid": pid,
            "session_dir": str(session_dir),
            "events_path": str(events_path),
        })

    return sessions


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, data: dict) -> None:
    """Atomic write via tempfile + rename."""
    try:
        fd, tmp = tempfile.mkstemp(dir=STATUS_DIR, prefix=".tmp.")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


CLEANUP_INTERVAL = 30.0
GRACE_PERIOD = 180  # 3 minutes — don't nuke sessions still spinning up
STALE_SECONDS = 60 * 60


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running.

    Works on Linux, macOS, and Windows.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except (OSError, AttributeError):
            # Fallback: use tasklist
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                return str(pid) in result.stdout
            except (OSError, subprocess.TimeoutExpired):
                return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False


def cleanup_dead_sessions() -> int:
    """Remove session files whose owning Claude process has exited.

    Returns the number of sessions cleaned up.
    """
    if not STATUS_DIR.is_dir():
        return 0

    removed = 0
    now = time.time()

    for hook_fp in STATUS_DIR.glob("*.json"):
        if hook_fp.name.endswith(".poller.json"):
            continue

        try:
            hook = json.loads(hook_fp.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        sid = hook.get("session_id", hook_fp.stem)

        # Grace period: skip sessions that started less than 3 minutes ago
        started_at = hook.get("started_at", "")
        if started_at:
            try:
                from datetime import datetime, timezone
                ts = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                if age < GRACE_PERIOD:
                    continue
            except (ValueError, TypeError):
                pass

        pid = hook.get("pid")
        is_dead = False

        if pid is not None and isinstance(pid, int) and pid > 0:
            # PID-based check
            is_dead = not _is_pid_alive(pid)
        else:
            # Staleness fallback for pre-PID session files
            last_activity = hook.get("last_activity", "")
            if last_activity:
                try:
                    from datetime import datetime, timezone
                    ts = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    is_dead = age > STALE_SECONDS
                except (ValueError, TypeError):
                    pass

        if is_dead:
            try:
                hook_fp.unlink(missing_ok=True)
            except OSError:
                pass
            poller_fp = STATUS_DIR / f"{sid}.poller.json"
            try:
                poller_fp.unlink(missing_ok=True)
            except OSError:
                pass
            removed += 1

    return removed


def read_new_jsonl_lines(
    transcript_path: str, offset: int, prev_inode: int = 0
) -> tuple[list[str], int, int]:
    """Read new lines from a JSONL file starting at byte offset.

    Also tracks the file's inode to detect atomic replacements (compaction).
    When the inode changes, offset resets to 0 so the full file is re-read.

    Returns (lines, new_offset, current_inode).
    """
    try:
        st = os.stat(transcript_path)
        size = st.st_size
        current_inode = st.st_ino
    except OSError:
        return [], offset, prev_inode

    # File was atomically replaced (compaction) — reset offset
    if prev_inode and current_inode != prev_inode:
        offset = 0

    if size <= offset:
        if size < offset:
            offset = 0  # file shrank — reset
        else:
            return [], offset, current_inode

    lines = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            if offset > 0:
                # Only discard the first read if we're mid-line (previous byte
                # isn't a newline).  Our offsets come from fh.tell() after
                # reading complete lines, so they're normally at line
                # boundaries — discarding unconditionally was silently dropping
                # one valid line every poll cycle.
                fh.seek(offset - 1)
                prev_char = fh.read(1)
                if prev_char != "\n":
                    fh.readline()  # discard remainder of partial line
            for line in fh:
                lines.append(line)
            new_offset = fh.tell()
    except OSError:
        return [], offset, current_inode

    return lines, new_offset, current_inode


# --- Subagent aggregation ---


def find_subagents_dir(transcript_path: str, session_id: str) -> Path | None:
    """Locate the subagents/ directory for a session.

    Handles both layouts:
      - Flat:   <project-dir>/<session-id>.jsonl  → <project-dir>/<session-id>/subagents/
      - Subdir: <project-dir>/<session-id>/<session-id>.jsonl → <project-dir>/<session-id>/subagents/
    """
    tp = Path(transcript_path)
    # Subdirectory layout: transcript is inside <session-id>/
    subdir = tp.parent / "subagents"
    if subdir.is_dir():
        return subdir
    # Flat layout: transcript is alongside <session-id>/
    subdir = tp.parent / session_id / "subagents"
    if subdir.is_dir():
        return subdir
    return None


def aggregate_subagent_tokens(
    subagents_dir: Path,
    offsets: dict[str, int],
) -> tuple[int, int, int, int, dict[str, int]]:
    """Read new lines from all subagent transcripts, sum token usage.

    Returns (input_delta, output_delta, cache_read_delta,
             cache_creation_delta, updated_offsets).
    """
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_creation = 0
    new_offsets = dict(offsets)

    for jsonl_fp in sorted(subagents_dir.glob("agent-*.jsonl")):
        fname = jsonl_fp.name
        offset = offsets.get(fname, 0)
        lines, new_offset, _ = read_new_jsonl_lines(str(jsonl_fp), offset)
        new_offsets[fname] = new_offset

        for raw_line in lines:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            usage = (obj.get("message") or {}).get("usage")
            if not isinstance(usage, dict):
                continue
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_create = usage.get("cache_creation_input_tokens", 0)
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)
            total_cache_read += cache_read
            total_cache_creation += cache_create

    return total_input, total_output, total_cache_read, total_cache_creation, new_offsets


# --- Git helpers ---


def resolve_git_branch(cwd: str) -> str | None:
    """Resolve a meaningful branch name when HEAD is detached.

    Tries, in order:
      1. Exact tag   → "\U0001f3f7\ufe0f v1.2.3"
      2. Branch name → returned as-is (symbolic-ref succeeds only when not detached)
      3. Short SHA   → "\U0001f500 abc1234"

    Returns None if all attempts fail or if cwd is not a git repo.
    """
    if not cwd or not Path(cwd).is_dir():
        return None

    # (command, emoji_prefix)
    attempts: list[tuple[list[str], str]] = [
        (["git", "describe", "--tags", "--exact-match", "HEAD"], "\U0001f3f7\ufe0f "),
        (["git", "symbolic-ref", "--short", "HEAD"], ""),
        (["git", "rev-parse", "--short", "HEAD"], "\U0001f500 "),
    ]

    for cmd, prefix in attempts:
        try:
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                value = result.stdout.strip()
                if value:
                    return f"{prefix}{value}" if prefix else value
        except (OSError, subprocess.TimeoutExpired):
            continue

    return None


def detect_worktree(cwd: str) -> str | None:
    """If cwd is a git worktree, return the original repo basename. Otherwise None.

    Compares git-dir vs git-common-dir; if they differ, it's a worktree.
    The common dir's parent is the original repo root.
    """
    if not cwd or not Path(cwd).is_dir():
        return None
    try:
        git_dir = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=2,
        )
        common_dir = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=2,
        )
        if git_dir.returncode != 0 or common_dir.returncode != 0:
            return None
        gd = git_dir.stdout.strip()
        cd = common_dir.stdout.strip()
        if gd == cd:
            return None
        # common_dir is like /path/to/repo/.git → parent is the repo root
        return Path(cd).parent.name
    except (OSError, subprocess.TimeoutExpired):
        return None


# --- Main loop ---


def _accumulate_deltas(poller_data: dict, updates: dict) -> None:
    """Merge incremental deltas into the poller state.

    Pops _delta_* keys from updates and accumulates them into the
    corresponding poller_data fields. Non-delta keys are left in updates
    for the subsequent poller_data.update(updates) call.
    """
    poller_data["turns"] = poller_data.get("turns", 0) + updates.pop("_delta_turns", 0)
    poller_data["tool_count"] = poller_data.get("tool_count", 0) + updates.pop("_delta_tool_count", 0)
    poller_data["subagent_count"] = poller_data.get("subagent_count", 0) + updates.pop("_delta_subagent_count", 0)
    poller_data["error_count"] = poller_data.get("error_count", 0) + updates.pop("_delta_error_count", 0)
    poller_data["cumulative_input_tokens"] = poller_data.get("cumulative_input_tokens", 0) + updates.pop("_delta_cumulative_input", 0)
    poller_data["cumulative_output_tokens"] = poller_data.get("cumulative_output_tokens", 0) + updates.pop("_delta_cumulative_output", 0)
    poller_data["cumulative_cache_read_tokens"] = poller_data.get("cumulative_cache_read_tokens", 0) + updates.pop("_delta_cumulative_cache_read", 0)
    poller_data["cumulative_cache_creation_tokens"] = poller_data.get("cumulative_cache_creation_tokens", 0) + updates.pop("_delta_cumulative_cache_creation", 0)

    # files_edited: merge new paths into existing list (deduplicated)
    new_files = updates.pop("_delta_files_edited", [])
    if new_files:
        existing = set(poller_data.get("files_edited", []))
        existing.update(new_files)
        poller_data["files_edited"] = sorted(existing)


def poll_once() -> None:
    """Process all sessions once."""
    if not STATUS_DIR.is_dir():
        return

    for hook_fp in STATUS_DIR.glob("*.json"):
        # Skip poller files (*.poller.json)
        if hook_fp.stem.endswith(".poller"):
            continue

        hook_data = read_json(hook_fp)
        if hook_data is None:
            continue

        sid = hook_data.get("session_id", hook_fp.stem)
        transcript_path = hook_data.get("transcript_path", "")
        if not transcript_path:
            continue

        # Read our own poller file
        poller_fp = STATUS_DIR / f"{sid}.poller.json"
        poller_data = read_json(poller_fp) or {}
        offset = poller_data.get("_poller_offset", 0)
        prev_inode = poller_data.get("_poller_inode", 0)

        # Migration: if tool_count was never tracked, full re-read to count
        # all tool_use blocks from the transcript.
        needs_full_reread = "tool_count" not in poller_data and offset > 0
        # Fix: if last_user_msg is a system-injected message, re-read to
        # recover the real last user message.
        bad_user_msg = poller_data.get("last_user_msg", "").startswith("<")
        if needs_full_reread or bad_user_msg:
            offset = 0

        lines, new_offset, current_inode = read_new_jsonl_lines(
            transcript_path, offset, prev_inode
        )

        changed = False

        # After a full re-read, freeze existing counters so the full-file
        # deltas don't double-count accumulated values.
        if needs_full_reread or bad_user_msg:
            _saved = {k: poller_data.get(k, 0) for k in (
                "turns", "tool_count", "subagent_count", "error_count",
                "cumulative_input_tokens", "cumulative_output_tokens",
                "cumulative_cache_read_tokens", "cumulative_cache_creation_tokens",
            )}

        if lines:
            updates = parse_new_lines(lines)

            # Enrich git branch: resolve detached HEAD, detect worktrees.
            # For worktrees, prefix branch with 🌿 and override project_name
            # to show the original repo name instead of the worktree dir.
            if "git_branch" in updates:
                cwd = hook_data.get("cwd", "")
                if updates["git_branch"] == "HEAD":
                    # resolve_git_branch returns None only when not a git
                    # repo (rev-parse --short HEAD always succeeds otherwise),
                    # so clearing to "" is correct for non-repo directories.
                    updates["git_branch"] = resolve_git_branch(cwd) or ""
                if cwd:
                    repo_name = detect_worktree(cwd)
                    if repo_name and updates["git_branch"]:
                        updates["git_branch"] = "\U0001f33f " + updates["git_branch"]
                        updates["project_name"] = repo_name

            _accumulate_deltas(poller_data, updates)
            poller_data.update(updates)
            changed = True

        # Restore frozen counters after full re-read so only the targeted
        # fields (tool_count for migration, last_user_msg for bad-msg fix)
        # reflect the full-file scan.
        if (needs_full_reread or bad_user_msg) and lines:
            poller_data.update(_saved)

        if new_offset != offset:
            poller_data["_poller_offset"] = new_offset
            changed = True
        if current_inode != prev_inode:
            poller_data["_poller_inode"] = current_inode
            changed = True

        # Subagent token aggregation
        subagents_dir = find_subagents_dir(transcript_path, sid)
        if subagents_dir:
            sub_offsets = poller_data.get("_subagent_offsets", {})
            sub_in, sub_out, sub_cr, sub_cc, new_sub_offsets = aggregate_subagent_tokens(
                subagents_dir, sub_offsets
            )
            if sub_in or sub_out or sub_cr or sub_cc:
                poller_data["subagent_input_tokens"] = (
                    poller_data.get("subagent_input_tokens", 0) + sub_in
                )
                poller_data["subagent_output_tokens"] = (
                    poller_data.get("subagent_output_tokens", 0) + sub_out
                )
                poller_data["subagent_cache_read_tokens"] = (
                    poller_data.get("subagent_cache_read_tokens", 0) + sub_cr
                )
                poller_data["subagent_cache_creation_tokens"] = (
                    poller_data.get("subagent_cache_creation_tokens", 0) + sub_cc
                )
                changed = True
            if new_sub_offsets != sub_offsets:
                poller_data["_subagent_offsets"] = new_sub_offsets
                changed = True

        if changed:
            write_json(poller_fp, poller_data)


def poll_copilot_sessions() -> None:
    """Discover and poll all active Copilot CLI sessions.

    For each active session (identified by an inuse.*.lock file), reads
    workspace.yaml for metadata and incrementally parses events.jsonl.
    Writes both hook-equivalent JSON and poller JSON to ~/.cctop/.
    """
    if not STATUS_DIR.is_dir():
        STATUS_DIR.mkdir(parents=True, exist_ok=True)

    copilot_sessions = discover_copilot_sessions()

    for cs in copilot_sessions:
        sid = cs["session_id"]
        pid = cs["pid"]
        events_path = cs["events_path"]
        session_dir = Path(cs["session_dir"])

        hook_fp = STATUS_DIR / f"{sid}.json"
        poller_fp = STATUS_DIR / f"{sid}.poller.json"

        # Read existing data
        hook_data = read_json(hook_fp) or {}
        poller_data = read_json(poller_fp) or {}

        # Parse workspace.yaml for metadata (only on first discovery)
        if not hook_data:
            ws = parse_simple_yaml(session_dir / "workspace.yaml")
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            hook_data = {
                "session_id": sid,
                "cwd": ws.get("cwd", ""),
                "status": "started",
                "current_tool": "",
                "last_event": "SessionStart",
                "last_activity": ws.get("created_at", now_iso),
                "started_at": ws.get("created_at", now_iso),
                "pid": pid,
                "transcript_path": events_path,
                "model": "",
                "tool_count": 0,
                "running_agents": 0,
                "client": "copilot",
            }
            if ws.get("branch"):
                poller_data["git_branch"] = ws["branch"]
            if ws.get("cwd"):
                poller_data["project_name"] = Path(ws["cwd"]).name
            if ws.get("summary"):
                poller_data["slug"] = ws["summary"]

        # Ensure client tag is set
        hook_data["client"] = "copilot"
        hook_data["pid"] = pid

        # Incremental events.jsonl parsing
        offset = poller_data.get("_poller_offset", 0)
        prev_inode = poller_data.get("_poller_inode", 0)

        lines, new_offset, current_inode = read_new_jsonl_lines(
            events_path, offset, prev_inode
        )

        changed = False

        if lines:
            updates = parse_copilot_events(lines)

            # Extract hook-level fields from updates.
            # If the hook plugin is active (last_event is a real hook event like
            # PostToolUse), don't overwrite status — the hook provides more
            # accurate real-time status than the poller's events.jsonl parsing.
            hook_is_active = hook_data.get("last_event", "") in (
                "PostToolUse", "PreToolUse", "UserPromptSubmit", "Stop",
                "SubagentStop", "SessionStart",
            )
            if "status" in updates:
                if not hook_is_active:
                    hook_data["status"] = updates.pop("status")
                else:
                    updates.pop("status")
            if "last_activity" in updates:
                hook_data["last_activity"] = updates.pop("last_activity")
            if "started_at" in updates and not hook_data.get("started_at"):
                hook_data["started_at"] = updates.pop("started_at")
            else:
                updates.pop("started_at", None)
            if "cwd" in updates:
                hook_data["cwd"] = updates.pop("cwd")

            # Handle running_agents delta
            ra_delta = updates.pop("_running_agents_delta", 0)
            if ra_delta:
                hook_data["running_agents"] = max(
                    0, hook_data.get("running_agents", 0) + ra_delta
                )

            _accumulate_deltas(poller_data, updates)
            poller_data.update(updates)
            changed = True

        if new_offset != offset:
            poller_data["_poller_offset"] = new_offset
            changed = True
        if current_inode != prev_inode:
            poller_data["_poller_inode"] = current_inode
            changed = True

        # Update hook tool_count from poller data
        hook_data["tool_count"] = poller_data.get("tool_count", 0)
        if poller_data.get("model"):
            hook_data["model"] = poller_data["model"]

        if changed:
            write_json(hook_fp, hook_data)
            write_json(poller_fp, poller_data)


def cleanup_copilot_sessions() -> int:
    """Remove cctop files for Copilot sessions whose process has exited.

    Checks for the absence of inuse.*.lock files or dead PIDs.
    Returns the number of sessions cleaned up.
    """
    if not STATUS_DIR.is_dir():
        return 0

    removed = 0
    for hook_fp in STATUS_DIR.glob("*.json"):
        if hook_fp.name.endswith(".poller.json"):
            continue
        try:
            hook = json.loads(hook_fp.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        if hook.get("client") != "copilot":
            continue

        sid = hook.get("session_id", hook_fp.stem)
        session_dir = COPILOT_SESSION_DIR / sid

        # If the session directory is gone, clean up
        is_dead = not session_dir.is_dir()

        if not is_dead:
            # Check for lock files
            lock_files = list(session_dir.glob("inuse.*.lock"))
            if not lock_files:
                is_dead = True
            else:
                # Check PID from lock file
                pid = hook.get("pid")
                if pid and isinstance(pid, int) and pid > 0:
                    is_dead = not _is_pid_alive(pid)

        if is_dead:
            try:
                hook_fp.unlink(missing_ok=True)
            except OSError:
                pass
            poller_fp = STATUS_DIR / f"{sid}.poller.json"
            try:
                poller_fp.unlink(missing_ok=True)
            except OSError:
                pass
            removed += 1

    return removed


def main() -> None:
    last_cleanup = 0.0
    while not _shutdown:
        poll_once()
        poll_copilot_sessions()
        now = time.monotonic()
        if now - last_cleanup >= CLEANUP_INTERVAL:
            cleanup_dead_sessions()
            cleanup_copilot_sessions()
            last_cleanup = now
        deadline = time.monotonic() + POLL_INTERVAL
        while time.monotonic() < deadline and not _shutdown:
            time.sleep(0.1)


if __name__ == "__main__":
    main()
