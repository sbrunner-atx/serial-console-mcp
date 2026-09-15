---
name: junos-operating
description: >
  Basic operating discipline for a Juniper Junos device (EX, SRX, MX, QFX) over
  its console port through serial-console-mcp: recognising which mode you are
  in from the prompt, getting out of the FreeBSD shell into the CLI and back,
  entering and leaving configuration mode safely, committing and rolling back,
  and the everyday show commands. Use whenever the device is a Juniper.
---

# Operating a Junos console

Connect with the `juniper-craft` preset (9600 8N1, CR, prompt regex
`[#>%] ?$`, ansi terminal). Send a bare return first and read the prompt. The
prompt tells you the mode; never assume it.

## The three modes, read from the prompt

| Prompt ends in | Mode | You are in |
| --- | --- | --- |
| `%` (e.g. `root@sw:RE:0%`) | FreeBSD shell | The Unix underneath Junos. Nothing here is a Junos command. |
| `>` (e.g. `user@sw>`) | Operational | `show ...`, `ping`, `request ...`. Read-only unless you `request`. |
| `#` with `[edit]` above it | Configuration | Editing the candidate config. Nothing takes effect until `commit`. |

`{master:0}` above the prompt means a Virtual Chassis member; ignore it.
`login:` means nobody is logged in: ask the user for credentials, never guess.

## Out of the BSD shell, and back

Junos runs on FreeBSD, and `root` always logs in to its shell, not to the CLI.
On an EX2200 (Junos 15.1) the shell prompt is `root@sw-office:RE:0%`. Newer
releases show a `sh` prompt such as `root@sw:~ #`, which ends in `#`: it is the
shell, not configuration mode, when there is no `[edit]` line above it.

| From | Send | You get |
| --- | --- | --- |
| Shell `%` | `cli` | The CLI, `root@sw>` (with `{master:0}` above it on EX) |
| CLI started from the shell | `exit` | Back to the shell `%` |
| CLI, any user | `start shell` | A shell inside the CLI session; `exit` returns to `>` |
| Shell | `cli -c "show system uptime \| no-more"` | One CLI command's output, still in the shell |
| Shell, finished | `exit` | Logged out: `login:` |

- Leave the console as you found it. Found in the shell: finish with `exit`
  from the CLI so it is back at `%`, and do not log the root session out unless
  the user asks. Found at `login:`: `exit` until `login:` returns.
- Nothing in the shell is a Junos command (`show: Command not found.`), and Unix
  commands there (`rm`, `vi`, `reboot`, `newfs`) bypass every Junos safeguard.
  Run none of them without the user's explicit words.
- A non-root user who needs the shell uses `start shell user root`, which asks
  for the root password: ask the user, never guess.
- csh echoes `exit` twice when a nested shell closes; that is normal.

## Into configuration mode, and out

- Enter: `configure exclusive` (locks the candidate; refuse-safe if someone
  else is editing) or `configure private`. Bare `configure` shares the
  candidate with anyone else editing; avoid it. Expect `# $` and `[edit]`.
- `error: configuration database locked` or `is modifying the configuration`:
  someone else is in. Stop and tell the user; do not force with `configure`.
- Move: `edit interfaces ge-0/0/1`, `up`, `top`, `exit` (one level up, or out
  of config mode at the top).
- Leave without changes: `rollback 0` then `exit`. If Junos says
  `The configuration has been changed but not committed`, answer `yes` only if
  the user wants the changes discarded, otherwise `commit` first.

## Committing and rolling back

1. `show | compare` and show the diff to the user before every commit.
2. `commit check` to validate without applying.
3. For anything that can cut the path you are on (interfaces, VLANs,
   management, firewall filters, routing): `commit confirmed 5`, verify, then
   `commit` within five minutes. If the box becomes unreachable, it rolls back
   itself.
4. Otherwise `commit` or `commit and-quit`; add `comment "why"`.
5. Success is `commit complete`. Anything with `error:` did not commit; read the
   line it quotes.
6. Undo the last commit: `rollback 1`, then `commit`. `show system commit`
   lists history; `show configuration | compare rollback 1` shows what changed.

## Everyday show commands (operational mode)

Junos has no `show system status`; it answers `syntax error, expecting
<command>.` The health questions are spread over the commands below.

| Question | Command |
| --- | --- |
| What is this box, what version | `show version`, `show chassis hardware` |
| Any alarms | `show system alarms`, `show chassis alarms` (good answer: `No alarms currently active`) |
| CPU, memory, uptime, last reboot reason | `show chassis routing-engine` |
| Power supplies and temperatures | `show chassis environment` |
| Is the flash filling up | `show system storage` (watch `/` and `/var`) |
| Uptime, and when the config last changed | `show system uptime` |
| Interfaces up/down, one line each | `show interfaces terse` (`\| match ge-` hides the internal ones) |
| Interfaces, a short block each | `show interfaces brief` (link, speed, duplex, flags; add a name for one port) |
| Interface descriptions | `show interfaces descriptions` |
| One interface in detail | `show interfaces ge-0/0/1` (add `extensive` for error counters) |
| Which VLAN each port is in | `show ethernet-switching interfaces`, `show vlans` |
| MAC table | `show ethernet-switching table` |
| Neighbours | `show lldp neighbors` |
| Routing | `show route`, `show route 10.0.0.0/8` |
| The config, readable and diffable | `show configuration \| display set` |
| One section of config | `show configuration interfaces ge-0/0/1` |
| Recent log | `show log messages \| last 50` |
| Who else is on the box | `show system users` |

From configuration mode, prefix with `run` (`run show interfaces terse`).

Not sure a command exists? Type the words so far and `?` (`show system ?`):
Junos lists the completions at once, no Enter needed. Send it with
`line_ending="NONE"`, read the list with `read_available`, then
`send_keys(["ctrl-u"])` to clear the half-typed line and a bare return to draw
a clean prompt. Junos redraws an edited line with spaces and backspaces, so a
prompt pattern ending in `$` does not match until that return; the same holds
after Tab completion.

## Paging and long output

Run `set cli screen-length 0` once per session, or add `| no-more` to a
command. Otherwise `---(more)---` becomes your prompt; if it appears, pass
`auto_reply={"---(more)---": " "}` or send a space.

## Interrupting and recovering

- A running `ping` or `monitor`: `send_keys(["ctrl-c"])`.
- Lost in a hierarchy: `top`, then `show | compare` to see where you are.
- Prompt is `%` but you expected `>`: you fell out to the shell; type `cli`.
- Stuck in `vi` or `more`: `send_keys(["esc"])`, then `text::q!` and enter, or `q`.
- A `syntax error` points at the offending word with `^`; retype, do not guess.

## Safety

`show` and `commit check` are safe. `commit`, `request system reboot`,
`request system zeroize` and `rollback` change state; each needs the user's
explicit intent in the conversation, and `commit confirmed` is the default for
anything reachable-affecting. Read-only mode (`SERIAL_CONSOLE_READ_ONLY`)
allows `show ...` and `cli`/`exit`, and refuses the rest.

## Verified

The shell transitions and every show command above were run on a Juniper
EX2200-C, Junos 15.1R6.7, through serial-console-mcp on 15 September 2026.
Entering and leaving configuration mode was checked with nothing committed;
`commit`, `commit confirmed` and `rollback 1` follow the Junos documentation.
