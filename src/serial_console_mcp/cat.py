"""Text CAT helpers for the ';'-terminated protocols: Kenwood, Elecraft, Yaesu.

Commands are two letters plus optional digits and end in ';' with no CR:
``FA;`` asks for VFO A, ``FA00014074000;`` sets it. Replies use the same shape.
Kenwood and Elecraft carry frequencies as 11 digits of Hz; Yaesu's newer rigs
(FT-991A, FTDX10, FT-710, FTDX101) use 9. Mode codes differ per family, so the
helpers take a ``flavor``.
"""

from __future__ import annotations

import re

FLAVORS = ("kenwood", "elecraft", "yaesu")
FREQ_DIGITS = {"kenwood": 11, "elecraft": 11, "yaesu": 9}

MODES = {
    "kenwood": {"1": "LSB", "2": "USB", "3": "CW", "4": "FM", "5": "AM", "6": "FSK (RTTY)",
                "7": "CW-R", "8": "DATA", "9": "FSK-R"},
    "elecraft": {"1": "LSB", "2": "USB", "3": "CW", "4": "FM", "5": "AM", "6": "DATA",
                 "7": "CW-R", "9": "DATA-R"},
    "yaesu": {"1": "LSB", "2": "USB", "3": "CW-U", "4": "FM", "5": "AM", "6": "RTTY-L",
              "7": "CW-L", "8": "DATA-L", "9": "RTTY-U", "A": "DATA-FM", "B": "FM-N",
              "C": "DATA-U", "D": "AM-N", "E": "PSK", "F": "DATA-FM-N"},
}

# Rig IDs as answered to "ID;". Kenwood/Elecraft use 3 digits, Yaesu 4.
RIG_IDS = {
    "015": "Kenwood TS-870S",
    "017": "Kenwood TS-570 (also the answer from Elecraft K2/K3/KX2/KX3/K4)",
    "018": "Kenwood TS-570", "019": "Kenwood TS-2000", "020": "Kenwood TS-480",
    "021": "Kenwood TS-590S", "022": "Kenwood TS-990S", "023": "Kenwood TS-590SG",
    "024": "Kenwood TS-890S",
    "0240": "Yaesu FT-450", "0241": "Yaesu FT-450D", "0251": "Yaesu FT-2000",
    "0252": "Yaesu FT-2000D", "0310": "Yaesu FT-950", "0362": "Yaesu FTDX5000",
    "0460": "Yaesu FTDX3000", "0570": "Yaesu FT-991A", "0581": "Yaesu FTDX1200",
    "0650": "Yaesu FT-891", "0670": "Yaesu FT-991", "0681": "Yaesu FTDX101D",
    "0682": "Yaesu FTDX101MP", "0761": "Yaesu FTDX10", "0800": "Yaesu FT-710",
}

COMMANDS = {
    "ID": "rig identification", "FA": "VFO A frequency", "FB": "VFO B frequency",
    "FC": "sub receiver frequency", "MD": "operating mode", "IF": "transceiver status",
    "PS": "power on/off", "TX": "transmit", "RX": "receive", "AI": "auto information",
    "SM": "S-meter", "PC": "output power", "AG": "AF gain", "RG": "RF gain",
    "SQ": "squelch", "KS": "keyer speed", "FR": "receive VFO", "FT": "transmit VFO",
    "SP": "split", "NB": "noise blanker", "NR": "noise reduction", "PA": "preamp",
    "RA": "attenuator", "VX": "VOX", "RT": "RIT", "XT": "XIT", "RC": "clear RIT",
    "BU": "band up", "BD": "band down", "UP": "up", "DN": "down", "KY": "send CW text",
    "EX": "menu/extended", "OM": "option/model (Elecraft)", "K3": "K3 extended mode",
    "K2": "K2 extended mode", "AN": "antenna", "FW": "filter bandwidth", "BW": "bandwidth",
    "SH": "high cut", "SL": "low cut", "VS": "VFO select", "SW": "VFO A/B swap",
}

