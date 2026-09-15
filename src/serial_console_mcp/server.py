#!/usr/bin/env python3
"""
serial-console-mcp
==================
A Model Context Protocol (MCP) server that gives Claude Desktop (or any MCP
client) control of serial ports, so you can talk to a network device's
craft/console port (Juniper, Cisco, etc.), a CAT radio, an Icom CI-V rig, a
rotator, a microcontroller, or anything else on an RS-232 / USB-to-serial cable.

It is deliberately GENERIC: raw read/write plus ASCII and hex helpers, built
around a real interactive-console model (a background reader thread per port +
read-until-prompt), so it behaves the way `minicom`/`pexpect` do rather than a
naive send-then-poll. Device presets, control lines, expect sequences, capture
and CI-V helpers sit on top of that core.

This runs as a HOST subprocess (stdio transport), launched by an entry in
claude_desktop_config.json (console script `serial-console-mcp`, or the frozen
installer binary). It does NOT run inside Claude's sandbox, which is exactly why
it can see /dev/cu.* (macOS), COMx (Windows), or /dev/ttyUSB* (Linux).

Interactive-console model
-------------------------
A serial console is not request/response. It echoes what you type, emits
unsolicited output (syslog, interface flaps), and can print pages of output that a
fixed timeout would truncate. So every open port has a background thread draining
it into a software buffer the whole time it is open:

  * `send_text` / `send_hex`  -> ONLY write to TX. They do not read.
  * `read_until_prompt`       -> read from the buffer until a prompt (e.g. "# ",
                                 "> ", "login: ") appears, or a timeout elapses.
  * `read_available`          -> drain whatever has accumulated (unsolicited data).
  * `expect`                  -> several send/wait steps in one call.
  * `clear_buffer`            -> discard buffered RX (e.g. right before a command).

Several ports can be open at once (a rig, a rotator, an amplifier); each has a
name, and tools default to the most recently used one.

Environment
-----------
  SERIAL_CONSOLE_READ_ONLY=1      refuse writes except read-style commands
  SERIAL_CONSOLE_ALLOW=<regex>    override the read-only allowlist
  SERIAL_CONSOLE_IDLE_MINUTES=15  auto-close a port idle that long (0 = never)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.stderr.write(
        "pyserial is not installed. Run:  pip install pyserial\n"
    )
    raise

from mcp.server.fastmcp import FastMCP

from . import cat, civ, configure, rotator, terminal
from . import terminal as terminal_module
from .presets import PRESETS
from .presets import describe as describe_presets

mcp = FastMCP("serial-console")

# ----------------------------------------------------------------------------
# Tunables and tables
# ----------------------------------------------------------------------------

# How often the reader wakes to check the stop flag (also the port read timeout).
_READER_POLL = 0.1
# Never let a write hang a tool call (e.g. a stuck adapter with flow control on).
_WRITE_TIMEOUT = 2.0
# Cap on buffered-but-unread RX. A chatty device left open for hours would
# otherwise grow memory without bound; we keep the newest bytes and count the loss.
_RX_MAX = 4 * 1024 * 1024
# Rolling transcript of everything received, independent of the consumable buffer.
_TRANSCRIPT_MAX = 256 * 1024
# How long the device must stay silent for an idle-terminated read to finish.
_IDLE_SETTLE = 0.15
# Fallback prompt when neither the call nor the connection names one: the received
# text ends in a shell/CLI prompt character.
_FALLBACK_PROMPT = r"[#>$%] ?$"

_LINE_ENDINGS = {"CR": "\r", "CRLF": "\r\n", "LF": "\n", "NONE": ""}
_PARITY = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
_STOPBITS = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE,
             2: serial.STOPBITS_TWO}
_SETTINGS_KEYS = ("port", "name", "preset", "baud", "bytesize", "parity", "stopbits",
                  "rtscts", "xonxoff", "line_ending", "prompt", "prompt_regex",
                  "terminal", "cols", "rows")
_BAUD_CANDIDATES = [9600, 115200, 19200, 38400, 57600, 4800, 2400, 1200, 230400]

LineEnding = Literal["CR", "CRLF", "LF", "NONE"]
Parity = Literal["N", "E", "O"]
CaptureFormat = Literal["raw", "annotated"]
Terminal = Literal["dumb", "ansi", "vt100", "xterm"]
ControlLine = Literal["DTR", "RTS"]


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


READ_ONLY = _env_flag("SERIAL_CONSOLE_READ_ONLY")
# Commands a read-only session may send: bare return, help, show/display/get,
# paging control, and two-letter CAT reads such as ID; FA; MD; (but not TX;/RX;).
_DEFAULT_ALLOW = (
    r"^(?:|\?|help|show(?:\s.*)?|display(?:\s.*)?|dir|ls(?:\s.*)?|cat\s.*|ping\s.*|"
    r"traceroute\s.*|get(?:\s.*)?|exit|quit|logout|enable|terminal length 0|"
    r"set cli screen-length 0|(?!TX|RX)[A-Z]{2};?|C2?|B|H)$"
)
ALLOW_RE = re.compile(os.environ.get("SERIAL_CONSOLE_ALLOW") or _DEFAULT_ALLOW, re.IGNORECASE)
try:
    IDLE_MINUTES = float(os.environ.get("SERIAL_CONSOLE_IDLE_MINUTES", "0") or 0)
except ValueError:
    IDLE_MINUTES = 0.0


# ----------------------------------------------------------------------------
# Connection registry
# ----------------------------------------------------------------------------

@dataclass
class Connection:
    """One open port: its settings, its reader thread, and its buffers."""

    name: str
    port: object  # serial.Serial
    settings: dict
    line_ending: str = "CR"  # default for send_text/query_text on this connection
    prompt: str | None = None  # default prompt for read_until_prompt/query_text
    prompt_regex: bool = False
    preset: str = ""
    terminal: str = "dumb"  # dumb | ansi | vt100 | xterm
    cols: int = 80
    rows: int = 24
    stripper: object = None  # terminal.EscapeStripper when terminal != dumb
    screen: object = None  # terminal.Screen when terminal is vt100/xterm and pyte is present
    reader: threading.Thread | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    rx: bytearray = field(default_factory=bytearray)
    dropped: int = 0
    reader_error: str | None = None
    transcript: bytearray = field(default_factory=bytearray)
    capture_lock: threading.Lock = field(default_factory=threading.Lock)
    capture_file: object = None
    capture_path: str | None = None
    capture_format: str = "raw"
    capture_bytes: int = 0
    cap_rx: bytearray = field(default_factory=bytearray)  # annotated mode: RX awaiting a line
    cap_rx_at: float = 0.0
    opened_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    @property
    def is_open(self) -> bool:
        return self.port is not None and bool(self.port.is_open)

    @property
    def reader_alive(self) -> bool:
        return self.reader is not None and self.reader.is_alive()

    def describe_settings(self) -> str:
        s = self.settings
        flow = _flow_desc(bool(s.get("rtscts")), bool(s.get("xonxoff")))
        return (f"{s['port']} at {s['baud']} baud ({s['bytesize']}{s['parity']}{s['stopbits']}, "
                f"flow control {flow})")


_conns: dict[str, Connection] = {}
_default_name: str | None = None
_registry_lock = threading.Lock()
_idle_notes: dict[str, str] = {}  # name -> why it was auto-closed
_idle_thread: threading.Thread | None = None


def _flow_desc(rtscts: bool, xonxoff: bool) -> str:
    parts = [n for n, on in (("RTS/CTS", rtscts), ("XON/XOFF", xonxoff)) if on]
    return "+".join(parts) if parts else "none"


def _derive_name(port: str) -> str:
    base = port.replace("\\", "/").rsplit("/", 1)[-1]
    for prefix in ("cu.", "tty."):
        if base.startswith(prefix):
            base = base[len(prefix):]
    return base or port


def _resolve(connection: str = "", strict: bool = True) -> tuple[Connection | None, str | None]:
    """Find the connection a tool should act on; return (conn, None) or (None, error)."""
    if not _conns:
        return None, "Not connected. Use connect first."
    name = connection or _default_name
    conn = _conns.get(name) if name else None
    if conn is None:
        return None, (f"No open connection named {connection!r}. Open connections: "
                      f"{', '.join(_conns)}. Leave `connection` empty for the default "
                      f"({_default_name}).")
    if not strict:
        return conn, None
    if not conn.is_open:
        return None, f"Connection {conn.name!r} is closed. Run disconnect, then connect again."
    if not conn.reader_alive:
        why = f" ({conn.reader_error})" if conn.reader_error else ""
        return None, (
            f"The port for {conn.name!r} is open but its background reader has stopped{why}. "
            f"The device was probably unplugged or the port was taken by another program. "
            f"Run disconnect, then connect (or reconnect_last) again."
        )
    conn.last_used = time.time()
    return conn, None


# ----------------------------------------------------------------------------
# Remembered connections
# ----------------------------------------------------------------------------

def _state_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "serial-console-mcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _recall() -> dict:
    """Return {"default": name|None, "connections": {name: settings}} (empty if none)."""
    empty = {"default": None, "connections": {}}
    try:
        data = json.loads((_state_dir() / "last_connection.json").read_text())
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    if "port" in data:  # file written by 0.1.x: a single flat settings dict
        s = {k: data[k] for k in _SETTINGS_KEYS if k in data}
        s.setdefault("name", _derive_name(s["port"]))
        return {"default": s["name"], "connections": {s["name"]: s}}
    conns = data.get("connections") or {}
    clean = {}
    for name, s in conns.items():
        if isinstance(s, dict) and "port" in s:
            clean[name] = {k: s[k] for k in _SETTINGS_KEYS if k in s}
            clean[name]["name"] = name
    default = data.get("default") if data.get("default") in clean else (next(iter(clean), None))
    return {"default": default, "connections": clean}


def _remember(settings: dict) -> None:
    state = _recall()
    kept = {k: settings[k] for k in _SETTINGS_KEYS if k in settings}
    state["connections"][settings["name"]] = kept
    state["default"] = settings["name"]
    try:
        (_state_dir() / "last_connection.json").write_text(json.dumps(state, indent=2))
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Reader thread, buffers, capture
# ----------------------------------------------------------------------------

# Annotated captures coalesce received bytes into one line per burst: flushed at a
# newline, before the next TX, after this much silence, or at capture_stop.
_CAP_COALESCE = 0.2


def _cap_line(conn: Connection, direction: str, data: bytes) -> None:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts} {direction}] {data.decode('utf-8', 'backslashreplace')!r}\n"
    conn.capture_file.write(line.encode("utf-8"))
    conn.capture_bytes += len(data)


def _capture_flush_rx(conn: Connection, force: bool = False) -> None:
    """Write pending annotated RX. Caller holds capture_lock."""
    if not conn.cap_rx:
        return
    if force or time.time() - conn.cap_rx_at >= _CAP_COALESCE:
        _cap_line(conn, "RX", bytes(conn.cap_rx))
        conn.cap_rx.clear()
        return
    nl = conn.cap_rx.rfind(b"\n")
    if nl >= 0:
        _cap_line(conn, "RX", bytes(conn.cap_rx[:nl + 1]))
        del conn.cap_rx[:nl + 1]


def _capture_write(conn: Connection, direction: str, data: bytes) -> None:
    if conn.capture_file is None:
        return
    with conn.capture_lock:
        try:
            if conn.capture_format == "raw":
                if direction == "RX" and data:  # a terminal log: what came back, echo included
                    conn.capture_file.write(data)
                    conn.capture_bytes += len(data)
            elif direction == "TX":
                _capture_flush_rx(conn, force=True)
                if data:
                    _cap_line(conn, "TX", data)
            else:
                if data:
                    if not conn.cap_rx:
                        conn.cap_rx_at = time.time()
                    conn.cap_rx.extend(data)
                _capture_flush_rx(conn)
            conn.capture_file.flush()
        except Exception:
            pass


def _reader_loop(conn: Connection) -> None:
    """Continuously drain the OS serial buffer into `conn.rx` until stopped.

    Owning all reads in one place is what makes interactive consoles work: TX echo,
    unsolicited output, and large multi-page dumps all land in the buffer as they
    arrive, instead of being raced by ad-hoc per-command polling loops.
    """
    port, stop = conn.port, conn.stop
    while not stop.is_set():
        try:
            n = port.in_waiting
            data = port.read(n if n else 1)  # read(1) blocks up to the port timeout
        except (OSError, serial.SerialException) as e:
            if not stop.is_set():
                conn.reader_error = f"{type(e).__name__}: {e}"
            break
        if data:
            with conn.lock:
                if conn.screen is not None:
                    try:
                        conn.screen.feed(data)
                    except Exception:
                        pass
                text_bytes = conn.stripper.feed(data) if conn.stripper is not None else data
                conn.rx.extend(text_bytes)
                overflow = len(conn.rx) - _RX_MAX
                if overflow > 0:
                    del conn.rx[:overflow]
                    conn.dropped += overflow
                conn.transcript.extend(data)
                extra = len(conn.transcript) - _TRANSCRIPT_MAX
                if extra > 0:
                    del conn.transcript[:extra]
        if conn.capture_file is not None:
            _capture_write(conn, "RX", data)  # empty data still lets a quiet gap flush


def _start_reader(conn: Connection) -> None:
    conn.stop = threading.Event()
    conn.reader = threading.Thread(target=_reader_loop, args=(conn,), daemon=True,
                                   name=f"serial-console-reader-{conn.name}")
    conn.reader.start()


def _close_conn(conn: Connection) -> str | None:
    """Stop the reader, close capture and port. Returns close() error text if any."""
    conn.stop.set()
    if conn.reader is not None:
        conn.reader.join(timeout=1.0)
    if conn.capture_file is not None:
        _capture_stop(conn)
    err = None
    try:
        conn.port.close()
    except Exception as e:
        err = str(e)
    with conn.lock:
        conn.rx.clear()
    return err


def _unregister(conn: Connection) -> None:
    global _default_name
    with _registry_lock:
        _conns.pop(conn.name, None)
        if _default_name == conn.name:
            _default_name = next(reversed(_conns), None) if _conns else None


def _drain(conn: Connection) -> bytes:
    with conn.lock:
        data = bytes(conn.rx)
        conn.rx.clear()
    return data


def _pushback(conn: Connection, data: bytes) -> None:
    if data:
        with conn.lock:
            conn.rx[:0] = data


def _read_until_idle(conn: Connection, read_timeout: float, settle: float | None = None) -> bytes:
    """Wait up to `read_timeout` for the first bytes, then keep collecting until the
    device has been silent for `settle` seconds. Returns b"" if nothing arrived."""
    if settle is None:
        settle = _IDLE_SETTLE
    deadline = time.time() + read_timeout
    buf = bytearray()
    while time.time() < deadline and not buf:
        buf.extend(_drain(conn))
        if not buf:
            time.sleep(0.02)
    if buf:
        quiet_until = time.time() + settle
        while time.time() < quiet_until:
            more = _drain(conn)
            if more:
                buf.extend(more)
                quiet_until = time.time() + settle
            else:
                time.sleep(0.02)
    return bytes(buf)


def _read_until(conn: Connection, pat: re.Pattern, timeout: float,
                auto_reply: dict[str, str] | None = None) -> tuple[bool, bytes, list[str]]:
    """Collect until `pat` matches (leftover pushed back) or timeout.

    `auto_reply` maps literal trigger text to a reply sent the moment the trigger
    shows up (e.g. {"--More--": " "}); each occurrence is answered once.
    """
    deadline = time.time() + timeout
    collected = bytearray()
    handled = 0
    replies: list[str] = []
    while True:
        collected.extend(_drain(conn))
        text = collected.decode("latin-1")  # 1 char == 1 byte: exact offsets
        m = pat.search(text)
        if m:
            end = m.end()
            _pushback(conn, bytes(collected[end:]))
            return True, bytes(collected[:end]), replies
        if auto_reply:
            for trigger, reply in auto_reply.items():
                idx = text.find(trigger, handled)
                if idx >= 0:
                    handled = idx + len(trigger)
                    if (err := _guard_text(reply)) is None:
                        _write(conn, reply.encode("latin-1", "replace"))
                        replies.append(trigger)
                    else:
                        replies.append(f"{trigger} (reply refused: {err})")
                    break
        if time.time() >= deadline:
            return False, bytes(collected), replies
        time.sleep(0.02)


def _guard_text(data: str) -> str | None:
    if READ_ONLY and not ALLOW_RE.match(data.strip()):
        return (f"Read-only mode (SERIAL_CONSOLE_READ_ONLY) refused to send {data!r}. "
                f"Allowed commands match the SERIAL_CONSOLE_ALLOW pattern; unset "
                f"SERIAL_CONSOLE_READ_ONLY to send anything.")
    return None


def _guard_hex(payload: bytes) -> str | None:
    if not READ_ONLY:
        return None
    frames = civ.split_frames(payload)
    if frames and all(len(f) >= 5 and f[4] in civ.READ_COMMANDS for f in frames):
        return None
    return ("Read-only mode (SERIAL_CONSOLE_READ_ONLY) refused raw bytes. Only CI-V read "
            "frames (commands 02, 03, 04, 19) are allowed while read-only.")


def _guard_lines() -> str | None:
    if READ_ONLY:
        return ("Read-only mode (SERIAL_CONSOLE_READ_ONLY) refused to change control lines: "
                "DTR/RTS can key a transmitter or reset a device.")
    return None


def _write(conn: Connection, payload: bytes) -> str | None:
    """Write to the port; return an error message on failure, else None."""
    try:
        conn.port.write(payload)
        conn.port.flush()
    except Exception as e:
        hint = ""
        if isinstance(e, serial.SerialTimeoutException) and conn.settings.get("rtscts"):
            hint = (" RTS/CTS flow control is on and the device never asserted CTS; "
                    "check the cable wiring or reconnect with rtscts=False.")
        return f"Write failed: {e}.{hint}"
    _capture_write(conn, "TX", payload)
    return None


def _render(data: bytes, conn: Connection | None = None) -> str:
    """Human-readable decode for display: plain UTF-8 for a dumb terminal, or
    terminal-style line rendering (CR overwrite, backspace, erase) otherwise."""
    if conn is not None and conn.terminal != "dumb":
        return terminal.render(data)
    return data.decode("utf-8", "replace")


def _capture_stop(conn: Connection) -> str:
    with conn.capture_lock:
        if conn.capture_file is not None and conn.cap_rx:
            try:
                _capture_flush_rx(conn, force=True)
            except Exception:
                pass
        f, path, n = conn.capture_file, conn.capture_path, conn.capture_bytes
        conn.capture_file = None
        conn.capture_path = None
        conn.capture_bytes = 0
        if f is not None:
            try:
                f.close()
            except Exception:
                pass
    return f"Capture stopped: {path} ({n} bytes written)."


def _port_holder(port: str) -> str | None:
    """Name and pid of the process holding `port`, via lsof (macOS/Linux)."""
    if os.name == "nt":
        return None
    lsof = shutil.which("lsof")
    if not lsof:
        return None
    try:
        out = subprocess.run([lsof, "-F", "pcn", port], capture_output=True, text=True,
                             timeout=3).stdout
    except Exception:
        return None
    holders, pid, cmd = [], None, None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("c"):
            cmd = line[1:]
            if pid:
                holders.append(f"{cmd} (pid {pid})")
    return ", ".join(dict.fromkeys(holders)) or None


def _idle_check(now: float | None = None) -> list[str]:
    """Close connections idle longer than IDLE_MINUTES. Returns the names closed."""
    if IDLE_MINUTES <= 0:
        return []
    now = time.time() if now is None else now
    closed = []
    for conn in list(_conns.values()):
        idle = now - conn.last_used
        if idle > IDLE_MINUTES * 60:
            _close_conn(conn)
            _unregister(conn)
            _idle_notes[conn.name] = (f"auto-closed after {idle / 60:.0f} min idle "
                                      f"(SERIAL_CONSOLE_IDLE_MINUTES={IDLE_MINUTES:g})")
            closed.append(conn.name)
    return closed


def _idle_loop() -> None:
    while True:
        time.sleep(30)
        try:
            _idle_check()
        except Exception:
            pass


def _ensure_idle_thread() -> None:
    global _idle_thread
    if IDLE_MINUTES > 0 and (_idle_thread is None or not _idle_thread.is_alive()):
        _idle_thread = threading.Thread(target=_idle_loop, daemon=True, name="serial-console-idle")
        _idle_thread.start()


# ----------------------------------------------------------------------------
# Tools: discovery and connection
# ----------------------------------------------------------------------------

@mcp.tool()
def list_serial_ports() -> str:
    """List every serial port the computer can currently see.

    Call this FIRST whenever the user wants to connect to a device but hasn't given
    an exact port name, or when a connection fails. Returns each port's system name
    (what you pass to `connect`), a human description, and the USB hardware id, so
    you can guess which one is the user's gear. Ports already open here are marked.
    """
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return (
            "No serial ports found.\n"
            "Things to check: is the device powered on? Is the USB cable a real "
            "data cable (not charge-only)? On Windows, does it show up under "
            "Device Manager > Ports (COM & LPT)?"
        )
    open_ports = {c.settings["port"]: c.name for c in _conns.values()}
    lines = ["Serial ports found:"]
    for p in ports:
        desc = p.description or "(no description)"
        hwid = p.hwid or "(no hardware id)"
        mark = f"  [open here as {open_ports[p.device]!r}]" if p.device in open_ports else ""
        lines.append(f"  • {p.device}  —  {desc}  [{hwid}]{mark}")
    lines.append(
        "\nTip: on macOS prefer the /dev/cu.* name over /dev/tty.* for talking "
        "to a device. Not sure of the baud rate? Try detect_baud. Not sure which "
        "settings a device wants? See list_presets."
    )
    return "\n".join(lines)


@mcp.tool()
def list_presets() -> str:
    """Show the device presets `connect(preset=...)` understands, with their settings.

    A preset supplies baud, data bits, parity, stop bits, flow control, the line
    ending the device expects, and the prompt that ends a reply. Anything you pass
    explicitly to connect overrides the preset. Families: network consoles (Cisco,
    Juniper, Linux), text CAT radios (Kenwood, Elecraft, Yaesu), Icom CI-V,
    rotators, Arduino, NMEA GPS.
    """
    return describe_presets()


@mcp.tool()
def connect(
    port: str,
    preset: str = "",
    name: str = "",
    baud: int | None = None,
    bytesize: int | None = None,
    parity: Parity | None = None,
    stopbits: float | None = None,
    rtscts: bool | None = None,
    xonxoff: bool | None = None,
    line_ending: LineEnding | None = None,
    prompt: str | None = None,
    prompt_regex: bool | None = None,
    terminal: Terminal | None = None,
    cols: int | None = None,
    rows: int | None = None,
) -> str:
    """Open a serial port and start its background reader.

    Defaults are 9600 baud, 8 data bits, no parity, 1 stop bit, no flow control
    ("9600 8N1"), CR line ending, which is what most console/craft ports expect.
    Give `preset` (see list_presets) to load a device family's usual settings, and
    override any field explicitly, e.g. "38400 with XON/XOFF" -> baud=38400,
    xonxoff=True. Several ports can be open at once; each gets a `name` and later
    tools default to the most recently used one.

    Args:
        port: System port name, e.g. "COM4" (Windows), "/dev/cu.usbserial-10"
            (macOS), or "/dev/ttyUSB0" (Linux). Get exact names from
            `list_serial_ports`.
        preset: Device preset name such as "cisco-console", "juniper-craft",
            "kenwood-cat", "icom-civ", "yaesu-rotator", "arduino", "nmea-gps".
        name: Nickname for this connection ("rig", "rotator"). Defaults to the
            port's short name. Use it in other tools' `connection` argument.
        baud: Baud rate. Common values: 1200, 2400, 4800, 9600, 19200, 38400,
            57600, 115200. Unknown? Use detect_baud first.
        bytesize: Data bits: 5, 6, 7, or 8. Almost always 8.
        parity: "N" none, "E" even, "O" odd. Almost always "N".
        stopbits: 1, 1.5, or 2. Almost always 1.
        rtscts: Hardware (RTS/CTS) flow control. Off unless the manual says so
            and the cable carries those lines.
        xonxoff: Software (XON/XOFF) flow control. Off by default. Never for
            binary protocols (it swallows 0x11 / 0x13 bytes).
        line_ending: Default line ending for send_text/query_text on this
            connection: "CR" (most rigs, consoles), "LF" (Unix, Arduino), "CRLF",
            or "NONE" (Kenwood-style CAT ending in ';').
        prompt: Default prompt for read_until_prompt/query_text on this
            connection, e.g. "# ", "> ", ";" (CAT replies). Say what the device
            shows, with its trailing space.
        prompt_regex: Treat `prompt` as a regular expression.
        terminal: How to interpret what the device sends. "dumb" (default):
            raw bytes, right for CAT, CI-V, rotators and most CLIs. "ansi":
            strip colour/escape sequences and apply CR/backspace overwrites so
            shells and coloured prompts read cleanly (the console presets use
            it). "vt100"/"xterm": additionally keep a real screen for
            full-screen menus, BIOS/BMC consoles, vi/top; read it with the
            `screen` tool, navigate with `send_keys`.
        cols: Screen width for vt100/xterm (default 80).
        rows: Screen height for vt100/xterm (default 24).
    """
    global _default_name
    p = {}
    if preset:
        p = PRESETS.get(preset)
        if p is None:
            return f"Unknown preset {preset!r}. Known: {', '.join(PRESETS)}. See list_presets."

    def pick(explicit, key, default):
        if explicit is not None:
            return explicit
        return p.get(key, default) if p else default

    baud = pick(baud, "baud", 9600)
    bytesize = pick(bytesize, "bytesize", 8)
    parity = pick(parity, "parity", "N")
    stopbits = pick(stopbits, "stopbits", 1)
    rtscts = bool(pick(rtscts, "rtscts", False))
    xonxoff = bool(pick(xonxoff, "xonxoff", False))
    line_ending = pick(line_ending, "line_ending", "CR")
    prompt = pick(prompt, "prompt", None)
    prompt_regex = bool(pick(prompt_regex, "prompt_regex", False))
    term = pick(terminal, "terminal", "dumb")
    cols = int(pick(cols, "cols", 80))
    rows = int(pick(rows, "rows", 24))
    name = name or _derive_name(port)
    if term not in terminal_module.TERMINALS:
        return f"terminal must be one of {', '.join(terminal_module.TERMINALS)} (got {term!r})."
    if not (20 <= cols <= 500 and 5 <= rows <= 200):
        return "cols must be 20..500 and rows 5..200."

    if stopbits not in _STOPBITS:
        return f"stopbits must be 1, 1.5, or 2 (got {stopbits!r})."
    if parity not in _PARITY:
        return f"parity must be N, E, or O (got {parity!r})."
    if line_ending not in _LINE_ENDINGS:
        return f"line_ending must be CR, CRLF, LF, or NONE (got {line_ending!r})."
    if prompt and prompt_regex:
        try:
            re.compile(prompt)
        except re.error as e:
            return f"Bad prompt regex {prompt!r}: {e}"

    # Same port already open (under any name): replace it. Same name on another
    # port: refuse, so "rig" can't silently become the rotator.
    for existing in list(_conns.values()):
        if existing.settings["port"] == port:
            _close_conn(existing)
            _unregister(existing)
        elif existing.name == name:
            return (f"A connection named {name!r} is already open on "
                    f"{existing.settings['port']}. Pick another name, or disconnect it first.")

    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=bytesize,
            parity=_PARITY[parity],
            stopbits=_STOPBITS[stopbits],
            timeout=_READER_POLL,  # short, so the reader stays responsive to stop
            write_timeout=_WRITE_TIMEOUT,
            rtscts=rtscts,
            xonxoff=xonxoff,
        )
    except ValueError as e:
        # pyserial rejects impossible settings (baud 0, 9 data bits, ...) this way.
        return f"Invalid serial settings for {port}: {e}"
    except (serial.SerialException, OSError) as e:
        msg = str(e)
        hint = ""
        low = msg.lower()
        # Check "not found" first: pyserial phrases a missing device as "could not
        # open port X: [Errno 2] No such file or directory", which would otherwise
        # trip the "in use" hint below.
        if "filenotfound" in low or "no such" in low or "errno 2]" in low:
            hint = (
                "\nThat port name wasn't found. Run list_serial_ports to see the "
                "exact current names — they can change when you replug the cable."
            )
        elif "could not open" in low or "access" in low or "denied" in low or "busy" in low:
            holder = _port_holder(port)
            who = f" It is held by {holder}." if holder else ""
            hint = (
                f"\nThat port is probably already in use.{who} Close any logging or "
                "device-control software that might be holding it (a terminal, "
                "minicom/PuTTY, WSJT-X, a contest logger, the Arduino Serial "
                "Monitor) and try again. A serial port can only be open in one "
                "program at a time."
            )
        return f"Could not open {port}: {msg}{hint}"

    settings = {
        "port": port, "name": name, "preset": preset, "baud": baud, "bytesize": bytesize,
        "parity": parity, "stopbits": stopbits, "rtscts": rtscts, "xonxoff": xonxoff,
        "line_ending": line_ending, "prompt": prompt, "prompt_regex": prompt_regex,
        "terminal": term, "cols": cols, "rows": rows,
    }
    conn = Connection(name=name, port=ser, settings=settings, line_ending=line_ending,
                      prompt=prompt, prompt_regex=prompt_regex, preset=preset,
                      terminal=term, cols=cols, rows=rows)
    screen_note = ""
    if term != "dumb":
        conn.stripper = terminal_module.EscapeStripper()
    if term in ("vt100", "xterm"):
        if terminal_module.pyte is None:
            screen_note = (" The screen model needs the [screen] extra "
                           "(pip install 'serial-console-mcp[screen]'); running as ansi "
                           "(clean text, no screen) until then.")
            conn.terminal = "ansi"
            settings["terminal"] = "ansi"
        else:
            def _answer(b: bytes, _c=conn) -> None:
                try:
                    _c.port.write(b)
                except Exception:
                    pass
            conn.screen = terminal_module.Screen(cols, rows, answer=_answer)
    _start_reader(conn)
    with _registry_lock:
        _conns[name] = conn
        _default_name = name
    _idle_notes.pop(name, None)
    _remember(settings)
    _ensure_idle_thread()

    if prompt is None:
        pd = "none (reads until idle)"
    else:
        pd = f"{prompt!r}{' regex' if prompt_regex else ''}"
    note = f"\nPreset {preset}: {p['notes']}" if p else ""
    others = [n for n in _conns if n != name]
    multi = f"\nOther open connections: {', '.join(others)}." if others else ""
    ro = "\nREAD-ONLY MODE is on: only read-style commands will be sent." if READ_ONLY else ""
    tdesc = conn.terminal + (f" {cols}x{rows}" if conn.screen is not None else "")
    return (
        f"Connected {name!r}: {conn.describe_settings()}. Line ending {line_ending}, "
        f"prompt {pd}, terminal {tdesc}. Background reader running.{screen_note}"
        f"{note}{multi}{ro}\n"
        f"For an interactive console, send a bare return then read_until_prompt."
    )


@mcp.tool()
def reconnect_last(name: str = "") -> str:
    """Reconnect to a remembered port with the same settings as last time.

    Every successful connect is remembered by name across sessions (port, baud,
    flow control, line ending, prompt, preset). With no `name`, reopens the most
    recently used one. Handy at the start of a chat so the user doesn't repeat the
    port and baud rate. If nothing is remembered, says to use `connect` instead.
    """
    state = _recall()
    if not state["connections"]:
        return "No previous connection on record. Use connect (after list_serial_ports)."
    target = name or state["default"]
    s = state["connections"].get(target)
    if s is None:
        return (f"No remembered connection named {name!r}. Remembered: "
                f"{', '.join(state['connections'])}.")
    return connect(**s)


@mcp.tool()
def disconnect(connection: str = "", all_connections: bool = False) -> str:
    """Close a serial port and free it for other programs.

    Args:
        connection: Which connection to close (name from connect). Default: the
            current one.
        all_connections: Close every open port.
    """
    if all_connections:
        if not _conns:
            return "Nothing to disconnect."
        names = []
        for conn in list(_conns.values()):
            _close_conn(conn)
            _unregister(conn)
            names.append(conn.name)
        return f"Disconnected: {', '.join(names)}."
    conn, err = _resolve(connection, strict=False)
    if conn is None:
        return "Nothing to disconnect." if not _conns else err
    close_err = _close_conn(conn)
    _unregister(conn)
    # Drop the reference even if close() complained (e.g. device already gone), so
    # the server never gets stuck in a half-open state.
    rest = f" Now current: {_default_name!r}." if _conns else ""
    if close_err:
        return (f"Disconnected {conn.name!r} from {conn.settings['port']} "
                f"(close reported: {close_err}).{rest}")
    return f"Disconnected {conn.name!r} from {conn.settings['port']}.{rest}"


@mcp.tool()
def status() -> str:
    """Report every open port: settings, reader state, buffered bytes, control-line
    states, capture, and which connection is current. With nothing open, lists
    what reconnect_last remembers."""
    lines = []
    if READ_ONLY:
        lines.append("READ-ONLY MODE (SERIAL_CONSOLE_READ_ONLY): "
                     "only read-style commands are sent.")
    if IDLE_MINUTES > 0:
        lines.append(f"Idle auto-close after {IDLE_MINUTES:g} min (SERIAL_CONSOLE_IDLE_MINUTES).")
    for name, why in _idle_notes.items():
        lines.append(f"Note: {name!r} was {why}.")
    if not _conns:
        state = _recall()
        if state["connections"]:
            rem = ", ".join(f"{n} ({s['port']} at {s['baud']})"
                            for n, s in state["connections"].items())
            dflt = f" (default {state['default']!r})" if state["default"] else ""
            lines.append(f"Not connected. Remembered: {rem}. Try reconnect_last{dflt}.")
        else:
            lines.append("Not connected, and no previous connection on record.")
        return "\n".join(lines)
    for name, conn in _conns.items():
        with conn.lock:
            buffered, dropped = len(conn.rx), conn.dropped
        if conn.reader_alive:
            reader = "running"
        else:
            reader = "STOPPED" + (f" ({conn.reader_error})" if conn.reader_error else "") + \
                " — run disconnect then connect again"
        extra = f" ({dropped} older bytes discarded: buffer filled up)" if dropped else ""
        ctl = []
        for line in ("cts", "dsr", "cd", "ri"):
            try:
                ctl.append(f"{line.upper()}={'1' if getattr(conn.port, line) else '0'}")
            except Exception:
                pass
        for line in ("dtr", "rts"):
            try:
                ctl.append(f"{line.upper()}={'1' if getattr(conn.port, line) else '0'}")
            except Exception:
                pass
        cap = ""
        if conn.capture_path:
            cap = f" Capturing to {conn.capture_path} ({conn.capture_bytes} bytes)."
        if conn.prompt is None:
            pd = "none"
        else:
            pd = repr(conn.prompt) + (" regex" if conn.prompt_regex else "")
        star = " (current)" if name == _default_name else ""
        tdesc = conn.terminal + (f" {conn.cols}x{conn.rows}" if conn.screen is not None else "")
        lines.append(
            f"{name!r}{star}: {conn.describe_settings()}"
            f"{', preset ' + conn.preset if conn.preset else ''}; line ending {conn.line_ending}, "
            f"prompt {pd}, terminal {tdesc}. Reader {reader}; {buffered} bytes buffered{extra}. "
            f"Lines: {' '.join(ctl) or 'n/a'}.{cap}"
        )
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Tools: sending and reading
# ----------------------------------------------------------------------------

@mcp.tool()
def send_text(data: str, line_ending: LineEnding | None = None, clear_buffer_first: bool = False,
              connection: str = "") -> str:
    """Send an ASCII command to the device. Writes to TX only; does NOT read.

    On an interactive console the device will ECHO this text back and then print
    its output; call `read_until_prompt` (or `read_available`) afterward to see it.
    Use `clear_buffer_first=True` to discard any stale/unsolicited output so the
    next read starts clean.

    Args:
        data: The command text, e.g. "show version", "FA014250000", or "ID".
            Non-ASCII characters are sent as "?"; use send_hex for raw bytes.
        line_ending: What to append — "CR" (\\r, most rigs), "CRLF" (\\r\\n),
            "LF" (\\n, most Unix-style consoles), or "NONE". Default: the
            connection's line ending (from connect or its preset).
        clear_buffer_first: Discard buffered RX before sending.
        connection: Which open connection (name). Default: the current one.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    if err := _guard_text(data):
        return err
    ending = line_ending or conn.line_ending
    payload = (data + _LINE_ENDINGS[ending]).encode("ascii", "replace")
    if clear_buffer_first:
        _drain(conn)
    if err := _write(conn, payload):
        return err
    return (f"Sent {len(payload)} bytes to {conn.name!r}: {data!r} + {ending} "
            f"(use read_until_prompt to see the reply)")


