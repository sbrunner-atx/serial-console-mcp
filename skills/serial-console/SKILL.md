---
name: serial-console
description: >
  Operating discipline for driving serial devices through serial-console-mcp:
  network craft/console ports (Juniper, Cisco, Linux), text CAT radios
  (Kenwood, Elecraft, Yaesu), Icom CI-V, rotators, microcontrollers, IoT
  gateways, alarm and control panels, instruments, and anything else with a
  serial port, whether it speaks raw bytes, ANSI or a VT100 screen, on a local
  cable or over the network (console servers, ser2net, RFC 2217, SSH). Use
  whenever the user wants to connect to, read from, or command anything on a
  serial cable or a network console: picking settings, sending with the right
  line ending, reading until the prompt instead of guessing, logging in without
  ever handling a password, and handing the port back cleanly.
---

# Operating a serial console through serial-console-mcp

## The model you are working with

Every open port has a background reader that drains bytes into a buffer from
the moment it opens. Sending never reads. Reading takes from the buffer until
a prompt appears (`read_until_prompt`, `expect`, `query_text` with a prompt) or
until the line goes quiet (`read_available`, `query_text` without one). Leftover
bytes after a matched prompt stay buffered for the next read. Nothing is lost
between calls; `get_transcript` re-reads what earlier calls already returned.

## The tools, by job

| Job | Tools |
| --- | --- |
| Find and open | `list_serial_ports`, `list_presets`, `connect`, `reconnect_last`, `disconnect`, `status` |
| Send | `send_text`, `send_hex` |
| Read | `read_until_prompt`, `read_available`, `query_text`, `clear_buffer` |
| Script | `expect` (steps + `auto_reply`; `send_credential` for passwords) |
| Keys and screens | `send_keys` (Ctrl-C, Esc, Tab, arrows, F-keys), `screen` (xterm/vt100 grid) |
| Lines | `set_lines`, `pulse_line`, `send_break` |
| Record | `capture_start`, `capture_stop`, `get_transcript` (also resource `serial://transcript/{name}`) |
| Diagnose | `port_in_use_by`, `detect_baud` |
| Text CAT (Kenwood, Elecraft, Yaesu) | `cat_build`, `cat_parse` |
| Icom CI-V | `civ_build`, `civ_parse`, `civ_freq` |
| Rotator (GS-232) | `rotator_build`, `rotator_parse` |

For a Juniper, also load `junos-operating`; for a Cisco or IOS-like CLI,
`ios-operating`. They carry the mode model and the commit/save discipline.

## The six rules

1. **Sending never reads.** After `send_text` or `send_hex`, call a read tool.
   Log in with `expect`: send, wait for `Password:`, `send_credential`, wait for
   the shell.
2. **Name the prompt with its trailing space.** `"# "`, `"> "`, `"login: "`.
   The match runs over everything received, including the echo of your own
   command, so a bare `#` matches inside a banner. Hostname prompts change after
   `configure`; use a regex like `[\w.-]+[#>] ?$`. Set the prompt once on
   `connect` (or via a preset) and every read uses it.
3. **Clear before a command whose reply must be clean.** `clear_buffer_first`
   on send, or `query_text`, discards syslog noise and stale output.
4. **Prompts for CLIs, idle reads for everything else.** Routers and shells:
   `read_until_prompt`. Kenwood-style CAT: the prompt is `;`, and `cat_parse`
   turns the reply into a model, frequency or mode. Icom CI-V and other binary
   protocols: `send_hex`, then `read_available`, then `civ_parse`. Rotators:
   `rotator_build` for the command, `rotator_parse` for the position; a move
   replies nothing, so read the position afterwards to confirm.
5. **Garbage means baud, silence means line ending.** `�` and box characters:
   run `detect_baud`. Echo but no reply: the device wants CR instead of LF or
   the reverse. Nothing at all: send a bare return to draw a prompt, then check
   the cable and `port_in_use_by`.
6. **Disconnect before handing the port to anything else.** One process per
   port, and one session per console server port. Also `capture_stop` before
   you disconnect if the user wants the file.

## Network consoles

`connect` reaches consoles over the network with `transport` and `host`
(plus `tcp_port`), or one URL as `port`. Telnet and SSH usually go straight to
the device: a Juniper or Cisco management address, a Linux box, a BMC. They
reach the same CLI as its console port. A console server port is the other
case.

| Transport | Use it for | Lines |
| --- | --- | --- |
| `telnet` | A device's own telnet; console server reverse-telnet ports (Opengear, Cisco 2000+line, Lantronix); ser2net telnet | BREAK only |
| `ssh` | A device's own SSH (router, switch, bench Pi, BMC); a console server's per-port SSH; `~/.ssh/config` aliases work | BREAK only |
| `rfc2217` | ser2net or a device server with RFC 2217: baud, DTR/RTS and BREAK reach the remote port | All |
| `tcp` | ser2net raw ports and simple device servers | None |

