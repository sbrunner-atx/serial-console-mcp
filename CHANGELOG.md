# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-14

First public, experimental release.

### Added
- Eleven tools: `list_serial_ports`, `connect`, `reconnect_last`, `send_text`,
  `send_hex`, `read_until_prompt`, `read_available`, `query_text`, `clear_buffer`,
  `status`, `disconnect`.
- Interactive-console model: a background reader drains the port into a buffer
  from the moment it opens; reads match a literal or regex prompt and push
  leftover bytes back, or return once the line goes idle.
- Every Quick-Connect setting on `connect`: baud, data bits, parity, stop bits,
  RTS/CTS and XON/XOFF. Default 9600 8N1, no flow control. Settings are
  remembered for `reconnect_last`.
- Console script `serial-console-mcp`; `serial-console-mcp configure` writes the
  Claude Desktop config entry (merging, with backup) and `--remove` undoes it.
- Windows (Inno Setup) and macOS (.pkg) installers built by CI on `v*` tags.
- The serial-console-mcp Field Guide (`docs/`).
- 37-case test suite on a simulated serial port; CI on Linux, macOS, Windows.

### Notes
- The MCP SDK is pinned `<2`; the 2.x line removed `mcp.server.fastmcp`.
- A dead reader (unplugged adapter) is reported by every tool instead of a
  silent timeout; the receive buffer is capped at 4 MB.
- Verified with the simulated port and a live MCP stdio handshake. A run against
  physical hardware is the next milestone.