@mcp.tool()
def send_hex(hex_bytes: str, clear_buffer_first: bool = False, connection: str = "") -> str:
    """Send raw bytes given as hex. Writes to TX only; does NOT read.

    Use for binary protocols — notably Icom CI-V, which is all hex (e.g.
    "FE FE 94 E0 03 FD"; civ_build makes these). Spaces in the hex string are
    ignored. To see a binary reply, call `read_available` afterward and hand the
    hex to civ_parse (binary replies rarely have a text prompt, so
    read_until_prompt usually isn't the right tool for these).

    Args:
        hex_bytes: Bytes as hex, e.g. "FE FE 94 E0 03 FD".
        clear_buffer_first: Discard buffered RX before sending.
        connection: Which open connection (name). Default: the current one.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    try:
        payload = bytes.fromhex(hex_bytes.replace(" ", ""))
    except ValueError:
        return f"That isn't valid hex: {hex_bytes!r}"
    if not payload:
        return "Nothing to send: the hex string was empty."
    if err := _guard_hex(payload):
        return err
    if clear_buffer_first:
        _drain(conn)
    if err := _write(conn, payload):
        return err
    return (f"Sent {len(payload)} bytes to {conn.name!r}: {payload.hex(' ')} "
            f"(use read_available to see any reply)")


def _prompt_pattern(
    conn: Connection, prompt: str | None, regex: bool | None
) -> tuple[re.Pattern | None, str, str | None]:
    """Resolve the prompt to use: explicit, else the connection's, else fallback."""
    if prompt is not None:
        use, is_re = prompt, bool(regex)
    elif conn.prompt is not None:
        use, is_re = conn.prompt, conn.prompt_regex
    else:
        use, is_re = _FALLBACK_PROMPT, True
    try:
        return re.compile(use if is_re else re.escape(use)), use, None
    except re.error as e:
        return None, use, f"Bad regex {use!r}: {e}"


