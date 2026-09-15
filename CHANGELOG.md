# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-09-15

The "shack" release: several ports at once, control lines, presets, expect
sequences, capture, and diagnostics. Existing single-port conversations keep
working unchanged; every new argument has a default.

### Added
- **Several ports at once.** `connect(name="rig")`, `connect(name="rotator")`;
  every tool takes `connection=` and defaults to the most recently used one.
  `status` lists all; `disconnect(all_connections=True)`.
- **Device presets.** `connect(preset="kenwood-cat")` and friends load the
  family's usual baud, framing, flow control, line ending and prompt; explicit
  arguments override. `list_presets` shows them: cisco-console, juniper-craft,
  linux-console, kenwood-cat, elecraft-cat, yaesu-cat, icom-civ, yaesu-rotator,
  arduino, nmea-gps.
- **Per-connection defaults.** `connect(line_ending=..., prompt=...)` sets what
  `send_text`, `query_text` and `read_until_prompt` use when not told otherwise.
  Say "this device wants LF" once.
- **Control lines.** `set_lines(dtr, rts)`, `pulse_line("DTR", ms)`,
  `send_break(ms)`; `status` reports CTS/DSR/CD/RI/DTR/RTS. Key a rig's PTT,
  reset an Arduino, interrupt a boot.
- **`expect`.** A list of send-and-wait steps in one call, for login flows and
  scripted command sequences, with `auto_reply` for pagers (`--More--`).
  `read_until_prompt` accepts `auto_reply` too.
- **Capture.** `capture_start(path, format="raw"|"annotated")` /
  `capture_stop`. Raw is a terminal log; annotated is timestamped TX/RX lines.
- **Transcript.** `get_transcript` returns the rolling last 256 KB received on
  a connection regardless of what reads consumed; also the MCP resource
  `serial://transcript/{name}`.
- **Diagnostics.** `port_in_use_by(port)` names the process holding a port
  (lsof); the busy-port hint on `connect` includes it. `detect_baud(port)`
  tries common rates and ranks them by how readable the reply is.
- **Icom CI-V helpers.** `civ_build`, `civ_parse` (frames, echo vs. reply,
  BCD frequency, mode, OK/NG, PTT) and `civ_freq`.
- **Read-only mode.** `SERIAL_CONSOLE_READ_ONLY=1` refuses writes except
  read-style commands (show/display/get, two-letter CAT reads, CI-V read
  frames) and refuses control-line changes; `SERIAL_CONSOLE_ALLOW` overrides
  the allowlist.
- **Idle auto-close.** `SERIAL_CONSOLE_IDLE_MINUTES=15` closes ports left
  idle, so a forgotten connection stops blocking WSJT-X. Off by default.
- The `serial-console` operating skill (`skills/serial-console/SKILL.md`).
- `list_serial_ports` marks ports already open here.

### Changed
- `read_until_prompt` with no prompt anywhere now falls back to "the received
  text ends in #, >, $ or %" instead of a bare literal `#`.
- `reconnect_last(name)` reopens a named remembered connection; the remembered
  file now holds every connection by name (0.1.x files are read transparently).
- The unused `timeout` argument of `connect` is gone.
- 24 tools (was 11); 50 tests.

## [0.1.1] - 2026-09-15

### Added
- `server.json` and the README `mcp-name` marker so the package can be listed
  in the official MCP Registry as `io.github.sbrunner-atx/serial-console-mcp`.

### Changed
- Release workflow publishes with a repository token secret and can be run by
  hand; Trusted Publishing wiring kept.
- Installer builds install the `[freeze]` extra (mcp[cli] is needed by
  `--collect-all mcp`); Inno `[UninstallRun]` no longer uses a flag it rejects.

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