_TOKEN = re.compile(r"([A-Z]{2}|K3|K2)([^;]*);")


def build(command: str, value: str = "", frequency_mhz: float | None = None,
          flavor: str = "kenwood") -> str:
    """Return the wire string, e.g. build("FA", frequency_mhz=14.074) -> 'FA00014074000;'."""
    cmd = command.strip().upper().rstrip(";")
    if not re.fullmatch(r"[A-Z0-9]{2,3}", cmd):
        raise ValueError(f"not a CAT command: {command!r}")
    if flavor not in FLAVORS:
        raise ValueError(f"flavor must be one of {FLAVORS}")
    if frequency_mhz is not None:
        hz = int(round(frequency_mhz * 1_000_000))
        return f"{cmd}{hz:0{FREQ_DIGITS[flavor]}d};"
    return f"{cmd}{value};"


def _describe(cmd: str, arg: str, flavor: str) -> str:
    label = COMMANDS.get(cmd, "command")
    if cmd in ("FA", "FB", "FC") and arg.isdigit():
        return f"{label}: {int(arg) / 1e6:.6f} MHz"
    if cmd == "ID":
        model = RIG_IDS.get(arg, f"id {arg} (not in the table; check the CAT manual)")
        return f"{label}: {model}"
    if cmd == "MD":
        code = arg[-1:] if arg else ""
        return f"{label}: {MODES[flavor].get(code, 'code ' + arg)}"
    if cmd == "PS":
        return f"{label}: {'on' if arg == '1' else 'off' if arg == '0' else arg}"
    if cmd == "AI":
        return f"{label}: {'off' if arg == '0' else 'on (' + arg + ')'}"
    if cmd in ("TX", "RX"):
        return f"{label}{': ' + arg if arg else ''}"
    if cmd == "SM" and arg[-4:].isdigit():
        return f"{label}: {int(arg[-4:])} (raw meter units)"
    if cmd == "IF":
        return _describe_if(arg, flavor)
    return f"{label}{': ' + arg if arg else ''}"


def _describe_if(arg: str, flavor: str) -> str:
    if flavor == "yaesu":
        m = re.match(r"\d{3}(\d{9})", arg)
        if m:
            return (f"transceiver status: {int(m.group(1)) / 1e6:.6f} MHz "
                    f"(Yaesu IF; other fields not decoded)")
        return f"transceiver status: {arg}"
    if len(arg) < 35 or not arg[:11].isdigit():
        return f"transceiver status: {arg} (unexpected length)"
    freq = int(arg[:11]) / 1e6
    rit = arg[16:21]
    tx = arg[26] == "1"
    mode = MODES[flavor].get(arg[27], "code " + arg[27])
    vfo = {"0": "VFO A", "1": "VFO B", "2": "memory"}.get(arg[28], "vfo " + arg[28])
    split = "split on" if arg[30] == "1" else "simplex"
    return (f"transceiver status: {freq:.6f} MHz, {mode}, {vfo}, {split}, "
            f"{'TRANSMITTING' if tx else 'receiving'}, RIT {rit}")


def describe(reply: str, flavor: str = "kenwood") -> str:
    """Decode one or more ';'-terminated replies."""
    if flavor not in FLAVORS:
        return f"flavor must be one of {FLAVORS}"
    text = reply.strip()
    if not text:
        return "Empty reply."
    out = []
    for m in _TOKEN.finditer(text):
        out.append(f"{m.group(0)} → {_describe(m.group(1), m.group(2), flavor)}")
    for bare in re.findall(r"(?:^|;)\s*([?EO]);", ";" + text):
        out.append({"?": "?; → the rig did not understand the command, or is busy "
                         "(Kenwood: try again)",
                    "E": "E; → communication error (framing/parity: check the baud rate)",
                    "O": "O; → the rig's input buffer overflowed"}[bare])
    if not out:
        return f"No ';'-terminated CAT replies in: {text!r}"
    return "\n".join(out)