@mcp.tool()
def read_until_prompt(prompt: str | None = None, timeout: float = 10.0, regex: bool | None = None,
                      auto_reply: dict[str, str] | None = None, connection: str = "") -> str:
    """Read accumulated output until a prompt appears, or until timeout.

    This is the right tool for interactive CLI sessions (routers, switches, shells).
    It reads from the background buffer until `prompt` is seen, then returns
    everything up to and including it, leaving anything after the prompt in the
    buffer for the next read. Because the reader runs continuously, large multi-page
    outputs are captured in full rather than being cut off by a fixed delay.

    Typical flow:
        send_text("show version", clear_buffer_first=True)
        read_until_prompt(prompt="# ")

    Args:
        prompt: The text that marks the end of output. Literal by default, e.g.
            "# ", "> ", "login: ", "$ ", "Password:". Prefer a prompt with its
            trailing space over a bare "#": the match is searched in everything
            received, including the echo of your own command. Default: the
            connection's prompt (from connect/preset), else "ends with #, >, $ or %".
        timeout: Max seconds to wait for the prompt to appear.
        regex: Treat `prompt` as a Python regular expression (e.g.
            r"[\\w.-]+[#>] ?$" for a hostname-style prompt).
        auto_reply: Text to send automatically when a trigger shows up while
            waiting, e.g. {"--More--": " "} to page through long output, or
            {"[confirm]": "\\r"}. Each occurrence is answered once.
        connection: Which open connection (name). Default: the current one.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    pat, used, err = _prompt_pattern(conn, prompt, regex)
    if err:
        return err
    matched, data, replies = _read_until(conn, pat, timeout, auto_reply)
    rep = f" (auto-replied to {', '.join(replies)})" if replies else ""
    if matched:
        return f"Matched prompt {used!r} after {len(data)} bytes{rep}:\n{_render(data, conn)}"
    if data:
        # Timed out but we have data; hand it back rather than dropping it.
        return (
            f"Prompt {used!r} not seen within {timeout}s{rep}. Got {len(data)} "
            f"bytes so far (returned; buffer now empty):\n{_render(data, conn)}\n"
            f"If the device is still printing, call read_until_prompt again; if the "
            f"prompt differs, adjust `prompt`; if it is paging, pass auto_reply."
        )
    return (
        f"Prompt {used!r} not seen within {timeout}s and nothing was received. "
        f"Try sending a bare return to draw a fresh prompt, or check the baud rate / "
        f"line ending (detect_baud can find the rate)."
    )


@mcp.tool()
def read_available(read_timeout: float = 1.0, connection: str = "") -> str:
    """Drain and return whatever the device has sent, without transmitting anything.

    Use for unsolicited/streaming output (syslog on a console, GPS, a sensor, a rig
    in auto-info mode), or to grab a binary reply after `send_hex`. Waits up to
    `read_timeout` for the first bytes, then briefly settles so a full chunk is
    captured, then returns everything buffered, as text and as hex.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    data = _read_until_idle(conn, read_timeout)
    if not data:
        return "Nothing received within the timeout."
    return (f"Received {len(data)} bytes from {conn.name!r}\n"
            f"Text: {_render(data, conn)!r}\nHex: {data.hex(' ')}")


