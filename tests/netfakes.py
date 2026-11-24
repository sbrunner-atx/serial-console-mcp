"""In-process network console fakes for the transport tests.

  * FakeTelnetServer  a console server's reverse-telnet port: option negotiation
                      (ECHO, SGA, TTYPE, NAWS, BINARY on request, COM-PORT
                      refused), an optional login/password dialogue, a tiny
                      switch CLI, IAC BRK counting.
  * FakeTcpServer     ser2net "raw": an echoing line console, bytes untouched.
  * FakeRfc2217Server pyserial's RFC 2217 PortManager in front of a loopback
                      serial port, so baud, DTR/RTS and BREAK land somewhere
                      we can inspect.
  * FakeSSHServer     asyncssh server with a host key, one authorized client
                      key, one password user, a shell that records the pty and
                      break requests.

Everything listens on 127.0.0.1 with an ephemeral port.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time

IAC, DONT, DO, WONT, WILL, SB, SE, BRK, DM = 255, 254, 253, 252, 251, 250, 240, 243, 242
BINARY, ECHO, SGA, TTYPE, NAWS, COM_PORT = 0, 1, 3, 24, 31, 44


class _Listener:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.clients: list[socket.socket] = []
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stop:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.clients.append(c)
            threading.Thread(target=self._run, args=(c,), daemon=True).start()

    def _run(self, c):
        try:
            self.handle(c)
        except OSError:
            pass
        finally:
            try:
                c.close()
            except OSError:
                pass

    def drop_clients(self):
        for c in self.clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass

    def close(self):
        self._stop = True
        self.drop_clients()
        try:
            self.sock.close()
        except OSError:
            pass


class FakeTelnetServer(_Listener):
    def __init__(self, login: tuple[str, str] | None = None, binary_ok: bool = True,
                 prompt: bytes = b"switch> ", synch: bool = False):
        self.login, self.binary_ok, self.prompt = login, binary_ok, prompt
        self.synch = synch  # send IAC DM as TCP urgent data before login, like inetutils
        self.received = bytearray()      # data bytes after IAC decoding
        self.raw = bytearray()           # exactly what came over the wire
        self.commands: list[tuple[int, int]] = []
        self.ttype: bytes | None = None
        self.naws: tuple[int, int] | None = None
        self.breaks = 0
        self.lines: list[bytes] = []
        self.login_attempts: list[tuple[bytes, bytes]] = []
        super().__init__()

    def _send(self, c, data: bytes):
        c.sendall(data.replace(b"\xff", b"\xff\xff"))

    def handle(self, c):
        c.sendall(bytes([IAC, WILL, ECHO, IAC, WILL, SGA, IAC, DO, TTYPE, IAC, DO, NAWS,
                         IAC, DO, COM_PORT]))
        state, cmd, sb, line = "data", 0, bytearray(), bytearray()
        after_cr = False  # the previous data byte was CR (it survives line.clear())
        logged_in = self.login is None
        user = None
        if not logged_in:
            c.sendall(b"\r\nOpengear-ish console server\r\n")
            if self.synch:
                time.sleep(0.05)
                c.send(bytes([IAC, DM]), socket.MSG_OOB)
                time.sleep(0.05)
            c.sendall(b"login: ")
        else:
            c.sendall(b"\r\nConnected to port 5\r\n")
        while True:
            data = c.recv(4096)
            if not data:
                return
            self.raw.extend(data)
            for b in data:
                if state == "data":
                    if b == IAC:
                        state = "iac"
                        continue
                    was_cr, after_cr = after_cr, b == 13
                    if b == 0 and was_cr:
                        continue  # CR NUL: NVT end of line
                    self.received.append(b)
                    if b == 10 and was_cr:
                        continue  # CR LF: the CR already ended the line
                    if b in (13, 10):
                        text = bytes(line)
                        line.clear()
                        if not logged_in:
                            if user is None and not text:
                                c.sendall(b"\r\nlogin: ")  # like getty: ask again
                                continue
                            if user is None:
                                user = text
                                c.sendall(b"\r\nPassword: ")
                            else:
                                self.login_attempts.append((user, text))
                                if (user.decode(), text.decode()) == self.login:
                                    logged_in = True
                                    c.sendall(b"\r\nWelcome\r\n" + self.prompt)
                                else:
                                    user = None
                                    c.sendall(b"\r\nLogin incorrect\r\nlogin: ")
                            continue
                        self.lines.append(text)
                        self._command(c, text)
                    else:
                        line.append(b)
                        if logged_in:
                            self._send(c, bytes([b]))  # echo
                elif state == "iac":
                    if b in (WILL, WONT, DO, DONT):
                        cmd, state = b, "opt"
                    elif b == SB:
                        sb.clear()
                        state = "sb"
                    elif b == IAC:
                        self.received.append(IAC)
                        line.append(IAC)
                        state = "data"
                    else:
                        if b == BRK:
                            self.breaks += 1
                            self._send(c, b"\r\n[BREAK]\r\n" + self.prompt)
                        state = "data"
                elif state == "opt":
                    self.commands.append((cmd, b))
                    self._negotiate(c, cmd, b)
                    state = "data"
                elif state == "sb":
                    if b == IAC:
                        state = "sb_iac"
                    else:
                        sb.append(b)
                elif state == "sb_iac":
                    if b == SE:
                        self._subneg(bytes(sb))
                        state = "data"
                    else:
                        sb.append(b)
                        state = "sb"

    def _negotiate(self, c, cmd, opt):
        if cmd == WILL and opt == TTYPE:
            c.sendall(bytes([IAC, SB, TTYPE, 1, IAC, SE]))
        elif cmd == DO and opt == BINARY:
            c.sendall(bytes([IAC, WILL if self.binary_ok else WONT, BINARY]))
        elif cmd == WILL and opt == BINARY:
            c.sendall(bytes([IAC, DO if self.binary_ok else DONT, BINARY]))

    def _subneg(self, sb: bytes):
        if sb[:2] == bytes([TTYPE, 0]):
            self.ttype = sb[2:]
        elif sb[:1] == bytes([NAWS]) and len(sb) >= 5:
            self.naws = (sb[1] << 8 | sb[2], sb[3] << 8 | sb[4])

    def _command(self, c, text: bytes):
        if text == b"show version":
            out = b"\r\nModel: ex2200-c-12t-2g\r\nJunos: 12.3R12.4\r\n"
        elif text == b"binary":
            out = b"\r\nbytes:\xff\x00\x01:end\r\n"
        elif text == b"":
            out = b"\r\n"
        elif text == b"quit":
            c.sendall(b"\r\nbye\r\n")
            c.shutdown(socket.SHUT_RDWR)
            return
        else:
            out = b"\r\n% Unknown command.\r\n"
        self._send(c, out + self.prompt)


class FakeTcpServer(_Listener):
    """Raw console: echoes each byte; a CR ends a line and draws the prompt."""

    def __init__(self):
        self.received = bytearray()
        super().__init__()

    def handle(self, c):
        c.sendall(b"ser2net port 4001\r\n> ")
        while True:
            data = c.recv(4096)
            if not data:
                return
            self.received.extend(data)
            c.sendall(data)
            if b"\r" in data:
                c.sendall(b"\r\nok\r\n> ")


class FakeRfc2217Server(_Listener):
    """pyserial's RFC 2217 server side in front of a recording loopback port."""

    def __init__(self):
        import serial.urlhandler.protocol_loop as loop

        class RecordingLoop(loop.Serial):
            def __init__(self, *a, **k):
                self.break_log: list[bool] = []
                self.dtr_log: list[bool] = []
                self.rts_log: list[bool] = []
                super().__init__(*a, **k)

            def _update_break_state(self):
                self.break_log.append(self._break_state)

            def _update_dtr_state(self):
                self.dtr_log.append(self._dtr_state)

            def _update_rts_state(self):
                self.rts_log.append(self._rts_state)

        self.serial = RecordingLoop("loop://", timeout=0.05)
        super().__init__()

    def handle(self, c):
        import serial.rfc2217

        lock = threading.Lock()

        class Conn:
            def write(self, data):
                with lock:
                    c.sendall(data)

        pm = serial.rfc2217.PortManager(self.serial, Conn())
        alive = [True]

        def pump():
            while alive[0]:
                try:
                    data = self.serial.read(self.serial.in_waiting or 1)
                    if data:
                        Conn().write(b"".join(pm.escape(data)))
                    pm.check_modem_lines()
                except Exception:
                    return
        threading.Thread(target=pump, daemon=True).start()
        try:
            while True:
                data = c.recv(1024)
                if not data:
                    return
                self.serial.write(b"".join(pm.filter(data)))
        finally:
            alive[0] = False


