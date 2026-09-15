#!/usr/bin/env python3
"""
serial-console-mcp
==================
A small Model Context Protocol (MCP) server that gives Claude Desktop control of
a serial port, so you can talk to a network device's craft/console port (Juniper,
Cisco, etc.), a radio, a rotator, a microcontroller, or anything else that speaks
over an RS-232 / USB-to-serial cable.

It is deliberately GENERIC: raw read/write plus ASCII and hex helpers, built
around a real interactive-console model (a background reader thread + read-until
-prompt), so it behaves the way `minicom`/`pexpect` do rather than a naive
send-then-poll. Specialize it later for a specific device if you want, but this is
enough to drive most gear by conversation.

This runs as a HOST subprocess (stdio transport), launched by an entry in
claude_desktop_config.json. It does NOT run inside Claude's sandbox, which is
exactly why it can see /dev/cu.* (macOS), COMx (Windows), or /dev/ttyUSB* (Linux).

Interactive-console model
-------------------------
A serial console is not request/response. It echoes what you type, emits
unsolicited output (syslog, interface flaps), and can print pages of output that a
fixed timeout would truncate. So this server keeps a background thread draining the
port into a software buffer the whole time a port is open:

  * `send_text` / `send_hex`  -> ONLY write to TX. They do not read.
  * `read_until_prompt`       -> read from the buffer until a prompt (e.g. "# ",
                                 "> ", "login: ") appears, or a timeout elapses.
  * `read_available`          -> drain whatever has accumulated (unsolicited data).
  * `clear_buffer`            -> discard buffered RX (e.g. right before a command).

`query_text` remains as a convenience wrapper (clear -> send -> read_until_prompt)
for the common "send a command and read its output" case.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Literal

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.stderr.write(
        "pyserial is not installed. Run:  pip install pyserial\n"
    )
    raise

from mcp.server.fastmcp import FastMCP

import configure_claude  # bundled so `serial-console-mcp configure ...` works in the frozen binary

mcp = FastMCP("serial-console")

# ----------------------------------------------------------------------------
# Connection state (one open port at a time keeps the mental model simple)
# ----------------------------------------------------------------------------

_port = None  # type: serial.Serial | None
_last_settings = None  # remembered so we can auto-reconnect next session

# Background reader thread + the software RX buffer it fills.
_reader_thread = None  # type: threading.Thread | None
_reader_stop = None  # type: threading.Event | None
_reader_error = None  # type: str | None  # why the reader exited, if it died
_rx_buffer = bytearray()
_rx_dropped = 0  # bytes discarded because nobody read them before _RX_MAX filled
_rx_lock = threading.Lock()

# How often the reader wakes to check the stop flag (also the port read timeout).
_READER_POLL = 0.1
# Never let a write hang a tool call (e.g. a stuck adapter with flow control on).
_WRITE_TIMEOUT = 2.0
# Cap on buffered-but-unread RX. A chatty device left open for hours would
# otherwise grow memory without bound; we keep the newest bytes and count the loss.
_RX_MAX = 4 * 1024 * 1024
# How long the device must stay silent for an idle-terminated read to finish.
_IDLE_SETTLE = 0.15

_LINE_ENDINGS = {"CR": "\r", "CRLF": "\r\n", "LF": "\n", "NONE": ""}
_PARITY = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
_STOPBITS = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE,
             2: serial.STOPBITS_TWO}
_SETTINGS_KEYS = ("port", "baud", "bytesize", "parity", "stopbits", "rtscts", "xonxoff", "timeout")

LineEnding = Literal["CR", "CRLF", "LF", "NONE"]
Parity = Literal["N", "E", "O"]


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


def _remember(settings: dict) -> None:
    global _last_settings
    _last_settings = settings
    try:
        (_state_dir() / "last_connection.json").write_text(json.dumps(settings))
    except Exception:
        pass


def _recall() -> dict | None:
    try:
        data = json.loads((_state_dir() / "last_connection.json").read_text())
    except Exception:
        return None
    if not isinstance(data, dict) or "port" not in data:
        return None
    # Only keep keys `connect` understands, so a file written by an older or newer
    # version can't break reconnect_last with an unexpected-argument error.
    return {k: data[k] for k in _SETTINGS_KEYS if k in data}


def _is_open() -> bool:
    return _port is not None and _port.is_open


def _not_ready() -> str | None:
    """Return an error message if a tool can't proceed, else None."""
    if not _is_open():
        return "Not connected. Use connect first."
    if _reader_thread is None or not _reader_thread.is_alive():
        return (
            f"The port is open but its background reader has stopped"
            f"{' (' + _reader_error + ')' if _reader_error else ''}. The device was "
            f"probably unplugged or the port was taken by another program. Run "
            f"disconnect, then connect (or reconnect_last) again."
        )
    return None


