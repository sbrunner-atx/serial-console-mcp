"""SSH transport: a device's own SSH (router, switch, Linux box), or a console
server's per-port SSH.

Authentication and host trust come from the user's own OpenSSH setup, never
from the conversation:

  * ~/.ssh/config is read, so Host aliases, HostName, User, Port, IdentityFile,
    IdentityAgent and ProxyJump work as they do for the `ssh` command.
  * Keys come from the SSH agent and the default key files / IdentityFile.
  * The server's host key must already be in ~/.ssh/known_hosts (or the
    config's UserKnownHostsFile). An unknown or changed key refuses the
    connection; nothing here ever accepts one on its own.
  * A password is used only when the caller passes one: from a named
    credential (see credentials.py) or typed by the user into a dialog on
    their own screen (ask.py); the library never prompts.
  * A host key seen for the first time can be trusted only by the user, in a
    dialog that shows its fingerprint (host_key_check / trust_host_key). A
    key that differs from a known one is always refused.

Needs the [ssh] extra (asyncssh). One asyncio loop thread serves every SSH
connection; the reader thread sees an ordinary blocking `read()`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import asyncssh
except ImportError:  # optional extra
    asyncssh = None
else:
    # asyncssh logs every connection step at INFO; keep stderr to real problems.
    logging.getLogger("asyncssh").setLevel(logging.WARNING)

from .transports import TransportClosed, _hostport

# Overridable (tests point these at temporary files). None = OpenSSH defaults.
CONFIG_PATHS: list[str] | None = None
KNOWN_HOSTS: str | None = None
CLIENT_KEYS: list[str] | None = None
AGENT_PATH: str | None = None

_CONNECT_TIMEOUT = 15.0

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


class SSHSetupError(Exception):
    """Connecting failed for a reason the user can act on; str() says what.

    `reason` is "hostkey" (not verifiable), "auth" (login refused) or "other"."""

    def __init__(self, message: str, reason: str = "other"):
        super().__init__(message)
        self.reason = reason


def available() -> bool:
    return asyncssh is not None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            loop = asyncio.new_event_loop()
            t = threading.Thread(target=loop.run_forever, daemon=True,
                                 name="serial-console-ssh-loop")
            t.start()
            _loop = loop
        return _loop


def _run(coro, timeout: float):
    fut = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    try:
        return fut.result(timeout)
    except BaseException:
        fut.cancel()
        raise


if asyncssh is not None:
    class _Session(asyncssh.SSHClientSession):
        def __init__(self, sink: SSHTransport):
            self.sink = sink

        def data_received(self, data, datatype):
            self.sink._push(data if isinstance(data, bytes) else data.encode())

        def connection_lost(self, exc):
            self.sink._lost(f"SSH session ended: {exc}" if exc else
                            "the remote end closed the SSH session")

        def eof_received(self):
            self.sink._lost("the remote end closed the SSH session")
            return False


class SSHTransport:
    """An SSH shell channel with a pty, behaving like a serial port."""

    kind = "ssh"
    capabilities = frozenset({"break"})

    def __init__(self, host: str, port: int | None = None, username: str = "",
                 password: str | None = None, timeout: float = 0.1,
                 term_type: str = "xterm", cols: int = 80, rows: int = 24,
                 connect_timeout: float = _CONNECT_TIMEOUT):
        if asyncssh is None:
            raise SSHSetupError(
                "SSH needs the optional asyncssh package: pip install "
                "'serial-console-mcp[ssh]' (the desktop connector includes it).")
        self.host, self.ssh_port, self.username = host, port, username
        self.timeout = timeout
        self._buf = bytearray()
        self._cond = threading.Condition()
        self._eof: str | None = None
        self.is_open = False
        self._conn = None
        self._chan = None
        self.remote_user = ""
        self.remote_host = ""
        term = term_type or "dumb"
        try:
            _run(self._open(password, term, cols, rows, connect_timeout),
                 connect_timeout + 5)
        except SSHSetupError:
            raise
        except BaseException as e:  # map library errors to something actionable
            reason = "other"
            if isinstance(e, asyncssh.HostKeyNotVerifiable):
                reason = "hostkey"
            elif isinstance(e, asyncssh.PermissionDenied):
                reason = "auth"
            raise SSHSetupError(_explain(e, host), reason) from e
        self.is_open = True

    async def _open(self, password, term, cols, rows, connect_timeout):
        opts = {
            "known_hosts": KNOWN_HOSTS if KNOWN_HOSTS is not None else (),
            "config": CONFIG_PATHS if CONFIG_PATHS is not None else (),
            "connect_timeout": connect_timeout,
            "agent_forwarding": False,
            "x11_forwarding": False,
        }
        if self.ssh_port:
            opts["port"] = int(self.ssh_port)
        if self.username:
            opts["username"] = self.username
        if CLIENT_KEYS is not None:
            opts["client_keys"] = CLIENT_KEYS
        if AGENT_PATH is not None:
            opts["agent_path"] = AGENT_PATH or None
        if password is not None:
            opts["password"] = password
        else:
            opts["password_auth"] = False
            opts["kbdint_auth"] = False
        conn = await asyncssh.connect(self.host, **opts)
        try:
            chan, _ = await conn.create_session(
                lambda: _Session(self), term_type=term, term_size=(cols, rows),
                encoding=None)
        except BaseException:
            conn.close()
            raise
        self._conn, self._chan = conn, chan
        self.remote_user = conn.get_extra_info("username") or self.username
        peer = conn.get_extra_info("peername")
        self.remote_host = peer[0] if peer else self.host

    # -- called on the loop thread ------------------------------------------

    def _push(self, data: bytes) -> None:
        with self._cond:
            self._buf.extend(data)
            self._cond.notify_all()

    def _lost(self, why: str) -> None:
        with self._cond:
            if self._eof is None:
                self._eof = why
            self._cond.notify_all()

    # -- serial-like interface ----------------------------------------------

    @property
    def in_waiting(self) -> int:
        with self._cond:
            return len(self._buf)

    def read(self, n: int = 1) -> bytes:
        deadline = time.monotonic() + (self.timeout or 0)
        with self._cond:
            while not self._buf:
                if self._eof:
                    raise TransportClosed(self._eof)
                if not self.is_open:
                    raise TransportClosed("closed")
                left = deadline - time.monotonic()
                if left <= 0:
                    return b""
                self._cond.wait(left)
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out

    def write(self, data: bytes) -> int:
        if not self.is_open or self._chan is None:
            raise TransportClosed("closed")
        if self._eof:
            raise TransportClosed(self._eof)
        _get_loop().call_soon_threadsafe(self._chan.write, bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def send_break(self, duration: float = 0.25) -> None:
        if not self.is_open or self._chan is None:
            raise TransportClosed("closed")
        msec = max(1, int(duration * 1000))
        _get_loop().call_soon_threadsafe(self._chan.send_break, msec)

    def close(self) -> None:
        if self._conn is None:
            self.is_open = False
            return
        self.is_open = False
        conn, self._conn = self._conn, None

        async def _close():
            conn.close()
            try:
                await asyncio.wait_for(conn.wait_closed(), 2)
            except Exception:
                pass
        try:
            _run(_close(), 3)
        except Exception:
            pass
        self._lost("closed")

    def describe(self) -> str:
        who = f"{self.remote_user}@" if self.remote_user else ""
        target = _hostport(self.host, self.ssh_port) if self.ssh_port else self.host
        return f"SSH to {who}{target}"


def _explain(e: BaseException, host: str) -> str:
    """Turn an asyncssh / socket failure into advice."""
    kh = KNOWN_HOSTS or str(Path("~/.ssh/known_hosts"))
    if asyncssh is not None:
        if isinstance(e, asyncssh.HostKeyNotVerifiable):
            return (f"The SSH host key of {host} could not be verified against {kh} ({e}). "
                    f"Nothing was sent. If this is the first connection, check the key "
                    f"fingerprint with the device's owner and add it yourself, for "
                    f"example by running `ssh {host}` once in a terminal and accepting it. "
                    f"If the key changed unexpectedly, find out why before trusting it. "
                    f"Host key checking is never turned off here.")
        if isinstance(e, asyncssh.PermissionDenied):
            return (f"SSH login to {host} was refused ({e}). No key from the SSH agent, "
                    f"~/.ssh or the IdentityFile in ~/.ssh/config was accepted. Add your "
                    f"public key to the device, set User/IdentityFile for this host in "
                    f"~/.ssh/config, or, for a device that only takes a password, store it "
                    f"with `serial-console-mcp credential set NAME` and pass credential=NAME.")
        if isinstance(e, asyncssh.DisconnectError):
            return f"SSH connection to {host} failed: {e}"
        if isinstance(e, asyncssh.ChannelOpenError):
            return f"{host} accepted the login but refused a shell session: {e}"
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return f"SSH connection to {host} timed out. Check the address, port and network."
    if isinstance(e, OSError):
        return f"Could not reach {host} over SSH: {e}"
    if isinstance(e, ValueError):  # bad config value, unreadable key file, ...
        return f"SSH setup problem for {host}: {e}"
    return f"SSH connection to {host} failed: {type(e).__name__}: {e}"


# ----------------------------------------------------------------------------
# First-contact host keys: the user decides, in a dialog, after seeing the key.
# ----------------------------------------------------------------------------

@dataclass
class HostKeyCheck:
    host: str            # as the caller named it (may be a ~/.ssh/config alias)
    hostname: str        # after ~/.ssh/config HostName
    port: int
    addr: str            # an IP address of hostname, "" if it did not resolve
    alias: str           # HostKeyAlias from the config, else ""
    known_hosts: Path    # where a trusted key is written
    known: bool          # some key is already on record for this host
    algorithm: str
    fingerprint: str     # "SHA256:..."
    key_line: str        # the public key, "algorithm base64"

    def pattern(self) -> str:
        names = [self.alias] if self.alias else [self.hostname]
        if self.addr and self.addr not in names:
            names.append(self.addr)
        if self.port != 22:
            names = [f"[{n}]:{self.port}" for n in names]
        return ",".join(names)


def _ssh_g(host: str, port: int | None) -> dict[str, list[str]]:
    """What OpenSSH itself resolves for `host` (`ssh -G`), or {} without ssh."""
    exe = shutil.which("ssh")
    if not exe:
        return {}
    cmd = [exe, "-G"]
    if CONFIG_PATHS:
        cmd += ["-F", str(CONFIG_PATHS[0])]
    elif CONFIG_PATHS is not None:  # tests: no config at all
        cmd += ["-F", os.devnull]
    if port:
        cmd += ["-p", str(int(port))]
    cmd.append(host)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except Exception:
        return {}
    if r.returncode != 0:
        return {}
    out: dict[str, list[str]] = {}
    for line in r.stdout.splitlines():
        k, _, v = line.partition(" ")
        out.setdefault(k.lower(), []).append(v.strip())
    return out


def _known_hosts_files(cfg: dict[str, list[str]]) -> list[Path]:
    if KNOWN_HOSTS is not None:
        return [Path(KNOWN_HOSTS)]
    files = []
    for k in ("userknownhostsfile", "globalknownhostsfile"):
        for v in cfg.get(k, []):
            files += [Path(x).expanduser() for x in v.split()]
    return files or [Path("~/.ssh/known_hosts").expanduser()]


_PREFERRED_HOST_KEYS = ["ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
                        "ecdsa-sha2-nistp521", "rsa-sha2-512", "rsa-sha2-256", "ssh-rsa"]


def host_key_check(host: str, port: int | None = None, timeout: float = 15.0) -> HostKeyCheck:
    """Fetch the server's host key (no login is attempted) and say whether any
    key is on record for it. Raises SSHSetupError if the server can't be reached."""
    if asyncssh is None:
        raise SSHSetupError("SSH needs the optional asyncssh package.")
    cfg = _ssh_g(host, port)
    hostname = (cfg.get("hostname") or [host])[0]
    real_port = int(port or (cfg.get("port") or ["22"])[0])
    alias = (cfg.get("hostkeyalias") or [""])[0]
    if alias.lower() == "none":
        alias = ""
    try:
        addr = socket.getaddrinfo(hostname, real_port, type=socket.SOCK_STREAM)[0][4][0]
    except OSError:
        addr = ""

    async def _fetch(algs):
        return await asyncssh.get_server_host_key(
            host, port or (), server_host_key_algs=algs,
            config=CONFIG_PATHS if CONFIG_PATHS is not None else ())
    try:
        try:  # the key OpenSSH would show first, so fingerprints match `ssh`
            key = _run(_fetch(_PREFERRED_HOST_KEYS), timeout + 5)
        except asyncssh.Error:
            key = _run(_fetch(()), timeout + 5)
    except BaseException as e:
        raise SSHSetupError(_explain(e, host)) from e
    if key is None:
        raise SSHSetupError(f"{host} sent no host key.")
    files = _known_hosts_files(cfg)
    known = False
    match_port = real_port if real_port != 22 else None
    for f in files:
        if not f.is_file():
            continue
        try:
            kh = asyncssh.read_known_hosts(str(f))
        except Exception:
            continue
        found = kh.match(alias or hostname, addr, match_port)
        if any(found[:3]):  # host keys, CA keys or revoked keys on record
            known = True
            break
    line = key.export_public_key().decode().strip()
    return HostKeyCheck(host=host, hostname=hostname, port=real_port, addr=addr,
                        alias=alias, known_hosts=files[0], known=known,
                        algorithm=key.get_algorithm(), fingerprint=key.get_fingerprint(),
                        key_line=" ".join(line.split()[:2]))


def trust_host_key(check: HostKeyCheck) -> Path:
    """Append the checked key to known_hosts. Only for a host with no key on record."""
    if check.known:
        raise SSHSetupError(f"A different host key is already on record for {check.host}; "
                            f"it is not replaced.")
    path = check.known_hosts
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if not new and path.stat().st_size and not path.read_bytes().endswith(b"\n"):
            f.write("\n")
        f.write(f"{check.pattern()} {check.key_line}\n")
    if new and os.name != "nt":
        os.chmod(path, 0o600)
    return path
