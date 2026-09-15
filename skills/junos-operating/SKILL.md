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

## Out of the shell, and back

- Shell to CLI: `cli`. Expect `> $`.
- CLI back to shell: `exit` at the `>` prompt when the session was started from
  the shell (it returns to `%`). `start shell` from `>` also opens a shell.
- If the console was found in the shell, put it back in the shell when done.
  If it was found at `login:`, log out fully: `exit` until `login:` reappears.
- Junos consoles log in as `root` only into the shell. `root` must type `cli`
  to reach the CLI; other users land in the CLI directly.

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

| Question | Command |
| --- | --- |
| What is this box, what version | `show version`, `show chassis hardware` |
| Is it healthy | `show system alarms`, `show chassis alarms`, `show system uptime` |
| Interfaces up/down at a glance | `show interfaces terse` |
| Interface descriptions | `show interfaces descriptions` |
| One interface in detail | `show interfaces ge-0/0/1` (add `extensive` for counters) |
| VLANs and who is where | `show vlans`, `show ethernet-switching table` |
| Neighbours | `show lldp neighbors` |
| Routing | `show route`, `show route 10.0.0.0/8` |
| The config, readable and diffable | `show configuration | display set` |
| One section of config | `show configuration interfaces ge-0/0/1` |
| Recent log | `show log messages | last 50` |
| Who else is on the box | `show system users` |

From configuration mode, prefix with `run` (`run show interfaces terse`).

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