@mcp.tool()
def query_text(data: str, line_ending: LineEnding | None = None, prompt: str | None = None,
               read_timeout: float = 5.0, connection: str = "") -> str:
    """Convenience: clear the buffer, send an ASCII command, and read the reply.

    For the common "ask the device something and read its answer" case. Reads
    until `prompt` (or the connection's prompt) appears; with no prompt anywhere,
    or prompt="", reads until the device goes idle for a short beat (best for
    line-based rigs/CAT that reply with a terminated string and no shell prompt).

    Args:
        data: Command text, e.g. "show version" or "ID".
        line_ending: "CR", "CRLF", "LF", or "NONE" (see send_text). Default: the
            connection's line ending.
        prompt: Literal prompt to read until, e.g. "# " or ";". "" forces an
            idle-terminated read even if the connection has a prompt.
        read_timeout: How long to wait overall, in seconds.
        connection: Which open connection (name). Default: the current one.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    if err := _guard_text(data):
        return err
    ending = line_ending or conn.line_ending
    payload = (data + _LINE_ENDINGS[ending]).encode("ascii", "replace")
    _drain(conn)
    if err := _write(conn, payload):
        return err

    use_prompt = conn.prompt if prompt is None else prompt
    if use_prompt:
        return read_until_prompt(prompt=use_prompt, timeout=read_timeout,
                                 regex=(conn.prompt_regex if prompt is None else False),
                                 connection=conn.name)

    data_b = _read_until_idle(conn, read_timeout)
    if not data_b:
        return ("Sent, but no reply within the timeout. Wrong baud rate or wrong "
                "line ending are the usual causes; for a CLI, pass the device's "
                "prompt so it reads until the prompt instead.")
    return f"Reply ({len(data_b)} bytes): {_render(data_b, conn)!r}\nHex: {data_b.hex(' ')}"


class ExpectStep(BaseModel):
    """One step of an expect sequence: optionally send, then optionally wait."""

    send: str | None = Field(None, description="Text to send (line ending appended).")
    send_hex: str | None = Field(None, description="Raw bytes to send as hex instead of text.")
    line_ending: LineEnding | None = Field(
        None, description="Override the connection's line ending for this step.")
    expect: str | None = Field(
        None, description="Prompt/text to wait for after sending; omit to not wait.")
    regex: bool = Field(False, description="Treat `expect` as a regular expression.")
    timeout: float = Field(10.0, description="Seconds to wait for `expect`.")
    clear_first: bool = Field(False, description="Discard buffered RX before sending.")


@mcp.tool()
def expect(steps: list[ExpectStep], auto_reply: dict[str, str] | None = None,
           stop_on_timeout: bool = True, connection: str = "") -> str:
    """Run a scripted sequence of send-and-wait steps in one call, like `expect`.

    Use it for login flows and multi-step commands instead of one tool call per
    line: [{send: "", expect: "login: "}, {send: "admin", expect: "Password:"},
    {send: "<pw>", expect: "> "}, {send: "show version", expect: "> "}]. Each
    step's output is returned. A step with no `send` just waits; one with no
    `expect` just sends.

    Args:
        steps: The steps, in order.
        auto_reply: Triggers answered automatically while waiting, e.g.
            {"--More--": " "}.
        stop_on_timeout: Stop at the first step whose `expect` isn't seen.
        connection: Which open connection (name). Default: the current one.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    if not steps:
        return "No steps given."
    out = []
    for i, st in enumerate(steps, 1):
        sent = ""
        if st.clear_first:
            _drain(conn)
        if st.send_hex is not None:
            try:
                payload = bytes.fromhex(st.send_hex.replace(" ", ""))
            except ValueError:
                out.append(f"Step {i}: invalid hex {st.send_hex!r}; stopped.")
                break
            if err := _guard_hex(payload) or _write(conn, payload):
                out.append(f"Step {i}: {err}; stopped.")
                break
            sent = f"sent hex {payload.hex(' ')}"
        elif st.send is not None:
            if err := _guard_text(st.send):
                out.append(f"Step {i}: {err}; stopped.")
                break
            ending = st.line_ending or conn.line_ending
            payload = (st.send + _LINE_ENDINGS[ending]).encode("ascii", "replace")
            if err := _write(conn, payload):
                out.append(f"Step {i}: {err}; stopped.")
                break
            sent = f"sent {st.send!r}+{ending}"
        prefix = f"Step {i}: {sent + '; ' if sent else ''}"
        if st.expect is None:
            out.append(f"{prefix}no wait.")
            continue
        try:
            pat = re.compile(st.expect if st.regex else re.escape(st.expect))
        except re.error as e:
            out.append(f"{prefix}bad regex {st.expect!r}: {e}; stopped.")
            break
        matched, data, replies = _read_until(conn, pat, st.timeout, auto_reply)
        rep = f" (auto-replied to {', '.join(replies)})" if replies else ""
        if matched:
            out.append(f"{prefix}matched {st.expect!r} after {len(data)} bytes{rep}:\n"
                       f"{_render(data, conn)}")
        else:
            out.append(f"{prefix}TIMEOUT: {st.expect!r} not seen in {st.timeout}s{rep}. "
                       f"Got {len(data)} bytes:\n{_render(data, conn)}")
            if stop_on_timeout:
                out.append(f"Stopped after step {i} of {len(steps)}.")
                break
    return "\n".join(out)


