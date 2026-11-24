"""Ask the person at this computer, in a dialog of its own, never in the chat.

For one-off logins: connect(ask=True) opens a password box on the user's
screen; what they type goes straight to this process, is used for that
connection only and is never returned to the assistant or written anywhere.
The same mechanism asks whether to trust an SSH host key seen for the first
time (showing its fingerprint).

Backends: macOS `osascript`, Linux `zenity` or `kdialog` (needs a desktop
session), Windows PowerShell. All text goes in as arguments, never spliced into
a script, so a device or host name cannot inject commands.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

TITLE = "serial-console-mcp"
TIMEOUT = 180  # seconds a dialog waits for the user


class AskUnavailable(Exception):
    """No way to show a dialog here; str() says what to do instead."""


_NO_DIALOG = ("No dialog can be shown on this computer ({why}). Store the password "
              "instead, in a terminal: `serial-console-mcp credential set NAME`, or set "
              "SERIAL_CONSOLE_PASSWORD_<NAME> in the server's environment, then connect "
              "with credential=NAME.")


def _backend() -> str:
    if sys.platform == "darwin" and shutil.which("osascript"):
        return "osascript"
    if os.name == "nt" and shutil.which("powershell"):
        return "powershell"
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        for b in ("zenity", "kdialog"):
            if shutil.which(b):
                return b
        raise AskUnavailable(_NO_DIALOG.format(why="neither zenity nor kdialog is installed"))
    raise AskUnavailable(_NO_DIALOG.format(why="no desktop session"))


def available() -> tuple[bool, str]:
    try:
        return True, _backend()
    except AskUnavailable as e:
        return False, str(e)


def _run(cmd: list[str], stdin: str | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                           timeout=TIMEOUT + 15)
    except subprocess.TimeoutExpired:
        return 1, ""
    except OSError as e:
        raise AskUnavailable(_NO_DIALOG.format(why=str(e))) from e
    return r.returncode, r.stdout.rstrip("\r\n")


_OSA_TEXT = """on run argv
  set r to display dialog (item 1 of argv) default answer (item 3 of argv) {hidden}\
with title (item 2 of argv) buttons {{"Cancel", "OK"}} default button "OK" \
cancel button "Cancel" giving up after {t} with icon note
  if gave up of r then error number -128
  return text returned of r
end run"""

_OSA_CONFIRM = """on run argv
  set r to display dialog (item 1 of argv) with title (item 2 of argv) \
buttons {"Cancel", (item 3 of argv)} default button "Cancel" cancel button "Cancel" \
giving up after %d with icon caution
  if gave up of r then error number -128
  return button returned of r
end run"""

# PowerShell reads its arguments from $args; nothing is interpolated.
_PS_TEXT = r"""
Add-Type -AssemblyName Microsoft.VisualBasic
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object Windows.Forms.Form; $f.Text = $args[1]; $f.Width = 460; $f.Height = 190
$f.TopMost = $true; $f.StartPosition = 'CenterScreen'
$l = New-Object Windows.Forms.Label; $l.Text = $args[0]; $l.Left = 10; $l.Top = 10
$l.Width = 420; $l.Height = 60
$t = New-Object Windows.Forms.TextBox; $t.Left = 10; $t.Top = 75; $t.Width = 420
$t.Text = $args[2]; if ($args[3] -eq '1') { $t.UseSystemPasswordChar = $true }
$ok = New-Object Windows.Forms.Button; $ok.Text = 'OK'; $ok.Left = 270; $ok.Top = 110
$ok.DialogResult = 'OK'; $f.AcceptButton = $ok
$c = New-Object Windows.Forms.Button; $c.Text = 'Cancel'; $c.Left = 355; $c.Top = 110
$c.DialogResult = 'Cancel'; $f.CancelButton = $c
$f.Controls.AddRange(@($l, $t, $ok, $c))
if ($f.ShowDialog() -eq 'OK') { [Console]::Out.Write($t.Text); exit 0 } else { exit 1 }
"""

_PS_CONFIRM = r"""
Add-Type -AssemblyName System.Windows.Forms
$r = [Windows.Forms.MessageBox]::Show($args[0], $args[1], 'OKCancel', 'Warning', 'Button2')
if ($r -eq 'OK') { exit 0 } else { exit 1 }
"""


def _text(message: str, hidden: bool, default: str = "") -> str | None:
    b = _backend()
    if b == "osascript":
        script = _OSA_TEXT.format(hidden="with hidden answer " if hidden else "", t=TIMEOUT)
        rc, out = _run(["osascript", "-e", script, message, TITLE, default])
    elif b == "powershell":
        rc, out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_TEXT,
                        message, TITLE, default, "1" if hidden else "0"])
    elif b == "zenity":
        cmd = ["zenity", "--title", TITLE, f"--timeout={TIMEOUT}"]
        cmd += (["--password"] if hidden else
                ["--entry", "--text", message, "--entry-text", default])
        rc, out = _run(cmd)
        if hidden and rc == 0 and "|" in out:  # some versions print user|password
            out = out.split("|", 1)[1]
    else:  # kdialog
        cmd = ["kdialog", "--title", TITLE]
        cmd += ["--password", message] if hidden else ["--inputbox", message, default]
        rc, out = _run(cmd)
    return out if rc == 0 else None


def password(message: str) -> str | None:
    """Hidden text box. None if the user cancelled or let it time out."""
    pw = _text(message, hidden=True)
    return pw or None


def text(message: str, default: str = "") -> str | None:
    """Plain text box (a user name). None if cancelled."""
    out = _text(message, hidden=False, default=default)
    if out is None:
        return None
    return out.strip() or None


def confirm(message: str, ok_label: str = "Trust") -> bool:
    """A yes/no question whose default is No."""
    b = _backend()
    if b == "osascript":
        rc, out = _run(["osascript", "-e", _OSA_CONFIRM % TIMEOUT, message, TITLE, ok_label])
        return rc == 0 and out == ok_label
    if b == "powershell":
        rc, _ = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_CONFIRM,
                      message, TITLE])
        return rc == 0
    if b == "zenity":
        rc, _ = _run(["zenity", "--question", "--title", TITLE, "--text", message,
                      "--ok-label", ok_label, "--cancel-label", "Cancel", "--default-cancel",
                      f"--timeout={TIMEOUT}"])
        return rc == 0
    rc, _ = _run(["kdialog", "--title", TITLE, "--warningcontinuecancel", message,
                  "--continue-label", ok_label])
    return rc == 0
