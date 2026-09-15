"""Minimal FastMCP stand-in for host-side hardware testing only.

Lets serial_console_mcp import and its @mcp.tool()-decorated functions be called
directly, without the real MCP SDK (which needs Python 3.10+). This does NOT
exercise the MCP transport/schema layer — that is verified separately against the
real SDK. It only makes the serial logic runnable on the host's Python 3.9.
"""


class FastMCP:
    def __init__(self, name):
        self.name = name

    def tool(self, *args, **kwargs):
        def decorator(fn):
            return fn  # return the function unchanged so it stays directly callable
        return decorator

    def run(self, *args, **kwargs):
        raise SystemExit("FastMCP stub: run() is not supported in host test mode")
