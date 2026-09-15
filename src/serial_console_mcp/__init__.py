"""serial-console-mcp — let an MCP client such as Claude Desktop drive a serial console.

A generic serial-port server: raw read/write plus ASCII and hex helpers, built
around an interactive-console model (a background reader thread and
read-until-prompt) so it behaves like ``minicom``/``expect`` rather than a naive
send-then-poll. Network craft ports, CAT radios, Icom CI-V, rotators,
microcontrollers: anything on an RS-232 or USB-to-serial cable.
"""

__version__ = "0.3.2"
