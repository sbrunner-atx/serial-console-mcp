# serial-console-mcp

**Let Claude drive your serial console.**

[![build](https://github.com/sbrunner-atx/serial-console-mcp/actions/workflows/build.yml/badge.svg)](https://github.com/sbrunner-atx/serial-console-mcp/actions/workflows/build.yml)
[![PyPI](https://img.shields.io/pypi/v/serial-console-mcp.svg)](https://pypi.org/project/serial-console-mcp/)
&nbsp;MIT licensed &nbsp;·&nbsp; Python 3.10+ &nbsp;·&nbsp; **status: experimental (0.1.0)**

This adds a few tools to **Claude Desktop** so you can talk to anything on a serial
port — a network device's console/craft port (Juniper, Cisco, etc.), a radio,
rotator, amplifier, antenna switch, or a microcontroller — just by *asking Claude*.

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
| `list_serial_ports` | Enumerate ports with description and USB hardware id |
| `connect` / `reconnect_last` / `disconnect` | Open a port. Defaults to 9600 8N1, no flow control; baud, data bits, parity, stop bits, RTS/CTS and XON/XOFF are all settable by asking. Remembers the last one |
| `send_text` | Write an ASCII command with CR / LF / CRLF / no line ending. Write-only |
| `send_hex` | Write raw bytes given as hex (Icom CI-V and other binary protocols) |
| `read_until_prompt` | Return buffered output up to a literal or regex prompt, leaving the rest |
| `read_available` | Return whatever has arrived, as text and hex |
| `query_text` | Clear, send, then read until a prompt or until the line goes idle |
| `clear_buffer` / `status` | Housekeeping |

One port is open at a time. The receive buffer is capped at 4 MB; if a device
streams for hours unread, the oldest bytes are dropped and `status` says how many.

## The Field Guide

[serial-console-mcp Field Guide (PDF)](docs/serial-console-mcp%20Field%20Guide.pdf) is the
operator's manual: what each tool does, every connection setting said in plain
language, the console rules, a per-device playbook (craft ports, text CAT,
Icom CI-V, rotators and microcontrollers), four worked sessions, and a
troubleshooting table. Source is `docs/brand/` (HTML + CSS, rendered with
WeasyPrint).

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
