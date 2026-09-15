#!/usr/bin/env python3
"""
ham-serial-mcp
==============
A tiny Model Context Protocol (MCP) server that gives Claude Desktop control of
a serial port, so you can talk to a radio, rotator, amplifier, antenna switch,
or any other device that speaks over a USB-to-serial cable.

It is deliberately GENERIC: raw read/write plus ASCII and hex helpers. Specialize
it later for a specific rig (Icom CI-V, Yaesu/Kenwood CAT, Hamlib, etc.) if you
want, but this is enough to drive most gear by conversation.

This runs as a HOST subprocess (stdio transport), launched by an entry in
claude_desktop_config.json. It does NOT run inside Claude's sandbox, which is
exactly why it can see /dev/cu.* (macOS), COMx (Windows), or /dev/ttyUSB* (Linux).
"""

import json
import os
import sys
import time
from pathlib import Path

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.stderr.write(
        "pyserial is not installed. Run:  pip install pyserial\n"
    )
    raise

from mcp.server.fastmcp import FastMCP

import configure_claude  # bundled so `ham-serial-mcp configure ...` works in the frozen binary

mcp = FastMCP("ham-serial")

# ----------------------------------------------------------------------------
# Connection state (one open port at a time keeps the mental model simple)
# ----------------------------------------------------------------------------

_port = None  # type: serial.Serial | None
_last_settings = None  # remembered so we can auto-reconnect next session


def _state_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "ham-serial-mcp"
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
        return json.loads((_state_dir() / "last_connection.json").read_text())
    except Exception:
        return None


def _is_open() -> bool:
    return _port is not None and _port.is_open


# ----------------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------------

