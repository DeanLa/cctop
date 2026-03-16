# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""cctop poller — incremental JSONL reader for the cctop dashboard.

Runs as a background loop (~1s interval). For each active session, seeks to the
last-read byte offset in the JSONL transcript and parses only new lines.

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

import json
import os
import re
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

STATUS_DIR = Path.home() / ".cctop"
CODEX_HOME = Path.home() / ".codex"
CODEX_SESSIONS_DIR = CODEX_HOME / "sessions"
CODEX_ARCHIVED_SESSIONS_DIR = CODEX_HOME / "archived_sessions"
CODEX_SESSION_INDEX = CODEX_HOME / "session_index.jsonl"
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


def parse_codex_new_lines(lines: list[str]) -> dict:
    """Parse Codex transcript lines into the normalized poller fields."""
    updates: dict = {}
    turns_delta = 0
    tool_count_delta = 0
    error_count_delta = 0
    files_edited_delta: set[str] = set()
    latest_input = 0
    latest_output = 0
    latest_window = 0
    latest_status = ""

    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        record_type = obj.get("type", "")
        payload = obj.get("payload") or {}

        if record_type == "session_meta":
            cwd = payload.get("cwd", "")
            if cwd:
                updates["cwd"] = cwd
                updates["project_name"] = Path(cwd).name
            started_at = payload.get("timestamp", "")
            if started_at:
                updates["started_at"] = started_at
            model_provider = payload.get("model_provider", "")
            if model_provider:
                updates["model_provider"] = model_provider
            continue

        if record_type == "turn_context":
            model = payload.get("model", "")
            if model:
                updates["model"] = model
            cwd = payload.get("cwd", "")
            if cwd:
                updates["cwd"] = cwd
                updates["project_name"] = Path(cwd).name
            continue

        if record_type == "event_msg":
            event_type = payload.get("type", "")
            if event_type == "task_started":
                latest_status = "thinking"
                if payload.get("model_context_window"):
                    latest_window = payload["model_context_window"]
            elif event_type == "task_complete":
                latest_status = "idle"
            elif event_type == "user_message":
                message = payload.get("message", "")
                if isinstance(message, str) and message.strip():
                    turns_delta += 1
                    updates["last_user_msg"] = message
            elif event_type == "agent_message":
                message = payload.get("message", "")
                if isinstance(message, str) and message.strip():
                    updates["last_assistant_msg"] = message
            elif event_type == "token_count":
                info = payload.get("info") or {}
                total = info.get("total_token_usage") or {}
                last = info.get("last_token_usage") or {}
                latest_input = last.get("input_tokens", 0) + last.get("cached_input_tokens", 0)
                latest_output = last.get("output_tokens", 0) + last.get("reasoning_output_tokens", 0)
                updates["cumulative_input_tokens"] = total.get("input_tokens", 0)
                updates["cumulative_output_tokens"] = total.get("output_tokens", 0)
                updates["cumulative_cache_read_tokens"] = total.get("cached_input_tokens", 0)
                updates["cumulative_cache_creation_tokens"] = 0
                updates["reasoning_output_tokens"] = total.get("reasoning_output_tokens", 0)
                latest_window = info.get("model_context_window", latest_window)
            continue

        if record_type != "response_item":
            continue

        item_type = payload.get("type", "")
        if item_type == "function_call":
            tool_count_delta += 1
            name = payload.get("name", "")
            arguments_raw = payload.get("arguments", "")
            arguments = {}
            if isinstance(arguments_raw, str):
                try:
                    arguments = json.loads(arguments_raw)
                except json.JSONDecodeError:
                    arguments = {}
            latest_status = infer_codex_status(name, arguments)
            files_edited_delta.update(extract_codex_edited_files(name, arguments))
        elif item_type == "function_call_output":
            output = payload.get("output", "")
            if isinstance(output, str) and "Exit code:" in output:
                try:
                    exit_code = int(output.split("Exit code:", 1)[1].splitlines()[0].strip())
                except (ValueError, IndexError):
                    exit_code = 0
                if exit_code != 0:
                    error_count_delta += 1
            latest_status = "thinking"
        elif item_type == "message":
            if payload.get("role") == "assistant":
                texts: list[str] = []
                for block in payload.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        text = block.get("text", "")
                        if text:
                            texts.append(text)
                if texts:
                    updates["last_assistant_msg"] = "\n".join(texts).strip()

    if latest_input:
        updates["input_tokens"] = latest_input
    if latest_output:
        updates["output_tokens"] = latest_output
    if latest_window:
        updates["context_window"] = latest_window

    updates["_status"] = latest_status
    updates["_delta_turns"] = turns_delta
    updates["_delta_tool_count"] = tool_count_delta
    updates["_delta_files_edited"] = sorted(files_edited_delta)
    updates["_delta_subagent_count"] = 0
    updates["_delta_error_count"] = error_count_delta
    updates["_delta_cumulative_input"] = 0
    updates["_delta_cumulative_output"] = 0
    updates["_delta_cumulative_cache_read"] = 0
    updates["_delta_cumulative_cache_creation"] = 0
    updates["_delta_reasoning_output"] = 0

    return updates


