"""Network transports against in-process fakes: telnet, raw TCP, RFC 2217, SSH.

No hardware and no outside network: every server listens on 127.0.0.1. The same
tools as on a serial port (read_until_prompt, expect, query_text, send_keys,
capture, transcript, status, reconnect_last, read-only mode, idle close) are
exercised over each transport, plus what is specific to the network: option
negotiation, IAC escaping, BREAK, refused control lines, named credentials,
host-key checking, ~/.ssh/config aliases and remote hang-ups.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from serial_console_mcp import credentials, transports
from serial_console_mcp import server as m

from .netfakes import (
    BINARY,
    COM_PORT,
    DO,
    ECHO,
    SGA,
    WILL,
    WONT,
    FakeRfc2217Server,
    FakeTcpServer,
    FakeTelnetServer,
    wait_for,
)

try:
    import asyncssh  # noqa: F401

    from serial_console_mcp import ssh_transport
    HAVE_SSH = True
except ImportError:  # pragma: no cover
    HAVE_SSH = False

needs_ssh = pytest.mark.skipif(not HAVE_SSH, reason="asyncssh ([ssh] extra) not installed")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(m, "_conns", {})
    monkeypatch.setattr(m, "_default_name", None)
    monkeypatch.setattr(m, "_idle_notes", {})
    monkeypatch.setattr(m, "READ_ONLY", False)
    monkeypatch.setattr(m, "IDLE_MINUTES", 0.0)
    monkeypatch.setattr(m, "NETWORK_HOSTS", "")
    monkeypatch.setattr(m, "_LOGIN_TIMEOUT", 6.0)
    monkeypatch.setattr(m, "_LOGIN_SETTLE", 1.0)
    monkeypatch.delenv("SERIAL_CONSOLE_CREDENTIALS", raising=False)
    # Never touch the real keychain or keyring from tests.
    monkeypatch.setattr(credentials, "_from_keyring", lambda name: None)
    monkeypatch.setattr(credentials, "_from_macos_keychain", lambda name: None)
    yield
    m.disconnect(all_connections=True)


def _ok(out: str) -> str:
    assert out.startswith("Connected"), out
    return out


def _expect(steps, **kw):
    return m.expect([m.ExpectStep(**st) for st in steps], **kw)


def _store(tmp_path, name, password, username=""):
    credentials.store_file(name, password, username, tmp_path)


# ----------------------------------------------------------------------------
# Target parsing and the host allowlist
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("args,expect", [
    (dict(port="telnet://cs1:2003"), ("telnet", "cs1", 2003, "", "telnet://cs1:2003")),
    (dict(port="ssh://admin@bench-pi"), ("ssh", "bench-pi", None, "admin",
                                         "ssh://admin@bench-pi")),
    (dict(port="tcp://10.0.0.5:4001"), ("tcp", "10.0.0.5", 4001, "", "tcp://10.0.0.5:4001")),
    (dict(port="raw://h:1"), ("tcp", "h", 1, "", "tcp://h:1")),
    (dict(port="rfc2217://[fe80::1]:3001"), ("rfc2217", "fe80::1", 3001, "",
                                             "rfc2217://[fe80::1]:3001")),
    (dict(transport="telnet", host="cs1"), ("telnet", "cs1", 23, "", "telnet://cs1:23")),
    (dict(transport="telnet", host="cs1:2005"), ("telnet", "cs1", 2005, "",
                                                 "telnet://cs1:2005")),
    (dict(transport="ssh", host="lab", username="u"), ("ssh", "lab", None, "u", "ssh://u@lab")),
    (dict(port="/dev/cu.x"), ("serial", "", None, "", "/dev/cu.x")),
])
def test_parse_target(args, expect):
    a = dict(port="", transport="serial", host="", tcp_port=None, username="")
    a.update(args)
    t, err = m._parse_target(a["port"], a["transport"], a["host"], a["tcp_port"],
                             a["username"])
    assert err is None, err
    assert (t["transport"], t["host"], t["tcp_port"], t["username"], t["target"]) == expect


@pytest.mark.parametrize("args,needle", [
    (dict(port="telnet://admin:pw@cs1:23"), "Never put a password"),
    (dict(port="gopher://x"), "Unknown scheme"),
    (dict(port="telnet://cs1", transport="ssh"), "but transport="),
    (dict(transport="tcp", host="cs1"), "needs tcp_port"),
    (dict(transport="rfc2217", host="cs1"), "needs tcp_port"),
    (dict(transport="telnet"), "needs a host"),
    (dict(transport="telnet", host="cs1", tcp_port=70000), "1..65535"),
    (dict(), "Give the serial port name"),
])
def test_parse_target_errors(args, needle):
    out = m.connect(**args)
    assert needle in out, out


def test_host_allowlist(monkeypatch):
    srv = FakeTelnetServer()
    try:
        monkeypatch.setattr(m, "NETWORK_HOSTS", "10.20.0.0/16, *.bench.lan")
        out = m.connect(transport="telnet", host="127.0.0.1", tcp_port=srv.port)
        assert "not in the hosts this server may reach" in out
        assert not m._conns
        monkeypatch.setattr(m, "NETWORK_HOSTS", "127.0.0.0/8")
        _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=srv.port))
        monkeypatch.setattr(m, "NETWORK_HOSTS", "none")
        assert "turned off" in m.connect(transport="telnet", host="127.0.0.1",
                                         tcp_port=srv.port)
        assert m._host_allowed("cs1.bench.lan") is not None  # "none" refuses all
        monkeypatch.setattr(m, "NETWORK_HOSTS", "*.bench.lan")
        assert m._host_allowed("CS1.bench.lan") is None
        assert "Network hosts limited to" in m.status()
    finally:
        srv.close()


def test_connection_refused_and_unresolvable():
    s = transports.socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens there now
    out = m.connect(transport="telnet", host="127.0.0.1", tcp_port=port)
    assert "refused" in out and "nothing is listening" in out
    out = m.connect(transport="tcp", host="no-such-host.invalid", tcp_port=4001)
    assert "Could not resolve" in out


# ----------------------------------------------------------------------------
# Telnet
# ----------------------------------------------------------------------------

@pytest.fixture
def telnet():
    srv = FakeTelnetServer()
    yield srv
    srv.close()


def test_telnet_negotiation(telnet):
    out = _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                        terminal="xterm", cols=100, rows=30, name="cs"))
    assert "telnet to 127.0.0.1" in out and "send_break sends a" in out
    assert wait_for(lambda: telnet.ttype is not None and telnet.naws is not None)
    assert telnet.ttype == b"XTERM"
    assert telnet.naws == (100, 30)
    cmds = set(telnet.commands)
    assert (DO, ECHO) in cmds and (DO, SGA) in cmds
    assert (WONT, COM_PORT) in cmds        # we refuse options we do not implement
    assert (DO, BINARY) in cmds and (WILL, BINARY) in cmds
    conn = m._conns["cs"]
    assert wait_for(lambda: BINARY in conn.port.local and BINARY in conn.port.remote)
    assert "binary mode" in conn.describe_settings()


def test_telnet_console_reads_and_expect(telnet):
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                  prompt="switch> ", name="cs"))
    out = m.query_text("show version")
    assert "Model: ex2200" in out and out.startswith("Matched prompt"), out
    out = _expect([{"send": "", "expect": "switch> "},
                    {"send": "show version", "expect": "Junos: "},
                    {"expect": "switch> "}])
    assert "TIMEOUT" not in out and "12.3R12.4" in out, out
    assert "Model: ex2200" in m.get_transcript()


def test_telnet_iac_escaping_both_ways(telnet):
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                  prompt="switch> "))
    assert "bytes:" in m.query_text("binary")
    conn = next(iter(m._conns.values()))
    assert b"bytes:\xff\x00\x01:end" in bytes(conn.transcript)  # IAC IAC -> one 0xFF
    m.send_hex("41 FF 42")
    assert wait_for(lambda: b"A\xffB" in telnet.received)
    assert b"A\xff\xffB" in telnet.raw  # escaped on the wire


def test_telnet_cr_handling_binary_and_nvt():
    for binary_ok, wire in ((True, b"hello\r"), (False, b"hello\r\x00")):
        srv = FakeTelnetServer(binary_ok=binary_ok)
        try:
            _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=srv.port,
                          name="t"))
            conn = m._conns["t"]
            assert wait_for(lambda c=conn, b=binary_ok, sv=srv: (BINARY in c.port.local) == b
                            and ((DO, BINARY) in sv.commands))
            m.send_text("hello", line_ending="CR")
            assert wait_for(lambda s=srv: b"hello" in s.lines)
            assert wire in srv.raw, (binary_ok, bytes(srv.raw))
            if not binary_ok:
                assert "NVT text mode" in conn.describe_settings()
            m.disconnect(all_connections=True)
        finally:
            srv.close()


def test_telnet_break_and_refused_lines(telnet):
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                  prompt="switch> "))
    out = m.send_break()
    assert "IAC BRK" in out
    assert wait_for(lambda: telnet.breaks == 1)
    assert "[BREAK]" in m.read_until_prompt()
    out = m.set_lines(dtr=False)
    assert "not available over telnet" in out and "rfc2217" in out
    out = m.pulse_line("RTS", 50)
    assert "not available over telnet" in out
    assert "n/a (telnet: no modem lines; BREAK can be sent)" in m.status()


def test_telnet_keys_and_screen(telnet):
    pytest.importorskip("pyte")
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                  terminal="xterm", prompt="switch> "))
    m.send_keys(["text:show version", "enter"])
    m.read_until_prompt()
    assert "Model: ex2200" in m.screen()
    m.send_keys(["ctrl-c"])
    assert wait_for(lambda: b"\x03" in telnet.received)


def test_telnet_capture_and_status(telnet, tmp_path):
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                  prompt="switch> ", name="cs"))
    out = m.capture_start("cs.txt", format="annotated")
    assert "Capturing" in out
    m.query_text("show version")
    m.capture_stop()
    text = (tmp_path / "captures" / "cs.txt").read_text()
    assert "telnet to 127.0.0.1" in text and "[" in text and "TX]" in text
    st = m.status()
    assert "'cs' (current): telnet to 127.0.0.1" in st and "Reader running" in st


def test_telnet_remote_hangup_is_reported(telnet):
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                  prompt="switch> ", name="cs"))
    m.send_text("quit")
    conn = m._conns["cs"]
    assert wait_for(lambda: not conn.reader_alive)
    out = m.send_text("show version")
    assert "telnet connection 'cs' has stopped" in out and "remote end closed" in out
    assert "STOPPED" in m.status()


def test_telnet_reconnect_last_remembers_target(telnet, tmp_path):
    _ok(m.connect(f"telnet://127.0.0.1:{telnet.port}", prompt="switch> ", name="cs",
                  baud=115200))
    saved = json.loads((tmp_path / "last_connection.json").read_text())["connections"]["cs"]
    assert saved["transport"] == "telnet" and saved["tcp_port"] == telnet.port
    assert saved["port"] == f"telnet://127.0.0.1:{telnet.port}"
    assert "baud" not in saved  # serial framing does not apply over telnet
    m.disconnect()
    out = _ok(m.reconnect_last())
    assert "not applied" not in out  # nothing explicit on reconnect
    assert "Model" in m.query_text("show version")
    assert f"cs (telnet://127.0.0.1:{telnet.port})" in (m.disconnect() and m.status())


def test_explicit_baud_over_telnet_is_flagged(telnet):
    out = _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                        baud=115200, rtscts=True))
    assert "baud, rtscts not applied" in out and "rfc2217" in out


def test_same_target_replaces_connection(telnet):
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port, name="a"))
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port, name="b"))
    assert list(m._conns) == ["b"]


def test_read_only_over_telnet(monkeypatch, telnet):
    monkeypatch.setattr(m, "READ_ONLY", True)
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                  prompt="switch> "))
    assert "Read-only" in m.send_text("configure")
    assert "Read-only" in m.send_break()
    assert "Model" in m.query_text("show version")


def test_idle_close_over_telnet(monkeypatch, telnet):
    monkeypatch.setattr(m, "IDLE_MINUTES", 1.0)
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port, name="cs"))
    closed = m._idle_check(now=m.time.time() + 120)
    assert closed == ["cs"] and not m._conns
    assert "auto-closed" in m.status()


def test_list_ports_and_diagnostics_mention_network(telnet, monkeypatch):
    monkeypatch.setattr(m.serial.tools.list_ports, "comports", lambda: [])
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port, name="cs"))
    out = m.list_serial_ports()
    assert "Network connections open here" in out and "cs: telnet://127.0.0.1" in out
    assert "network target" in m.port_in_use_by(f"telnet://127.0.0.1:{telnet.port}")
    assert "local serial port" in m.detect_baud(f"telnet://127.0.0.1:{telnet.port}")


# ----------------------------------------------------------------------------
# Console-server login with a named credential
# ----------------------------------------------------------------------------

def test_telnet_console_login_with_credential(tmp_path):
    srv = FakeTelnetServer(login=("admin", "pässw0rd"))
    try:
        _store(tmp_path, "bench-cs", "pässw0rd", username="admin")
        out = _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=srv.port,
                            credential="bench-cs", prompt="switch> ", name="cs"))
        assert "Console login: logged in" in out, out
        assert "after the login are buffered" in out
        assert "pässw0rd" not in out and "(hidden)" in out
        assert srv.login_attempts == [(b"admin", "pässw0rd".encode())]
        assert "Welcome" in m.read_until_prompt()
        saved = (tmp_path / "last_connection.json").read_text()
        assert '"credential": "bench-cs"' in saved and "pässw0rd" not in saved
    finally:
        srv.close()


def test_telnet_synch_before_login_leaves_no_stray_bytes(tmp_path):
    """inetutils telnetd sends IAC DM as urgent data before `login:`; no byte of it
    may turn up as text (seen live as two 0xF2 bytes before the prompt)."""
    srv = FakeTelnetServer(login=("admin", "pw"), synch=True)
    try:
        _store(tmp_path, "pi", "pw", username="admin")
        out = _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=srv.port,
                            credential="pi", prompt="switch> "))
        assert "Console login: logged in" in out, out
        assert "\ufffd" not in out and "\xf2" not in out, out
        assert "console server\nlogin: \nPassword:" in out.replace("\r", ""), out
    finally:
        srv.close()


def test_telnet_console_login_wrong_password(tmp_path):
    srv = FakeTelnetServer(login=("admin", "right"))
    try:
        _store(tmp_path, "bench-cs", "wrong", username="admin")
        out = m.connect(transport="telnet", host="127.0.0.1", tcp_port=srv.port,
                        credential="bench-cs")
        assert "FAILED" in out and "wrong" not in out.replace("wrong password", ""), out
    finally:
        srv.close()


def test_telnet_login_needs_username(tmp_path):
    srv = FakeTelnetServer(login=("admin", "pw"))
    try:
        _store(tmp_path, "nouser", "pw")
        out = m.connect(transport="telnet", host="127.0.0.1", tcp_port=srv.port,
                        credential="nouser")
        assert "pass username=" in out, out
        m.disconnect(all_connections=True)
        out = m.connect(transport="telnet", host="127.0.0.1", tcp_port=srv.port,
                        credential="nouser", username="admin")
        assert "logged in" in out, out
    finally:
        srv.close()


def test_unknown_credential_is_explained(telnet):
    out = m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                    credential="nope")
    assert "No stored credential named 'nope'" in out
    assert "credential set nope" in out and "Never ask for the password" in out
    out = m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet.port,
                    credential="hunter2 is my password")
    assert "not a credential name" in out


def test_expect_send_credential_is_hidden_from_capture(tmp_path):
    srv = FakeTelnetServer(login=("root", "Jun0s!"))
    try:
        _store(tmp_path, "ex2200", "Jun0s!")
        _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=srv.port, name="sw"))
        m.capture_start("login.txt", format="annotated")
        out = _expect([{"expect": "login: "},
                        {"send": "root", "expect": "Password: "},
                        {"send_credential": "ex2200", "expect": "switch> "}])
        assert "TIMEOUT" not in out and "sent credential 'ex2200' (hidden)" in out, out
        assert "Jun0s!" not in out
        m.capture_stop()
        cap = (tmp_path / "captures" / "login.txt").read_text()
        assert "Jun0s!" not in cap and "<credential hidden>" in cap
        assert srv.login_attempts == [(b"root", b"Jun0s!")]
    finally:
        srv.close()


def test_send_credential_allowed_in_read_only(monkeypatch, tmp_path):
    srv = FakeTelnetServer(login=("root", "pw"))
    try:
        monkeypatch.setattr(m, "READ_ONLY", True)
        _store(tmp_path, "ro", "pw")
        _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=srv.port))
        out = _expect([{"expect": "login: "}, {"send": "root", "expect": "Password: "}])
        assert "Read-only" in out  # "root" is not a read-style command
        out = _expect([{"send_credential": "ro"}])
        assert "sent credential" in out
    finally:
        srv.close()


# ----------------------------------------------------------------------------
# Raw TCP
# ----------------------------------------------------------------------------

def test_tcp_raw_bytes_and_no_lines():
    srv = FakeTcpServer()
    try:
        out = _ok(m.connect(f"tcp://127.0.0.1:{srv.port}", prompt="> ", name="raw"))
        assert "raw TCP to 127.0.0.1" in out and "Bytes only" in out
        assert "ser2net" in m.read_until_prompt()
        m.send_hex("FF FE 0D")
        assert wait_for(lambda: bytes(srv.received) == b"\xff\xfe\r")  # untouched
        assert "ok" in m.read_until_prompt()
        assert "not available over tcp" in m.send_break()
        assert "not available over tcp" in m.set_lines(rts=True)
        assert "bytes only" in m.status()
        srv.drop_clients()
        assert wait_for(lambda: not m._conns["raw"].reader_alive)
        assert "has stopped" in m.read_available()
    finally:
        srv.close()


# ----------------------------------------------------------------------------
# RFC 2217
# ----------------------------------------------------------------------------

def test_rfc2217_baud_lines_break_and_data():
    srv = FakeRfc2217Server()
    try:
        out = _ok(m.connect(f"rfc2217://127.0.0.1:{srv.port}", baud=19200, parity="E",
                            name="r"))
        assert "RFC 2217 to 127.0.0.1" in out and "19200 baud (8E1" in out
        assert wait_for(lambda: srv.serial.baudrate == 19200)
        assert srv.serial.parity == "E"
        m.send_text("hello", line_ending="CR")
        assert "hello" in m.read_available()  # loopback port echoes
        assert "DTR=0" in m.set_lines(dtr=False)
        assert wait_for(lambda: False in srv.serial.dtr_log)
        out = m.pulse_line("RTS", 50, level=False)
        assert "Pulsed RTS low" in out
        assert wait_for(lambda: srv.serial.rts_log[-2:] == [False, True])
        assert "Sent BREAK for 100 ms" in m.send_break(100)
        assert wait_for(lambda: srv.serial.break_log[-2:] == [True, False])
        st = m.status()
        assert "CTS=" in st and "DTR=0" in st
        saved = json.loads((m._state_dir() / "last_connection.json").read_text())
        assert saved["connections"]["r"]["baud"] == 19200  # framing kept for rfc2217
    finally:
        srv.close()


def test_rfc2217_against_plain_telnet_server_is_explained(telnet):
    out = m.connect(transport="rfc2217", host="127.0.0.1", tcp_port=telnet.port)
    assert not out.startswith("Connected"), out
    assert "RFC 2217" in out or "rfc2217" in out.lower()


# ----------------------------------------------------------------------------
# SSH
# ----------------------------------------------------------------------------

@pytest.fixture
def sshd(tmp_path, monkeypatch):
    if not HAVE_SSH:
        pytest.skip("asyncssh not installed")
    from .netfakes import FakeSSHServer

    srv = FakeSSHServer(tmp_path)
    kh = tmp_path / "known_hosts"
    kh.write_text(srv.known_hosts_line())
    cfg = tmp_path / "ssh_config"
    cfg.write_text(f"Host benchpi\n  HostName 127.0.0.1\n  Port {srv.port}\n  User tester\n"
                   f"  IdentityFile {srv.client_key_path}\n")
    monkeypatch.setattr(ssh_transport, "KNOWN_HOSTS", str(kh))
    monkeypatch.setattr(ssh_transport, "CONFIG_PATHS", [str(cfg)])
    monkeypatch.setattr(ssh_transport, "CLIENT_KEYS", [str(srv.client_key_path)])
    monkeypatch.setattr(ssh_transport, "AGENT_PATH", "")  # never the user's real agent
    srv.known_hosts = kh
    yield srv
    m.disconnect(all_connections=True)
    srv.close()


@needs_ssh
def test_ssh_key_login_shell(sshd):
    out = _ok(m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port,
                        username="tester", prompt="bench$ ", terminal="xterm", cols=120,
                        rows=40, name="pi"))
    assert "SSH to tester@127.0.0.1" in out and "send_break sends a" in out
    assert "Welcome to the bench" in m.read_until_prompt()
    out = m.query_text("uname -a")
    assert "Linux benchpi" in out, out
    assert sshd.terms[-1] == ("xterm", (120, 40))
    out = _expect([{"send": "echo hi", "expect": "bench$ "}])
    assert "ran echo hi" in out


@needs_ssh
def test_ssh_config_alias(sshd, monkeypatch):
    monkeypatch.setattr(ssh_transport, "CLIENT_KEYS", None)  # IdentityFile from the config
    monkeypatch.setenv("HOME", str(sshd.client_key_path.parent))  # no real ~/.ssh keys
    monkeypatch.setenv("USERPROFILE", str(sshd.client_key_path.parent))
    out = _ok(m.connect("ssh://benchpi", prompt="bench$ "))
    assert "benchpi" in out
    assert "Welcome" in m.read_until_prompt()
    assert sshd.auth_attempts[-1] == "tester"  # User from the config


@needs_ssh
def test_ssh_unknown_host_key_is_refused(sshd):
    sshd.known_hosts.write_text("")
    out = m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, username="tester")
    assert "could not be verified" in out and "never turned off" in out, out
    assert not m._conns and sshd.sessions == 0


@needs_ssh
def test_ssh_changed_host_key_is_refused(sshd):
    other = ssh_transport.asyncssh.generate_private_key("ssh-ed25519")
    sshd.known_hosts.write_text(sshd.known_hosts_line(other))
    out = m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, username="tester")
    assert "could not be verified" in out, out
    assert sshd.sessions == 0


@needs_ssh
def test_ssh_password_only_via_credential(sshd, monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_transport, "CLIENT_KEYS", [])
    out = m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, username="pwuser")
    assert "was refused" in out and "credential set" in out, out
    _store(tmp_path, "bench-ssh", "s3cret!", username="pwuser")
    out = _ok(m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port,
                        credential="bench-ssh", prompt="bench$ ", name="pw"))
    assert "s3cret!" not in out and "pwuser@" in out
    assert "Welcome" in m.read_until_prompt()
    saved = (tmp_path / "last_connection.json").read_text()
    assert "s3cret!" not in saved and '"credential": "bench-ssh"' in saved
    m.disconnect()
    _ok(m.reconnect_last("pw"))


@needs_ssh
def test_ssh_break_lines_and_hangup(sshd):
    _ok(m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, username="tester",
                  prompt="bench$ ", name="pi"))
    m.read_until_prompt()
    assert "SSH break request (300 ms)" in m.send_break(300)
    assert wait_for(lambda: sshd.breaks == [300])
    assert "[BREAK]" in m.read_until_prompt()
    assert "not available over ssh" in m.set_lines(dtr=True)
    assert "n/a (ssh: no modem lines; BREAK can be sent)" in m.status()
    m.send_text("exit")
    assert wait_for(lambda: not m._conns["pi"].reader_alive, 5)
    assert "ssh connection 'pi' has stopped" in m.send_text("x")


@needs_ssh
def test_ssh_missing_extra_is_explained(monkeypatch):
    monkeypatch.setattr(ssh_transport, "asyncssh", None)
    out = m.connect(transport="ssh", host="127.0.0.1", tcp_port=22)
    assert "pip install 'serial-console-mcp[ssh]'" in out


# ----------------------------------------------------------------------------
# Credentials store and CLI
# ----------------------------------------------------------------------------

def test_credentials_file_round_trip_and_perms(tmp_path):
    p = credentials.store_file("cs1", "pw1", "admin", tmp_path)
    if os.name != "nt":
        assert (p.stat().st_mode & 0o777) == 0o600
    c = credentials.lookup("cs1", tmp_path)
    assert (c.password, c.username) == ("pw1", "admin")
    assert "pw1" not in repr(c)
    assert credentials.names(tmp_path) == [("cs1", "admin")]
    if os.name != "nt":
        os.chmod(p, 0o644)
        with pytest.raises(credentials.CredentialError, match="chmod 600"):
            credentials.lookup("cs1", tmp_path)
        os.chmod(p, 0o600)
    assert credentials.remove_file("cs1", tmp_path)
    with pytest.raises(credentials.CredentialError, match="No stored credential"):
        credentials.lookup("cs1", tmp_path)


def test_credentials_env_path_and_keyring_fallback(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere.json"
    monkeypatch.setenv("SERIAL_CONSOLE_CREDENTIALS", str(other))
    credentials.store_file("x", "y", "", tmp_path)
    assert other.exists()
    monkeypatch.setattr(credentials, "_from_keyring", lambda n: "from-ring" if n == "kr" else None)
    assert credentials.lookup("kr", tmp_path).source == "keyring"


def test_credential_cli(tmp_path, monkeypatch, capsys):
    answers = iter(["topsecret", "topsecret"])
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt="": next(answers))
    assert credentials.cli(["set", "lab", "--username", "ops"], tmp_path) == 0
    assert credentials.lookup("lab", tmp_path).password == "topsecret"
    assert credentials.cli(["list"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "lab  (user ops)" in out and "topsecret" not in out
    answers = iter(["a", "b"])
    assert credentials.cli(["set", "lab2"], tmp_path) == 1  # mismatch: nothing stored
    assert credentials.cli(["set", "bad name"], tmp_path) == 2
    assert credentials.cli(["remove", "lab"], tmp_path) == 0
    assert credentials.cli([], tmp_path) == 2


def test_main_dispatches_credential_cli(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(credentials, "cli", lambda argv, d: seen.setdefault("argv", argv) and 0)
    with pytest.raises(SystemExit):
        m.main(["credential", "list"])
    assert seen["argv"] == ["list"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_credentials_file_written_private_even_with_open_umask(tmp_path):
    old = os.umask(0)
    try:
        p = credentials.store_file("u", "p", "", tmp_path)
    finally:
        os.umask(old)
    assert (p.stat().st_mode & 0o077) == 0
