"""Named credentials: passwords the user stores, referenced by name only.

A password never travels through the conversation or a tool argument. The user
stores it once, outside the chat, and tools refer to it by a name such as
"bench-opengear". Lookup order:

  0. Environment variables of the server process, set by the user (for
     example in the MCP client's config): SERIAL_CONSOLE_PASSWORD_<NAME> and,
     optionally, SERIAL_CONSOLE_USERNAME_<NAME>. <NAME> is the credential
     name in capitals with every other character turned into "_", so
     "bench-pi" is SERIAL_CONSOLE_PASSWORD_BENCH_PI.
  1. The credentials file: credentials.json in the app's settings folder, or the
     path in SERIAL_CONSOLE_CREDENTIALS. On macOS/Linux it must not be readable
     by group or others (chmod 600), or it is refused. Entries look like
     {"bench-opengear": {"username": "admin", "password": "..."}}.
  2. The `keyring` package, if installed: service "serial-console-mcp",
     user name = the credential name (Windows Credential Manager, Secret
     Service, macOS Keychain).
  3. macOS Keychain via `security`: a generic password with service
     "serial-console-mcp" and account = the credential name.

`serial-console-mcp credential set NAME` stores one from a terminal prompt.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SERVICE = "serial-console-mcp"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")


class CredentialError(Exception):
    """A credential could not be found or used; str() says what to do."""


@dataclass
class Credential:
    name: str
    password: str
    username: str = ""
    source: str = ""

    def __repr__(self) -> str:  # never show the password in a traceback or log
        return f"Credential(name={self.name!r}, username={self.username!r}, source={self.source!r})"


def valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or ""))


def env_names(name: str) -> tuple[str, str]:
    """(password variable, user-name variable) for credential `name`."""
    key = re.sub(r"[^A-Z0-9]", "_", name.upper())
    return f"SERIAL_CONSOLE_PASSWORD_{key}", f"SERIAL_CONSOLE_USERNAME_{key}"


def file_path(state_dir: Path) -> Path:
    env = os.environ.get("SERIAL_CONSOLE_CREDENTIALS", "").strip()
    return Path(env).expanduser() if env else state_dir / "credentials.json"


def _check_private(path: Path) -> None:
    if os.name == "nt":
        return  # %APPDATA% is private to the user by default; no mode bits to check
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise CredentialError(
            f"{path} is readable by other users (mode {stat.S_IMODE(mode):o}), so it is "
            f"not used. Run: chmod 600 '{path}'")


def _read_file(path: Path) -> dict:
    if not path.exists():
        return {}
    _check_private(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CredentialError(f"Could not read {path}: {e}") from e
    if not isinstance(data, dict):
        raise CredentialError(f"{path} must hold a JSON object of name -> entry.")
    return data


def _from_keyring(name: str) -> str | None:
    try:
        import keyring  # optional
    except Exception:
        return None
    try:
        return keyring.get_password(SERVICE, name)
    except Exception:
        return None


def _from_macos_keychain(name: str) -> str | None:
    if sys.platform != "darwin" or not shutil.which("security"):
        return None
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name,
                            "-w"], capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.rstrip("\n")


def lookup(name: str, state_dir: Path) -> Credential:
    """Find credential `name`; raise CredentialError with advice if absent."""
    if not valid_name(name):
        raise CredentialError(
            f"{name!r} is not a credential name. Names are letters, digits and . _ @ -, "
            f"up to 64 characters. Pass the name of a stored credential, never a password.")
    pw_var, user_var = env_names(name)
    if os.environ.get(pw_var):
        return Credential(name, os.environ[pw_var], os.environ.get(user_var, "").strip(),
                          f"environment {pw_var}")
    path = file_path(state_dir)
    entry = _read_file(path).get(name)
    if isinstance(entry, dict) and isinstance(entry.get("password"), str):
        return Credential(name, entry["password"], str(entry.get("username") or ""),
                          f"file {path}")
    if isinstance(entry, str):
        return Credential(name, entry, "", f"file {path}")
    pw = _from_keyring(name)
    if pw is not None:
        return Credential(name, pw, "", "keyring")
    pw = _from_macos_keychain(name)
    if pw is not None:
        return Credential(name, pw, "", "macOS Keychain")
    raise CredentialError(
        f"No stored credential named {name!r}. Ask the user to store it themselves, in a "
        f"terminal (not in this chat): `serial-console-mcp credential set {name}` "
        f"(add --username USER for a login name), or to set {pw_var} in the server's "
        f"environment. For a one-off login, connect with ask=True instead: the user types "
        f"the password into a dialog on their screen. Never ask for the password in the "
        f"chat.")


def names(state_dir: Path) -> list[tuple[str, str]]:
    """(name, username) for every entry in the credentials file."""
    try:
        data = _read_file(file_path(state_dir))
    except CredentialError:
        return []
    out = []
    for k, v in data.items():
        user = v.get("username", "") if isinstance(v, dict) else ""
        out.append((k, str(user or "")))
    return out


def _write_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def store_file(name: str, password: str, username: str, state_dir: Path) -> Path:
    path = file_path(state_dir)
    data = _read_file(path)
    entry = {"password": password}
    if username:
        entry["username"] = username
    data[name] = entry
    _write_file(path, data)
    return path


def remove_file(name: str, state_dir: Path) -> bool:
    path = file_path(state_dir)
    data = _read_file(path)
    if name not in data:
        return False
    del data[name]
    _write_file(path, data)
    return True


# ----------------------------------------------------------------------------
# Command line: serial-console-mcp credential set|remove|list
# ----------------------------------------------------------------------------

_USAGE = """usage:
  serial-console-mcp credential set NAME [--username USER] [--keychain]
  serial-console-mcp credential remove NAME [--keychain]
  serial-console-mcp credential list

