"""Terminal handling: escape stripping, line rendering, keys, screen model, and
the connect/send_keys/screen tools around them."""
from __future__ import annotations

import time

import pytest

from serial_console_mcp import server as m
from serial_console_mcp import terminal as t
from tests.test_serial_console import (  # noqa: F401  (isolated is an autouse fixture)
    _connect,
    _wait_reader_drained,
    isolated,
)

# --- stripping -------------------------------------------------------------------------------

def test_strip_colours_osc_charset_and_private_modes():
    raw = (b"\x1b]0;window title\x07\x1b[1;32muser@host\x1b[0m:\x1b[34m~\x1b[0m$ "
           b"\x1b(B\x1b[?25h\x1b=\x1b>")
    assert t.strip(raw) == b"user@host:~$ "


def test_strip_is_chunk_safe():
    s = t.EscapeStripper()
    assert s.feed(b"ok \x1b[3") == b"ok "
    assert s.feed(b"2mgreen\x1b") == b"green"
    assert s.feed(b"[0m done\x1b]0;ti") == b" done"
    assert s.feed(b"tle\x07!") == b"!"


def test_strip_keeps_utf8_and_drops_bel_nul():
    assert t.strip("caf\u00e9 \u00da\x00\x07!".encode()) == "caf\u00e9 \u00da!".encode()
    assert t.render("caf\u00e9 \u00da".encode()) == "caf\u00e9 \u00da"


# --- rendering -----------------------------------------------------------------------------

def test_render_cr_overwrite_progress_bar():
    assert t.render(b"Downloading  10%\rDownloading  55%\rDownloading 100%\r\ndone") == \
        "Downloading 100%\ndone"


def test_render_backspace_and_tab():
    assert t.render(b"abcd\x08\x08XY\n\tx") == "abXY\n        x"


def test_render_erase_line_and_clear():
    assert t.render(b"--More--\x1b[2K\rnext page\n") == "next page\n"
    assert t.render(b"tail garbage\x1b[K") == "tail garbage"  # erase from cursor at end: no-op
    assert t.render(b"12345\x1b[3D\x1b[Kxy") == "12xy"
    assert t.render(b"header\x1b[2J\x1b[Hbody") == "header\nbody"
    assert t.render(b"\x1b[10Gcol10") == "         col10"


def test_render_plain_text_unchanged():
    out = t.render(b"show version\r\nJunos: 21.4\r\nuser@r1> ")
    assert out == "show version\nJunos: 21.4\nuser@r1>"
    assert t.render(b"a\n") == "a\n" and t.render(b"") == ""


# --- keys ------------------------------------------------------------------------------------

def test_encode_keys():
    payload, desc, err = t.encode_keys(
        ["ctrl-c", "ESC", "tab", "up", "f5", "F12", "a", "text:hi there", "^z"])
    assert err is None
    assert payload == b"\x03\x1b\t\x1b[A\x1b[15~\x1b[24~ahi there\x1a"
    assert desc[0] == "ctrl-c" and desc[-1] == "^z"
    _, _, err = t.encode_keys(["ctrl-c", "bogus"])
    assert err.startswith("Unknown key 'bogus'")


# --- screen model ----------------------------------------------------------------------------

def test_screen_model_and_answerback():
    answers = []
    sc = t.Screen(20, 4, answer=answers.append)
    sc.feed(b"\x1b[2J\x1b[H Main Menu\r\n 1. Foo\r\n 2. Bar\x1b[c\x1b[6n")
    assert sc.text() == " Main Menu\n 1. Foo\n 2. Bar"
    assert sc.cursor() == (7, 2)
    assert answers == [b"\x1b[?6c", b"\x1b[3;8R"]
    sc.feed(b"\x1b[1;1HXXXX")
    assert sc.text().startswith("XXXXn Menu")
    sc.reset()
    assert sc.text() == ""


def test_screen_requires_pyte(monkeypatch):
    monkeypatch.setattr(t, "pyte", None)
    with pytest.raises(RuntimeError):
        t.Screen()


# --- tools -------------------------------------------------------------------------------------

