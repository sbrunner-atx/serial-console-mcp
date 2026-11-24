# ruff: noqa: F811  (pytest fixtures imported from test_network)
"""Ad-hoc user name/password logins: environment-variable credentials and
connect(ask=True), where the user types into a dialog on their own screen.

The dialogs are replaced by a recorder; nothing here opens a real window."""
from __future__ import annotations

import json

import pytest

from serial_console_mcp import ask as ask_module
from serial_console_mcp import credentials
from serial_console_mcp import server as m

from .netfakes import FakeTelnetServer
from .test_network import HAVE_SSH, _ok, isolated, needs_ssh, sshd  # noqa: F401

if HAVE_SSH:
    from serial_console_mcp import ssh_transport


class FakeAsk:
    def __init__(self, password="", text="", confirm=False):
        self.answers = {"password": password, "text": text, "confirm": confirm}
        self.calls: list[tuple[str, str]] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(ask_module, "available", lambda: (True, "test"))
        monkeypatch.setattr(ask_module, "password", self._password)
        monkeypatch.setattr(ask_module, "text", self._text)
        monkeypatch.setattr(ask_module, "confirm", self._confirm)
        return self

    def _password(self, message):
        self.calls.append(("password", message))
        return self.answers["password"] or None

    def _text(self, message, default=""):
        self.calls.append(("text", message))
        return self.answers["text"] or None

    def _confirm(self, message, ok_label="Trust"):
        self.calls.append(("confirm", message))
        return self.answers["confirm"]

    def kinds(self):
        return [k for k, _ in self.calls]


@pytest.fixture
def telnet_login():
    srv = FakeTelnetServer(login=("admin", "typed-pw!"))
    yield srv
    srv.close()


# ----------------------------------------------------------------------------
# Environment variables
# ----------------------------------------------------------------------------

def test_env_names():
    assert credentials.env_names("bench-pi") == ("SERIAL_CONSOLE_PASSWORD_BENCH_PI",
                                                 "SERIAL_CONSOLE_USERNAME_BENCH_PI")
    assert credentials.env_names("mx1.lab@x")[0] == "SERIAL_CONSOLE_PASSWORD_MX1_LAB_X"


def test_env_credential_telnet_login(telnet_login, monkeypatch, tmp_path):
    monkeypatch.setenv("SERIAL_CONSOLE_PASSWORD_LAB_SW", "typed-pw!")
    monkeypatch.setenv("SERIAL_CONSOLE_USERNAME_LAB_SW", "admin")
    out = _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet_login.port,
                        credential="lab-sw", prompt="switch> "))
    assert "Console login: logged in" in out and "typed-pw!" not in out, out
    assert telnet_login.login_attempts == [(b"admin", b"typed-pw!")]
    saved = (tmp_path / "last_connection.json").read_text()
    assert "typed-pw!" not in saved and '"credential": "lab-sw"' in saved


def test_env_credential_wins_over_file(monkeypatch, tmp_path):
    credentials.store_file("x", "from-file", "", tmp_path)
    monkeypatch.setenv("SERIAL_CONSOLE_PASSWORD_X", "from-env")
    c = credentials.lookup("x", tmp_path)
    assert c.password == "from-env" and "environment" in c.source
    assert "from-env" not in repr(c)


def test_missing_credential_mentions_env_and_ask(tmp_path):
    with pytest.raises(credentials.CredentialError) as e:
        credentials.lookup("nope", tmp_path)
    assert "SERIAL_CONSOLE_PASSWORD_NOPE" in str(e.value) and "ask=True" in str(e.value)


# ----------------------------------------------------------------------------
# ask=True over telnet
# ----------------------------------------------------------------------------

def test_ask_telnet_login(telnet_login, monkeypatch, tmp_path):
    fake = FakeAsk(password="typed-pw!", text="admin").install(monkeypatch)
    out = _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet_login.port,
                        ask=True, prompt="switch> ", name="sw"))
    assert "Console login: logged in" in out, out
    assert "the password typed in the dialog (hidden)" in out
    assert "typed-pw!" not in out
    assert fake.kinds() == ["text", "password"]
    assert f"telnet to 127.0.0.1:{telnet_login.port}" in fake.calls[1][1]
    assert "admin on" in fake.calls[1][1] and "not shown to the assistant" in fake.calls[1][1]
    assert telnet_login.login_attempts == [(b"admin", b"typed-pw!")]
    assert "Welcome" in m.read_until_prompt()
    saved = json.loads((tmp_path / "last_connection.json").read_text())["connections"]["sw"]
    assert saved["ask"] is True and saved["username"] == "admin"
    assert "typed-pw!" not in json.dumps(saved)
    # reconnect_last asks for the password again, not the (remembered) user name
    m.disconnect()
    fake.calls.clear()
    out = _ok(m.reconnect_last("sw"))
    assert "logged in" in out and fake.kinds() == ["password"]


def test_ask_telnet_cancelled_sends_nothing(telnet_login, monkeypatch):
    FakeAsk(password="", text="admin").install(monkeypatch)
    out = m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet_login.port,
                    ask=True)
    assert "password dialog was canceled; nothing more was sent" in out, out
    assert telnet_login.login_attempts == []


def test_ask_stored_credential_first(telnet_login, monkeypatch, tmp_path):
    fake = FakeAsk(password="never-used").install(monkeypatch)
    credentials.store_file("sw", "typed-pw!", "admin", tmp_path)
    out = _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet_login.port,
                        credential="sw", ask=True))
    assert "logged in" in out and fake.calls == []


def test_ask_unavailable_is_explained(telnet_login, monkeypatch):
    monkeypatch.setattr(ask_module, "available", lambda: (False, "No dialog can be shown"))
    out = m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet_login.port,
                    ask=True)
    assert out == "No dialog can be shown" and not m._conns