class FakeSSHServer:
    """asyncssh test server on its own loop thread."""

    def __init__(self, tmp_path, password_user: tuple[str, str] = ("pwuser", "s3cret!")):
        import asyncssh

        self.asyncssh = asyncssh
        self.password_user = password_user
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.client_key = asyncssh.generate_private_key("ssh-ed25519")
        self.client_key_path = tmp_path / "id_test"
        self.client_key.write_private_key(str(self.client_key_path))
        self.breaks: list[int] = []
        self.terms: list[tuple] = []
        self.auth_attempts: list[str] = []
        self.received = []
        self.sessions = 0
        self._procs = []
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        fut = asyncio.run_coroutine_threadsafe(self._start(), self.loop)
        self.port = fut.result(10)

    async def _start(self):
        asyncssh = self.asyncssh
        outer = self
        allowed = self.client_key.export_public_key()

        class Server(asyncssh.SSHServer):
            def begin_auth(self, username):
                outer.auth_attempts.append(username)
                return True

            def public_key_auth_supported(self):
                return True

            def validate_public_key(self, username, key):
                return username == "tester" and key.export_public_key() == allowed

            def password_auth_supported(self):
                return True

            def validate_password(self, username, password):
                return (username, password) == outer.password_user

        async def shell(process):
            outer.sessions += 1
            outer._procs.append(process)
            outer.terms.append((process.get_terminal_type(), process.get_terminal_size()[:2]))
            process.stdout.write("Welcome to the bench\r\nbench$ ")
            line = ""
            while True:
                try:
                    data = await process.stdin.read(1024)
                except asyncssh.BreakReceived as b:
                    outer.breaks.append(b.msec)
                    process.stdout.write("\r\n[BREAK]\r\nbench$ ")
                    continue
                except asyncssh.TerminalSizeChanged:
                    continue
                if not data:
                    break
                outer.received.append(data)
                for ch in data:
                    if ch == "\r":
                        cmd, line = line, ""
                        if cmd == "exit":
                            process.stdout.write("\r\nlogout\r\n")
                            process.exit(0)
                            return
                        reply = "Linux benchpi 6.6" if cmd == "uname -a" else f"ran {cmd}"
                        process.stdout.write(f"\r\n{reply}\r\nbench$ ")
                    else:
                        line += ch
                        process.stdout.write(ch)
            process.exit(0)

        self.server = await asyncssh.create_server(
            Server, "127.0.0.1", 0, server_host_keys=[self.host_key], process_factory=shell,
            line_editor=False)
        return self.server.sockets[0].getsockname()[1]

    def known_hosts_line(self, key=None) -> str:
        key = key or self.host_key
        return f"[127.0.0.1]:{self.port} {key.export_public_key().decode().strip()}\n"

    def kick(self):
        """Close every session from the server side."""
        def _go():
            for p in self._procs:
                try:
                    p.exit(0)
                except Exception:
                    pass
        self.loop.call_soon_threadsafe(_go)

    def close(self):
        async def _stop():
            self.server.close()
        try:
            asyncio.run_coroutine_threadsafe(_stop(), self.loop).result(5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)


def wait_for(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False