def test_connect_ansi_mode_cleans_output_and_prompt_matching():
    p = _connect(terminal="ansi", prompt="$ ")
    assert "terminal ansi" in m.status()
    p.feed(b"\x1b[1;32muser@host\x1b[0m:~$ \x1b[K")
    out = m.read_until_prompt(timeout=2)  # prompt "$ " is found despite the colour codes
    assert out.startswith("Matched prompt '$ ' after") and out.endswith("user@host:~$")
    p.feed(b"pkg 10%\rpkg 50%\rpkg 100%\r\n")
    _wait_reader_drained(p)
    assert "Text: 'pkg 100%\\n'" in m.read_available(0.5)


def test_connect_ansi_mode_escape_split_across_chunks():
    p = _connect(terminal="ansi")
    p.feed(b"before \x1b[")
    _wait_reader_drained(p)
    p.feed(b"31mred\x1b[0m after# ")
    out = m.read_until_prompt("# ", timeout=2)
    assert out.endswith("before red after#")


def test_console_presets_use_ansi_and_dumb_stays_raw():
    _connect(preset="cisco-console")
    assert m._conns["fake"].terminal == "ansi"
    m.disconnect()
    p = _connect(preset="kenwood-cat")
    assert m._conns["fake"].terminal == "dumb" and m._conns["fake"].stripper is None
    p.feed(b"\x1b[0mID021;")  # dumb mode passes bytes through untouched
    out = m.query_text("ID", read_timeout=1)
    assert "\x1b[0mID021;" in out


def test_connect_rejects_bad_terminal_and_size():
    assert m.connect("/dev/cu.fake", terminal="wyse60").startswith("terminal must be one of")
    assert m.connect("/dev/cu.fake", terminal="xterm", cols=10).startswith("cols must be")


def test_screen_tool_with_pyte():
    p = _connect(preset="screen-console", baud=9600)
    assert "terminal xterm 80x25" in m.status()
    p.feed(b"\x1b[2J\x1b[H  BIOS SETUP\r\n\r\n  > Boot Order\r\n    Exit\x1b[c")
    _wait_reader_drained(p)
    out = m.screen()
    assert out.startswith("Screen of 'fake' (80x25, cursor at column 9, row 4)")
    assert "  BIOS SETUP\n\n  > Boot Order\n    Exit" in out
    assert bytes(p.tx) == b"\x1b[?6c"  # device-attributes query answered as a VT100
    assert m.send_keys(["down", "enter"]).startswith("Pressed down, enter on 'fake' (4 bytes)")
    assert bytes(p.tx).endswith(b"\x1b[B\r")
    out = m.screen(reset=True)
    assert "— reset" in out
    assert m.screen().endswith("row 1):\n")


def test_screen_tool_without_model_and_pyte_fallback(monkeypatch):
    _connect()
    assert "has no screen model (terminal=dumb)" in m.screen()
    m.disconnect()
    monkeypatch.setattr(t, "pyte", None)
    out = m.connect("/dev/cu.fake", terminal="xterm")
    assert "needs the [screen] extra" in out and "running as ansi" in out
    assert m._conns["fake"].terminal == "ansi" and m._conns["fake"].screen is None
    assert "Install the [screen] extra" in m.screen()


def test_send_keys_errors_and_read_only(monkeypatch):
    p = _connect()
    assert m.send_keys(["nope"]).startswith("Unknown key 'nope'")
    assert m.send_keys([]) == "No keys given."
    assert m.send_keys(["ctrl-c"]).startswith("Pressed ctrl-c")
    assert bytes(p.tx) == b"\x03"
    monkeypatch.setattr(m, "READ_ONLY", True)
    assert m.send_keys(["esc", "up"]).startswith("Pressed esc, up")
    assert m.send_keys(["q"]).startswith("Read-only mode")
    assert m.send_keys(["text:reload"]).startswith("Read-only mode")


def test_terminal_settings_are_remembered(tmp_path):
    _connect(terminal="xterm", cols=132, rows=50, name="bmc")
    m.disconnect()
    out = m.reconnect_last("bmc")
    assert "terminal xterm 132x50" in out
    assert m._conns["bmc"].cols == 132


def test_transcript_renders_in_ansi_mode():
    p = _connect(terminal="ansi")
    p.feed(b"\x1b[1mbold\x1b[0m line\r\n")
    _wait_reader_drained(p)
    time.sleep(0.05)
    assert m.get_transcript().endswith("bold line\n")
    assert m.transcript_resource("fake") == "bold line\n"
