"""Unit tests for the serial logic, using a fake serial.Serial.

No hardware needed. The fake has a scripted RX queue, a TX log, control lines,
and a per-baud responder hook, so we can exercise the reader threads, prompt
matching / push-back, idle reads, presets, multiple connections, expect
sequences, control lines, capture, read-only mode, baud detection, error paths,
and the remembered-connection round trip.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
import serial

from serial_console_mcp import civ
from serial_console_mcp import configure as configure_claude
from serial_console_mcp import server as m


class FakeSerial:
    """Stands in for serial.Serial: scripted RX bytes + a TX log + control lines."""

    on_write = None  # optional hook: fn(fake, payload) -> bytes to feed back

    def __init__(self, port, baudrate=9600, bytesize=8, parity="N", stopbits=1, timeout=1.0,
                 write_timeout=None, rtscts=False, xonxoff=False):
        if baudrate <= 0:
            raise ValueError(f"Not a valid baudrate: {baudrate!r}")
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
        self.dtr, self.rts = True, True
        self.cts, self.dsr, self.cd, self.ri = True, False, False, False
        self.breaks: list[float] = []
        self.line_log: list[tuple[str, bool]] = []

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._rx)

    def read(self, n):
        # Like a real port: block up to `timeout`, but return as soon as any byte
        # lands rather than always sleeping the full timeout.
        deadline = time.time() + self.timeout
        while True:
            with self._lock:
                if self._rx:
                    out = bytes(self._rx[:n])
                    del self._rx[:n]
                    return out
            if self.fail_reads:
                raise serial.SerialException(
                    "device reports readiness to read but returned no data")
            if time.time() >= deadline:
                return b""
            time.sleep(0.005)

    def write(self, b):
        self.tx.extend(b)
        if FakeSerial.on_write is not None:
            reply = FakeSerial.on_write(self, bytes(b))
            if reply:
                self.feed(reply)
        return len(b)

    def flush(self):
        pass

    def reset_input_buffer(self):
        with self._lock:
            self._rx.clear()

    def send_break(self, duration=0.25):
        self.breaks.append(duration)

    def close(self):
        self.is_open = False

    def __setattr__(self, k, v):
        if k in ("dtr", "rts") and "line_log" in self.__dict__:
            self.line_log.append((k, v))
        object.__setattr__(self, k, v)

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
            time.sleep(0.05)
            return
        time.sleep(0.01)
    raise AssertionError("reader never drained the fake port")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(m.serial, "Serial", FakeSerial)
    monkeypatch.setattr(m, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(m, "_conns", {})
    monkeypatch.setattr(m, "_default_name", None)
    monkeypatch.setattr(m, "_idle_notes", {})
    monkeypatch.setattr(m, "READ_ONLY", False)
    monkeypatch.setattr(m, "IDLE_MINUTES", 0.0)
    FakeSerial.on_write = None
    yield
    m.disconnect(all_connections=True)


def _connect(port="/dev/cu.fake", **kw) -> FakeSerial:
    out = m.connect(port, **kw)
    assert out.startswith("Connected"), out
    return m._conns[m._default_name].port


# --- connect / status / disconnect -------------------------------------------------

def test_connect_defaults_are_9600_8n1_no_flow_control():
    p = _connect()
    assert (p.baudrate, p.bytesize, p.parity, p.stopbits) == (9600, 8, "N", 1)
    assert (p.rtscts, p.xonxoff) == (False, False)
    assert p.write_timeout == m._WRITE_TIMEOUT and p.timeout == m._READER_POLL
    assert m._default_name == "fake"  # derived from /dev/cu.fake
    st = m.status()
    assert "'fake' (current): /dev/cu.fake at 9600 baud (8N1, flow control none)" in st
    assert "Reader running" in st and "CTS=1" in st and "DTR=1" in st


def test_connect_explicit_settings_and_name():
    out = m.connect("/dev/cu.fake", name="rig", baud=38400, bytesize=7, parity="E",
                    stopbits=2, xonxoff=True, line_ending="LF", prompt="> ")
    assert "Connected 'rig': /dev/cu.fake at 38400 baud (7E2, flow control XON/XOFF)" in out
    assert "Line ending LF, prompt '> '" in out
    p = m._conns["rig"].port
    assert (p.baudrate, p.bytesize, p.parity, p.stopbits, p.xonxoff) == (38400, 7, "E", 2, True)


def test_connect_preset_and_override():
    out = m.connect("/dev/cu.fake", preset="kenwood-cat")
    assert "9600 baud (8N1" in out and "Line ending NONE, prompt ';'" in out
    assert "Preset kenwood-cat:" in out
    m.connect("/dev/cu.fake", preset="icom-civ", baud=9600)
    assert m._conns["fake"].port.baudrate == 9600  # explicit beats preset (19200)
    assert m.connect("/dev/cu.fake", preset="nope").startswith("Unknown preset 'nope'")


def test_connect_bad_values():
    assert m.connect("/dev/cu.fake", stopbits=3).startswith("stopbits must be")
    assert m.connect("/dev/cu.fake", baud=0).startswith("Invalid serial settings")
    assert m.connect("/dev/cu.fake", prompt="(", prompt_regex=True).startswith("Bad prompt regex")
    assert m._conns == {}


def test_connect_hints_missing_and_busy(monkeypatch):
    out = m.connect("/dev/missing")
    assert "list_serial_ports" in out and "already in use" not in out
    monkeypatch.setattr(m, "_port_holder", lambda port: "screen (pid 4242)")
    out = m.connect("/dev/busy")
    assert "already in use" in out and "held by screen (pid 4242)" in out


def test_same_port_reconnect_replaces_and_name_conflict_refused():
    p1 = _connect("/dev/cu.one", name="rig")
    p2 = _connect("/dev/cu.one", name="rig", baud=19200)
    assert not p1.is_open and p2.is_open and list(m._conns) == ["rig"]
    out = m.connect("/dev/cu.two", name="rig")
    assert out.startswith("A connection named 'rig' is already open on /dev/cu.one")


def test_multiple_connections_and_default_switching():
    rig = _connect("/dev/cu.rig", name="rig")
    rot = _connect("/dev/cu.rot", name="rotator")
    assert m._default_name == "rotator"
    m.send_text("C", connection="rig")
    m.send_text("M180")  # default = rotator
    assert bytes(rig.tx) == b"C\r" and bytes(rot.tx) == b"M180\r"
    assert "Other open connections: rig" in m.status() or "'rig':" in m.status()
    assert m.send_text("x", connection="amp").startswith("No open connection named 'amp'")
    out = m.disconnect("rotator")
    assert out.startswith("Disconnected 'rotator'") and "Now current: 'rig'" in out
    assert m._default_name == "rig"
    assert m.disconnect(all_connections=True) == "Disconnected: rig."
    assert m.disconnect() == "Nothing to disconnect."
    assert m.status().startswith("Not connected. Remembered: rig (/dev/cu.rig at 9600), rotator")


def test_tools_refuse_when_not_connected():
    for fn, args in [(m.send_text, ("x",)), (m.send_hex, ("00",)),
                     (m.read_until_prompt, ()), (m.read_available, ()),
                     (m.query_text, ("x",)), (m.clear_buffer, ()), (m.set_lines, (True,)),
                     (m.pulse_line, ("DTR",)), (m.send_break, ()), (m.capture_start, ()),
                     (m.get_transcript, ()), (m.expect, ([m.ExpectStep(send="x")],))]:
        assert fn(*args) == "Not connected. Use connect first.", fn.__name__


def test_list_serial_ports_marks_open(monkeypatch):
    class P:
        device, description, hwid = "/dev/cu.fake", "CP2102N", "USB VID:PID=10C4:EA60"
    monkeypatch.setattr(m.serial.tools.list_ports, "comports", lambda: [P()])
    _connect()
    out = m.list_serial_ports()
    assert "/dev/cu.fake  —  CP2102N" in out and "[open here as 'fake']" in out


def test_list_presets_mentions_every_preset():
    out = m.list_presets()
    for name in m.PRESETS:
        assert name in out


# --- sending -------------------------------------------------------------------------

@pytest.mark.parametrize("ending,suffix", [("CR", b"\r"), ("LF", b"\n"),
                                           ("CRLF", b"\r\n"), ("NONE", b"")])
def test_send_text_line_endings(ending, suffix):
    p = _connect()
    out = m.send_text("show version", line_ending=ending)
    assert out.startswith(f"Sent {12 + len(suffix)} bytes to 'fake'")
    assert bytes(p.tx) == b"show version" + suffix


def test_send_text_uses_connection_line_ending():
    p = _connect(line_ending="LF")
    m.send_text("a")
    m.send_text("b", line_ending="NONE")
    assert bytes(p.tx) == b"a\nb"


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
    assert out.startswith("Sent 6 bytes to 'fake': fe fe 94 e0 03 fd")
    assert bytes(p.tx) == bytes.fromhex("FEFE94E003FD")
    assert m.send_hex("zz").startswith("That isn't valid hex")
    assert m.send_hex("").startswith("Nothing to send")


def test_write_failure_is_reported_with_rtscts_hint():
    p = _connect(rtscts=True)

    def boom(_b):
        raise serial.SerialTimeoutException("Write timeout")
    p.write = boom
    out = m.send_text("x")
    assert out.startswith("Write failed: Write timeout.") and "never asserted CTS" in out
    assert m.send_hex("00").startswith("Write failed")
    assert m.query_text("x").startswith("Write failed")


# --- reading -------------------------------------------------------------------------

def test_read_until_prompt_pushes_back_leftover():
    p = _connect()
    p.feed(b"show version\r\nJunos: 21.4R3\r\nuser@r1> ")
    p.feed(b"%unsolicited log line\r\n", delay=0.05)
    out = m.read_until_prompt("> ", timeout=2)
    assert out.startswith("Matched prompt '> ' after")
    assert out.endswith("user@r1> ")
    later = m.read_available(1.0)
    assert "%unsolicited log line" in later


def test_read_until_prompt_uses_connection_prompt_then_fallback():
    p = _connect(prompt="Password:")
    p.feed(b"admin\r\nPassword:")
    assert m.read_until_prompt(timeout=2).startswith("Matched prompt 'Password:'")
    m.disconnect()
    p = _connect()  # no prompt anywhere -> fallback "ends with a prompt char"
    p.feed(b"banner\r\nrouter-1# ")
    out = m.read_until_prompt(timeout=2)
    assert out.startswith(f"Matched prompt {m._FALLBACK_PROMPT!r}")


def test_read_until_prompt_regex_and_bad_regex():
    p = _connect()
    p.feed(b"banner\r\nrouter-1# ")
    out = m.read_until_prompt(r"[\w.-]+[#>] ?$", timeout=2, regex=True)
    assert out.startswith("Matched prompt")
    assert m.read_until_prompt("(", timeout=0.1, regex=True).startswith("Bad regex")


def test_read_until_prompt_auto_reply_pages_through_more():
    p = _connect()

    def pager(fake, payload):
        return b"page 2\r\nsw# " if payload == b" " else b""
    FakeSerial.on_write = pager
    p.feed(b"page 1\r\n--More--")
    out = m.read_until_prompt("# ", timeout=2, auto_reply={"--More--": " "})
    assert out.startswith("Matched prompt '# ' after") and "(auto-replied to --More--)" in out
    assert "page 1" in out and "page 2" in out
    assert bytes(p.tx) == b" "


def test_read_until_prompt_timeout_returns_partial_or_nothing():
    p = _connect()
    p.feed(b"still printing...")
    out = m.read_until_prompt("# ", timeout=0.3)
    assert "not seen within 0.3s. Got 17 bytes" in out and "still printing..." in out
    assert "nothing was received" in m.read_until_prompt("# ", timeout=0.2)


def test_read_until_prompt_non_utf8_offsets_are_exact():
    p = _connect()
    p.feed(b"\xff\xfe\x80abc# tail")
    assert m.read_until_prompt("# ", timeout=2).startswith("Matched prompt '# ' after 8 bytes")
    assert "tail" in m.read_available(0.5)


def test_read_available_hex_and_settle(monkeypatch):
    monkeypatch.setattr(m, "_IDLE_SETTLE", 1.0)
    p = _connect()
    p.feed(b"\xfe\xfe\xe0\x94\x03", delay=0.05)
    p.feed(b"\x00\x50\x42\x14\x00\xfd", delay=0.4)
    t0 = time.time()
    out = m.read_available(2.0)
    assert out.startswith("Received 11 bytes from 'fake'")
    assert "Hex: fe fe e0 94 03 00 50 42 14 00 fd" in out
    assert 1.3 < time.time() - t0 < 2.0


def test_query_text_idle_mode_and_connection_prompt():
    p = _connect(line_ending="NONE")
    p.feed(b"stale;")
    _wait_reader_drained(p)
    p.feed(b"ID0;", delay=0.1)
    out = m.query_text("ID", read_timeout=2)
    assert bytes(p.tx) == b"ID" and out.startswith("Reply (4 bytes): 'ID0;'")
    m.disconnect()
    p = _connect(preset="kenwood-cat")
    p.feed(b"FA00014074000;", delay=0.05)
    out = m.query_text("FA", read_timeout=2)
    assert bytes(p.tx) == b"FA" and out.startswith("Matched prompt ';'")
    p.feed(b"x", delay=0.05)
    # prompt="" forces an idle-terminated read even though the preset has a prompt
    assert m.query_text("MD", prompt="", read_timeout=0.5).startswith("Reply (1 bytes)")


def test_query_text_prompt_mode():
    p = _connect()
    p.feed(b"show clock\r\n12:00:00\r\nsw# ", delay=0.05)
    out = m.query_text("show clock", line_ending="LF", prompt="# ", read_timeout=2)
    assert bytes(p.tx) == b"show clock\n" and out.startswith("Matched prompt '# '")


def test_expect_login_flow_and_timeout():
    p = _connect()

    def device(fake, payload):
        return {b"\r": b"\r\nlogin: ", b"admin\r": b"admin\r\nPassword:",
                b"secret\r": b"\r\nadmin@r1> ",
                b"show version\r": b"show version\r\nJunos 21.4\r\nadmin@r1> "}.get(payload, b"")
    FakeSerial.on_write = device
    out = m.expect([
        m.ExpectStep(send="", expect="login: "),
        m.ExpectStep(send="admin", expect="Password:"),
        m.ExpectStep(send="secret", expect="> "),
        m.ExpectStep(send="show version", expect="> ", clear_first=True),
    ])
    assert "Step 1: sent ''+CR; matched 'login: '" in out
    assert "Step 4: sent 'show version'+CR; matched '> '" in out and "Junos 21.4" in out
    out = m.expect([m.ExpectStep(send="nothing", expect="never", timeout=0.2),
                    m.ExpectStep(send="after")])
    assert "TIMEOUT: 'never' not seen in 0.2s" in out and "Stopped after step 1 of 2." in out
    assert b"after" not in bytes(p.tx)
    assert m.expect([]) == "No steps given."
    out = m.expect([m.ExpectStep(send_hex="FE FD"), m.ExpectStep(expect="x", timeout=0.1)],
                   stop_on_timeout=False)
    assert "sent hex fe fd; no wait" in out and "Step 2: TIMEOUT" in out


def test_clear_buffer_counts():
    p = _connect()
    p.feed(b"12345")
    _wait_reader_drained(p)
    assert m.clear_buffer() == "Cleared 5 buffered bytes on 'fake'."
    assert m.clear_buffer() == "Cleared 0 buffered bytes on 'fake'."


def test_rx_buffer_is_capped(monkeypatch):
    monkeypatch.setattr(m, "_RX_MAX", 16)
    p = _connect()
    p.feed(b"A" * 10)
    _wait_reader_drained(p)
    p.feed(b"B" * 10)
    _wait_reader_drained(p)
    assert "4 older bytes discarded" in m.status()
    assert "'AAAAAABBBBBBBBBB'" in m.read_available(0.5)


def test_reader_death_is_surfaced():
    p = _connect()
    p.fail_reads = True
    conn = m._conns["fake"]
    deadline = time.time() + 2
    while conn.reader.is_alive() and time.time() < deadline:
        time.sleep(0.02)
    assert not conn.reader.is_alive()
    assert "Reader STOPPED" in m.status()
    assert "background reader has stopped" in m.send_text("x")
    assert m.disconnect().startswith("Disconnected 'fake'")


def test_disconnect_survives_close_error():
    p = _connect()

    def boom():
        raise OSError("already gone")
    p.close = boom
    assert "(close reported: already gone)" in m.disconnect()
    assert m._conns == {}


# --- control lines -----------------------------------------------------------------------

def test_set_lines_pulse_and_break():
    p = _connect()
    assert m.set_lines() == "Nothing to do: give dtr and/or rts."
    assert m.set_lines(rts=False) == "Control lines on 'fake': DTR=1 RTS=0."
    assert p.rts is False and p.dtr is True
    t0 = time.time()
    out = m.pulse_line("DTR", ms=60, level=False)
    assert out.startswith("Pulsed DTR low for 60 ms") and "restored to 1" in out
    assert 0.05 < time.time() - t0 < 0.5 and p.dtr is True
    assert p.line_log[-2:] == [("dtr", False), ("dtr", True)]
    assert m.send_break(100) == "Sent BREAK for 100 ms on 'fake'."
    assert p.breaks == [0.1]


# --- capture and transcript ------------------------------------------------------------------

def test_capture_raw_and_annotated(tmp_path):
    p = _connect()
    out = m.capture_start()
    assert out.startswith("Capturing 'fake' (raw) to ") and str(tmp_path / "captures") in out
    assert m.capture_start().startswith("Already capturing")
    m.send_text("ID", line_ending="NONE")
    p.feed(b"ID021;")
    _wait_reader_drained(p)
    assert "Capturing to" in m.status()
    out = m.capture_stop()
    assert out.startswith("Capture stopped:") and "(6 bytes written)" in out
    raw = next((tmp_path / "captures").glob("fake-*.log")).read_bytes()
    assert raw == b"ID021;"  # RX only, echo-less CAT device
    out = m.capture_start(path="proto.txt", format="annotated")
    m.send_text("FA", line_ending="NONE")
    p.feed(b"FA00014074000;")
    _wait_reader_drained(p)
    m.capture_stop()
    text = (tmp_path / "captures" / "proto.txt").read_text()
    assert text.startswith("# serial-console-mcp capture of 'fake'")
    assert " TX] 'FA'" in text and " RX] 'FA00014074000;'" in text
    assert m.capture_stop() == "No capture running on 'fake'."


def test_capture_stops_on_disconnect(tmp_path):
    _connect()
    m.capture_start(path=str(tmp_path / "abs.log"))
    m.disconnect()
    assert (tmp_path / "abs.log").exists()


def test_transcript_survives_reads():
    p = _connect()
    assert m.get_transcript() == "Nothing received yet on 'fake'."
    p.feed(b"first line\r\nsw# ")
    m.read_until_prompt("# ", timeout=2)
    p.feed(b"second\r\n")
    _wait_reader_drained(p)
    out = m.get_transcript()
    assert "last 24 of 24 bytes" in out and "first line" in out and "second" in out
    assert m.get_transcript(last_bytes=6).endswith("cond\r\n")
    assert m.transcript_resource("fake").startswith("first line")
    assert m.transcript_resource("nope").startswith("No open connection")


# --- read-only mode ------------------------------------------------------------------------

def test_read_only_mode(monkeypatch):
    monkeypatch.setattr(m, "READ_ONLY", True)
    p = _connect()
    assert "READ-ONLY MODE" in m.status()
    assert m.send_text("show version").startswith("Sent")
    assert m.send_text("").startswith("Sent")
    assert m.send_text("configure terminal").startswith("Read-only mode")
    assert m.send_text("ID;", line_ending="NONE").startswith("Sent")
    assert m.send_text("TX;", line_ending="NONE").startswith("Read-only mode")
    assert m.query_text("reload").startswith("Read-only mode")
    assert m.send_hex("FE FE 94 E0 03 FD").startswith("Sent")
    assert m.send_hex("FE FE 94 E0 05 00 00 07 14 00 FD").startswith("Read-only mode")
    assert m.send_hex("00 01").startswith("Read-only mode")
    assert m.set_lines(rts=True).startswith("Read-only mode")
    assert m.pulse_line("RTS").startswith("Read-only mode")
    assert m.send_break().startswith("Read-only mode")
    out = m.expect([m.ExpectStep(send="write memory")])
    assert "Read-only mode" in out and "stopped" in out
    assert b"write" not in bytes(p.tx)


def test_read_only_custom_allowlist(monkeypatch):
    monkeypatch.setattr(m, "READ_ONLY", True)
    monkeypatch.setattr(m, "ALLOW_RE", m.re.compile(r"^C$"))
    _connect()
    assert m.send_text("C").startswith("Sent")
    assert m.send_text("show version").startswith("Read-only mode")


# --- diagnosis helpers ------------------------------------------------------------------------

def test_port_in_use_by(monkeypatch):
    monkeypatch.setattr(m.os, "name", "posix")
    monkeypatch.setattr(m, "_port_holder",
                        lambda port: "screen (pid 5)" if port == "/dev/cu.x" else None)
    assert m.port_in_use_by("/dev/cu.x") == "/dev/cu.x is held by screen (pid 5)."
    assert m.port_in_use_by("/dev/cu.y").startswith("No process is holding /dev/cu.y")
    _connect("/dev/cu.fake", name="rig")
    assert m.port_in_use_by("/dev/cu.fake") == \
        "/dev/cu.fake is open here, by this server (connection 'rig')."
    monkeypatch.setattr(m.os, "name", "nt")
    assert m.port_in_use_by("COM4").startswith("Windows doesn't report")


def test_port_holder_parses_lsof(monkeypatch):
    class R:
        stdout = "p4242\ncscreen\nn/dev/cu.x\np77\ncminicom\nn/dev/cu.x\n"
    monkeypatch.setattr(m.os, "name", "posix")
    monkeypatch.setattr(m.shutil, "which", lambda x: "/usr/sbin/lsof")
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: R())
    assert m._port_holder("/dev/cu.x") == "screen (pid 4242), minicom (pid 77)"


def test_detect_baud_ranks_clean_text():
    def device(fake, payload):
        if fake.baudrate == 38400:
            return b"\r\nrouter> "
        if fake.baudrate == 9600:
            return b"\xff\xfe\x80\x00\xf8"
        return b""
    FakeSerial.on_write = device
    out = m.detect_baud("/dev/cu.fake", candidates=[9600, 38400, 115200], settle=0.05)
    assert "Best guess: 38400 baud (clean text)" in out
    assert "  115200:    0 bytes" in out
    _connect()
    assert m.detect_baud("/dev/cu.fake").startswith("/dev/cu.fake is open here")
    m.disconnect()
    FakeSerial.on_write = lambda fake, payload: b""
    out = m.detect_baud("/dev/cu.fake", candidates=[9600], settle=0.05)
    assert "No rate produced any reply" in out


def test_civ_tools():
    out = m.civ_build("03")
    assert out.startswith("FE FE 94 E0 03 FD")
    out = m.civ_build("05", "00 00 07 14 00", rig="A4")
    assert out.startswith("FE FE A4 E0 05 00 00 07 14 00 FD")
    assert m.civ_build("zz").startswith("Could not build frame")
    out = m.civ_parse("fe fe 94 e0 03 fd fe fe e0 94 03 00 00 07 14 00 fd")
    assert "echo of our own command" in out and "14.070000 MHz" in out
    assert "→ OK" in m.civ_parse("FE FE E0 94 FB FD")
    assert "USB, filter 1" in m.civ_parse("FE FE E0 94 04 01 01 FD")
    assert "PTT on" in m.civ_parse("FE FE E0 94 1C 00 01 FD")
    assert m.civ_parse("zz").startswith("That isn't valid hex")
    assert m.civ_parse("00 11").startswith("No CI-V frames")
    assert m.civ_freq(mhz=14.07).startswith("14.070000 MHz = 00 00 07 14 00")
    assert m.civ_freq(bcd_hex="00 00 07 14 00") == "00 00 07 14 00 = 14.070000 MHz"
    assert m.civ_freq(bcd_hex="AA") .startswith("Not valid BCD")
    assert m.civ_freq() == "Give mhz or bcd_hex."
    bcd = bytes.fromhex("5041250700")
    assert civ.freq_to_bcd(civ.bcd_to_freq(bcd)) == bcd


# --- remembered connections -------------------------------------------------------------

def test_reconnect_last_round_trip_and_named(tmp_path):
    _connect("/dev/cu.rig", name="rig", baud=38400, parity="E", stopbits=2, preset="")
    _connect("/dev/cu.rot", name="rotator", preset="yaesu-rotator")
    m.disconnect(all_connections=True)
    saved = json.loads((tmp_path / "last_connection.json").read_text())
    assert saved["default"] == "rotator" and set(saved["connections"]) == {"rig", "rotator"}
    assert m.reconnect_last().startswith("Connected 'rotator': /dev/cu.rot at 9600")
    assert m.reconnect_last("rig").startswith("Connected 'rig': /dev/cu.rig at 38400 baud (8E2")
    assert m.reconnect_last("amp").startswith("No remembered connection named 'amp'")


def test_reconnect_last_reads_old_flat_format_and_ignores_unknown_keys(tmp_path):
    (tmp_path / "last_connection.json").write_text(json.dumps(
        {"port": "/dev/cu.fake", "baud": 4800, "timeout": 1.0, "future_option": True}))
    assert m.reconnect_last().startswith("Connected 'fake': /dev/cu.fake at 4800")


def test_reconnect_last_nothing_or_corrupt(tmp_path):
    assert m.reconnect_last().startswith("No previous connection on record")
    (tmp_path / "last_connection.json").write_text("{not json")
    assert m.reconnect_last().startswith("No previous connection on record")


# --- idle auto-close -------------------------------------------------------------------------

def test_idle_check_closes_and_notes(monkeypatch):
    monkeypatch.setattr(m, "IDLE_MINUTES", 15.0)
    _connect()
    assert m._idle_check() == []
    m._conns["fake"].last_used -= 16 * 60
    assert m._idle_check() == ["fake"]
    assert m._conns == {}
    assert "auto-closed after 16 min idle" in m.status()


# --- configure_claude ----------------------------------------------------------------------

def test_write_config_merges_and_backs_up(tmp_path):
    cfg = configure_claude.claude_config_path(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    out = configure_claude.write_config("/opt/scm", ["--flag"], config_home=tmp_path)
    assert out == cfg
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["other"] == {"command": "x"}
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
