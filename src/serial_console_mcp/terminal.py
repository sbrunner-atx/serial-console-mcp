"""Terminal handling for the VT100 / ANSI / xterm family.

Three layers, each optional per connection:

* ``EscapeStripper`` removes escape sequences from the byte stream as it
  arrives (chunk-safe: a sequence split across reads is held back until it is
  complete), so prompt matching and the agent's view see plain text.
* ``render`` applies the line editing a terminal would show: a bare CR rewinds
  to column 0 and overwrites (progress bars), backspace moves left, and the few
  CSI cursor/erase controls that survive stripping are honoured.
* ``Screen`` keeps a real character grid using ``pyte`` (installed with the
  ``[screen]`` extra) for full-screen interfaces: BIOS setup, RAID and BMC
  consoles, menu-driven switches, ``vi`` and ``top``. It also answers the
  device-attribute and cursor-position queries such programs send.

``KEYS`` maps key names to the byte sequences an xterm-compatible terminal
sends, so an agent can press Ctrl-C, Tab, the arrows or F-keys by name.
"""

from __future__ import annotations

import re

try:  # optional: the [screen] extra
    import pyte
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    pyte = None

TERMINALS = ("dumb", "ansi", "vt100", "xterm")

# ---------------------------------------------------------------------------
# Keys: names -> bytes (xterm / VT100 conventions; what devices expect today)
# ---------------------------------------------------------------------------

KEYS: dict[str, bytes] = {
    "enter": b"\r", "return": b"\r", "cr": b"\r", "lf": b"\n", "crlf": b"\r\n",
    "tab": b"\t", "esc": b"\x1b", "escape": b"\x1b", "space": b" ",
    "backspace": b"\x7f", "bs": b"\x08", "delete": b"\x1b[3~", "del": b"\x1b[3~",
    "insert": b"\x1b[2~", "home": b"\x1b[H", "end": b"\x1b[F",
    "pageup": b"\x1b[5~", "pgup": b"\x1b[5~", "pagedown": b"\x1b[6~", "pgdn": b"\x1b[6~",
    "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C", "left": b"\x1b[D",
    "f1": b"\x1bOP", "f2": b"\x1bOQ", "f3": b"\x1bOR", "f4": b"\x1bOS",
    "f5": b"\x1b[15~", "f6": b"\x1b[17~", "f7": b"\x1b[18~", "f8": b"\x1b[19~",
    "f9": b"\x1b[20~", "f10": b"\x1b[21~", "f11": b"\x1b[23~", "f12": b"\x1b[24~",
    "nul": b"\x00",
}
for _i in range(26):
    KEYS[f"ctrl-{chr(97 + _i)}"] = bytes([_i + 1])
KEYS.update({"ctrl-[": b"\x1b", "ctrl-\\": b"\x1c", "ctrl-]": b"\x1d", "ctrl-^": b"\x1e",
             "ctrl-_": b"\x1f", "ctrl-space": b"\x00"})
# Aliases people type
KEYS.update({"^c": KEYS["ctrl-c"], "^z": KEYS["ctrl-z"], "^d": KEYS["ctrl-d"],
             "^]": KEYS["ctrl-]"], "^u": KEYS["ctrl-u"], "^l": KEYS["ctrl-l"]})


def encode_keys(keys: list[str]) -> tuple[bytes, list[str], str | None]:
    """Turn key names / single characters / "text:..." items into bytes.

    Returns (payload, description of each item, error or None).
    """
    out = bytearray()
    desc = []
    for k in keys:
        name = k.strip().lower()
        if name in KEYS:
            out += KEYS[name]
            desc.append(name)
        elif k.lower().startswith("text:"):
            out += k[5:].encode("ascii", "replace")
            desc.append(f"text {k[5:]!r}")
        elif len(k) == 1:
            out += k.encode("ascii", "replace")
            desc.append(repr(k))
        else:
            return b"", desc, (f"Unknown key {k!r}. Use a name such as ctrl-c, esc, tab, "
                               f"enter, up, f1, a single character, or 'text:...'.")
    return bytes(out), desc, None


# ---------------------------------------------------------------------------
# Escape stripping (stateful, chunk-safe)
# ---------------------------------------------------------------------------

_ESC = 0x1B