@mcp.tool()
def clear_buffer(connection: str = "") -> str:
    """Discard any buffered received data. Handy right before sending a command so
    the next read starts clean (drops old echo, prior output, or syslog noise)."""
    conn, err = _resolve(connection, strict=False)
    if conn is None:
        return err
    dropped = _drain(conn)
    return f"Cleared {len(dropped)} buffered bytes on {conn.name!r}."


@mcp.tool()
def send_keys(keys: list[str], connection: str = "") -> str:
    """Press keys by name, encoded the way a VT100/xterm terminal sends them.

    Use it for what send_text can't type: Ctrl-C to interrupt a running command
    (ping, monitor, a stuck process), Ctrl-Z, Esc, Tab (completion), arrows
    (history, menus), Enter alone, function keys, Page Up/Down. Each item is a
    key name ("ctrl-c", "esc", "tab", "enter", "up", "down", "left", "right",
    "home", "end", "pgup", "pgdn", "backspace", "delete", "f1".."f12", "space"),
    a single character, or "text:..." for a literal run. Nothing is read;
    follow with read_until_prompt, read_available, or screen.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    payload, desc, err = terminal_module.encode_keys(keys)
    if err:
        return err
    if READ_ONLY:
        literal = [d for d in desc if d.startswith("'") or d.startswith("text ")]
        if literal:
            return (f"Read-only mode (SERIAL_CONSOLE_READ_ONLY) refused literal keys "
                    f"{', '.join(literal)}; named keys (ctrl-c, esc, arrows...) are allowed.")
    if not payload:
        return "No keys given."
    if err := _write(conn, payload):
        return err
    return f"Pressed {', '.join(desc)} on {conn.name!r} ({len(payload)} bytes)."


@mcp.tool()
def screen(reset: bool = False, connection: str = "") -> str:
    """Show the current terminal screen of a vt100/xterm connection: what a
    person at a real terminal would see right now, as rows of text, plus the
    cursor position. This is how to read full-screen interfaces (BIOS setup,
    RAID/BMC consoles, menu-driven switches, vi, top): press keys with
    send_keys, then look at the screen again.

    Args:
        reset: Clear the screen model first (after garbage, or a resize).
        connection: Which open connection (name). Default: the current one.
    """
    conn, err = _resolve(connection, strict=False)
    if conn is None:
        return err
    if conn.screen is None:
        hint = ("Install the [screen] extra (pip install 'serial-console-mcp[screen]') and "
                if terminal_module.pyte is None else "")
        return (f"{conn.name!r} has no screen model (terminal={conn.terminal}). {hint}"
                f"connect with terminal=\"xterm\" (or the screen-console preset) to use it. "
                f"For line-oriented output use read_until_prompt or get_transcript.")
    with conn.lock:
        text = conn.screen.text()
        x, y = conn.screen.cursor()
        if reset:
            conn.screen.reset()
    return (f"Screen of {conn.name!r} ({conn.cols}x{conn.rows}, cursor at column {x + 1}, "
            f"row {y + 1}){' — reset' if reset else ''}:\n{text}")


# ----------------------------------------------------------------------------
# Tools: control lines
# ----------------------------------------------------------------------------

@mcp.tool()
def set_lines(dtr: bool | None = None, rts: bool | None = None, connection: str = "") -> str:
    """Set the DTR and/or RTS output lines and hold them.

    Uses: key a transmitter whose PTT is wired to RTS or DTR (set it True to
    transmit, False to stop; pulse_line is safer for that), hold an Arduino in
    reset (DTR False), or tell a modem you're present. Leave a line at None to keep
    it unchanged. Refused in read-only mode.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    if err := _guard_lines():
        return err
    if dtr is None and rts is None:
        return "Nothing to do: give dtr and/or rts."
    try:
        if dtr is not None:
            conn.port.dtr = dtr
        if rts is not None:
            conn.port.rts = rts
        now = f"DTR={'1' if conn.port.dtr else '0'} RTS={'1' if conn.port.rts else '0'}"
    except Exception as e:
        return f"Could not set control lines: {e}"
    return f"Control lines on {conn.name!r}: {now}."


