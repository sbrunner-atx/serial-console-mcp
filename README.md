# serial-console-mcp

<!-- mcp-name: io.github.sbrunner-atx/serial-console-mcp -->

Project page: [ae5vg.com/products/serial-console-mcp](https://ae5vg.com/products/serial-console-mcp.html)

**AI for the configuration of telecommunication equipment, through the console port it already has.**

[![build](https://github.com/sbrunner-atx/serial-console-mcp/actions/workflows/build.yml/badge.svg)](https://github.com/sbrunner-atx/serial-console-mcp/actions/workflows/build.yml)
[![PyPI](https://img.shields.io/pypi/v/serial-console-mcp?label=pypi&cacheSeconds=3600)](https://pypi.org/project/serial-console-mcp/)
[![license: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](https://github.com/sbrunner-atx/serial-console-mcp/blob/main/LICENSE)
&nbsp;Python 3.10+ &nbsp;·&nbsp; **status: 0.3.2, verified live on a Juniper EX2200 console**

> **Free for hobbyists. Commercial use needs a license.**
> Personal use, amateur radio, education and nonprofit organizations are free.
> Any use by or for a business, including internal use, needs a commercial
> license. Licenses, support for more devices, custom skills and support plans:
> see [COMMERCIAL.md](https://github.com/sbrunner-atx/serial-console-mcp/blob/main/COMMERCIAL.md).

serial-console-mcp connects an AI assistant such as Claude to the serial console
of the equipment that runs networks and radio stations, and of anything else
with a serial port:

- **Routers and switches.** Juniper (Junos) and Cisco (IOS, IOS-XE) console and
  craft ports: log in, read state, change configuration with a safety net
  (`commit confirmed` on Junos, `reload in` on IOS), and leave the console as it
  was found.
- **Radio transceivers.** Icom and Xiegu over CI-V; Kenwood, Yaesu and Elecraft
  over their text CAT protocols: identify the rig, read and set frequency and
  mode.
- **Antenna rotators.** Yaesu GS-232 and the many controllers that speak it:
  read the heading, turn, stop.
- **Anything else with a serial port.** Nothing is tied to telecom: the server
  talks raw bytes, cleans up ANSI output, or keeps a full VT100/xterm screen, so
  IoT gateways, alarm and access-control panels, UPS and PDU menus, lab
  instruments, industrial controllers, Linux and BIOS consoles and
  microcontrollers work too.
- **Consoles on the network.** Telnet or SSH straight to the device (a Juniper
  or Cisco management address, a Linux box, a BMC), a console server's
  reverse-telnet or per-port SSH (Opengear, Cisco, Lantronix, Digi, Avocent),
  ser2net and RFC 2217 device servers, and raw TCP, with the same tools as a
  local cable. User name and password logins work without the password ever
  going through the chat. See [Network consoles](#network-consoles).

You say what you want in plain language. The assistant drives the console
through this server, Claude Desktop asks you to approve each step, and bundled
operating skills teach it the Junos and IOS basics: which mode a prompt means,
how to get in and out, which show commands answer everyday questions.

Juniper support is verified on a real EX2200 console. Cisco, CI-V, CAT and
rotator support follows the vendors' protocol references and has not yet met
hardware on this bench. The other device types use the generic tools rather than
device-specific helpers.

You do **not** need to know any programming. Once it is installed, you talk to Claude
normally:

> **You:** What serial ports do you see?
> **Claude:** I found two. One looks like a Silicon Labs CP210x on COM4, which is
> probably your device.
>
> **You:** Connect to COM4 at 9600 baud.
> **Claude:** Connected.
>
> **You:** Log in and show me the version.

## How it works (the important part)

A serial console is not a simple question-and-answer channel. It echoes what you
type, prints unsolicited messages on its own (logs, interface flaps), and can dump
pages of output. So the moment a port is open, a background reader keeps draining
it into a buffer. That means Claude can:

- **send** a command (this only writes; it does not guess when the reply is done), then
- **read until a prompt** appears (`# `, `> `, `login:` …) to capture the whole
  reply, even a long one, without cutting it off, or
- **read whatever's waiting** for streaming/unsolicited output.

This is the same model `minicom` and `expect` use, which is why it handles
interactive CLIs properly.

### The tools

| Tool | What it does |
| --- | --- |
| `list_serial_ports` | Enumerate ports with description and USB hardware id; marks ones open here |
| `list_presets` | Usual settings per device family: Cisco/Juniper/Linux consoles, full-screen consoles (BIOS, BMC, menus), Kenwood/Elecraft/Yaesu CAT, Icom and Xiegu CI-V, rotators, Arduino, NMEA GPS |
| `connect` / `reconnect_last` / `disconnect` | Open a port by name. Defaults 9600 8N1, no flow control; baud, data bits, parity, stop bits, RTS/CTS, XON/XOFF, line ending and prompt are all settable, or loaded from a preset. Or a network console: `transport` telnet, ssh, tcp or rfc2217 with `host` and `tcp_port`. Several at once |
| `send_keys` / `screen` | Press Ctrl-C, Esc, Tab, arrows, F-keys by name; view the VT100/xterm screen of a full-screen console (BIOS, BMC, menu switches, vi) |
| `send_text` / `send_hex` | Write an ASCII line (CR / LF / CRLF / none) or raw hex bytes. Write-only |
| `read_until_prompt` | Return buffered output up to a literal or regex prompt, leaving the rest; `auto_reply` pages through `--More--` |
| `read_available` | Return whatever has arrived, as text and hex |
| `query_text` | Clear, send, then read until the prompt or until the line goes idle |
| `expect` | A scripted list of send-and-wait steps in one call: logins (passwords by credential name with `send_credential`), command sequences |
| `set_lines` / `pulse_line` / `send_break` | Drive DTR and RTS (PTT, Arduino reset), send BREAK. Over the network: RFC 2217 carries all three, telnet and SSH BREAK only, raw TCP none; the tools say so |
| `capture_start` / `capture_stop` / `get_transcript` | Log a session to a file (raw or timestamped TX/RX); re-read the rolling transcript |
| `port_in_use_by` / `detect_baud` | Which program holds a port; which baud rate produces readable text |
| `cat_build` / `cat_parse` | Kenwood, Elecraft and Yaesu `;` commands: build a frequency set, decode ID, FA, MD, IF and error replies |
| `civ_build` / `civ_parse` / `civ_freq` | Icom CI-V frames: build, decode (echo vs reply, BCD frequency, mode, PTT), convert |
| `rotator_build` / `rotator_parse` | GS-232 rotator commands (read, move, stop, speed) and position replies |
| `clear_buffer` / `status` | Housekeeping; status shows every open port and network console with control-line states |

Ports are opened by name ("rig", "rotator"); tools default to the most recently
used one. Each connection has a terminal mode: `dumb` (raw bytes, the default),
`ansi` (colors and escape sequences stripped, CR/backspace overwrites applied,
prompts matched on the line as displayed even after a device redraws it with
spaces and backspaces; used by the console presets), or `xterm`/`vt100` (a real screen you can read
with `screen`; needs `pip install 'serial-console-mcp[screen]'`, included in
the desktop connector). The receive buffer is capped at 4 MB per port; if a device streams for
hours unread, the oldest bytes are dropped and `status` says how many.

### Network consoles

Many devices answer the same CLI three ways: on the serial console or craft
port, over telnet and over SSH. A Juniper router is a good example. Once a
network console is open, every tool above works on it the same way: prompts,
`expect`, `send_keys` and `screen`, capture, the transcript, read-only mode,
idle auto-close and `status`.

| Transport | For | BREAK, DTR/RTS, baud |
| --- | --- | --- |
| `telnet` | The device's own telnet (routers, switches, Linux boxes); reverse-telnet ports on console servers (Opengear, Cisco, Lantronix, Digi, Avocent); ser2net telnet ports | BREAK (IAC BRK): through a console server it reaches the serial line; a device's own telnet decides what it means |
| `ssh` | The device's own SSH (routers, switches, BMCs, a bench Pi); a console server's per-port SSH | BREAK (SSH break request): passed on by console servers, usually ignored by a device's own SSH |
| `rfc2217` | ser2net and device servers with RFC 2217 COM-port control | All of them, set from Claude |
| `tcp` | Raw TCP: ser2net raw ports, simple device servers | Bytes only |

A telnet or SSH session to the device itself reaches the running system. The
boot loader, ROMMON and password recovery are only on the serial console:
connect to it directly, or through a console server.

Give `transport` and `host` (plus `tcp_port`), or one address in `port`:
`ssh://admin@mx1`, `telnet://192.0.2.1`, `telnet://cs1:2003`,
`tcp://10.0.0.5:4001`, `rfc2217://ser2net:3001`. Telnet defaults to port 23 and SSH to your
`~/.ssh/config` or 22; raw TCP and RFC 2217 need the port.

**Credentials never go through the chat.**

- **SSH** reads `~/.ssh/config`, so Host aliases, `User`, `Port`,
  `IdentityFile` and `ProxyJump` work. Keys come from the SSH agent and your
  key files. The host key must be in `~/.ssh/known_hosts`: a changed key is
  always refused before anything is sent, and host key checking is never
  turned off. Only you can trust a new device. Run `ssh host` once in a
  terminal, or let `ask` below show you its fingerprint in a dialog.
- **A user name and password for this one connection.** Say so ("SSH to
  192.0.2.1, I will type the password"), and the assistant connects with
  `ask=True`. A dialog on your screen asks for the password, and for the user
  name if it is not known. What you type goes straight to the server process,
  is used for that connection only, and is never shown to the assistant or
  written anywhere. For SSH, your keys are tried first and the dialog only
  appears if they are refused. On first contact, a dialog first shows the
  host key fingerprint and saves the key to `known_hosts` only if you click
  Trust. Dialogs are built for macOS, Windows, and Linux with zenity or
  kdialog, and so far tested on macOS.
- **Environment variables**, if you prefer them: set
  `SERIAL_CONSOLE_PASSWORD_<NAME>` (and optionally
  `SERIAL_CONSOLE_USERNAME_<NAME>`) in the server's environment, for example
  the `env` block of your MCP client config. Then `credential="<name>"` uses
  them. `<NAME>` is the name in capitals with other characters turned into
  `_`, so `bench-pi` becomes `SERIAL_CONSOLE_PASSWORD_BENCH_PI`. Anyone who
  can read that config can read the password.
- **Stored passwords.** You store a password (a console server's telnet
  login, a password-only SSH device, a device's own login) under a name, from
  a terminal:

  ```bash
  serial-console-mcp credential set bench-opengear --username admin
  ```

  You are prompted for the password. It goes to `credentials.json` in the
  settings folder below, mode 600, and is refused if others can read it.
  `--keychain` keeps it in the macOS Keychain or, with the `keyring` package,
  your system's keyring. The assistant passes `credential="bench-opengear"`
  to `connect`, which answers the console server's `login:` and `Password:`
  prompts or offers the password to SSH. For a device's own login, it uses
  `send_credential` in an `expect` step. The password is never shown, and
  annotated captures record `<credential hidden>`. `credential list` shows the
  names; `credential remove NAME` deletes one.
- `reconnect_last` and named connections remember the transport, host, port,
  user name and credential name, never a secret.

SSH needs the `ssh` extra (`pip install 'serial-console-mcp[ssh]'`); the
desktop connector includes it. Telnet, RFC 2217 and raw TCP need nothing extra.

### Operating skills

Three skill files in `skills/` teach an agent the discipline. In the Claude
desktop app, upload them as described under
[Add the operating skills](#add-the-operating-skills); in Claude Code, copy them into
`~/.claude/skills/` or a project's `.claude/skills/`:

| Skill | What it teaches |
| --- | --- |
| `serial-console` | The console model, the six rules, terminal modes, keys, presets |
| `junos-operating` | Junos modes from the prompt (`%` shell, `>` operational, `#` configure), out of the BSD shell and back, `configure exclusive`, `show \| compare`, `commit confirmed`, `rollback`, `cli -c` and `start shell`, health and interface show commands verified on an EX2200 (`show chassis routing-engine`, `show interfaces terse`/`brief`) |
| `ios-operating` | IOS modes (`>`, `#`, `(config)#`), `enable`/`configure terminal`/`end`, `reload in 10` as the safety net, `write memory`, everyday show commands (`show ip interface brief`, `show processes cpu sorted`) |

### Environment variables

| Variable | Effect |
| --- | --- |
| `SERIAL_CONSOLE_READ_ONLY=1` | Refuse writes except read-style commands (`show`, `ID;`, CI-V reads) and refuse control-line changes |
| `SERIAL_CONSOLE_ALLOW=<regex>` | Override the read-only allowlist |
| `SERIAL_CONSOLE_IDLE_MINUTES=15` | Auto-close a port idle that long (default: never) |
| `SERIAL_CONSOLE_NETWORK_HOSTS=10.20.0.0/16,*.bench.lan` | Hosts network consoles may reach: names with `*` wildcards and address ranges. Empty (the default) allows any host; `none` turns network consoles off |
| `SERIAL_CONSOLE_CREDENTIALS=/path/file.json` | Use this credentials file instead of `credentials.json` in the settings folder |
| `SERIAL_CONSOLE_PASSWORD_<NAME>`, `SERIAL_CONSOLE_USERNAME_<NAME>` | Password (and user name) for `credential="<name>"`, checked before the file and keychain |

Set them in the server's entry in `claude_desktop_config.json` under `"env"`.

## Installing

1. Download `serial-console-mcp.mcpb` from the
   [Releases page](https://github.com/sbrunner-atx/serial-console-mcp/releases).
   The same file works on Mac, Windows and Linux.
2. In Claude Desktop, open **Settings → Desktop app**, click **Manage** next to
   **Extensions**, and drag the file onto that page (or use **Advanced settings →
   Install extension**). It then shows as a connector under **Settings →
   Customize → Connectors**. A short settings form offers
   read-only mode, an idle timeout and a list of network hosts the server may
   reach; all are off or open unless you set them.
3. In a new chat, type: **"What serial ports do you see?"** If Claude lists your
   ports, you are done.

No terminal, no Python, nothing to configure by hand.

**Upgrading from the 0.3.x `.pkg` or `.exe` installer?** Remove the old copy
first, or Claude Desktop starts two servers. On a Mac:

```bash
"/Library/Application Support/SerialConsoleMCP/serial-console-mcp" configure --remove
sudo rm -rf "/Library/Application Support/SerialConsoleMCP"
```

On Windows, uninstall **Serial Console MCP** under Settings → Apps. Then quit
and reopen Claude Desktop.

## Add the operating skills

The desktop connector installs only the server. Its operating skills are separate downloads: `serial-console.skill`, `junos-operating.skill` and `ios-operating.skill`, from the [latest release](https://github.com/sbrunner-atx/serial-console-mcp/releases/latest) or [ae5vg.com/downloads.html](https://ae5vg.com/downloads.html#serial-console-mcp). In Claude, open **Customize → Skills**, click **Add**, then **Upload skill**, and choose all the files at once.

For Claude Code or another MCP client, copy the folders under `skills/` into the client's skills folder, for Claude Code `~/.claude/skills/`.

To run the server itself in Claude Code, Codex, Cursor or VS Code, see [Other AI assistants](https://ae5vg.com/getting-started.html#other-clients) for the command or configuration block to copy.

## Installing from PyPI

If you already have Python 3.10+ and [uv](https://docs.astral.sh/uv/) or pipx,
you can skip the desktop connector:

```bash
uvx serial-console-mcp --version                                   # fetches and runs it
uvx serial-console-mcp configure --command uvx --arg serial-console-mcp
```

or

```bash
pipx install serial-console-mcp
serial-console-mcp configure --command "$(which serial-console-mcp)"
```

`configure` writes a `serial-console` entry into `claude_desktop_config.json`
(merging with whatever is already there and backing the old file up first).
Quit and reopen Claude Desktop. `serial-console-mcp configure --remove` undoes
it. Any other MCP client can launch the same command over stdio.

## Installing from source (developers)

```bash
git clone https://github.com/sbrunner-atx/serial-console-mcp.git
cd serial-console-mcp
uv sync --extra screen --extra ssh  # or: pip install -e '.[screen,ssh]' pytest
uv run pytest                 # fake serial port and local telnet/SSH/TCP/RFC 2217 servers, no hardware
uv run serial-console-mcp configure --command "$PWD/.venv/bin/serial-console-mcp"
```

The package lives in `src/serial_console_mcp/`: `server.py` is the MCP server,
`configure.py` the Claude Desktop registrar. `manifest.json` describes the
desktop extension; `npx @anthropic-ai/mcpb pack` builds it. [BUILD.md](https://github.com/sbrunner-atx/serial-console-mcp/blob/main/BUILD.md)
covers the retired `.pkg` and `.exe` installers.

## Using it

Plain-English requests work. Some examples:

- "List my serial ports."
- "Connect to the console on /dev/cu.usbserial-10 at 9600 baud."
- "Connect to /dev/cu.BLTH at 38400, 8 data bits, no parity, 1 stop bit, XON/XOFF flow control."
- "Connect to the Kenwood with the kenwood-cat preset and call it rig; connect the rotator on the other port."
- "Log in as admin with the lab-switch credential and run show version" (one `expect` call: return, login, `send_credential`, command).
- "What baud rate is this thing?" (`detect_baud`).
- "Key the rig for two seconds" (`pulse_line("RTS", 2000)`, after you confirm).
- "Ask the IC-7300 for its frequency" (`civ_build`, `send_hex`, `read_available`, `civ_parse`).
- "What is the TS-590 tuned to?" (`query_text("FA;")`, `cat_parse`).
- "Turn the rotator to 45 degrees and confirm" (`rotator_build`, `query_text`, `rotator_parse`).
- "Record this console session to a file."
- "Hit Ctrl-C, that ping is still running." (`send_keys`)
- "Open the BIOS console at 115200 and show me the screen; go down two and press Enter." (`screen-console` preset, `screen`, `send_keys`)
- "Reconnect to the same port as last time." (it remembers)
- "Telnet to the console server cs1, port 2003, with the bench-opengear credential."
- "SSH to the MX at 192.0.2.1 as admin; I will type the password." (`ask=True`)
- "SSH to bench-pi and run dmesg." (a `~/.ssh/config` alias)
- "Connect to rfc2217://ser2net:3001 at 115200 and reset the board with DTR."
- "Send a return, then read until the login prompt."
- "Log in as admin and run `show interfaces terse`, then show me all of it."
- "Just read whatever the device is printing right now."
- "Disconnect when you're done."

For a router/switch console, tell Claude the prompt it should wait for (often `# `
for enable mode or `> ` for user mode) and it will read until it sees it. For an
Icom radio (CI-V), tell Claude; it can send the hex commands those radios expect.

## If something does not work

**"No serial ports found."**
- Is the device turned on?
- Is the USB cable a real *data* cable, not a charge-only one? (A very common gotcha.)
- On Windows, open Device Manager and look under **Ports (COM & LPT)**. If nothing's
  there, Windows needs the cable's driver (often FTDI, CP210x, or CH340).

**"Could not open the port" / "access denied."**
- A serial port can only be used by one program at a time. Close anything else that
  might be holding it: a terminal (PuTTY/minicom/screen), WSJT-X, your contest
  logger, the device's own software.

**Claude says it cannot access serial ports at all.**
- Make sure you fully quit and reopened Claude Desktop after installing.
- Start a brand-new chat and ask "What serial ports do you see?" again.

**A network console does not connect.**
- "Refused: nothing is listening there": the device's telnet or SSH service
  is off (telnet often is by default), or, on a console server, the TCP port
  for that serial line is wrong or disabled. Check its port map.
- "Host key could not be verified": run `ssh host` once in a terminal, check
  the fingerprint, accept it, then try again. Or ask Claude to connect with
  `ask` and compare the fingerprint in the dialog.
- "Host key is NOT the one on record": the device was reinstalled, or
  something is in the way. Find out which before removing the old line with
  `ssh-keygen -R host`.
- "SSH login was refused": add your public key to the device, ask Claude to
  connect with `ask` so you can type the password, or store it with
  `serial-console-mcp credential set NAME`.
- A console server usually lets one session use a port at a time; close the
  other session.

**It connected but a command gets no reply.**
- Almost always the **baud rate** is wrong, or the **line ending** is wrong for your
  gear. Most Unix-style consoles want a plain newline (LF); most rigs want a carriage
  return (CR). Ask Claude to send a return first to draw a fresh prompt.

## A word on safety

These tools send exactly what you (through Claude) ask them to send, to whatever
device is on the cable. Claude Desktop asks you to approve each tool call, so you
see every command before it runs. Read it. A console session on a router or a
rig can reconfigure, reboot, or transmit, and this server does not try to guess
which commands are dangerous. If something looks wrong, decline it, and keep a
real terminal handy for anything you would not want an assistant to type.

## License

[PolyForm Noncommercial License 1.0.0](https://github.com/sbrunner-atx/serial-console-mcp/blob/main/LICENSE),
starting with the first release after 0.3.3.

- **Free** for personal use, hobby projects, amateur radio, education, and
  charitable, public research, public safety and government organizations.
- **Commercial license required** for any use by or for a business, including
  internal use by employees and work for clients.
- **Paid offers:** an annual commercial license per company, support for
  additional devices and protocols, custom skills and integrations, and support
  plans. See [COMMERCIAL.md](https://github.com/sbrunner-atx/serial-console-mcp/blob/main/COMMERCIAL.md).

Versions up to and including 0.3.3 were released under the MIT License and
remain under it.

73
