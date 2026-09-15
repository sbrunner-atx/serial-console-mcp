"""Icom CI-V helpers: build request frames and describe replies.

A CI-V frame is  FE FE <to> <from> <cmd> [<sub>] [<data>...] FD.  The rig's
address is per model (IC-7300 = 0x94, IC-705 = 0xA4, IC-7610 = 0x98, IC-9700 =
0xA2); the controller is conventionally 0xE0. Frequencies travel as BCD, least
significant byte first: 14.070000 MHz is 00 00 07 14 00.
"""

from __future__ import annotations

MODES = {
    0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY", 0x05: "FM",
    0x06: "WFM", 0x07: "CW-R", 0x08: "RTTY-R", 0x12: "PSK", 0x13: "PSK-R", 0x17: "DV",
}
COMMANDS = {
    0x00: "set frequency (transceive)", 0x01: "set mode (transceive)",
    0x02: "read band edges", 0x03: "read operating frequency", 0x04: "read operating mode",
    0x05: "set frequency", 0x06: "set mode", 0x07: "select VFO", 0x08: "select memory",
    0x0E: "scan", 0x0F: "split", 0x10: "tuning step", 0x11: "attenuator",
    0x13: "speech", 0x14: "set/read levels", 0x15: "read meters/status", 0x16: "set/read functions",
    0x17: "send CW", 0x18: "power on/off", 0x19: "read rig address", 0x1A: "misc/memory",
    0x1B: "tone", 0x1C: "PTT / tuner", 0x1E: "band stacking", 0x21: "RIT/XIT",
    0x25: "VFO frequency (selected/unselected)", 0x26: "VFO mode (selected/unselected)",
    0xFA: "NG (command rejected)", 0xFB: "OK",
}
# Commands that only read state: allowed in read-only mode.
READ_COMMANDS = {0x02, 0x03, 0x04, 0x19}


def _hexbytes(s: str) -> bytes:
    try:
        return bytes.fromhex(s.replace(" ", "").replace(":", ""))
    except ValueError as e:
        raise ValueError(f"not valid hex: {s!r}") from e


def build(command: str, data: str = "", rig: str = "94", controller: str = "E0") -> bytes:
    cmd = _hexbytes(command)
    if not cmd:
        raise ValueError("command is empty")
    return b"\xfe\xfe" + _hexbytes(rig) + _hexbytes(controller) + cmd + _hexbytes(data) + b"\xfd"


def freq_to_bcd(hz: int) -> bytes:
    digits = f"{hz:010d}"  # 10 digits, most significant first
    pairs = [digits[i:i + 2] for i in range(0, 10, 2)]  # 5 pairs, MSB first
    return bytes(int(p, 16) for p in reversed(pairs))


def bcd_to_freq(data: bytes) -> int:
    hz = 0
    for i, b in enumerate(data):
        hi, lo = b >> 4, b & 0x0F
        if hi > 9 or lo > 9:
            raise ValueError("not BCD")
        hz += (hi * 10 + lo) * (100 ** i)
    return hz


def split_frames(payload: bytes) -> list[bytes]:
    frames, i = [], 0
    while True:
        start = payload.find(b"\xfe\xfe", i)
        if start < 0:
            break
        end = payload.find(b"\xfd", start)
        if end < 0:
            frames.append(payload[start:])  # truncated tail
            break
        frames.append(payload[start:end + 1])
        i = end + 1
    return frames


def parse_frame(frame: bytes, controller: int = 0xE0) -> dict:
    d = {"hex": frame.hex(" "), "complete": frame.endswith(b"\xfd") and len(frame) >= 5}
    if len(frame) < 5:
        d["meaning"] = "truncated frame"
        return d
    to, frm = frame[2], frame[3]
    body = frame[4:-1] if d["complete"] else frame[4:]
    cmd = body[0] if body else None
    data = body[1:]
    d.update(to=f"{to:02X}", frm=f"{frm:02X}", cmd=f"{cmd:02X}" if cmd is not None else None,
             data=data.hex(" "))
    d["echo"] = frm == controller  # a frame FROM the controller is our own command coming back
    if cmd is None:
        d["meaning"] = "empty frame"
        return d
    label = COMMANDS.get(cmd, "command")
    meaning = label
    try:
        if cmd in (0x00, 0x03, 0x05) and len(data) in (4, 5):
            meaning = f"{label}: {bcd_to_freq(data) / 1e6:.6f} MHz"
        elif cmd == 0x25 and len(data) >= 6:
            which = "selected" if data[0] == 0 else "unselected"
            meaning = f"{label}: {which} VFO {bcd_to_freq(data[1:6]) / 1e6:.6f} MHz"
        elif cmd in (0x01, 0x04, 0x06) and len(data) >= 1:
            mode = MODES.get(data[0], f"mode 0x{data[0]:02X}")
            filt = f", filter {data[1]}" if len(data) > 1 else ""
            meaning = f"{label}: {mode}{filt}"
        elif cmd == 0x19 and len(data) >= 2:
            meaning = f"{label}: 0x{data[1]:02X}"
        elif cmd == 0x1C and len(data) >= 2:
            what = {0x00: "PTT", 0x01: "antenna tuner"}.get(data[0], f"sub 0x{data[0]:02X}")
            meaning = f"{what} {'on' if data[1] else 'off'}"
    except ValueError:
        meaning = f"{label}: data is not BCD"
    if d["echo"]:
        meaning += " (echo of our own command; the rig's reply follows)"
    d["meaning"] = meaning
    return d


def describe(payload: bytes, controller: str = "E0") -> str:
    ctl = int(controller, 16)
    frames = split_frames(payload)
    if not frames:
        return f"No CI-V frames (no FE FE preamble) in: {payload.hex(' ')}"
    out = []
    for i, f in enumerate(frames, 1):
        p = parse_frame(f, ctl)
        out.append(f"Frame {i}: {p['hex']}\n    to {p.get('to')} from {p.get('frm')} "
                   f"cmd {p.get('cmd')} data [{p.get('data', '')}] → {p['meaning']}")
    return "\n".join(out)
