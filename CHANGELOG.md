# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] - 2026-11-23

### Changed
- Package descriptions (README opening, PyPI summary, connector description,
  MCP Registry entry) name all three ways in: a serial cable, telnet or SSH.
  No code changes.

## [0.4.0] - 2026-11-23

### Added
- **Network consoles.** `connect` takes `transport` (`serial` by default,
  `telnet`, `ssh`, `tcp`, `rfc2217`) with `host` and `tcp_port`, or a URL such
  as `telnet://cs1:2003` or `ssh://admin@bench-pi`. Every tool works the same
  over every transport: the background reader, `read_until_prompt`, `expect`,
  `send_keys` and `screen`, capture, the transcript, read-only mode, idle
  auto-close and `status`.
  - **telnet**: a device's own telnet (a router's or switch's management
    address, a Linux box), console servers' reverse-telnet ports (Opengear,
    Cisco, Lantronix, Digi, Avocent) and ser2net. Negotiates BINARY, SGA, ECHO,
    terminal type and window size and refuses other options; IAC is escaped
    both ways and a CR is sent as CR NUL in NVT mode. TCP urgent data stays in
    the stream, so a telnet Synch (inetutils telnetd sends one before
    `login:`) leaves no stray bytes. `send_break` sends IAC BRK.
  - **rfc2217**: RFC 2217 COM-port control through pyserial, so baud,
    framing, DTR/RTS and BREAK reach the remote serial port.
  - **ssh**: a device's own SSH (router, switch, BMC, a bench Pi) or a
    console server's per-port SSH, through asyncssh (new `ssh` extra,
    included in the desktop connector). Uses `~/.ssh/config` (Host aliases,
    User, Port, IdentityFile, ProxyJump), the SSH agent and key files, or a
    password. The host key must be in `known_hosts`. A changed key is always
    refused, and host key checking is never turned off. `send_break` sends an
    SSH break request.
  - **tcp**: raw bytes, for ser2net raw ports and simple device servers.
  - `set_lines`, `pulse_line` and `send_break` say what a transport cannot do
    and what would work instead; `status` says which lines a transport has.
- **Named credentials.** Passwords never go through the chat or a tool
  argument. `serial-console-mcp credential set NAME [--username USER]
  [--keychain]` stores one from a terminal prompt in `credentials.json` (mode
  600, refused if others can read it), the macOS Keychain or the `keyring`
  package's store. `connect(credential=NAME)` answers a console server's
  `login:`/`Password:` prompts or offers the password to SSH; the new `expect`
  step `send_credential` sends a device's own password. Credentials are hidden
  from tool results and annotated captures. `credential list` and
  `credential remove` manage them.
- **One-off user name and password logins: `connect(ask=True)`.** A dialog
  on the user's own screen (macOS, Windows, or Linux with zenity/kdialog) asks
  for the password, and for the user name if it is not known. What they type
  goes straight to the server, is used for that connection only, and is never
  returned to the assistant, saved or captured. Over SSH, keys are tried
  first. On first contact the dialog shows the host key's fingerprint, and
  the key is saved to `known_hosts` only if the user clicks Trust. A key that
  differs from one on record is never offered. The new `expect` step
  `ask_password` does the same for a login prompt later in a session, such as
  a craft port's `login:`.
- **Credentials from environment variables:** `SERIAL_CONSOLE_PASSWORD_<NAME>`
  and `SERIAL_CONSOLE_USERNAME_<NAME>` back `credential="<name>"` and are
  checked before the file and keychain.
- `reconnect_last` and named connections remember network targets (transport,
  host, port, user name, credential name), never a secret.
- `SERIAL_CONSOLE_NETWORK_HOSTS` (the connector's "Network consoles allowed"
  setting) limits which hosts network consoles may reach; `none` turns them
  off. `SERIAL_CONSOLE_CREDENTIALS` points at another credentials file.
- Tests run local telnet, raw TCP, RFC 2217 and SSH servers, so the network
  paths are covered in CI without hardware.
- Field Guide chapter 07, "Network Consoles: Telnet, SSH, ser2net"; the
  `serial-console` skill covers network consoles and password logins, and the
  Junos and IOS skills cover reaching the same CLI over SSH or telnet.

### Changed
- The `expect` login example in the tool description, README and Field Guide
  sends the password with `send_credential` instead of typing it in the chat.
