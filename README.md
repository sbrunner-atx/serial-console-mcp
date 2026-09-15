# serial-console-mcp

<!-- mcp-name: io.github.sbrunner-atx/serial-console-mcp -->

**AI for the configuration of telecommunication equipment, through the console port it already has.**

[![build](https://github.com/sbrunner-atx/serial-console-mcp/actions/workflows/build.yml/badge.svg)](https://github.com/sbrunner-atx/serial-console-mcp/actions/workflows/build.yml)
[![PyPI](https://img.shields.io/pypi/v/serial-console-mcp?label=pypi&cacheSeconds=3600)](https://pypi.org/project/serial-console-mcp/)
&nbsp;MIT licensed &nbsp;·&nbsp; Python 3.10+ &nbsp;·&nbsp; **status: 0.3.1, verified live on a Juniper EX2200 console**

serial-console-mcp connects an AI assistant such as Claude to the serial console
of the equipment that runs networks and radio stations:

- **Routers and switches.** Juniper (Junos) and Cisco (IOS, IOS-XE) console and
  craft ports: log in, read state, change configuration with a safety net
  (`commit confirmed` on Junos, `reload in` on IOS), and leave the console as it
  was found.
- **Radio transceivers.** Icom and Xiegu over CI-V; Kenwood, Yaesu and Elecraft
  over their text CAT protocols: identify the rig, read and set frequency and
  mode.
- **Antenna rotators.** Yaesu GS-232 and the many controllers that speak it:
  read the heading, turn, stop.

You say what you want in plain language. The assistant drives the console
through this server, Claude Desktop asks you to approve each step, and bundled
operating skills teach it the Junos and IOS basics: which mode a prompt means,
how to get in and out, which show commands answer everyday questions.

Juniper support is verified on a real EX2200 console. Cisco, CI-V, CAT and
rotator support follows the vendors' protocol references and has not yet met
hardware on this bench. Anything else on a serial cable works too: Linux
consoles, BIOS and BMC screens, microcontrollers, GPS receivers.

You do **not** need to know any programming. After it's installed you talk to Claude
normally:

> **You:** What serial ports do you see?
> **Claude:** I found two. One looks like a Silicon Labs CP210x on COM4 — that's
> probably your device.
>
> **You:** Connect to COM4 at 9600 baud.
> **Claude:** Connected.
>
> **You:** Log in and show me the version.

## How it works (the important part)

A serial console isn't a simple question-and-answer channel. It echoes what you
type, prints unsolicited messages on its own (logs, interface flaps), and can dump
pages of output. So the moment a port is open, a background reader keeps draining
it into a buffer. That means Claude can:

- **send** a command (writes only — doesn't guess when the reply is done), then
- **read until a prompt** appears (`# `, `> `, `login:` …) to capture the whole
  reply — even a long one — without cutting it off, or
- **read whatever's waiting** for streaming/unsolicited output.

This is the same model `minicom` and `expect` use, which is why it handles
interactive CLIs properly.

### The tools

| Tool | What it does |
| --- | --- |
| `list_serial_ports` | Enumerate ports with description and USB hardware id; marks ones open here |
| `list_presets` | Usual settings per device family: Cisco/Juniper/Linux consoles, Kenwood/Elecraft/Yaesu CAT, Icom CI-V, rotators, Arduino, NMEA GPS |
| `connect` / `reconnect_last` / `disconnect` | Open a port by name. Defaults 9600 8N1, no flow control; baud, data bits, parity, stop bits, RTS/CTS, XON/XOFF, line ending and prompt are all settable, or loaded from a preset. Several ports at once |
| `send_keys` / `screen` | Press Ctrl-C, Esc, Tab, arrows, F-keys by name; view the VT100/xterm screen of a full-screen console (BIOS, BMC, menu switches, vi) |
| `send_text` / `send_hex` | Write an ASCII line (CR / LF / CRLF / none) or raw hex bytes. Write-only |
| `read_until_prompt` | Return buffered output up to a literal or regex prompt, leaving the rest; `auto_reply` pages through `--More--` |
| `read_available` | Return whatever has arrived, as text and hex |
| `query_text` | Clear, send, then read until the prompt or until the line goes idle |
| `expect` | A scripted list of send-and-wait steps in one call: logins, command sequences |
| `set_lines` / `pulse_line` / `send_break` | Drive DTR and RTS (PTT, Arduino reset), send BREAK |
| `capture_start` / `capture_stop` / `get_transcript` | Log a session to a file (raw or timestamped TX/RX); re-read the rolling transcript |
| `port_in_use_by` / `detect_baud` | Which program holds a port; which baud rate produces readable text |
| `cat_build` / `cat_parse` | Kenwood, Elecraft and Yaesu `;` commands: build a frequency set, decode ID, FA, MD, IF and error replies |
| `civ_build` / `civ_parse` / `civ_freq` | Icom CI-V frames: build, decode (echo vs reply, BCD frequency, mode, PTT), convert |
| `rotator_build` / `rotator_parse` | GS-232 rotator commands (read, move, stop, speed) and position replies |
| `clear_buffer` / `status` | Housekeeping; status shows every open port with control-line states |

Ports are opened by name ("rig", "rotator"); tools default to the most recently
used one. Each connection has a terminal mode: `dumb` (raw bytes, the default),
`ansi` (colours and escape sequences stripped, CR/backspace overwrites applied,
prompts matched on the line as displayed even after a device redraws it with
spaces and backspaces; used by the console presets), or `xterm`/`vt100` (a real screen you can read
with `screen`; needs `pip install 'serial-console-mcp[screen]'`, included in
the installers). The receive buffer is capped at 4 MB per port; if a device streams for
hours unread, the oldest bytes are dropped and `status` says how many.

### Operating skills

Three skill files in `skills/` teach an agent the discipline, the way the
fldigi-mcp skills do; copy them into `~/.claude/skills/` or a project's
`.claude/skills/`:

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

Set them in the server's entry in `claude_desktop_config.json` under `"env"`.

## Installing

1. Download the installer for your computer from the
   [Releases page](https://github.com/sbrunner-atx/serial-console-mcp/releases)
   (the `.exe` on Windows, or the `.pkg` on a Mac) and click through it like any
   normal program. It sets everything up for you. The installers are unsigned for
   now, so expect a Gatekeeper / SmartScreen warning.
2. **Completely quit Claude Desktop** — not just closing the window. On Windows,
   right-click the Claude icon near the clock and choose Quit. On a Mac, press
   ⌘Q or choose **Claude → Quit**.
3. Open Claude Desktop again.
4. In a new chat, type: **"What serial ports do you see?"** If Claude lists your
   ports, you're done.

That's the whole thing. There's no separate program to keep open and nothing to
configure by hand.

## Installing from PyPI

If you already have Python 3.10+ and [uv](https://docs.astral.sh/uv/) or pipx,
you don't need the installer:

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
uv sync                       # or: python3 -m venv .venv && . .venv/bin/activate && pip install -e . pytest
uv run pytest                 # fake serial port, no hardware needed
uv run serial-console-mcp configure --command "$PWD/.venv/bin/serial-console-mcp"
```

The package lives in `src/serial_console_mcp/`: `server.py` is the MCP server,
`configure.py` the Claude Desktop registrar. See [BUILD.md](BUILD.md) for the
installers.

## Using it

Plain-English requests work. Some examples:

- "List my serial ports."
- "Connect to the console on /dev/cu.usbserial-10 at 9600 baud."
- "Connect to /dev/cu.BLTH at 38400, 8 data bits, no parity, 1 stop bit, XON/XOFF flow control."
- "Connect to the Kenwood with the kenwood-cat preset and call it rig; connect the rotator on the other port."
- "Log in as admin and run show version" (one `expect` call: return, login, password, command).
- "What baud rate is this thing?" (`detect_baud`).
- "Key the rig for two seconds" (`pulse_line("RTS", 2000)`, after you confirm).
- "Ask the IC-7300 for its frequency" (`civ_build`, `send_hex`, `read_available`, `civ_parse`).
- "What is the TS-590 tuned to?" (`query_text("FA;")`, `cat_parse`).
- "Turn the rotator to 45 degrees and confirm" (`rotator_build`, `query_text`, `rotator_parse`).
- "Record this console session to a file."
- "Hit Ctrl-C, that ping is still running." (`send_keys`)
- "Open the BIOS console at 115200 and show me the screen; go down two and press Enter." (`screen-console` preset, `screen`, `send_keys`)
- "Reconnect to the same port as last time." (it remembers)
- "Send a return, then read until the login prompt."
- "Log in as admin and run `show interfaces terse`, then show me all of it."
- "Just read whatever the device is printing right now."
- "Disconnect when you're done."

For a router/switch console, tell Claude the prompt it should wait for (often `# `
for enable mode or `> ` for user mode) and it will read until it sees it. For an
Icom radio (CI-V), tell Claude — it can send the hex commands those radios expect.

## If something doesn't work

**"No serial ports found."**
- Is the device turned on?
- Is the USB cable a real *data* cable, not a charge-only one? (A very common gotcha.)
- On Windows, open Device Manager and look under **Ports (COM & LPT)**. If nothing's
  there, Windows needs the cable's driver (often FTDI, CP210x, or CH340).

**"Could not open the port" / "access denied."**
- A serial port can only be used by one program at a time. Close anything else that
  might be holding it: a terminal (PuTTY/minicom/screen), WSJT-X, your contest
  logger, the device's own software.

**Claude says it can't access serial ports at all.**
- Make sure you fully quit and reopened Claude Desktop after installing.
- Start a brand-new chat and ask "What serial ports do you see?" again.

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

MIT. See [LICENSE](LICENSE).

73!