@mcp.tool()
def pulse_line(line: ControlLine, ms: int = 100, level: bool = True, connection: str = "") -> str:
    """Drive DTR or RTS to `level` for `ms` milliseconds, then restore it.

    Uses: reset an Arduino (DTR, 100 ms), enter a bootloader, or key a rig's PTT
    for a timed transmission (RTS or DTR per the interface's wiring). The line is
    always restored, even if the wait is interrupted. Refused in read-only mode.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    if err := _guard_lines():
        return err
    ms = max(1, min(int(ms), 60_000))
    attr = line.lower()
    try:
        before = bool(getattr(conn.port, attr))
        setattr(conn.port, attr, level)
        try:
            time.sleep(ms / 1000)
        finally:
            setattr(conn.port, attr, before)
    except Exception as e:
        return f"Could not pulse {line}: {e}"
    return (f"Pulsed {line} {'high' if level else 'low'} for {ms} ms on {conn.name!r}, "
            f"restored to {'1' if before else '0'}.")


@mcp.tool()
def send_break(ms: int = 250, connection: str = "") -> str:
    """Send a BREAK condition (TX held low) for `ms` milliseconds.

    Some consoles use BREAK to enter ROMMON or interrupt boot; some serial
    devices use it as an attention signal. Refused in read-only mode.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    if err := _guard_lines():
        return err
    ms = max(1, min(int(ms), 5000))
    try:
        conn.port.send_break(duration=ms / 1000)
    except Exception as e:
        return f"Could not send BREAK: {e}"
    return f"Sent BREAK for {ms} ms on {conn.name!r}."


