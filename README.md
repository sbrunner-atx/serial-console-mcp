# Let Claude Run Your Radio

This adds a few tools to **Claude Desktop** so you can control a radio (or rotator,
amplifier, antenna switch — anything on a USB serial cable) just by *asking Claude*.

You do **not** need to know any programming. After it's installed you talk to Claude
normally:

> **You:** What serial ports do you see?
> **Claude:** I found two. One looks like a Silicon Labs CP210x on COM4 — that's
> probably your radio.
>
> **You:** Connect to COM4 at 38400 baud.
> **Claude:** Connected.
>
> **You:** Ask the rig what frequency it's on.

## Installing

1. Run the installer you were given (the `.exe` on Windows, or the `.pkg` on a Mac)
   and click through it like any normal program. It sets everything up for you.
2. **Completely quit Claude Desktop** — not just closing the window. On Windows,
   right-click the Claude icon near the clock and choose Quit. On a Mac, press
   ⌘Q or choose **Claude → Quit**.
3. Open Claude Desktop again.
4. In a new chat, type: **"What serial ports do you see?"** If Claude lists your
   ports, you're done.

That's the whole thing. There's no separate program to keep open and nothing to
configure by hand.

## Using it

Plain-English requests work. Some examples:

- "List my serial ports."
- "Connect to the radio on COM4 at 9600 baud."
- "Reconnect to the same port as last time." (it remembers)
- "Send the command `ID` and read the reply."
- "Disconnect when you're done."

If you have an Icom (which uses CI-V), tell Claude — it can send the hex commands
those radios expect.

## If something doesn't work

**"No serial ports found."**
- Is the device turned on?
- Is the USB cable a real *data* cable, not a charge-only one? (A very common gotcha.)
- On Windows, open Device Manager and look under **Ports (COM & LPT)**. If nothing's
  there, Windows needs the cable's driver (often FTDI, CP210x, or CH340).

**"Could not open the port" / "access denied."**
- A serial port can only be used by one program at a time. Close anything else that
  might be holding it: WSJT-X, your contest logger, a terminal program, the radio's
  own software.

**Claude says it can't access serial ports at all.**
- Make sure you fully quit and reopened Claude Desktop after installing.
- Start a brand-new chat and ask "What serial ports do you see?" again.

**It connected but a command gets no reply.**
- Almost always the **baud rate** is wrong, or the **line ending** is wrong for your
  gear. Tell Claude to try the rig's documented baud rate, or a different line ending
  (most rigs want a carriage return).

## A word on safety

These tools send exactly what you (through Claude) ask them to send, to whatever
device is on the cable. Claude will show you each action before it runs it. If
something looks wrong, decline it.

73!