Stores a password under NAME so tools can use it as credential=NAME without the
password ever appearing in a chat. You are prompted for it; it is never taken
from the command line. By default it goes to credentials.json in the app's
settings folder (mode 600). --keychain stores it in the macOS Keychain (or the
keyring package's backend) instead; a login name is then given to connect as
username=..."""


def cli(argv: list[str], state_dir: Path) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    keychain = "--keychain" in rest
    rest = [a for a in rest if a != "--keychain"]
    username = ""
    if "--username" in rest:
        i = rest.index("--username")
        if i + 1 >= len(rest):
            print("--username needs a value", file=sys.stderr)
            return 2
        username = rest[i + 1]
        del rest[i:i + 2]
    if cmd == "list":
        found = names(state_dir)
        path = file_path(state_dir)
        if not found:
            print(f"No credentials in {path}.")
        for n, u in found:
            print(f"{n}{'  (user ' + u + ')' if u else ''}")
        print("Keychain/keyring entries (service serial-console-mcp) are not listed.")
        return 0
    if cmd not in ("set", "remove") or len(rest) != 1:
        print(_USAGE, file=sys.stderr)
        return 2
    name = rest[0]
    if not valid_name(name):
        print(f"{name!r} is not a valid name (letters, digits, . _ @ -).", file=sys.stderr)
        return 2
    try:
        if cmd == "remove":
            if keychain:
                return _keychain_remove(name)
            ok = remove_file(name, state_dir)
            print(f"Removed {name}." if ok else f"No credential named {name} in the file.")
            return 0 if ok else 1
        if keychain:
            if username:
                print("Note: the keychain holds only the password; pass username= to connect.")
            return _keychain_set(name)
        pw = getpass.getpass(f"Password for {name}: ")
        if not pw:
            print("Empty password; nothing stored.", file=sys.stderr)
            return 1
        if getpass.getpass("Again: ") != pw:
            print("The two entries differ; nothing stored.", file=sys.stderr)
            return 1
        path = store_file(name, pw, username, state_dir)
        print(f"Stored {name} in {path}. Use it as credential=\"{name}\".")
        return 0
    except CredentialError as e:
        print(str(e), file=sys.stderr)
        return 1


def _keychain_set(name: str) -> int:
    try:
        import keyring  # noqa: F401
        pw = getpass.getpass(f"Password for {name}: ")
        keyring.set_password(SERVICE, name, pw)
        print(f"Stored {name} with keyring. Use it as credential=\"{name}\".")
        return 0
    except ImportError:
        pass
    if sys.platform != "darwin" or not shutil.which("security"):
        print("No keychain available: install the keyring package, or omit --keychain.",
              file=sys.stderr)
        return 1
    # `-w` last makes `security` prompt on the terminal itself.
    r = subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", name,
                        "-l", f"{SERVICE}: {name}", "-w"])
    if r.returncode == 0:
        print(f"Stored {name} in the macOS Keychain. Use it as credential=\"{name}\".")
    return r.returncode


def _keychain_remove(name: str) -> int:
    try:
        import keyring
        keyring.delete_password(SERVICE, name)
        print(f"Removed {name} from keyring.")
        return 0
    except ImportError:
        pass
    except Exception as e:
        print(f"keyring: {e}", file=sys.stderr)
        return 1
    r = subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", name],
                       capture_output=True, text=True)
    print(f"Removed {name} from the macOS Keychain." if r.returncode == 0
          else f"No keychain entry {name}.")
    return r.returncode