- Once open, everything works as on a local port. Baud and framing apply only
  to serial and `rfc2217`; telnet and SSH to a device have none, and through a
  console server its port settings decide.
- A session to the device itself never reaches the boot loader, ROMMON or
  password recovery, and BREAK there means whatever the device's server makes
  of it (usually nothing). Those need the serial console.
- If a tool says a transport cannot do something (`set_lines` over telnet),
  tell the user what would work: the serial console, `rfc2217`, or the console
  server's port menu.
- A dropped network console says the connection stopped. Run `disconnect`, then
  `reconnect_last`. A console server port usually takes one session at a time.

## Passwords: by name only

Never ask for a password in the chat, never put one in `send`, `send_text`,
`query_text` or a URL, and never repeat one the user pastes. If the user offers
to tell you a password, decline and use one of these instead. The user name is
not a secret and can be passed as `username`.

- **One-off login (the usual case for an ad-hoc device):** `connect(...,
  ask=True)`. The user types the password, and the user name if needed, into a
  dialog on their own screen. SSH keys are tried first. On first contact the
  dialog also shows the SSH host key fingerprint, and the key is saved only if
  the user clicks Trust. Tell the user a dialog is coming before you call it.
  A cancelled dialog means stop; don't retry on your own.
- **Stored by name:** the user runs `serial-console-mcp credential set NAME
  --username USER` in a terminal, or sets `SERIAL_CONSOLE_PASSWORD_<NAME>` in
  the server's environment. Then:
- `connect(..., credential="NAME")` answers the `login:` and `Password:`
  prompts right after connecting (the device's own over telnet, or a console
  server's), or offers the password to SSH.
- `expect([... {"send_credential": "NAME", "expect": "> "}])` sends a device's
  own password at its prompt.
- If the result says no credential has that name, give the user the command
  above to run and wait for them.

SSH keys and trust come from the user's own setup: the SSH agent, key files
and `~/.ssh/config`. If the host key could not be verified against
`known_hosts`, the user decides: connect again with `ask=True` so they can
compare the fingerprint in the dialog, or have them run `ssh host` once in a
terminal. If the result says the key is NOT the one on record, stop and tell
the user; never offer to remove the old key, and never suggest turning host
key checking off.

## Choosing settings

- Default is 9600 8N1, no flow control, CR. Say only what differs.
- No preset for the device (alarm panel, UPS, instrument, IoT gateway)? Start
  at 9600 8N1, run `detect_baud` if it stays silent, and pick the terminal mode
  by what it prints: `dumb` for plain text or binary, `ansi` for colours and
  cursor codes, `xterm` with `screen` for a full-screen menu. For anything that
  can arm, disarm, unlock or switch power, use read-only mode and confirm each
  command with the user.
- `list_presets` has the usual settings per family; `connect(preset=...)`
  loads them and explicit arguments override. Presets set the line ending and
  prompt too, so a `kenwood-cat` connection needs only `query_text("ID")`.
- Leave RTS/CTS and XON/XOFF off unless the manual says so. Never XON/XOFF on
  a binary protocol.
- Several ports can be open at once. Give each a `name` ("rig", "rotator") and
  pass `connection=` when it is not the most recently used one. `status` shows
  all of them and which is current.

## Terminals

- `dumb` is the default and right for CAT, CI-V, rotators and most CLIs.
- Shells and coloured prompts: `terminal="ansi"` (the console presets set it).
  Escape sequences are stripped as they arrive, so prompts match through colour
  codes and progress bars render as their final line.
- Full-screen programs (BIOS setup, RAID/BMC consoles, menu switches, vi, top):
  `terminal="xterm"` or the `screen-console` preset, then loop `send_keys` and
  `screen`. Read the screen after every keypress; never assume a menu moved.
- To interrupt anything: `send_keys(["ctrl-c"])`, then read. Never send Ctrl-C
  as text.

## Long output and paging

Turn paging off once (`terminal length 0`, `set cli screen-length 0`), or pass
`auto_reply={"--More--": " "}` to `read_until_prompt` or `expect`. Use a longer
`timeout` for a full config dump; the reader captures everything regardless.

## Control lines

`pulse_line` and `set_lines` drive DTR and RTS: reset an Arduino (DTR low for
100 ms), or key a rig whose PTT is wired to RTS. Keying a transmitter is a
transmission: confirm with the user, keep pulses short, and never leave PTT
asserted with `set_lines` unless the user asked for that explicitly.
`send_break` interrupts boot on some consoles.

## Safety

The server sends exactly what you ask. Claude Desktop shows each call for
approval; the user is the operator. Prefer read-only commands first (`show`,
`ID;`, CI-V `03`). Commands that change configuration, reboot, or transmit
need the user's explicit intent in the conversation. If `SERIAL_CONSOLE_READ_ONLY`
is on, writes outside the allowlist are refused; say so and stop rather than
working around it.

## Capture

`capture_start` writes the session to a file (`raw` for a terminal log,
`annotated` for timestamped TX/RX lines), `capture_stop` reports the path. Use
it whenever the user wants a record of a console session.
