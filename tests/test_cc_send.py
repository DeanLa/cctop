# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest>=8.0"]
# ///
"""Tests for cc-send — frame building, roster lookup, socket resolution, delivery."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import shutil
import socket
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

# cc-send has no .py extension (it's a program, not a module), so load it via
# an explicit source loader rather than extension-based import.
_scripts = Path(__file__).resolve().parent.parent / "plugin" / "scripts"
_loader = importlib.machinery.SourceFileLoader("cc_send", str(_scripts / "cc-send"))
_spec = importlib.util.spec_from_loader("cc_send", _loader)
cc_send = importlib.util.module_from_spec(_spec)
_loader.exec_module(cc_send)


def _args(**kw) -> SimpleNamespace:
    """Build an args namespace matching cc-send's parser defaults."""
    base = dict(
        message=None, session_id=None, socket=None, rename=None,
        sender="cc-send", now=False, list=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def short_sockdir():
    """A short /tmp dir — AF_UNIX paths must fit macOS's ~104-char sun_path limit."""
    d = tempfile.mkdtemp(prefix="ccs-", dir="/tmp")
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _serve_once(sock_path: str, received: list, ready: threading.Event) -> None:
    """Accept one connection, read to EOF, stash the bytes."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    ready.set()
    conn, _ = srv.accept()
    with conn:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
    received.append(data)
    srv.close()


def _start_server(sock_path: str) -> tuple[threading.Thread, list]:
    received: list = []
    ready = threading.Event()
    t = threading.Thread(target=_serve_once, args=(sock_path, received, ready))
    t.start()
    assert ready.wait(timeout=2), "server did not bind in time"
    return t, received


# --- build_frame ---

class TestBuildFrame:
    def test_message_default_priority_next(self):
        frame = cc_send.build_frame(_args(message="hi"))
        assert frame == {
            "type": "user",
            "message": {"role": "user", "content": "hi"},
            "from": "cc-send",
            "priority": "next",
        }

    def test_now_flag_sets_priority_now(self):
        assert cc_send.build_frame(_args(message="hi", now=True))["priority"] == "now"

    def test_custom_sender(self):
        assert cc_send.build_frame(_args(message="hi", sender="alice"))["from"] == "alice"

    def test_rename_control_frame(self):
        frame = cc_send.build_frame(_args(rename="New Title"))
        assert frame == {"type": "control", "action": "rename", "name": "New Title"}


# --- find_by_session_id ---

class TestFindBySessionId:
    def test_match(self, tmp_path):
        (tmp_path / "111.json").write_text(json.dumps(
            {"sessionId": "abc", "messagingSocketPath": "/tmp/cc-socks/111.sock"}))
        (tmp_path / "222.json").write_text(json.dumps(
            {"sessionId": "def", "messagingSocketPath": "/tmp/cc-socks/222.sock"}))
        assert cc_send.find_by_session_id("def", str(tmp_path)) == "/tmp/cc-socks/222.sock"

    def test_miss(self, tmp_path):
        (tmp_path / "111.json").write_text(json.dumps(
            {"sessionId": "abc", "messagingSocketPath": "/s.sock"}))
        assert cc_send.find_by_session_id("nope", str(tmp_path)) is None

    def test_match_without_socket_is_unaddressable(self, tmp_path):
        # pre-2.1.224 session: has an id but never bound a socket
        (tmp_path / "111.json").write_text(json.dumps({"sessionId": "abc"}))
        assert cc_send.find_by_session_id("abc", str(tmp_path)) is None

    def test_skips_corrupt_json(self, tmp_path):
        (tmp_path / "bad.json").write_text("{not json")
        (tmp_path / "ok.json").write_text(json.dumps(
            {"sessionId": "abc", "messagingSocketPath": "/s.sock"}))
        assert cc_send.find_by_session_id("abc", str(tmp_path)) == "/s.sock"


# --- resolve_socket precedence ---

class TestResolveSocket:
    def test_explicit_socket_wins_over_session_id(self, monkeypatch):
        monkeypatch.setattr(cc_send, "ROSTER_DIR", "/nonexistent")
        args = _args(socket="/explicit.sock", session_id="abc")
        assert cc_send.resolve_socket(args) == "/explicit.sock"

    def test_session_id_wins_over_env(self, tmp_path, monkeypatch):
        (tmp_path / "1.json").write_text(json.dumps(
            {"sessionId": "abc", "messagingSocketPath": "/from-roster.sock"}))
        monkeypatch.setattr(cc_send, "ROSTER_DIR", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/from-env.sock")
        assert cc_send.resolve_socket(_args(session_id="abc")) == "/from-roster.sock"

    def test_session_id_not_addressable_raises_code_2(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc_send, "ROSTER_DIR", str(tmp_path))
        with pytest.raises(cc_send.ResolveError) as exc:
            cc_send.resolve_socket(_args(session_id="ghost"))
        assert exc.value.code == 2

    def test_env_var_used_when_no_selector(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc_send, "SOCK_DIR", str(tmp_path))  # empty
        monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/from-env.sock")
        assert cc_send.resolve_socket(_args()) == "/from-env.sock"

    def test_sole_socket_fallback(self, tmp_path, monkeypatch):
        (tmp_path / "only.sock").write_text("")
        monkeypatch.setattr(cc_send, "SOCK_DIR", str(tmp_path))
        monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)
        assert cc_send.resolve_socket(_args()) == str(tmp_path / "only.sock")

    def test_ambiguous_sockets_raise_code_2(self, tmp_path, monkeypatch):
        (tmp_path / "a.sock").write_text("")
        (tmp_path / "b.sock").write_text("")
        monkeypatch.setattr(cc_send, "SOCK_DIR", str(tmp_path))
        monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)
        with pytest.raises(cc_send.ResolveError) as exc:
            cc_send.resolve_socket(_args())
        assert exc.value.code == 2

    def test_no_sockets_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc_send, "SOCK_DIR", str(tmp_path))
        monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)
        with pytest.raises(cc_send.ResolveError):
            cc_send.resolve_socket(_args())


# --- list_roster ---

def test_list_roster_skips_non_session_files(tmp_path):
    (tmp_path / "1.json").write_text(json.dumps(
        {"name": "sess-a", "sessionId": "abc",
         "messagingSocketPath": "/a.sock", "status": "idle"}))
    (tmp_path / "notes.json").write_text(json.dumps({"foo": "bar"}))
    assert cc_send.list_roster(str(tmp_path)) == [("sess-a", "abc", "/a.sock", "idle")]


# --- send_frame + main over a real socket ---

class TestDelivery:
    def test_send_frame_delivers_exact_newline_json(self, short_sockdir):
        sock_path = str(short_sockdir / "peer.sock")
        t, received = _start_server(sock_path)
        cc_send.send_frame(sock_path, {"type": "control", "action": "rename", "name": "hello"})
        t.join(timeout=2)
        assert received[0] == b'{"type":"control","action":"rename","name":"hello"}\n'

    def test_main_rename_via_session_id(self, short_sockdir, monkeypatch):
        sock_path = str(short_sockdir / "peer.sock")
        roster = short_sockdir / "roster"
        roster.mkdir()
        (roster / "1.json").write_text(json.dumps(
            {"sessionId": "abc", "messagingSocketPath": sock_path}))
        monkeypatch.setattr(cc_send, "ROSTER_DIR", str(roster))
        t, received = _start_server(sock_path)
        rc = cc_send.main(["--session-id", "abc", "--rename", "Renamed"])
        t.join(timeout=2)
        assert rc == 0
        assert json.loads(received[0]) == {
            "type": "control", "action": "rename", "name": "Renamed"}

    def test_main_message_via_socket(self, short_sockdir):
        sock_path = str(short_sockdir / "peer.sock")
        t, received = _start_server(sock_path)
        rc = cc_send.main(["--socket", sock_path, "--now", "-f", "alice", "ping"])
        t.join(timeout=2)
        assert rc == 0
        assert json.loads(received[0]) == {
            "type": "user",
            "message": {"role": "user", "content": "ping"},
            "from": "alice",
            "priority": "now",
        }


# --- main exit codes ---

class TestMainExitCodes:
    def test_session_not_found_exits_2(self, tmp_path, monkeypatch):
        roster = tmp_path / "roster"
        roster.mkdir()
        monkeypatch.setattr(cc_send, "ROSTER_DIR", str(roster))
        assert cc_send.main(["--session-id", "ghost", "--rename", "X"]) == 2

    def test_dead_socket_exits_1(self, short_sockdir):
        # path resolves but nothing is listening
        assert cc_send.main(["--socket", str(short_sockdir / "dead.sock"), "--rename", "X"]) == 1

    def test_empty_rename_exits_2(self, short_sockdir):
        assert cc_send.main(["--socket", str(short_sockdir / "x.sock"), "--rename", "   "]) == 2

    def test_no_message_body_exits_2(self, short_sockdir, monkeypatch):
        class _FakeTTY:
            def isatty(self):
                return True

        monkeypatch.setattr(cc_send.sys, "stdin", _FakeTTY())
        assert cc_send.main(["--socket", str(short_sockdir / "x.sock")]) == 2