- **License.** From this release on, serial-console-mcp is licensed under the
  [PolyForm Noncommercial License 1.0.0](https://github.com/sbrunner-atx/serial-console-mcp/blob/main/LICENSE):
  free for personal use, hobby projects, amateur radio, education, and nonprofit
  and public organizations. Use by or for a business, including internal use,
  needs a commercial license; see
  [COMMERCIAL.md](https://github.com/sbrunner-atx/serial-console-mcp/blob/main/COMMERCIAL.md).
  Versions up to and including 0.3.3 were released under the MIT License and
  remain under the MIT License.
- Package metadata (`pyproject.toml`, `manifest.json`), README and Field Guide
  updated to match. The Field Guide has a new closing chapter, I, on commercial
  use and support.

## [0.3.3] - 2026-11-12

Package page only; no code or behavior changes.

### Changed
- The PyPI page links to the project page on ae5vg.com, the changelog and the issue tracker..
- Links in the README point to GitHub, so they also work on PyPI.
- More keywords and classifiers, so the package turns up in PyPI searches for AI assistants and ham radio.
- The release workflow also publishes `server.json` to the official MCP Registry once the PyPI upload is live, so the registry entry no longer falls behind PyPI.

## [0.3.2] - 2026-09-15

Says what else the server is for: any device on a serial port, not only
telecommunication equipment.

### Changed
- Descriptions now also say what else the server is for: any device on a serial
  port, in raw, ANSI or VT100 mode, such as IoT gateways, alarm and control
  panels, UPS and PDU menus, lab instruments and industrial controllers. README
  opening, PyPI summary and keywords, registry title, `serial-console` skill,
  `connect` tool description, and the Field Guide cover, chapter A, chapter C,
  a new "everything else" row in the device playbook, and the terminal modes
  table.

## [0.3.1] - 2026-09-15

Leads with what the server is for: AI configuring telecommunication equipment
through its console port. Adds the Junos and IOS operating skills, verified on a
Juniper EX2200, and fixes prompt matching after Junos redraws a line.

### Changed
- Registry title and description, PyPI summary, keywords and classifiers,
  README opening and Field Guide cover now lead with AI for the configuration of
  telecommunication equipment: Juniper and Cisco routers and switches, Icom CI-V
  and Kenwood, Yaesu and Elecraft CAT transceivers, GS-232 antenna rotators.

### Added
- Operating skills `junos-operating` and `ios-operating` (`skills/`): modes
  from the prompt, shell/CLI/configure transitions, commit and rollback or the
  IOS reload-timer discipline, everyday show commands, recovery. Field Guide
  chapter 06.
- Both skills checked against the real CLI. Junos shell transitions (`cli`,
  `exit`, `start shell`, `cli -c`) and every listed show command were run on
  the EX2200. Neither Junos nor IOS has `show system status`, and IOS has no
  `show interfaces brief` or `| last`; the skills now say what to use instead.
  The IOS skill had `show logging | last 50`, which is Junos syntax; fixed.
- Junos pager, `?` help and configuration mode (`configure exclusive`,
  `commit check`, `edit`/`up`/`top`, `run show`, `rollback 0`, `exit`) run on
  the EX2200 with nothing committed. The pager line is `---(more)---` or
  `---(more NN%)---`; the skill now matches the prefix and says to press `q`
  before typing, because keys at the pager are pager commands.

### Fixed
- Prompt matching in `ansi`, `vt100` and `xterm` mode sees the last line as the
  terminal shows it, up to the cursor. Junos redraws an edited line with a CR,
  padding spaces and backspaces after `?` and Ctrl-U, so the
  bytes ended in `\b` and `read_until_prompt` and `expect` timed out on any
  prompt ending in `$`, the `juniper-craft` default `[#>%] ?$` included (seen
  on the EX2200-C). A line that has ended, or that a bare CR has just rewound,
  is never taken for a prompt; `dumb` mode still matches the bytes exactly.
  Field Guide chapter 05; the `junos-operating` skill no longer needs a bare
  return after `?` or Ctrl-U.

### Verified
- Live session on 2026-09-15: the 0.3.0 PyPI package, launched via `uvx` as
  Claude Desktop does, drove a Juniper EX2200-C (Junos 15.1R6.7) console over
  a Prolific PL2303 adapter: `juniper-craft` preset, one `expect` sequence from
  shell to CLI through three `show` commands and back, annotated capture. The
  first hardware run; everything before it was the simulated port.
- `_hosttest/juniper_login_check.py`: interactive login check that asks for
  credentials with getpass.

## [0.3.0] - 2026-09-15

The terminal release: the VT100 / ANSI / xterm family, as an agent needs it.

### Added
- **Terminal modes** on `connect(terminal=...)` and in presets. `dumb` (default)
  passes raw bytes. `ansi` strips color and escape sequences as bytes arrive
  (chunk-safe) and renders CR overwrites, backspaces, erase-line and cursor
  moves, so shells, colored prompts and progress bars read cleanly and prompt
  matching works through the color codes. The console presets use it.
- **Screen model** (`terminal="xterm"` or `"vt100"`, `cols`/`rows`): a real
  character grid kept by `pyte`, for BIOS setup, RAID/BMC consoles, menu-driven
  switches, vi/top. The `screen` tool shows it with the cursor position; the
  terminal answers device-attribute and cursor-position queries as a VT100.
  Installed with the `[screen]` extra; the installers include it. Without it,
  `xterm` falls back to `ansi` with a note.
- **`send_keys`**: Ctrl-C and the other control keys, Esc, Tab, Enter, arrows,
  Home/End, Page Up/Down, Insert/Delete, F1..F12, single characters and
  `text:` runs, encoded as an xterm sends them. Interrupt a `ping`, complete a
  command, walk a menu. Read-only mode allows named keys, refuses literal text.
- Presets `xiegu-civ` (G90/X6100 speak CI-V, address 0x70; note on Baofeng and
  other handhelds having no CAT) and `screen-console` (115200 8N1, xterm 80x25).
- Transcript and reads render through the connection's terminal mode.

### Changed
- 30 tools (was 28), 12 presets (was 10), 73 tests.

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
- **Text CAT helpers.** `cat_build` and `cat_parse` for the Kenwood, Elecraft
  and Yaesu `;` protocols: frequency sets, rig ID to model, FA/FB, MD, IF
  decoding, and the `?; E; O;` error replies.
- **Icom CI-V helpers.** `civ_build`, `civ_parse` (frames, echo vs. reply,
  BCD frequency, mode, OK/NG, PTT) and `civ_freq`.
- **Rotator helpers.** `rotator_build` and `rotator_parse` for GS-232A/B:
  read, move, move with elevation, stop, jog, speed; `+0180`, `+0180+0045`
  and `AZ=180 EL=045` replies.
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
- 28 tools (was 11); 54 tests.

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