class EscapeStripper:
    """Remove ANSI/VT escape sequences from a byte stream fed in arbitrary chunks.

    Keeps the single-byte controls the renderer needs (CR, LF, BS, TAB) and
    turns the CSI erase/cursor controls the renderer understands into private
    one-byte markers; drops everything else. A sequence that is not yet
    complete is held back until the next chunk.
    """

    # Private markers understood by render(). 0xF8-0xFF never occur in valid UTF-8,
    # so they cannot collide with device text (unlike the C1 range).
    ERASE_LINE = b"\xf8"      # CSI K / CSI 0K   erase to end of line
    ERASE_WHOLE = b"\xf9"     # CSI 2K           erase whole line
    CLEAR = b"\xfa"           # CSI J / CSI H    clear screen or home: treat as line break
    LEFT = b"\xfb"            # CSI n D          cursor left n (next byte = n)
    COL = b"\xfc"             # CSI n G          column n, 1-based (next byte = n)

    def __init__(self) -> None:
        self.pending = bytearray()

    def feed(self, data: bytes) -> bytes:
        buf = self.pending + data
        self.pending = bytearray()
        out = bytearray()
        i, n = 0, len(buf)
        while i < n:
            b = buf[i]
            if b == _ESC:
                end, replacement = self._sequence_end(buf, i)
                if end is None:  # incomplete: keep for next chunk
                    self.pending = bytearray(buf[i:])
                    break
                out += replacement
                i = end
                continue
            if b in (0x07, 0x00, 0x0E, 0x0F):  # BEL, NUL, SO, SI
                i += 1
                continue
            out.append(b)
            i += 1
        return bytes(out)

    def _sequence_end(self, buf: bytearray, i: int) -> tuple[int | None, bytes]:
        if i + 1 >= len(buf):
            return None, b""
        c = buf[i + 1]
        if c == 0x5B:  # [  -> CSI
            return self._csi_end(buf, i + 2)
        if c == 0x5D:  # ]  -> OSC ... BEL or ESC backslash
            j = i + 2
            while j < len(buf):
                if buf[j] == 0x07:
                    return j + 1, b""
                if buf[j] == _ESC and j + 1 < len(buf) and buf[j + 1] == 0x5C:
                    return j + 2, b""
                j += 1
            return None, b""
        if 0x20 <= c <= 0x2F:  # ESC + intermediates + final (charset selection etc.)
            j = i + 2
            while j < len(buf) and 0x20 <= buf[j] <= 0x2F:
                j += 1
            if j >= len(buf):
                return None, b""
            return j + 1, b""
        return i + 2, b""  # ESC + single byte (ESC 7, ESC 8, ESC =, ESC >, ESC c, ...)

    def _csi_end(self, buf: bytearray, j: int) -> tuple[int | None, bytes]:
        k = j
        while k < len(buf) and 0x20 <= buf[k] <= 0x3F:
            k += 1
        if k >= len(buf):
            return None, b""
        final = buf[k]
        if not 0x40 <= final <= 0x7E:
            return k + 1, b""  # malformed; drop
        params = bytes(buf[j:k]).decode("ascii", "replace")
        m = re.match(r"\d+", params)
        n = int(m.group(0)) if m else 0
        if final == 0x4B:  # K
            return k + 1, (self.ERASE_WHOLE if n == 2 else self.ERASE_LINE)
        if final in (0x4A, 0x48, 0x66):  # J, H, f
            return k + 1, self.CLEAR
        if final == 0x44:  # D cursor left
            return k + 1, self.LEFT + bytes([max(1, min(n or 1, 255))])
        if final == 0x47:  # G column
            return k + 1, self.COL + bytes([max(1, min(n or 1, 255))])
        return k + 1, b""


def strip(data: bytes) -> bytes:
    """Stateless strip for text that is complete (already-buffered output, tests)."""
    s = EscapeStripper()
    out = s.feed(data)
    return out  # a trailing incomplete sequence is dropped


# ---------------------------------------------------------------------------
# Line rendering: CR overwrite, backspace, erase markers
# ---------------------------------------------------------------------------

# Marker bytes become private-use characters so UTF-8 text decodes untouched.
_T_ERASE_LINE, _T_ERASE_WHOLE, _T_CLEAR, _T_LEFT, _T_COL = (
    "", "", "", "", "")
_MARKERS = {0xF8: _T_ERASE_LINE, 0xF9: _T_ERASE_WHOLE, 0xFA: _T_CLEAR, 0xFB: _T_LEFT, 0xFC: _T_COL}


def _tokenize(data: bytes) -> str:
    """Decode UTF-8 text while turning marker bytes (and their count byte) into
    private-use characters that render() understands."""
    out: list[str] = []
    seg = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b in _MARKERS:
            if seg:
                out.append(seg.decode("utf-8", "replace"))
                seg = bytearray()
            out.append(_MARKERS[b])
            if b in (0xFB, 0xFC) and i + 1 < n:  # count byte follows
                out.append(chr(data[i + 1]))
                i += 1
        else:
            seg.append(b)
        i += 1
    if seg:
        out.append(seg.decode("utf-8", "replace"))
    return "".join(out)