# ----------------------------------------------------------------------------
# Background reader thread + software buffer helpers
# ----------------------------------------------------------------------------

def _reader_loop(port: "serial.Serial", stop_event: threading.Event) -> None:
    """Continuously drain the OS serial buffer into `_rx_buffer` until stopped.

    Owning all reads in one place is what makes interactive consoles work: TX echo,
    unsolicited output, and large multi-page dumps all land in the buffer as they
    arrive, instead of being raced by ad-hoc per-command polling loops.
    """
    global _reader_error, _rx_dropped
    while not stop_event.is_set():
        try:
            n = port.in_waiting
            data = port.read(n if n else 1)  # read(1) blocks up to the port timeout
        except (OSError, serial.SerialException) as e:
            # Port closed out from under us (unplug, or our own disconnect).
            if not stop_event.is_set():
                _reader_error = f"{type(e).__name__}: {e}"
            break
        if data:
            with _rx_lock:
                _rx_buffer.extend(data)
                overflow = len(_rx_buffer) - _RX_MAX
                if overflow > 0:
                    del _rx_buffer[:overflow]
                    _rx_dropped += overflow


def _start_reader() -> None:
    global _reader_thread, _reader_stop, _reader_error, _rx_dropped
    _stop_reader()  # ensure no previous thread lingers
    with _rx_lock:
        _rx_buffer.clear()
        _rx_dropped = 0
    _reader_error = None
    _reader_stop = threading.Event()
    _reader_thread = threading.Thread(
        target=_reader_loop, args=(_port, _reader_stop), daemon=True,
        name="serial-console-reader",
    )
    _reader_thread.start()


def _stop_reader() -> None:
    global _reader_thread, _reader_stop
    if _reader_stop is not None:
        _reader_stop.set()
    if _reader_thread is not None:
        _reader_thread.join(timeout=1.0)
    _reader_thread = None
    _reader_stop = None


def _drain_buffer() -> bytes:
    """Atomically take and clear everything currently in the software buffer."""
    with _rx_lock:
        data = bytes(_rx_buffer)
        _rx_buffer.clear()
    return data


def _pushback(data: bytes) -> None:
    """Return unconsumed bytes to the FRONT of the buffer (before newer arrivals)."""
    if not data:
        return
    with _rx_lock:
        _rx_buffer[:0] = data


def _read_until_idle(read_timeout: float, settle: float | None = None) -> bytes:
    """Wait up to `read_timeout` for the first bytes, then keep collecting until the
    device has been silent for `settle` seconds. Returns b"" if nothing arrived."""
    if settle is None:
        settle = _IDLE_SETTLE  # looked up at call time so tests can widen it
    deadline = time.time() + read_timeout
    buf = bytearray()
    while time.time() < deadline and not buf:
        buf.extend(_drain_buffer())
        if not buf:
            time.sleep(0.02)
    if buf:
        quiet_until = time.time() + settle
        while time.time() < quiet_until:
            more = _drain_buffer()
            if more:
                buf.extend(more)
                quiet_until = time.time() + settle
            else:
                time.sleep(0.02)
    return bytes(buf)


def _write(payload: bytes) -> str | None:
    """Write to the port; return an error message on failure, else None."""
    try:
        _port.write(payload)
        _port.flush()
    except Exception as e:
        hint = ""
        if isinstance(e, serial.SerialTimeoutException) and getattr(_port, "rtscts", False):
            hint = (" RTS/CTS flow control is on and the device never asserted CTS; "
                    "check the cable wiring or reconnect with rtscts=False.")
        return f"Write failed: {e}.{hint}"
    return None


def _flow_desc(rtscts: bool, xonxoff: bool) -> str:
    parts = [n for n, on in (("RTS/CTS", rtscts), ("XON/XOFF", xonxoff)) if on]
    return "+".join(parts) if parts else "none"


def _render(data: bytes) -> str:
    """Human-readable decode for display (UTF-8, lossy)."""
    return data.decode("utf-8", "replace")


# ----------------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------------