def infer_codex_status(name: str, arguments: dict) -> str:
    """Map a Codex tool call to a normalized status label."""
    if name == "multi_tool_use.parallel":
        return "tool:multi_tool_use.parallel"
    if name != "shell_command":
        return f"tool:{name}" if name else "thinking"

    command = str(arguments.get("command", "")).lower()
    if not command:
        return "tool:shell_command"

    if any(token in command for token in ("apply_patch", "set-content", "add-content", "out-file", "new-item")):
        return "tool:Edit"
    if any(token in command for token in ("git diff", "git show", "git status", "get-content", "cat ", "type ")):
        return "tool:Read"
    if any(token in command for token in ("rg ", "rg.exe", "select-string", "findstr", "get-childitem", "ls", "dir ")):
        return "tool:Glob"
    if any(token in command for token in ("pip install", "npm install", "uv ", "cargo ", "pytest", "python -m pytest", "npx ", "npm test", "npm run")):
        return "tool:Bash"
    return "tool:shell_command"


def extract_codex_edited_files(name: str, arguments: dict) -> set[str]:
    """Best-effort extraction of edited file paths from Codex tool calls."""
    if name != "shell_command":
        return set()

    command = str(arguments.get("command", ""))
    if not command:
        return set()

    files: set[str] = set()
    patterns = [
        r"Set-Content -Path ([^\s\"']+)",
        r"Set-Content -Path \"([^\"]+)\"",
        r"Add-Content -Path ([^\s\"']+)",
        r"Add-Content -Path \"([^\"]+)\"",
        r"Out-File ([^\s\"']+)",
        r"Get-Content ([^\n\r|]+)\s*\|",
        r"\*\*\* Update File: (.+)",
        r"\*\*\* Add File: (.+)",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, command):
            candidate = str(match).strip().strip("'\"")
            if candidate and not candidate.startswith("@'"):
                files.add(candidate)
    return files


# --- File I/O ---


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


def upsert_status_file(path: Path, updates: dict) -> None:
    """Merge updates into an existing JSON file and write atomically."""
    current = read_json(path) or {}
    current.update(updates)
    write_json(path, current)


CLEANUP_INTERVAL = 30.0
GRACE_PERIOD = 180  # 3 minutes — don't nuke sessions still spinning up
STALE_SECONDS = 60 * 60


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still alive
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
        provider = hook.get("provider", "claude")
        is_dead = False

        if provider == "codex":
            last_activity = hook.get("last_activity", "")
            if last_activity:
                try:
                    ts = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    is_dead = age > STALE_SECONDS
                except (ValueError, TypeError):
                    pass
        elif pid is not None and isinstance(pid, int) and pid > 0:
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


def read_codex_session_index() -> list[dict]:
    """Read recent Codex session index entries."""
    if not CODEX_SESSION_INDEX.is_file():
        return []

    entries: list[dict] = []
    try:
        lines = CODEX_SESSION_INDEX.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return entries

    now = datetime.now(timezone.utc)
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = obj.get("id", "")
        updated_at = obj.get("updated_at", "")
        if not sid or not updated_at:
            continue
        try:
            age = (now - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))).total_seconds()
        except (ValueError, TypeError):
            continue
        if age > STALE_SECONDS:
            continue
        entries.append(obj)

    entries.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return entries


def find_codex_transcript_path(session_id: str, updated_at: str) -> str:
    """Resolve the Codex transcript path for a session ID."""
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        dt = None

    search_roots: list[Path] = []
    if dt is not None:
        search_roots.append(CODEX_SESSIONS_DIR / dt.strftime("%Y") / dt.strftime("%m") / dt.strftime("%d"))
    search_roots.extend([CODEX_SESSIONS_DIR, CODEX_ARCHIVED_SESSIONS_DIR])

    pattern = f"*{session_id}.jsonl"
    for root in search_roots:
        if not root.exists():
            continue
        matches = sorted(root.glob(pattern))
        if matches:
            return str(matches[-1])
    return ""


