"""Device presets: the usual factory settings for common families of serial gear.

A preset fills in whatever `connect` was not told explicitly: line settings, flow
control, the line ending the device expects, and the prompt that ends a reply.
They are starting points. The device's manual or menu wins whenever they differ.
"""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "cisco-console": {
        "family": "Network console",
        "baud": 9600, "bytesize": 8, "parity": "N", "stopbits": 1,
        "line_ending": "CR", "prompt": r"[#>] ?$", "prompt_regex": True,
        "read": "read_until_prompt",
        "notes": "IOS/IOS-XE/NX-OS console. Send a bare return to draw a prompt. Run "
                 "'terminal length 0' first or --More-- becomes your prompt (or pass "
                 "auto_reply={'--More--': ' '}).",
    },
    "juniper-craft": {
        "family": "Network console",
        "baud": 9600, "bytesize": 8, "parity": "N", "stopbits": 1,
        "line_ending": "CR", "prompt": r"[#>%] ?$", "prompt_regex": True,
        "read": "read_until_prompt",
        "notes": "Junos craft/console port. '> ' operational, '# ' configure, '% ' shell. "
                 "Run 'set cli screen-length 0' to stop paging.",
    },
    "linux-console": {
        "family": "Shell console",
        "baud": 115200, "bytesize": 8, "parity": "N", "stopbits": 1,
        "line_ending": "CR", "prompt": r"[$#] $", "prompt_regex": True,
        "read": "read_until_prompt",
        "notes": "Serial getty on a Linux board, router, or SBC. U-Boot autoboot can be "
                 "caught with expect() and a short timeout.",
    },
    "kenwood-cat": {
        "family": "Text CAT radio",
        "baud": 9600, "bytesize": 8, "parity": "N", "stopbits": 1,
        "line_ending": "NONE", "prompt": ";", "prompt_regex": False,
        "read": "query_text",
        "notes": "Kenwood CAT (TS-590/890/2000...). Commands and replies end in ';' with no "
                 "CR: query_text('ID') -> 'ID021;'. Baud is a rig menu item; newer rigs "
                 "default to 115200 over USB. 'AI0;' silences auto-information.",
    },
    "elecraft-cat": {
        "family": "Text CAT radio",
        "baud": 38400, "bytesize": 8, "parity": "N", "stopbits": 1,
        "line_ending": "NONE", "prompt": ";", "prompt_regex": False,
        "read": "query_text",
        "notes": "Elecraft K3/K4/KX2/KX3, Kenwood-style ';' protocol. The rig does not echo. "
                 "Check the CONFIG:RS232 menu for the rate.",
    },
    "yaesu-cat": {
        "family": "Text CAT radio",
        "baud": 38400, "bytesize": 8, "parity": "N", "stopbits": 2,
        "line_ending": "NONE", "prompt": ";", "prompt_regex": False,
        "read": "query_text",
        "notes": "Yaesu FT-991/FTDX ';'-terminated CAT. Factory rate varies (4800 on some "
                 "FT-991A, 38400 on FTDX10/101); some models want 2 stop bits. Check the "
                 "CAT RATE and CAT STOP BIT menu items.",
    },
    "icom-civ": {
        "family": "Binary CAT radio",
        "baud": 19200, "bytesize": 8, "parity": "N", "stopbits": 1,
        "line_ending": "NONE", "prompt": None, "prompt_regex": False,
        "read": "read_available",
        "notes": "Icom CI-V (IC-7300/7610/705/9700...). Binary frames FE FE <rig> E0 <cmd> "
                 "... FD; build them with civ_build and decode replies with civ_parse. "
                 "Rate is the rig's CI-V Baud Rate menu (USB default 19200; the CI-V jack "
                 "is often 9600). Never enable XON/XOFF on a binary protocol.",
    },
    "yaesu-rotator": {
        "family": "Rotator",
        "baud": 9600, "bytesize": 8, "parity": "N", "stopbits": 1,
        "line_ending": "CR", "prompt": "\r", "prompt_regex": False,
        "read": "read_until_prompt",
        "notes": "Yaesu GS-232A/B protocol (also many third-party controllers). 'C' returns "
                 "'+0aaa' azimuth, 'Maaa' moves. GS-232A units may be 4800 baud.",
    },
    "arduino": {
        "family": "Microcontroller",
        "baud": 9600, "bytesize": 8, "parity": "N", "stopbits": 1,
        "line_ending": "LF", "prompt": None, "prompt_regex": False,
        "read": "read_available",
        "notes": "Arduino/ESP32 sketches usually print at 9600 or 115200 with LF. Opening "
                 "the port toggles DTR and resets most boards: expect a boot banner, wait a "
                 "second before sending.",
    },
    "nmea-gps": {
        "family": "Sensor / GPS",
        "baud": 4800, "bytesize": 8, "parity": "N", "stopbits": 1,
        "line_ending": "CRLF", "prompt": "\n", "prompt_regex": False,
        "read": "read_until_prompt",
        "notes": "NMEA-0183 talkers stream '$GPRMC,...*hh' sentences once a second. The "
                 "standard rate is 4800; USB pucks are often 9600 or 38400. Read one "
                 "sentence with read_until_prompt('\\n').",
    },
}

_PRESET_KEYS = ("baud", "bytesize", "parity", "stopbits", "rtscts", "xonxoff",
                "line_ending", "prompt", "prompt_regex")


def describe() -> str:
    """One line per preset, for the list_presets tool."""
    lines = ["Presets (values are common factory defaults; the device's menu wins):"]
    for name, p in PRESETS.items():
        flags = (("RTS/CTS", p.get("rtscts")), ("XON/XOFF", p.get("xonxoff")))
        flow = "+".join(n for n, on in flags if on) or "none"
        prompt = p.get("prompt")
        if prompt is None:
            pd = "none (idle read)"
        else:
            pd = f"{prompt!r}{' (regex)' if p.get('prompt_regex') else ''}"
        framing = f"{p['bytesize']}{p['parity']}{p['stopbits']}"
        lines.append(
            f"  • {name:<15} {p['family']:<18} {p['baud']} {framing}, "
            f"flow {flow}, line ending {p['line_ending']}, prompt {pd}"
        )
        lines.append(f"      {p['notes']}")
    return "\n".join(lines)