# ----------------------------------------------------------------------------
# Tools: capture and transcript
# ----------------------------------------------------------------------------

@mcp.tool()
def capture_start(path: str = "", format: CaptureFormat = "raw", connection: str = "") -> str:
    """Start writing this connection's traffic to a file until capture_stop.

    Args:
        path: File to write. Empty = an auto-named file in the app's captures
            folder. A bare name goes in that folder; an absolute path is used as is.
            Existing files are appended to.
        format: "raw" writes received bytes exactly as they arrive (a terminal
            log; echo included). "annotated" writes timestamped lines marked TX
            or RX, which is better for protocol debugging.
        connection: Which open connection (name). Default: the current one.
    """
    conn, err = _resolve(connection)
    if conn is None:
        return err
    if conn.capture_file is not None:
        return f"Already capturing {conn.name!r} to {conn.capture_path}. Call capture_stop first."
    cap_dir = _state_dir() / "captures"
    if not path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = cap_dir / f"{conn.name}-{stamp}.{'log' if format == 'raw' else 'txt'}"
    else:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = cap_dir / target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        f = open(target, "ab")
    except Exception as e:
        return f"Could not open {target}: {e}"
    with conn.capture_lock:
        conn.capture_file, conn.capture_path = f, str(target)
        conn.capture_format, conn.capture_bytes = format, 0
        if format == "annotated":
            hdr = (f"# serial-console-mcp capture of {conn.name!r} ({conn.describe_settings()}) "
                   f"started {datetime.now().isoformat(timespec='seconds')}\n")
            f.write(hdr.encode("utf-8"))
            f.flush()
    return f"Capturing {conn.name!r} ({format}) to {target}. Call capture_stop when done."


@mcp.tool()
def capture_stop(connection: str = "") -> str:
    """Stop the capture started by capture_start and report the file and size."""
    conn, err = _resolve(connection, strict=False)
    if conn is None:
        return err
    if conn.capture_file is None:
        return f"No capture running on {conn.name!r}."
    return _capture_stop(conn)


@mcp.tool()
def get_transcript(last_bytes: int = 4000, connection: str = "") -> str:
    """Return the tail of everything received on a connection since it was opened.

    This is a rolling record (last 256 KB) kept regardless of what the read tools
    consumed, so you can re-read output that an earlier call already returned or
    that arrived between calls. Also available as the MCP resource
    serial://transcript/{name}.
    """
    conn, err = _resolve(connection, strict=False)
    if conn is None:
        return err
    with conn.lock:
        total = len(conn.transcript)
        tail = bytes(conn.transcript[-max(1, last_bytes):])
    if not total:
        return f"Nothing received yet on {conn.name!r}."
    return (f"Transcript of {conn.name!r}: last {len(tail)} of {total} bytes"
            f"{' (rolling window full)' if total >= _TRANSCRIPT_MAX else ''}:\n"
            f"{_render(tail, conn)}")


@mcp.resource("serial://transcript/{name}")
def transcript_resource(name: str) -> str:
    """Everything received on connection `name` (rolling 256 KB)."""
    conn = _conns.get(name)
    if conn is None:
        return f"No open connection named {name!r}."
    with conn.lock:
        return _render(bytes(conn.transcript), conn)


# ----------------------------------------------------------------------------
# Tools: diagnosis and helpers
# ----------------------------------------------------------------------------

@mcp.tool()
def port_in_use_by(port: str) -> str:
    """Report which program currently holds a serial port (macOS/Linux, via lsof).

    Use when connect says the port is in use, or before asking the user to close
    something. On Windows the OS doesn't expose this; the usual suspects are a
    terminal (PuTTY), WSJT-X, a logger, or the Arduino Serial Monitor.
    """
    if os.name == "nt":
        return ("Windows doesn't report which process holds a COM port. Close terminals, "
                "WSJT-X, loggers, and the Arduino Serial Monitor, then try again.")
    if port in {c.settings["port"] for c in _conns.values()}:
        owner = next(c.name for c in _conns.values() if c.settings["port"] == port)
        return f"{port} is open here, by this server (connection {owner!r})."
    holder = _port_holder(port)
    if holder is None:
        return f"No process is holding {port} (or lsof is unavailable)."
    return f"{port} is held by {holder}."