def discover_codex_sessions() -> None:
    """Create or refresh normalized status files for recent Codex sessions."""
    for entry in read_codex_session_index():
        sid = entry["id"]
        transcript_path = find_codex_transcript_path(sid, entry["updated_at"])
        if not transcript_path:
            continue

        hook_fp = STATUS_DIR / f"{sid}.json"
        existing = read_json(hook_fp) or {}
        session_data = read_json_from_transcript(transcript_path)
        if session_data is None:
            session_data = {}

        cwd = session_data.get("cwd", existing.get("cwd", ""))
        project_name = Path(cwd).name if cwd else ""
        started_at = session_data.get("started_at", existing.get("started_at", entry["updated_at"]))
        model = session_data.get("model", existing.get("model", ""))
        last_status = existing.get("status", "idle")
        if not existing:
            last_status = "started"

        upsert_status_file(hook_fp, {
            "provider": "codex",
            "session_id": sid,
            "cwd": cwd,
            "status": last_status,
            "last_activity": entry["updated_at"],
            "started_at": started_at,
            "transcript_path": transcript_path,
            "model": model,
            "running_agents": 0,
            "tool_count": existing.get("tool_count", 0),
            "project_name": project_name,
        })
        poller_fp = STATUS_DIR / f"{sid}.poller.json"
        poller_existing = read_json(poller_fp) or {}
        if entry.get("thread_name") and not poller_existing.get("custom_title"):
            upsert_status_file(poller_fp, {
                "custom_title": entry["thread_name"],
                "slug": entry["thread_name"],
            })


def read_json_from_transcript(transcript_path: str) -> dict | None:
    """Read minimal session metadata from the first Codex transcript lines."""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for _ in range(8):
                line = fh.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "session_meta":
                    payload = obj.get("payload") or {}
                    return {
                        "cwd": payload.get("cwd", ""),
                        "started_at": payload.get("timestamp", ""),
                    }
                if obj.get("type") == "turn_context":
                    payload = obj.get("payload") or {}
                    return {
                        "cwd": payload.get("cwd", ""),
                        "model": payload.get("model", ""),
                    }
    except OSError:
        return None
    return None


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
    poller_data["reasoning_output_tokens"] = poller_data.get("reasoning_output_tokens", 0) + updates.pop("_delta_reasoning_output", 0)

    # files_edited: merge new paths into existing list (deduplicated)
    new_files = updates.pop("_delta_files_edited", [])
    if new_files:
        existing = set(poller_data.get("files_edited", []))
        existing.update(new_files)
        poller_data["files_edited"] = sorted(existing)


def poll_once() -> None:
    """Process all sessions once."""
    STATUS_DIR.mkdir(parents=True, exist_ok=True)

    discover_codex_sessions()

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
        provider = hook_data.get("provider", "claude")

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
            if provider == "codex":
                updates = parse_codex_new_lines(lines)
            else:
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

            status_update = updates.pop("_status", "")
            cwd_update = updates.get("cwd", "")
            started_at_update = updates.get("started_at", "")

            _accumulate_deltas(poller_data, updates)
            poller_data.update(updates)
            changed = True

            if provider == "codex":
                hook_updates = {
                    "last_activity": hook_data.get("last_activity", ""),
                }
                if status_update:
                    hook_updates["status"] = status_update
                if cwd_update:
                    hook_updates["cwd"] = cwd_update
                if started_at_update:
                    hook_updates["started_at"] = started_at_update
                if updates.get("model"):
                    hook_updates["model"] = updates["model"]
                line_ts = ""
                for line in reversed(lines):
                    try:
                        line_ts = json.loads(line).get("timestamp", "")
                    except json.JSONDecodeError:
                        continue
                    if line_ts:
                        break
                if line_ts:
                    hook_updates["last_activity"] = line_ts
                upsert_status_file(hook_fp, hook_updates)

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


def main() -> None:
    last_cleanup = 0.0
    while not _shutdown:
        poll_once()
        now = time.monotonic()
        if now - last_cleanup >= CLEANUP_INTERVAL:
            cleanup_dead_sessions()
            last_cleanup = now
        deadline = time.monotonic() + POLL_INTERVAL
        while time.monotonic() < deadline and not _shutdown:
            time.sleep(0.1)


if __name__ == "__main__":
    main()
