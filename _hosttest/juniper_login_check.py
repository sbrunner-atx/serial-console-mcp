#!/usr/bin/env python3
"""Interactive live check against a Junos console: log in, run read-only
commands, log out. Run it yourself in a terminal; it asks for the username and
password with getpass, so they never pass through a chat or a log.

    uv run python _hosttest/juniper_login_check.py [/dev/cu.usbserial-XXXX]

Everything it sends is read-only (show ...) except the login itself and the
final 'exit'. Output is printed as the tools return it.
"""
from __future__ import annotations

import getpass
import sys
import time

from serial_console_mcp import server as m

port = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbserial-14310"
user = input("Junos username: ").strip()
password = getpass.getpass("Junos password (not echoed): ")

print(m.connect(port, preset="juniper-craft", name="ex2200"))
print(m.send_text("", clear_buffer_first=True))
print(m.read_until_prompt(r"(login: |[#>%] )$", regex=True, timeout=6))

steps = [
    m.ExpectStep(send=user, expect="assword:", timeout=8),
    m.ExpectStep(send=password, expect=r"[>%] $", regex=True, timeout=15),
    m.ExpectStep(send="set cli screen-length 0", expect=r"> $", regex=True, timeout=8),
    m.ExpectStep(send="show version", expect=r"> $", regex=True, timeout=15, clear_first=True),
    m.ExpectStep(send="show chassis hardware", expect=r"> $", regex=True, timeout=15, clear_first=True),
    m.ExpectStep(send="show interfaces terse", expect=r"> $", regex=True, timeout=20, clear_first=True),
]
out = m.expect(steps)
print(out.replace(password, "********"))

print(m.status())
print(m.send_text("exit"))
time.sleep(1)
print(m.read_available(2))
print(m.disconnect())