@mcp.tool()
def list_serial_ports() -> str:
    """List every serial port the computer can currently see.

    Call this FIRST whenever the user wants to connect to a radio or device but
    hasn't given an exact port name, or when a connection fails. Returns each
    port's system name (what you pass to `connect`), a human description, and
    the USB hardware id, so you can guess which one is the user's gear.
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
    parity: str = "N",
    stopbits: float = 1,
    timeout: float = 1.0,
) -> str:
    """Open a serial port.

    Args:
        port: System port name, e.g. "COM4" (Windows), "/dev/cu.usbserial-10"
            (macOS), or "/dev/ttyUSB0" (Linux). Get exact names from
            `list_serial_ports`.
        baud: Baud rate. Common rig values: 4800, 9600, 19200, 38400, 57600,
            115200. Check the device's CAT/serial menu if unsure.
        bytesize: Data bits (5,6,7,8). Almost always 8.
        parity: "N" none, "E" even, "O" odd. Almost always "N".
        stopbits: 1, 1.5, or 2. Almost always 1.
        timeout: Read timeout in seconds.
    """
    global _port
    if _is_open():
        try:
            _port.close()
        except Exception:
            pass
        _port = None

    parity_map = {
        "N": serial.PARITY_NONE,
        "E": serial.PARITY_EVEN,
        "O": serial.PARITY_ODD,
    }
    stop_map = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE,
                2: serial.STOPBITS_TWO}
    try:
        _port = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=bytesize,
            parity=parity_map.get(parity.upper(), serial.PARITY_NONE),
            stopbits=stop_map.get(stopbits, serial.STOPBITS_ONE),
            timeout=timeout,
        )
    except serial.SerialException as e:
        msg = str(e)
        hint = ""
        if "could not open" in msg.lower() or "access" in msg.lower() or "denied" in msg.lower():
            hint = (
                "\nThat port is probably already in use. Close any logging or "
                "rig-control software that might be holding it (WSJT-X, your "
                "contest logger, a terminal, the Arduino Serial Monitor) and try "
                "again. A serial port can only be open in one program at a time."
            )
        elif "FileNotFound" in msg or "no such" in msg.lower():
            hint = (
                "\nThat port name wasn't found. Run list_serial_ports to see the "
                "exact current names — they can change when you replug the cable."
            )
        return f"Could not open {port}: {msg}{hint}"

    _remember({
        "port": port, "baud": baud, "bytesize": bytesize,
        "parity": parity, "stopbits": stopbits, "timeout": timeout,
    })
    return f"Connected to {port} at {baud} baud ({bytesize}{parity}{stopbits})."


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
def send_text(data: str, line_ending: str = "CR") -> str:
    """Send an ASCII command to the device (no reply expected/read).

    Use for text-based CAT protocols (Yaesu/Kenwood-style) and most ASCII gear.

    Args:
        data: The command text, e.g. "FA014250000" or "ID".
        line_ending: What to append — "CR" (\\r, most rigs), "CRLF" (\\r\\n),
            "LF" (\\n), or "NONE".
    """
    if not _is_open():
        return "Not connected. Use connect first."
    endings = {"CR": "\r", "CRLF": "\r\n", "LF": "\n", "NONE": ""}
    payload = (data + endings.get(line_ending.upper(), "\r")).encode("ascii", "replace")
    try:
        _port.reset_input_buffer()
        _port.write(payload)
        _port.flush()
    except Exception as e:
        return f"Write failed: {e}"
    return f"Sent {len(payload)} bytes: {data!r}"


@mcp.tool()
def query_text(data: str, line_ending: str = "CR", read_timeout: float = 1.0) -> str:
    """Send an ASCII command AND read back whatever the device replies.

    Use for 'ask the rig something' commands — read frequency, read mode, poll
    status, etc.

    Args:
        data: Command text.
        line_ending: "CR", "CRLF", "LF", or "NONE" (see send_text).
        read_timeout: How long to wait for the reply, in seconds.
    """
    if not _is_open():
        return "Not connected. Use connect first."
    endings = {"CR": "\r", "CRLF": "\r\n", "LF": "\n", "NONE": ""}
    payload = (data + endings.get(line_ending.upper(), "\r")).encode("ascii", "replace")
    try:
        _port.reset_input_buffer()
        _port.write(payload)
        _port.flush()
    except Exception as e:
        return f"Write failed: {e}"

    deadline = time.time() + read_timeout
    buf = bytearray()
    while time.time() < deadline:
        n = _port.in_waiting
        if n:
            buf += _port.read(n)
            deadline = time.time() + 0.15  # short settle after data arrives
        else:
            time.sleep(0.02)
    if not buf:
        return "Sent, but no reply within the timeout. Wrong baud rate or wrong line ending are the usual causes."
    text = buf.decode("ascii", "replace")
    return f"Reply ({len(buf)} bytes): {text!r}\nHex: {buf.hex(' ')}"


@mcp.tool()
def send_hex(hex_bytes: str, read_reply: bool = True, read_timeout: float = 1.0) -> str:
    """Send raw bytes given as hex, optionally reading the reply.

    Use for binary protocols — notably Icom CI-V, which is all hex (e.g.
    "FE FE 94 E0 03 FD"). Spaces in the hex string are ignored.

    Args:
        hex_bytes: Bytes as hex, e.g. "FE FE 94 E0 03 FD".
        read_reply: Whether to read and return the device's response.
        read_timeout: Seconds to wait for the reply.
    """
    if not _is_open():
        return "Not connected. Use connect first."
    try:
        payload = bytes.fromhex(hex_bytes.replace(" ", ""))
    except ValueError:
        return f"That isn't valid hex: {hex_bytes!r}"
    try:
        _port.reset_input_buffer()
        _port.write(payload)
        _port.flush()
    except Exception as e:
        return f"Write failed: {e}"
    if not read_reply:
        return f"Sent {len(payload)} bytes: {payload.hex(' ')}"

    deadline = time.time() + read_timeout
    buf = bytearray()
    while time.time() < deadline:
        n = _port.in_waiting
        if n:
            buf += _port.read(n)
            deadline = time.time() + 0.15
        else:
            time.sleep(0.02)
    if not buf:
        return f"Sent {payload.hex(' ')}, no reply within timeout."
    return f"Sent {payload.hex(' ')}\nReply ({len(buf)} bytes): {buf.hex(' ')}"


@mcp.tool()
def read_available(read_timeout: float = 1.0) -> str:
    """Read whatever bytes are waiting on the port without sending anything.

    Use for devices that stream data on their own (GPS, some sensors, a rig in
    auto-info mode).
    """
    if not _is_open():
        return "Not connected. Use connect first."
    deadline = time.time() + read_timeout
    buf = bytearray()
    while time.time() < deadline:
        n = _port.in_waiting
        if n:
            buf += _port.read(n)
            deadline = time.time() + 0.15
        else:
            time.sleep(0.02)
    if not buf:
        return "Nothing received within the timeout."
    return f"Received {len(buf)} bytes\nText: {buf.decode('ascii','replace')!r}\nHex: {buf.hex(' ')}"


@mcp.tool()
def status() -> str:
    """Report whether a port is open and with what settings."""
    if not _is_open():
        s = _last_settings or _recall()
        if s:
            return f"Not connected. Last used: {s['port']} at {s['baud']} baud (try reconnect_last)."
        return "Not connected, and no previous connection on record."
    return (f"Connected: {_port.port} at {_port.baudrate} baud, "
            f"{_port.bytesize}{_port.parity}{_port.stopbits}.")


@mcp.tool()
def disconnect() -> str:
    """Close the serial port and free it for other programs."""
    global _port
    if not _is_open():
        return "Nothing to disconnect."
    name = _port.port
    try:
        _port.close()
    except Exception as e:
        return f"Error while closing: {e}"
    _port = None
    return f"Disconnected from {name}."


if __name__ == "__main__":
    # `ham-serial-mcp configure --command ... [--config-home ...]` -> write the
    # Claude Desktop config entry and exit. Installers call this. With no args,
    # Claude Desktop launches us and we run the stdio server.
    if len(sys.argv) > 1 and sys.argv[1] == "configure":
        raise SystemExit(configure_claude.main(sys.argv[2:]))
    mcp.run()  # stdio transport by default