def render(data: bytes) -> str:
    """Show what a terminal would show for a line-oriented stream.

    Carriage return without newline rewinds to column 0 and later characters
    overwrite (progress bars, spinners, "--More--" being erased); backspace
    moves the cursor left; tabs expand to the next multiple of 8. Escape
    sequences are stripped first if any remain.
    """
    text = _tokenize(strip(data))
    lines, cur, _ = _edit(text)
    if cur or not lines or text.endswith("\n"):
        lines.append(cur)  # a trailing newline leaves an empty current line, as on a terminal
    return "\n".join("".join(line).rstrip() for line in lines)


def line_start(data: bytes | bytearray) -> int:
    """Offset where the last line of stripped stream bytes begins: just after the
    last LF or clear marker that is not the count byte of a cursor marker."""
    end = len(data)
    while True:
        pos = max(data.rfind(b"\n", 0, end), data.rfind(EscapeStripper.CLEAR, 0, end))
        if pos <= 0 or data[pos - 1] not in (0xFB, 0xFC):
            return pos + 1
        end = pos - 1


_LINE_EDITS = re.compile(rb"[\x00-\x09\x0b-\x1f\x7f\xf8-\xfc]")


def has_line_edits(data: bytes) -> bool:
    """True if stripped bytes hold anything that makes the displayed line differ
    from the bytes: CR, backspace, tab, other controls, or erase/cursor markers."""
    return _LINE_EDITS.search(data) is not None


def cursor_line(data: bytes) -> str:
    """One unterminated line of stripped stream bytes (see `line_start`) as a
    terminal shows it, from column 0 up to the cursor.

    That is where a prompt ends once a device has redrawn its input line with CR,
    spaces and backspaces: ``\\rroot@sw> show system    \\b\\b\\b`` reads
    ``root@sw> show system ``. A line just rewound by a bare CR reads empty.
    """
    _, cur, col = _edit(_tokenize(data))  # already stripped: count bytes must not be re-stripped
    return "".join(cur[:col]).ljust(col)


def _edit(text: str) -> tuple[list[list[str]], list[str], int]:
    """Apply terminal line editing to tokenized text: the finished lines, the
    current line and the cursor column."""
    lines: list[list[str]] = []
    cur: list[str] = []
    col = 0

    def commit() -> None:
        nonlocal cur, col
        lines.append(cur)
        cur, col = [], 0

    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            commit()
        elif ch == "\r":
            col = 0
        elif ch == "\b":
            col = max(0, col - 1)
        elif ch == "\t":
            col = (col // 8 + 1) * 8
            while len(cur) < col:
                cur.append(" ")
        elif ch == _T_ERASE_LINE:  # erase to end of line
            del cur[col:]
        elif ch == _T_ERASE_WHOLE:  # erase whole line
            cur, col = [], 0
        elif ch == _T_CLEAR:  # clear screen / home
            if cur:
                commit()
        elif ch == _T_LEFT and i + 1 < len(text):  # cursor left n
            col = max(0, col - ord(text[i + 1]))
            i += 1
        elif ch == _T_COL and i + 1 < len(text):  # column n (1-based)
            col = max(0, ord(text[i + 1]) - 1)
            while len(cur) < col:
                cur.append(" ")
            i += 1
        elif ch < " " or ch == "\x7f":
            pass  # other controls: drop
        else:
            if col < len(cur):
                cur[col] = ch
            else:
                while len(cur) < col:
                    cur.append(" ")
                cur.append(ch)
            col += 1
        i += 1
    return lines, cur, col


# ---------------------------------------------------------------------------
# Screen model (pyte)
# ---------------------------------------------------------------------------

class Screen:
    """A VT102/xterm character grid fed from the raw byte stream.

    `answer` is called with bytes the terminal must send back (device
    attributes, cursor position report) so full-screen programs that probe the
    terminal get a VT100-style reply.
    """

    def __init__(self, cols: int = 80, rows: int = 24, answer=None) -> None:
        if pyte is None:
            raise RuntimeError("pyte is not installed")
        outer = self

        class _Screen(pyte.Screen):
            def write_process_input(self, data: str) -> None:
                if outer.answer is not None:
                    outer.answer(data.encode("latin-1", "replace"))

        self.answer = answer
        self.cols, self.rows = cols, rows
        self.screen = _Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)

    def feed(self, data: bytes) -> None:
        self.stream.feed(data)

    def text(self) -> str:
        return "\n".join(line.rstrip() for line in self.screen.display).rstrip("\n")

    def cursor(self) -> tuple[int, int]:
        return self.screen.cursor.x, self.screen.cursor.y

    def reset(self) -> None:
        self.screen.reset()
