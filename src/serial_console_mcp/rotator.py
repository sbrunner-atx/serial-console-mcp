"""Yaesu GS-232A/B rotator protocol helpers (also spoken by many third-party controllers).

Commands are short letters, sent with CR: ``C`` reads azimuth, ``C2`` azimuth
and elevation, ``M180`` turns to 180 degrees, ``W180 045`` sets azimuth and
elevation, ``S`` stops everything. Replies look like ``+0180`` (GS-232A) or
``AZ=180 EL=045`` (GS-232B).
"""

from __future__ import annotations

import re

ACTIONS = {
    "azimuth": "C", "position": "C2", "elevation": "B",
    "stop": "S", "stop_azimuth": "A", "stop_elevation": "E",
    "left": "L", "right": "R", "up": "U", "down": "D",
    "move": None, "move_azel": None, "speed": None, "help": "H",
}


def build(action: str, azimuth: int | None = None, elevation: int | None = None,
          speed: int | None = None) -> str:
    act = action.strip().lower()
    if act not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; one of {', '.join(ACTIONS)}")
    if act == "move":
        if azimuth is None:
            raise ValueError("move needs azimuth")
        if not 0 <= azimuth <= 450:
            raise ValueError("azimuth must be 0..450")
        return f"M{int(azimuth):03d}"
    if act == "move_azel":
        if azimuth is None or elevation is None:
            raise ValueError("move_azel needs azimuth and elevation")
        if not 0 <= azimuth <= 450 or not 0 <= elevation <= 180:
            raise ValueError("azimuth 0..450, elevation 0..180")
        return f"W{int(azimuth):03d} {int(elevation):03d}"
    if act == "speed":
        if speed is None or not 1 <= speed <= 4:
            raise ValueError("speed must be 1..4")
        return f"X{speed}"
    return ACTIONS[act]


def describe(reply: str) -> str:
    text = reply.strip()
    if not text:
        return "Empty reply."
    out = []
    for az, el in re.findall(r"AZ=(\d{3})\s*EL=(\d{3})", text):
        out.append(f"azimuth {int(az)}°, elevation {int(el)}° (GS-232B)")
    if not out:
        for az in re.findall(r"AZ=(\d{3})", text):
            out.append(f"azimuth {int(az)}° (GS-232B)")
    pairs = re.findall(r"\+0(\d{3})\+0(\d{3})", text)
    if pairs and not out:
        for az, el in pairs:
            out.append(f"azimuth {int(az)}°, elevation {int(el)}° (GS-232A)")
    if not out:
        for az in re.findall(r"\+0(\d{3})", text):
            out.append(f"azimuth {int(az)}° (GS-232A)")
    if "?>" in text:
        out.append("?> → the controller rejected the command (check the letter, or the range)")
    if not out:
        return f"No GS-232 position in: {text!r}"
    return "\n".join(out)
