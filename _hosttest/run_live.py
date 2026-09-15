#!/usr/bin/env python3
"""Drive serial_console_mcp against a REAL serial port, one JSON 'program' per run.

Usage:  run_live.py <steps.json>
Runs every step in a single process so the open port + reader thread persist.
Each step is a dict:
  {"op":"connect","port":"/dev/cu.usbserial-XXX","baud":9600}
  {"op":"send","data":"show version","ending":"LF","clear":true}
  {"op":"send_cr"}                              # write a bare carriage return
  {"op":"read_until","prompt":"> ","timeout":8}
  {"op":"read_avail","timeout":2}
  {"op":"clear"}
  {"op":"status"}
  {"op":"disconnect"}
Prints a labeled result per step. Read-only by design; it sends only what's listed.
"""
import json
import sys

_here = __file__.rsplit("/", 1)[0]
sys.path.insert(0, _here + "/mcpshim")   # stub 'mcp' package
sys.path.insert(0, _here + "/../src")     # the serial_console_mcp package
from serial_console_mcp import server as m  # noqa: E402


def run(steps):
    for i, s in enumerate(steps, 1):
        op = s.get("op")
        print(f"\n=== step {i}: {op} {({k: v for k, v in s.items() if k != 'op'})} ===")
        if op == "connect":
            print(m.connect(s["port"], s.get("baud", 9600),
                            timeout=s.get("timeout", 1.0)))
        elif op == "send":
            print(m.send_text(s["data"], s.get("ending", "CR"),
                              clear_buffer_first=s.get("clear", False)))
        elif op == "send_cr":
            print(m.send_text("", "CR"))
        elif op == "read_until":
            print(m.read_until_prompt(s.get("prompt", "#"),
                                      s.get("timeout", 10.0),
                                      regex=s.get("regex", False)))
        elif op == "read_avail":
            print(m.read_available(s.get("timeout", 1.0)))
        elif op == "clear":
            print(m.clear_buffer())
        elif op == "status":
            print(m.status())
        elif op == "disconnect":
            print(m.disconnect())
        else:
            print("UNKNOWN OP")


if __name__ == "__main__":
    with open(sys.argv[1]) as f:
        run(json.load(f))
    print("\n[done]")