# ----------------------------------------------------------------------------
# ask=True over SSH: password, user name, first-contact host key
# ----------------------------------------------------------------------------

@needs_ssh
def test_ask_ssh_password(sshd, monkeypatch):
    monkeypatch.setattr(ssh_transport, "CLIENT_KEYS", [])  # no keys: a password login
    fake = FakeAsk(password="s3cret!").install(monkeypatch)
    out = _ok(m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port,
                        username="pwuser", ask=True, prompt="bench$ "))
    assert "pwuser@" in out and "s3cret!" not in out, out
    assert fake.kinds() == ["password"]
    assert "Welcome" in m.read_until_prompt()


@needs_ssh
def test_ask_ssh_keys_first_no_dialog(sshd, monkeypatch):
    fake = FakeAsk(password="unused").install(monkeypatch)
    _ok(m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, username="tester",
                  ask=True))
    assert fake.calls == []


@needs_ssh
def test_ask_ssh_username_and_password(sshd, monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_transport, "CLIENT_KEYS", [])
    fake = FakeAsk(password="s3cret!", text="pwuser").install(monkeypatch)
    out = _ok(m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, ask=True,
                        name="adhoc"))
    assert fake.kinds() == ["text", "password"], fake.calls
    assert "pwuser@" in out and sshd.auth_attempts[-1] == "pwuser"
    saved = json.loads((tmp_path / "last_connection.json").read_text())["connections"]
    assert saved["adhoc"]["username"] == "pwuser"


@needs_ssh
def test_ask_ssh_wrong_password(sshd, monkeypatch):
    monkeypatch.setattr(ssh_transport, "CLIENT_KEYS", [])
    FakeAsk(password="nope").install(monkeypatch)
    out = m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port,
                    username="pwuser", ask=True)
    assert "refused with the password typed in the dialog" in out, out
    assert not m._conns


@needs_ssh
def test_ask_ssh_trust_new_host_key(sshd, monkeypatch):
    sshd.known_hosts.write_text("")
    fake = FakeAsk(confirm=True).install(monkeypatch)
    _ok(m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, username="tester",
                  ask=True))
    assert fake.kinds() == ["confirm"]
    msg = fake.calls[0][1]
    assert sshd.host_key.get_fingerprint() in msg and "ssh-ed25519" in msg
    line = sshd.known_hosts.read_text()
    assert line.startswith(f"[127.0.0.1]:{sshd.port} ssh-ed25519 ")
    # now trusted for good: no ask needed
    m.disconnect(all_connections=True)
    _ok(m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, username="tester"))


@needs_ssh
def test_ask_ssh_host_key_declined(sshd, monkeypatch):
    sshd.known_hosts.write_text("")
    FakeAsk(confirm=False).install(monkeypatch)
    out = m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, username="tester",
                    ask=True)
    assert "was not trusted" in out and sshd.host_key.get_fingerprint() in out, out
    assert sshd.known_hosts.read_text() == "" and sshd.sessions == 0


@needs_ssh
def test_ask_ssh_changed_key_is_never_offered(sshd, monkeypatch):
    other = ssh_transport.asyncssh.generate_private_key("ssh-ed25519")
    sshd.known_hosts.write_text(sshd.known_hosts_line(other))
    before = sshd.known_hosts.read_text()
    fake = FakeAsk(confirm=True).install(monkeypatch)
    out = m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, username="tester",
                    ask=True)
    assert "NOT the one on record" in out, out
    assert fake.calls == [] and sshd.known_hosts.read_text() == before
    assert sshd.sessions == 0


@needs_ssh
def test_ask_ssh_new_key_then_password(sshd, monkeypatch):
    sshd.known_hosts.write_text("")
    monkeypatch.setattr(ssh_transport, "CLIENT_KEYS", [])
    fake = FakeAsk(confirm=True, password="s3cret!").install(monkeypatch)
    _ok(m.connect(transport="ssh", host="127.0.0.1", tcp_port=sshd.port, username="pwuser",
                  ask=True))
    assert fake.kinds() == ["confirm", "password"]


# ----------------------------------------------------------------------------
# expect step ask_password (a device's own login, e.g. on a craft port)
# ----------------------------------------------------------------------------

def test_expect_ask_password(telnet_login, monkeypatch, tmp_path):
    fake = FakeAsk(password="typed-pw!").install(monkeypatch)
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet_login.port,
                  name="sw"))
    cap = tmp_path / "cap.txt"
    m.capture_start(str(cap), format="annotated")
    out = m.expect([m.ExpectStep(expect="login: "),
                    m.ExpectStep(send="admin", expect="Password: "),
                    m.ExpectStep(ask_password=True, expect="switch> ")])
    m.capture_stop()
    assert "sent the password typed in the dialog (hidden)" in out, out
    assert "Welcome" in out and "typed-pw!" not in out
    assert fake.kinds() == ["password"] and "'sw'" in fake.calls[0][1]
    assert telnet_login.login_attempts == [(b"admin", b"typed-pw!")]
    text = cap.read_text()
    assert "typed-pw!" not in text and "<credential hidden>" in text


def test_expect_ask_password_cancelled(telnet_login, monkeypatch):
    FakeAsk(password="").install(monkeypatch)
    _ok(m.connect(transport="telnet", host="127.0.0.1", tcp_port=telnet_login.port))
    out = m.expect([m.ExpectStep(expect="login: "),
                    m.ExpectStep(send="admin", expect="Password: "),
                    m.ExpectStep(ask_password=True, expect="switch> ")])
    assert "canceled; nothing sent, stopped" in out, out
    assert telnet_login.login_attempts == []
