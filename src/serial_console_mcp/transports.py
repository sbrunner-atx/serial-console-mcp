"""Network transports that stand in for a serial port.

Every transport here looks like a `serial.Serial` to the rest of the server:
`read(n)` blocks up to `timeout` and returns what arrived, `in_waiting` counts
bytes ready to read, `write`, `flush`, `close`, `is_open`, `send_break`. That is
all the background reader, the prompt matcher, capture and the screen model need,
so they work unchanged over a network.

What a transport can do beyond carrying bytes is in `capabilities`:

  * "break"  BREAK can be sent (telnet IAC BRK, SSH break request, RFC 2217)
  * "lines"  DTR/RTS can be set and CTS/DSR/CD/RI read (RFC 2217 only)
  * "baud"   the far serial port's baud, data bits, parity and stop bits can be
             set from here (RFC 2217 only)

A local `serial.Serial` has no `capabilities` attribute and can do all of them.

Transports:

  * tcp      raw bytes over TCP (ser2net "raw", many device servers)
  * telnet   RFC 854 client with option negotiation: BINARY, SGA, ECHO, TTYPE,
             NAWS; everything else is refused. A device's own telnet service
             (routers, switches, Linux boxes), reverse-telnet ports on console
             servers (Opengear, Cisco, Lantronix, Digi, Avocent) and ser2net
             "telnet" ports. CR is sent as CR NUL unless BINARY was agreed.
  * rfc2217  telnet plus RFC 2217 COM-port control, via pyserial's client: baud,
             framing, DTR/RTS and BREAK reach the remote serial port.
  * ssh      in `ssh_transport` (needs the [ssh] extra).
"""
from __future__ import annotations

import select
import socket
import threading
import time

ALL_CAPS = frozenset({"break", "lines", "baud"})

# Telnet bytes (RFC 854, 855).
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
NOP, BRK, GA = 241, 243, 249
# Options.
BINARY, ECHO, SGA, TTYPE, NAWS, COM_PORT = 0, 1, 3, 24, 31, 44
TTYPE_IS, TTYPE_SEND = 0, 1

# Options we accept when the server offers to do them (server WILL -> we DO).
_REMOTE_OK = {BINARY, ECHO, SGA}
# Options we agree to do when the server asks (server DO -> we WILL).
_LOCAL_OK = {BINARY, SGA, TTYPE, NAWS}

_CONNECT_TIMEOUT = 10.0
_WRITE_TIMEOUT = 5.0
_RECV_CHUNK = 65536


def capabilities(port) -> frozenset:
    """What `port` can do beyond bytes. A local serial port can do everything."""
    return getattr(port, "capabilities", ALL_CAPS)


def unsupported(port, what: str) -> str:
    """The message a tool returns when the transport cannot do `what`."""
    kind = getattr(port, "kind", "this transport")
    hints = {
        "tcp": "Raw TCP carries bytes only. If the device server speaks RFC 2217 "
               "(ser2net 'telnet' mode with rfc2217, many Moxa/Lantronix/Digi units), "
               "connect with transport=\"rfc2217\" to get DTR/RTS, BREAK and baud control.",
        "telnet": "Telnet carries BREAK (sent as IAC BRK) but no modem lines. Telnet "
                  "straight to a device has no serial line to control; use its serial "
                  "console for that. Through a console server that supports RFC 2217 on "
                  "this port, connect with transport=\"rfc2217\" for DTR/RTS and baud "
                  "control; otherwise use the console server's own port menu.",
        "ssh": "SSH carries BREAK (an RFC 4335 break request) but no modem lines or baud "
               "rate. SSH straight to a device has no serial line to control; use its "
               "serial console for that. Through a console server, set them in its port "
               "configuration.",
    }
    return f"{what} is not available over {kind}. {hints.get(kind, '')}".rstrip()


class TransportClosed(OSError):
    """The remote end closed the connection (or it was closed here)."""


# ----------------------------------------------------------------------------
# Raw TCP
# ----------------------------------------------------------------------------

