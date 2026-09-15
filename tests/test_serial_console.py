"""Unit tests for the serial logic, using a fake serial.Serial.

No hardware needed. The fake has a scripted RX queue and a TX log, so we can
exercise the background reader, prompt matching / push-back, idle-terminated
reads, error paths, and the remembered-connection round trip.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serial_console_mcp as m  # noqa: E402
import configure_claude  # noqa: E402


class FakeSerial:
    """Stands in for serial.Serial: scripted RX bytes + a TX log."""

    def __init__(self, port, baudrate, bytesize, parity, stopbits, timeout,
                 write_timeout=None, rtscts=False, xonxoff=False):
        if baudrate <= 0:
            raise ValueError("Not a valid baudrate: %r" % baudrate)
        if port == "/dev/busy":
            raise serial.SerialException(
                "could not open port /dev/busy: [Errno 16] Resource busy")
        if port == "/dev/missing":
            raise serial.SerialException(
                "could not open port /dev/missing: [Errno 2] No such file or directory")
        self.port, self.baudrate = port, baudrate
        self.bytesize, self.parity, self.stopbits = bytesize, parity, stopbits
        self.timeout, self.write_timeout = timeout, write_timeout
        self.rtscts, self.xonxoff = rtscts, xonxoff
        self.is_open = True
        self.tx = bytearray()
        self._rx = bytearray()
        self._lock = threading.Lock()
        self.fail_reads = False

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._rx)

    def read(self, n):
        # Like a real port: block up to `timeout`, but return as soon as any byte
        # lands rather than always sleeping the full timeout. (A fake that slept
        # the whole 100 ms added latency a real adapter doesn't have and made the
        # settle-window tests racy on slow CI runners.)
        deadline = time.time() + self.timeout
        while True:
            with self._lock:
                if self._rx:
                    out = bytes(self._rx[:n])
                    del self._rx[:n]
                    return out
            if self.fail_reads:
                raise serial.SerialException("device reports readiness to read but returned no data")
            if time.time() >= deadline:
                return b""
            time.sleep(0.005)

    def write(self, b):
        self.tx.extend(b)
        return len(b)

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    def feed(self, b: bytes, delay: float = 0.0):
        def _go():
            if delay:
                time.sleep(delay)
            with self._lock:
                self._rx.extend(b)
        if delay:
            threading.Thread(target=_go, daemon=True).start()
        else:
            _go()


def _wait_reader_drained(port: FakeSerial, timeout=2.0):
    """Block until the reader thread has moved everything from the fake into the buffer."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port.in_waiting == 0:
            time.sleep(0.05)  # one more poll for the read() in flight
            return
        time.sleep(0.01)
    raise AssertionError("reader never drained the fake port")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(m.serial, "Serial", FakeSerial)
    monkeypatch.setattr(m, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(m, "_last_settings", None)
    yield
    m.disconnect()


def _connect(port="/dev/fake", **kw) -> FakeSerial:
    out = m.connect(port, **kw)
    assert out.startswith("Connected to"), out
    return m._port


# --- connect / status / disconnect -------------------------------------------------

def test_connect_status_disconnect():
    p = _connect(baud=115200)
    assert p.write_timeout == m._WRITE_TIMEOUT
    assert p.timeout == m._READER_POLL
    st = m.status()
    assert "Connected: /dev/fake at 115200" in st and "Reader running" in st
    assert m.disconnect() == "Disconnected from /dev/fake."
    assert not p.is_open
    assert m.status().startswith("Not connected. Last used: /dev/fake at 115200")
    assert m.disconnect() == "Nothing to disconnect."


def test_connect_defaults_are_9600_8n1_no_flow_control():
    p = _connect()
    assert (p.baudrate, p.bytesize, p.parity, p.stopbits) == (9600, 8, "N", 1)
    assert (p.rtscts, p.xonxoff) == (False, False)
    assert m.status().startswith("Connected: /dev/fake at 9600 baud, 8N1, flow control none.")


def test_connect_flow_control_and_line_settings():
    out = m.connect("/dev/fake", baud=38400, bytesize=7, parity="E", stopbits=2,
                    rtscts=False, xonxoff=True)
    assert out.startswith("Connected to /dev/fake at 38400 baud (7E2, flow control XON/XOFF)")
    p = m._port
    assert (p.baudrate, p.bytesize, p.parity, p.stopbits, p.rtscts, p.xonxoff) == \
        (38400, 7, "E", 2, False, True)
    assert "flow control XON/XOFF" in m.status()
    m.connect("/dev/fake", rtscts=True, xonxoff=True)
    assert "flow control RTS/CTS+XON/XOFF" in m.status()


def test_write_timeout_with_rtscts_gets_hint():
    p = _connect(rtscts=True)

    def boom(_b):
        raise serial.SerialTimeoutException("Write timeout")
    p.write = boom
    assert "never asserted CTS" in m.send_text("x")


def test_connect_replaces_existing_port():
    p1 = _connect("/dev/one")
    p2 = _connect("/dev/two")
    assert not p1.is_open and p2.is_open
    assert m._port is p2


def test_connect_invalid_baud_is_reported_not_raised():
    out = m.connect("/dev/fake", baud=0)
    assert out.startswith("Invalid serial settings")
    assert m._port is None


def test_connect_bad_stopbits():
    assert m.connect("/dev/fake", stopbits=3).startswith("stopbits must be")


def test_connect_busy_port_gets_hint():
    out = m.connect("/dev/busy")
    assert out.startswith("Could not open /dev/busy")
    assert "already in use" in out


def test_connect_missing_port_gets_hint():
    out = m.connect("/dev/missing")
    assert "list_serial_ports" in out


def test_tools_refuse_when_not_connected():
    for fn, args in [(m.send_text, ("x",)), (m.send_hex, ("00",)),
                     (m.read_until_prompt, ()), (m.read_available, ()),
                     (m.query_text, ("x",)), (m.clear_buffer, ())]:
        assert fn(*args) == "Not connected. Use connect first."


# --- sending -------------------------------------------------------------------------

@pytest.mark.parametrize("ending,suffix", [("CR", b"\r"), ("LF", b"\n"),
                                           ("CRLF", b"\r\n"), ("NONE", b"")])
def test_send_text_line_endings(ending, suffix):
    p = _connect()
    out = m.send_text("show version", line_ending=ending)
    assert out.startswith(f"Sent {12 + len(suffix)} bytes")
    assert bytes(p.tx) == b"show version" + suffix


def test_send_text_non_ascii_is_replaced():
    p = _connect()
    m.send_text("ü", line_ending="NONE")
    assert bytes(p.tx) == b"?"


def test_send_text_clear_buffer_first():
    p = _connect()
    p.feed(b"old noise")
    _wait_reader_drained(p)
    m.send_text("x", clear_buffer_first=True)
    assert m.read_available(0.1) == "Nothing received within the timeout."


def test_send_hex():
    p = _connect()
    out = m.send_hex("FE FE 94 E0 03 FD")
    assert out.startswith("Sent 6 bytes: fe fe 94 e0 03 fd")
    assert bytes(p.tx) == bytes.fromhex("FEFE94E003FD")
    assert m.send_hex("zz").startswith("That isn't valid hex")
    assert m.send_hex("").startswith("Nothing to send")


def test_write_failure_is_reported():
    p = _connect()

    def boom(_b):
        raise serial.SerialTimeoutException("Write timeout")
    p.write = boom
    assert m.send_text("x") == "Write failed: Write timeout."
    assert m.send_hex("00") == "Write failed: Write timeout."
    assert m.query_text("x") == "Write failed: Write timeout."


# --- reading -------------------------------------------------------------------------

def test_read_until_prompt_pushes_back_leftover():
    p = _connect()
    p.feed(b"show version\r\nJunos: 21.4R3\r\nuser@r1> ")
    p.feed(b"%unsolicited log line\r\n", delay=0.05)
    out = m.read_until_prompt("> ", timeout=2)
    assert out.startswith("Matched prompt '> ' after")
    assert out.endswith("user@r1> ")
    # Anything after the prompt stays buffered for the next read.
    later = m.read_available(1.0)
    assert "%unsolicited log line" in later


def test_read_until_prompt_regex():
    p = _connect()
    p.feed(b"banner\r\nrouter-1# ")
    out = m.read_until_prompt(r"[\w.-]+[#>] ?$", timeout=2, regex=True)
    assert out.startswith("Matched prompt")
    assert m.read_until_prompt("(", timeout=0.1, regex=True).startswith("Bad regex")


def test_read_until_prompt_timeout_returns_partial():
    p = _connect()
    p.feed(b"still printing...")
    out = m.read_until_prompt("# ", timeout=0.3)
    assert "not seen within 0.3s. Got 17 bytes" in out
    assert "still printing..." in out


def test_read_until_prompt_timeout_nothing():
    _connect()
    out = m.read_until_prompt("# ", timeout=0.2)
    assert "nothing was received" in out


def test_read_until_prompt_non_utf8_offsets_are_exact():
    p = _connect()
    p.feed(b"\xff\xfe\x80abc# tail")
    out = m.read_until_prompt("# ", timeout=2)
    assert out.startswith("Matched prompt '# ' after 8 bytes")
    assert "tail" in m.read_available(0.5)


def test_read_available_hex_and_settle(monkeypatch):
    # Widen the idle window so the "second chunk arrives during settle" case has a
    # large timing margin on slow CI runners; the logic under test is the same.
    monkeypatch.setattr(m, "_IDLE_SETTLE", 1.0)
    p = _connect()
    p.feed(b"\xfe\xfe\xe0\x94\x03", delay=0.05)
    p.feed(b"\x00\x50\x42\x14\x00\xfd", delay=0.4)  # well inside the 1.0 s settle window
    t0 = time.time()
    out = m.read_available(2.0)
    assert out.startswith("Received 11 bytes")
    assert "Hex: fe fe e0 94 03 00 50 42 14 00 fd" in out
    # Returned once the line went quiet: after the 2nd chunk + settle, not the full timeout.
    assert 1.3 < time.time() - t0 < 2.0


def test_query_text_idle_mode():
    p = _connect()
    p.feed(b"stale;")
    _wait_reader_drained(p)
    p.feed(b"ID0;", delay=0.1)
    out = m.query_text("ID", line_ending="NONE", read_timeout=2)
    assert bytes(p.tx) == b"ID"
    assert out.startswith("Reply (4 bytes): 'ID0;'")  # stale data was cleared first


def test_query_text_prompt_mode():
    p = _connect()
    p.feed(b"show clock\r\n12:00:00\r\nsw# ", delay=0.05)
    out = m.query_text("show clock", line_ending="LF", prompt="# ", read_timeout=2)
    assert bytes(p.tx) == b"show clock\n"
    assert out.startswith("Matched prompt '# '")


def test_clear_buffer_counts():
    p = _connect()
    p.feed(b"12345")
    _wait_reader_drained(p)
    assert m.clear_buffer() == "Cleared 5 buffered bytes."
    assert m.clear_buffer() == "Cleared 0 buffered bytes."


def test_rx_buffer_is_capped(monkeypatch):
    monkeypatch.setattr(m, "_RX_MAX", 16)
    p = _connect()
    p.feed(b"A" * 10)
    _wait_reader_drained(p)
    p.feed(b"B" * 10)
    _wait_reader_drained(p)
    assert "4 older bytes were discarded" in m.status()
    out = m.read_available(0.5)
    assert out.startswith("Received 16 bytes")
    assert "'AAAAAABBBBBBBBBB'" in out


def test_reader_death_is_surfaced():
    p = _connect()
    p.fail_reads = True
    deadline = time.time() + 2
    while m._reader_thread.is_alive() and time.time() < deadline:
        time.sleep(0.02)
    assert not m._reader_thread.is_alive()
    assert "Reader STOPPED" in m.status()
    assert "background reader has stopped" in m.send_text("x")
    assert "background reader has stopped" in m.read_available(0.1)
    assert m.disconnect().startswith("Disconnected from /dev/fake")


def test_disconnect_survives_close_error():
    p = _connect()

    def boom():
        raise OSError("already gone")
    p.close = boom
    out = m.disconnect()
    assert out.startswith("Disconnected from /dev/fake (close reported")
    assert m._port is None


# --- remembered connection -------------------------------------------------------------

def test_reconnect_last_round_trip(tmp_path):
    _connect("/dev/fake", baud=38400, parity="E", stopbits=2)
    m.disconnect()
    saved = json.loads((tmp_path / "last_connection.json").read_text())
    assert saved["port"] == "/dev/fake" and saved["baud"] == 38400
    m._last_settings = None  # simulate a fresh process
    out = m.reconnect_last()
    assert out.startswith("Connected to /dev/fake at 38400 baud (8E2, flow control none)")


def test_reconnect_last_ignores_unknown_keys(tmp_path):
    (tmp_path / "last_connection.json").write_text(json.dumps(
        {"port": "/dev/fake", "baud": 4800, "future_option": True}))
    assert m.reconnect_last().startswith("Connected to /dev/fake at 4800")


def test_reconnect_last_nothing_on_record():
    assert m.reconnect_last().startswith("No previous connection on record")


def test_reconnect_last_corrupt_file(tmp_path):
    (tmp_path / "last_connection.json").write_text("{not json")
    assert m.reconnect_last().startswith("No previous connection on record")


# --- configure_claude ----------------------------------------------------------------------

def test_write_config_merges_and_backs_up(tmp_path):
    cfg = configure_claude.claude_config_path(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    out = configure_claude.write_config("/opt/scm", ["--flag"], config_home=tmp_path)
    assert out == cfg
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["other"] == {"command": "x"}  # untouched
    assert data["mcpServers"]["serial-console"] == {"command": "/opt/scm", "args": ["--flag"]}
    assert data["theme"] == "dark"
    assert list(cfg.parent.glob("claude_desktop_config.backup-*.json"))
    configure_claude.write_config("", [], remove=True, config_home=tmp_path)
    data = json.loads(cfg.read_text())
    assert "serial-console" not in data["mcpServers"] and "other" in data["mcpServers"]


def test_write_config_creates_missing_and_rescues_broken(tmp_path):
    cfg = configure_claude.claude_config_path(tmp_path)
    configure_claude.write_config("/opt/scm", [], config_home=tmp_path)
    assert json.loads(cfg.read_text())["mcpServers"]["serial-console"]["command"] == "/opt/scm"
    cfg.write_text("{ definitely not json")
    configure_claude.write_config("/opt/scm2", [], config_home=tmp_path)
    assert json.loads(cfg.read_text())["mcpServers"]["serial-console"]["command"] == "/opt/scm2"
    assert list(cfg.parent.glob("claude_desktop_config.broken-*.json"))


def test_cli_requires_command():
    with pytest.raises(SystemExit):
        configure_claude.main([])