@mcp.tool()
def list_serial_ports() -> str:
    """List every serial port the computer can currently see.

    Call this FIRST whenever the user wants to connect to a device but hasn't given
    an exact port name, or when a connection fails. Returns each port's system name
    (what you pass to `connect`), a human description, and the USB hardware id, so
    you can guess which one is the user's gear.
    """
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return (
            "No serial ports found.\n"
            "Things to check: is the device powered on? Is the USB cable a real "
            "data cable (not charge-only)? On Windows, does it show up under "
            "Device Manager > Ports (COM & LPT)?"
        )
    lines = ["Serial ports found:"]
    for p in ports:
        desc = p.description or "(no description)"
        hwid = p.hwid or "(no hardware id)"
        lines.append(f"  • {p.device}  —  {desc}  [{hwid}]")
    lines.append(
        "\nTip: on macOS prefer the /dev/cu.* name over /dev/tty.* for talking "
        "to a device."
    )
    return "\n".join(lines)


@mcp.tool()
def connect(
    port: str,
    baud: int = 9600,
    bytesize: int = 8,
    parity: Parity = "N",
    stopbits: float = 1,
    rtscts: bool = False,
    xonxoff: bool = False,
    timeout: float = 1.0,
) -> str:
    """Open a serial port and start the background reader.

    The defaults are 9600 baud, 8 data bits, no parity, 1 stop bit, no flow
    control ("9600 8N1"), which is what most console/craft ports and much radio
    gear expect. Every setting can be overridden when the user says so, e.g.
    "38400 with XON/XOFF" -> baud=38400, xonxoff=True.

    Args:
        port: System port name, e.g. "COM4" (Windows), "/dev/cu.usbserial-10"
            (macOS), or "/dev/ttyUSB0" (Linux). Get exact names from
            `list_serial_ports`.
        baud: Baud rate. Common values: 1200, 2400, 4800, 9600, 19200, 38400,
            57600, 115200. Check the device's console/CAT menu if unsure.
        bytesize: Data bits: 5, 6, 7, or 8. Almost always 8.
        parity: "N" none, "E" even, "O" odd. Almost always "N".
        stopbits: 1, 1.5, or 2. Almost always 1.
        rtscts: Hardware (RTS/CTS) flow control. Off by default; only turn on if
            the device's manual says so and the cable carries those lines.
        xonxoff: Software (XON/XOFF) flow control. Off by default. Do not use
            for binary protocols (it swallows 0x11 / 0x13 bytes).
        timeout: Reserved for compatibility; the reader thread polls the port on a
            fixed short interval regardless, so reads never block Claude.
    """
    global _port
    if _is_open():
        _stop_reader()
        try:
            _port.close()
        except Exception:
            pass
    _port = None

    if stopbits not in _STOPBITS:
        return f"stopbits must be 1, 1.5, or 2 (got {stopbits!r})."
    try:
        _port = serial.Serial(
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
            hint = (
                "\nThat port is probably already in use. Close any logging or "
                "device-control software that might be holding it (a terminal, "
                "minicom/PuTTY, WSJT-X, a contest logger, the Arduino Serial "
                "Monitor) and try again. A serial port can only be open in one "
                "program at a time."
            )
        return f"Could not open {port}: {msg}{hint}"

    _start_reader()
    _remember({
        "port": port, "baud": baud, "bytesize": bytesize,
        "parity": parity, "stopbits": stopbits,
        "rtscts": rtscts, "xonxoff": xonxoff, "timeout": timeout,
    })
    return (
        f"Connected to {port} at {baud} baud ({bytesize}{parity}{stopbits}, "
        f"flow control {_flow_desc(rtscts, xonxoff)}). "
        f"Background reader running. For an interactive console, try "
        f'read_until_prompt (send a newline first to draw a fresh prompt).'
    )


@mcp.tool()
def reconnect_last() -> str:
    """Reconnect to the most recently used port/settings from a previous session.

    Handy at the start of a chat so the user doesn't have to repeat the port and
    baud rate. If nothing is remembered, tells you to use `connect` instead.
    """
    s = _last_settings or _recall()
    if not s:
        return "No previous connection on record. Use connect (after list_serial_ports)."
    return connect(**s)


@mcp.tool()
def send_text(data: str, line_ending: LineEnding = "CR", clear_buffer_first: bool = False) -> str:
    """Send an ASCII command to the device. Writes to TX only; does NOT read.

    On an interactive console the device will ECHO this text back and then print
    its output; call `read_until_prompt` (or `read_available`) afterward to see it.
    Use `clear_buffer_first=True` to discard any stale/unsolicited output so the
    next read starts clean.

    Args:
        data: The command text, e.g. "show version", "FA014250000", or "ID".
            Non-ASCII characters are sent as "?"; use send_hex for raw bytes.
        line_ending: What to append — "CR" (\\r, most rigs), "CRLF" (\\r\\n),
            "LF" (\\n, most Unix-style consoles), or "NONE".
        clear_buffer_first: Discard buffered RX before sending.
    """
    if err := _not_ready():
        return err
    payload = (data + _LINE_ENDINGS[line_ending]).encode("ascii", "replace")
    if clear_buffer_first:
        _drain_buffer()
    if err := _write(payload):
        return err
    return f"Sent {len(payload)} bytes: {data!r} (use read_until_prompt to see the reply)"


@mcp.tool()
def send_hex(hex_bytes: str, clear_buffer_first: bool = False) -> str:
    """Send raw bytes given as hex. Writes to TX only; does NOT read.

    Use for binary protocols — notably Icom CI-V, which is all hex (e.g.
    "FE FE 94 E0 03 FD"). Spaces in the hex string are ignored. To see a binary
    reply, call `read_available` afterward (binary replies rarely have a text
    prompt, so read_until_prompt usually isn't the right tool for these).

    Args:
        hex_bytes: Bytes as hex, e.g. "FE FE 94 E0 03 FD".
        clear_buffer_first: Discard buffered RX before sending.
    """
    if err := _not_ready():
        return err
    try:
        payload = bytes.fromhex(hex_bytes.replace(" ", ""))
    except ValueError:
        return f"That isn't valid hex: {hex_bytes!r}"
    if not payload:
        return "Nothing to send: the hex string was empty."
    if clear_buffer_first:
        _drain_buffer()
    if err := _write(payload):
        return err
    return f"Sent {len(payload)} bytes: {payload.hex(' ')} (use read_available to see any reply)"


@mcp.tool()
def read_until_prompt(prompt: str = "#", timeout: float = 10.0, regex: bool = False) -> str:
    """Read accumulated output until a prompt appears, or until timeout.

    This is the right tool for interactive CLI sessions (routers, switches, shells).
    It reads from the background buffer until `prompt` is seen, then returns
    everything up to and including it, leaving anything after the prompt in the
    buffer for the next read. Because the reader runs continuously, large multi-page
    outputs are captured in full rather than being cut off by a fixed delay.

    Typical flow:
        send_text("show version", line_ending="LF", clear_buffer_first=True)
        read_until_prompt(prompt="# ")

    Args:
        prompt: The text that marks the end of output. Literal by default, e.g.
            "# ", "> ", "login: ", "$ ", "Password:". Common device prompts end in
            "# " (enable) or "> " (user). Note the match is searched in everything
            received, including the echo of your own command, so prefer a prompt
            with its trailing space over a bare "#" or ">" when the command text
            itself could contain that character.
        timeout: Max seconds to wait for the prompt to appear.
        regex: Treat `prompt` as a Python regular expression instead of literal
            text (e.g. r"[\\w.-]+[#>] ?$" to match a hostname-style prompt).
    """
    if err := _not_ready():
        return err
    try:
        pat = re.compile(prompt if regex else re.escape(prompt))
    except re.error as e:
        return f"Bad regex {prompt!r}: {e}"

    deadline = time.time() + timeout
    collected = bytearray()
    while True:
        collected.extend(_drain_buffer())
        # Index on latin-1 so 1 char == 1 byte (exact, reversible offsets).
        text = collected.decode("latin-1")
        m = pat.search(text)
        if m:
            end = m.end()
            leftover = bytes(collected[end:])
            _pushback(leftover)
            shown = bytes(collected[:end])
            return (
                f"Matched prompt {prompt!r} after {len(shown)} bytes:\n"
                f"{_render(shown)}"
            )
        if time.time() >= deadline:
            break
        time.sleep(0.02)

    if collected:
        # Timed out but we have data; hand it back rather than dropping it.
        return (
            f"Prompt {prompt!r} not seen within {timeout}s. Got {len(collected)} "
            f"bytes so far (returned; buffer now empty):\n{_render(bytes(collected))}\n"
            f"If the device is still printing, call read_until_prompt again; if the "
            f"prompt differs, adjust `prompt`."
        )
    return (
        f"Prompt {prompt!r} not seen within {timeout}s and nothing was received. "
        f"Try sending a newline to draw a fresh prompt, or check the baud rate / "
        f"line ending."
    )


@mcp.tool()
def read_available(read_timeout: float = 1.0) -> str:
    """Drain and return whatever the device has sent, without transmitting anything.

    Use for unsolicited/streaming output (syslog on a console, GPS, a sensor, a rig
    in auto-info mode), or to grab a binary reply after `send_hex`. Waits up to
    `read_timeout` for the first bytes, then briefly settles so a full chunk is
    captured, then returns everything buffered.
    """
    if err := _not_ready():
        return err
    data = _read_until_idle(read_timeout)
    if not data:
        return "Nothing received within the timeout."
    return f"Received {len(data)} bytes\nText: {_render(data)!r}\nHex: {data.hex(' ')}"


@mcp.tool()
def query_text(data: str, line_ending: LineEnding = "CR", prompt: str = "", read_timeout: float = 5.0) -> str:
    """Convenience: clear the buffer, send an ASCII command, and read the reply.

    For the common "ask the device something and read its answer" case. If `prompt`
    is given, reads until that prompt appears (best for interactive CLIs). If
    `prompt` is empty, reads until the device goes idle for a short beat (best for
    line-based rigs/CAT that reply with a terminated string and no shell prompt).

    Args:
        data: Command text, e.g. "show version" or "ID".
        line_ending: "CR", "CRLF", "LF", or "NONE" (see send_text).
        prompt: Optional literal prompt to read until, e.g. "# ". Empty = read
            until idle.
        read_timeout: How long to wait overall, in seconds.
    """
    if err := _not_ready():
        return err
    payload = (data + _LINE_ENDINGS[line_ending]).encode("ascii", "replace")
    _drain_buffer()
    if err := _write(payload):
        return err

    if prompt:
        return read_until_prompt(prompt=prompt, timeout=read_timeout)

    data_b = _read_until_idle(read_timeout)
    if not data_b:
        return ("Sent, but no reply within the timeout. Wrong baud rate or wrong "
                "line ending are the usual causes; for a CLI, pass the device's "
                "prompt so it reads until the prompt instead.")
    return f"Reply ({len(data_b)} bytes): {_render(data_b)!r}\nHex: {data_b.hex(' ')}"


@mcp.tool()
def clear_buffer() -> str:
    """Discard any buffered received data. Handy right before sending a command so
    the next read starts clean (drops old echo, prior output, or syslog noise)."""
    if not _is_open():
        return "Not connected. Use connect first."
    dropped = _drain_buffer()
    return f"Cleared {len(dropped)} buffered bytes."


@mcp.tool()
def status() -> str:
    """Report whether a port is open, with what settings, and how much RX is buffered."""
    if not _is_open():
        s = _last_settings or _recall()
        if s:
            return f"Not connected. Last used: {s['port']} at {s['baud']} baud (try reconnect_last)."
        return "Not connected, and no previous connection on record."
    with _rx_lock:
        buffered = len(_rx_buffer)
        dropped = _rx_dropped
    if _reader_thread and _reader_thread.is_alive():
        reader = "running"
    else:
        reader = "STOPPED" + (f" ({_reader_error})" if _reader_error else "") + \
            " — run disconnect then connect again"
    extra = f" ({dropped} older bytes were discarded because the buffer filled up)" if dropped else ""
    flow = _flow_desc(getattr(_port, "rtscts", False), getattr(_port, "xonxoff", False))
    return (f"Connected: {_port.port} at {_port.baudrate} baud, "
            f"{_port.bytesize}{_port.parity}{_port.stopbits}, flow control {flow}. "
            f"Reader {reader}; {buffered} bytes buffered{extra}.")


@mcp.tool()
def disconnect() -> str:
    """Close the serial port and free it for other programs."""
    global _port
    if _port is None:
        return "Nothing to disconnect."
    name = _port.port
    _stop_reader()
    err = None
    try:
        _port.close()
    except Exception as e:
        err = e
    # Drop the reference even if close() complained (e.g. device already gone), so
    # the server never gets stuck in a half-open state.
    _port = None
    with _rx_lock:
        _rx_buffer.clear()
    if err is not None:
        return f"Disconnected from {name} (close reported: {err})."
    return f"Disconnected from {name}."


if __name__ == "__main__":
    # `serial-console-mcp configure --command ... [--config-home ...]` -> write the
    # Claude Desktop config entry and exit. Installers call this. With no args,
    # Claude Desktop launches us and we run the stdio server.
    if len(sys.argv) > 1 and sys.argv[1] == "configure":
        raise SystemExit(configure_claude.main(sys.argv[2:]))
    mcp.run()  # stdio transport by default