class TcpTransport:
    """Raw bytes over a TCP socket."""

    kind = "tcp"
    capabilities = frozenset()

    def __init__(self, host: str, port: int, timeout: float = 0.1,
                 connect_timeout: float = _CONNECT_TIMEOUT):
        self.host, self.tcp_port, self.timeout = host, int(port), timeout
        self._sock = socket.create_connection((host, self.tcp_port), timeout=connect_timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        self._sock.settimeout(_WRITE_TIMEOUT)
        self._pending = bytearray()
        self._wlock = threading.Lock()
        self._eof: str | None = None
        self.is_open = True

    # -- receive -------------------------------------------------------------

    def _decode(self, data: bytes) -> bytes:
        return data

    def _fill(self, wait: float) -> None:
        """Move whatever the socket has into `_pending`, waiting up to `wait` s."""
        if self._eof or not self.is_open:
            return
        try:
            ready, _, _ = select.select([self._sock], [], [], max(0.0, wait))
        except (OSError, ValueError) as e:
            self._eof = self._eof or f"socket error: {e}"
            return
        if not ready:
            return
        try:
            data = self._sock.recv(_RECV_CHUNK)
        except (BlockingIOError, InterruptedError, TimeoutError):
            return
        except OSError as e:
            self._eof = f"connection lost: {e}"
            return
        if not data:
            self._eof = "the remote end closed the connection"
            return
        self._pending.extend(self._decode(data))

    @property
    def in_waiting(self) -> int:
        if not self._pending:
            self._fill(0)
        return len(self._pending)

    def read(self, n: int = 1) -> bytes:
        deadline = time.monotonic() + (self.timeout or 0)
        while not self._pending:
            if self._eof:
                raise TransportClosed(self._eof)
            if not self.is_open:
                raise TransportClosed("closed")
            left = deadline - time.monotonic()
            if left <= 0:
                return b""
            self._fill(left)
        out = bytes(self._pending[:n])
        del self._pending[:n]
        return out

    # -- send ----------------------------------------------------------------

    def _encode(self, data: bytes) -> bytes:
        return data

    def _send_raw(self, data: bytes) -> None:
        if not self.is_open:
            raise TransportClosed("closed")
        if self._eof:
            raise TransportClosed(self._eof)
        with self._wlock:
            self._sock.sendall(data)

    def write(self, data: bytes) -> int:
        self._send_raw(self._encode(bytes(data)))
        return len(data)

    def flush(self) -> None:
        pass

    def send_break(self, duration: float = 0.25) -> None:
        raise OSError(unsupported(self, "BREAK"))

    def close(self) -> None:
        if not self.is_open:
            return
        self.is_open = False
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def describe(self) -> str:
        return f"raw TCP to {_hostport(self.host, self.tcp_port)}"


# ----------------------------------------------------------------------------
# Telnet
# ----------------------------------------------------------------------------

class TelnetParser:
    """Stateful IAC decoder. Feed it received bytes; it returns the data bytes
    and calls `on_command(cmd, opt)` / `on_subneg(opt, payload)` for the rest.
    Sequences split across reads are carried over."""

    def __init__(self, on_command, on_subneg):
        self.on_command, self.on_subneg = on_command, on_subneg
        self.state = "data"
        self.cmd = 0
        self.sb_opt: int | None = None
        self.sb = bytearray()
        self.cr_pending = False
        self.binary_in = False  # server sends binary: CR NUL is not special

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            st = self.state
            if st == "data":
                if b == IAC:
                    self.state = "iac"
                elif self.cr_pending and b == 0 and not self.binary_in:
                    self.cr_pending = False  # CR NUL -> CR (already emitted)
                else:
                    self.cr_pending = b == 13
                    out.append(b)
            elif st == "iac":
                if b == IAC:
                    out.append(IAC)
                    self.cr_pending = False
                    self.state = "data"
                elif b in (WILL, WONT, DO, DONT):
                    self.cmd, self.state = b, "opt"
                elif b == SB:
                    self.sb_opt, self.state = None, "sb"
                    self.sb.clear()
                else:  # NOP, GA, DM, BRK, AYT ... from the server: nothing to do
                    self.state = "data"
            elif st == "opt":
                self.on_command(self.cmd, b)
                self.state = "data"
            elif st == "sb":
                if self.sb_opt is None:
                    self.sb_opt = b
                elif b == IAC:
                    self.state = "sb_iac"
                else:
                    self.sb.append(b)
            elif st == "sb_iac":
                if b == SE:
                    self.on_subneg(self.sb_opt, bytes(self.sb))
                    self.state = "data"
                elif b == IAC:
                    self.sb.append(IAC)
                    self.state = "sb"
                else:  # malformed: drop the subnegotiation
                    self.state = "data"
        return bytes(out)


class TelnetTransport(TcpTransport):
    """A telnet client that negotiates as little as a console needs."""

    kind = "telnet"
    capabilities = frozenset({"break"})

    def __init__(self, host: str, port: int = 23, timeout: float = 0.1,
                 term_type: str = "xterm", cols: int = 80, rows: int = 24,
                 connect_timeout: float = _CONNECT_TIMEOUT):
        self.term_type = term_type.upper()
        self.cols, self.rows = cols, rows
        self.local: set[int] = set()   # options we have agreed to do
        self.remote: set[int] = set()  # options the server has agreed to do
        self._asked: set[tuple[int, int]] = set()  # our outstanding requests
        self.parser = TelnetParser(self._on_command, self._on_subneg)
        super().__init__(host, port, timeout=timeout, connect_timeout=connect_timeout)
        # Ask for an 8-bit clean path both ways; a console server that refuses
        # leaves us in NVT mode (CR sent as CR NUL), which is still correct.
        self._request(DO, BINARY)
        self._request(WILL, BINARY)
        self._request(DO, SGA)

    @property
    def binary_out(self) -> bool:
        return BINARY in self.local

    def _request(self, cmd: int, opt: int) -> None:
        self._asked.add((cmd, opt))
        self._send_raw(bytes((IAC, cmd, opt)))

    def _reply(self, cmd: int, opt: int) -> None:
        try:
            self._send_raw(bytes((IAC, cmd, opt)))
        except OSError:
            pass

    def _on_command(self, cmd: int, opt: int) -> None:
        if cmd == WILL:
            if opt in _REMOTE_OK:
                if opt not in self.remote:
                    self.remote.add(opt)
                    if (DO, opt) not in self._asked:
                        self._reply(DO, opt)
                self._asked.discard((DO, opt))
            else:
                self._reply(DONT, opt)
        elif cmd == WONT:
            if opt in self.remote or (DO, opt) in self._asked:
                self.remote.discard(opt)
                if (DO, opt) not in self._asked:
                    self._reply(DONT, opt)
                self._asked.discard((DO, opt))
        elif cmd == DO:
            if opt in _LOCAL_OK:
                if opt not in self.local:
                    self.local.add(opt)
                    if (WILL, opt) not in self._asked:
                        self._reply(WILL, opt)
                self._asked.discard((WILL, opt))
                if opt == NAWS:
                    self._send_naws()
            else:
                self._reply(WONT, opt)
        elif cmd == DONT:
            if opt in self.local or (WILL, opt) in self._asked:
                self.local.discard(opt)
                if (WILL, opt) not in self._asked:
                    self._reply(WONT, opt)
                self._asked.discard((WILL, opt))
        self.parser.binary_in = BINARY in self.remote

    def _on_subneg(self, opt: int | None, payload: bytes) -> None:
        if opt == TTYPE and payload[:1] == bytes((TTYPE_SEND,)) and TTYPE in self.local:
            name = self.term_type.encode("ascii", "replace")
            self._reply_raw(bytes((IAC, SB, TTYPE, TTYPE_IS)) + name + bytes((IAC, SE)))

    def _reply_raw(self, data: bytes) -> None:
        try:
            self._send_raw(data)
        except OSError:
            pass

    def _send_naws(self) -> None:
        body = bytes(((self.cols >> 8) & 0xFF, self.cols & 0xFF,
                      (self.rows >> 8) & 0xFF, self.rows & 0xFF))
        body = body.replace(bytes((IAC,)), bytes((IAC, IAC)))
        self._reply_raw(bytes((IAC, SB, NAWS)) + body + bytes((IAC, SE)))

    def _decode(self, data: bytes) -> bytes:
        return self.parser.feed(data)

    def _encode(self, data: bytes) -> bytes:
        data = data.replace(bytes((IAC,)), bytes((IAC, IAC)))
        if not self.binary_out:
            # NVT: a bare CR must be followed by NUL (CR LF stays CR LF).
            out = bytearray()
            for i, b in enumerate(data):
                out.append(b)
                if b == 13 and (i + 1 >= len(data) or data[i + 1] != 10):
                    out.append(0)
            data = bytes(out)
        return data

    def send_break(self, duration: float = 0.25) -> None:
        # Telnet BREAK has no duration; a console server holds the line for its
        # own configured break time, a device's telnet server does what it likes.
        self._send_raw(bytes((IAC, BRK)))

    def describe(self) -> str:
        mode = "binary" if (self.binary_out and BINARY in self.remote) else "NVT text"
        return f"telnet to {_hostport(self.host, self.tcp_port)} ({mode} mode)"


# ----------------------------------------------------------------------------
# RFC 2217 (pyserial)
# ----------------------------------------------------------------------------

def open_rfc2217(host: str, port: int, *, baudrate: int, bytesize: int, parity: str,
                 stopbits, rtscts: bool, xonxoff: bool, timeout: float):
    """Open an RFC 2217 port through pyserial and return it.

    pyserial's client negotiates BINARY, SGA and COM-PORT-OPTION, then sets the
    remote port's baud, framing and flow control and keeps DTR/RTS/BREAK
    working. A server that does not speak RFC 2217 fails here with a clear
    "does not seem to support RFC2217" error.
    """
    import serial.rfc2217

    url = f"rfc2217://{_hostport(host, port)}?timeout=5"
    ser = serial.rfc2217.Serial(
        url, baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits,
        rtscts=rtscts, xonxoff=xonxoff, timeout=timeout)  # no write_timeout: unsupported
    ser.kind = "rfc2217"
    ser.capabilities = ALL_CAPS
    return ser


def _hostport(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