@mcp.tool()
def detect_baud(port: str, probe: str = "", probe_line_ending: LineEnding = "CR",
                candidates: list[int] | None = None, settle: float = 0.5) -> str:
    """Try common baud rates on a closed port and rank them by how readable the
    reply is. Use when the user doesn't know the device's rate.

    Each candidate is opened briefly, `probe` (default: a bare return) is sent, and
    whatever comes back is scored by its share of printable text. The right rate
    yields clean text; wrong rates yield garbage or nothing. Note: opening a port
    resets many Arduinos (DTR), and some devices (Kenwood CAT) only answer a real
    command, so pass e.g. probe="ID;" with probe_line_ending="NONE".

    Args:
        port: The port to probe (must not be open here).
        probe: Text to send at each rate. Empty sends only the line ending.
        probe_line_ending: Line ending appended to the probe ("NONE" for CAT).
        candidates: Rates to try. Default: 9600, 115200, 19200, 38400, 57600,
            4800, 2400, 1200, 230400.
        settle: Seconds to wait for a reply at each rate.
    """
    if port in {c.settings["port"] for c in _conns.values()}:
        return f"{port} is open here; disconnect it first, then detect_baud."
    rates = candidates or _BAUD_CANDIDATES
    payload = (probe + _LINE_ENDINGS[probe_line_ending]).encode("ascii", "replace")
    results = []
    for rate in rates:
        try:
            ser = serial.Serial(port=port, baudrate=rate, timeout=0.1, write_timeout=_WRITE_TIMEOUT)
        except (serial.SerialException, OSError, ValueError) as e:
            return f"Could not open {port} at {rate}: {e}"
        try:
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            if payload:
                ser.write(payload)
                ser.flush()
            time.sleep(settle)
            data = b""
            deadline = time.time() + 0.3
            while time.time() < deadline:
                n = ser.in_waiting
                if n:
                    data += ser.read(n)
                    deadline = time.time() + 0.3
                else:
                    time.sleep(0.02)
        except Exception as e:
            data = b""
            results.append((rate, 0, 0.0, f"error: {e}"))
            ser.close()
            continue
        ser.close()
        if data:
            printable = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
            ratio = printable / len(data)
            sample = data[:60].decode("utf-8", "replace").replace("\n", "\\n").replace("\r", "\\r")
            results.append((rate, len(data), ratio, sample))
        else:
            results.append((rate, 0, 0.0, ""))
    got = [r for r in results if r[1] > 0 and not str(r[3]).startswith("error")]
    lines = [f"Baud detection on {port} (probe {probe!r}+{probe_line_ending}):"]
    for rate, n, ratio, sample in results:
        lines.append(f"  {rate:>7}: {n:>4} bytes, {ratio:4.0%} printable  {sample}")
    if not got:
        lines.append("No rate produced any reply. The device may need a real command "
                     "(try probe='ID;' with probe_line_ending='NONE' for CAT), a different "
                     "line ending, more settle time, or the cable/power may be the problem.")
        return "\n".join(lines)
    best = max(got, key=lambda r: (r[2], r[1]))
    if best[2] >= 0.95:
        verdict = "clean text"
    elif best[2] >= 0.7:
        verdict = "mostly readable"
    else:
        verdict = "still garbled"
    lines.append(f"Best guess: {best[0]} baud ({verdict}). Connect with baud={best[0]}.")
    if best[2] < 0.7:
        lines.append("Nothing was clean; the rate may not be in the list, "
                     "or data bits/parity differ.")
    return "\n".join(lines)


@mcp.tool()
def civ_build(command: str, data: str = "", rig: str = "94", controller: str = "E0") -> str:
    """Build an Icom CI-V frame as hex, ready for send_hex.

    Args:
        command: Command byte(s) as hex, e.g. "03" read frequency, "04" read
            mode, "05" set frequency, "06" set mode, "1C 00" PTT, "19 00" read
            rig address.
        data: Data bytes as hex. For frequencies use civ_freq: set 14.070 MHz is
            command "05" with data "00 00 07 14 00" (BCD, low byte first).
        rig: The rig's CI-V address (IC-7300 94, IC-7610 98, IC-9700 A2, IC-705 A4).
        controller: Our address, conventionally E0.
    """
    try:
        frame = civ.build(command, data, rig, controller)
    except ValueError as e:
        return f"Could not build frame: {e}"
    return f"{frame.hex(' ').upper()}\n({civ.describe(frame, controller)})"


@mcp.tool()
def civ_parse(hex_bytes: str, controller: str = "E0") -> str:
    """Decode Icom CI-V frames from hex (as returned by read_available).

    Splits the bytes into frames, tells our own echoed command apart from the
    rig's reply, and decodes frequencies (BCD), modes, OK/NG and PTT state.
    """
    try:
        payload = bytes.fromhex(hex_bytes.replace(" ", ""))
    except ValueError:
        return f"That isn't valid hex: {hex_bytes!r}"
    return civ.describe(payload, controller)


@mcp.tool()
def civ_freq(mhz: float | None = None, bcd_hex: str | None = None) -> str:
    """Convert between a frequency in MHz and CI-V BCD data bytes.

    Give `mhz` to get the 5 data bytes for a set-frequency command (05), or
    `bcd_hex` (5 bytes from a reply) to get the frequency.
    """
    if mhz is not None:
        hz = int(round(mhz * 1_000_000))
        bcd = civ.freq_to_bcd(hz).hex(" ")
        return f"{mhz:.6f} MHz = {bcd.upper()}  (send: civ_build('05', '{bcd}'))"
    if bcd_hex is not None:
        try:
            hz = civ.bcd_to_freq(bytes.fromhex(bcd_hex.replace(" ", "")))
        except ValueError as e:
            return f"Not valid BCD: {e}"
        return f"{bcd_hex} = {hz / 1e6:.6f} MHz"
    return "Give mhz or bcd_hex."


@mcp.tool()
def cat_build(command: str, value: str = "", frequency_mhz: float | None = None,
              flavor: Literal["kenwood", "elecraft", "yaesu"] = "kenwood") -> str:
    """Build a ';'-terminated text CAT command (Kenwood, Elecraft, Yaesu) for
    query_text or send_text with line_ending="NONE".

    Common commands: ID (rig id), FA/FB (VFO A/B frequency; empty value = read),
    MD (mode; read, or set with the family's code), IF (full status), PS (power),
    TX/RX, AI0 (silence auto-info), SM0 (S-meter), PC (output power).

    Args:
        command: Two letters, e.g. "FA".
        value: Digits/letters to append for a set, e.g. "2" for MD2 (USB). Empty
            = read.
        frequency_mhz: For FA/FB sets: the frequency; formatted as 11 digits of
            Hz (Kenwood/Elecraft) or 9 (Yaesu).
        flavor: Which family's conventions: "kenwood" (default), "elecraft",
            "yaesu".
    """
    try:
        wire = cat.build(command, value, frequency_mhz, flavor)
    except ValueError as e:
        return f"Could not build: {e}"
    return f"{wire}\n({cat.describe(wire, flavor)})"


@mcp.tool()
def cat_parse(reply: str, flavor: Literal["kenwood", "elecraft", "yaesu"] = "kenwood") -> str:
    """Decode ';'-terminated text CAT replies: rig id to model, FA/FB to MHz, MD
    to mode name, IF to frequency/mode/VFO/split/TX state, and the ?; E; O;
    error answers. Pass the text returned by query_text.
    """
    return cat.describe(reply, flavor)


@mcp.tool()
def rotator_build(action: Literal["azimuth", "position", "elevation", "move", "move_azel",
                                  "stop", "stop_azimuth", "stop_elevation", "left", "right",
                                  "up", "down", "speed", "help"],
                  azimuth: int | None = None, elevation: int | None = None,
                  speed: int | None = None) -> str:
    """Build a Yaesu GS-232A/B rotator command for send_text (CR) or query_text.

    Actions: "azimuth" (C, read), "position" (C2, read az+el), "elevation" (B),
    "move" (M<az>), "move_azel" (W<az> <el>), "stop"/"stop_azimuth"/
    "stop_elevation", "left"/"right"/"up"/"down" (run until stop), "speed"
    (X1..X4), "help". Reads reply immediately; moves reply nothing, so read the
    position afterwards to confirm.
    """
    try:
        return rotator.build(action, azimuth, elevation, speed)
    except ValueError as e:
        return f"Could not build: {e}"


@mcp.tool()
def rotator_parse(reply: str) -> str:
    """Decode a GS-232 reply into azimuth/elevation degrees ("+0180",
    "+0180+0045", "AZ=180 EL=045") or the ?> rejection."""
    return rotator.describe(reply)


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (pyproject `[project.scripts]`) and frozen-binary entry.

    `serial-console-mcp configure --command ... [--config-home ...]` writes the
    Claude Desktop config entry and exits; installers call this. With no
    arguments, Claude Desktop launches us and we run the stdio server.
    """
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "configure":
        raise SystemExit(configure.main(args[1:]))
    if args and args[0] in ("--version", "-V"):
        from . import __version__
        print(f"serial-console-mcp {__version__}")
        return
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
