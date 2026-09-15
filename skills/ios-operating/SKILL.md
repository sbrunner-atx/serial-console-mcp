---
name: ios-operating
description: >
  Basic operating discipline for a Cisco IOS / IOS-XE device (Catalyst, ISR,
  ASR, and IOS-like CLIs such as NX-OS and Arista EOS) over its console port
  through serial-console-mcp: recognising the mode from the prompt, moving
  between user EXEC, privileged EXEC and configuration mode, saving safely,
  the reload-timer safety net, and the everyday show commands. Use whenever
  the device is a Cisco or speaks an IOS-style CLI.
---

# Operating an IOS console

Connect with the `cisco-console` preset (9600 8N1, CR, prompt regex
`[#>] ?$`, ansi terminal). Send a bare return first and read the prompt. The
prompt tells you the mode; never assume it.

## The modes, read from the prompt

| Prompt | Mode | You are in |
| --- | --- | --- |
| `Switch>` | User EXEC | A few `show` commands, `ping`. |
| `Switch#` | Privileged EXEC | All `show`, `copy`, `reload`, `debug`. |
| `Switch(config)#` | Global configuration | Changes apply immediately as you type them. |
| `Switch(config-if)#` etc. | Sub-mode | Interface, line, router, VLAN sections. |
| `Username:` / `Password:` | Login | Ask the user for credentials; never guess. |
| `rommon 1 >` / `switch:` | Boot monitor | The OS is not running. Stop and tell the user. |

`--More--` at the bottom is the pager, not a prompt.

## Moving between modes

- User to privileged: `enable`. Expect `Password:` (ask the user) or `#`.
- Privileged to configuration: `configure terminal`. Expect `(config)#`.
- Into a section: `interface GigabitEthernet1/0/1`, `line vty 0 4`, `vlan 10`.
- One level up: `exit`. All the way to `#`: `end` (or Ctrl-Z).
- Privileged back to user: `disable`. Log out: `exit` at `>` or `#`.
- Leave the console as you found it: same mode, and logged out if it was.

## IOS has no commit: changes are live as you type

Every configuration line takes effect the moment it is entered and is lost at
the next reload unless saved. That changes the discipline:

1. Before any change that could cut the path you are on (interfaces, VLANs,
   management, ACLs, routing): `reload in 10` from `#` first. It schedules a
   reboot that will restore the saved config if you lose the box. After you
   verify, `reload cancel`.
2. Make the change, `end`, verify with `show`.
3. Save: `write memory` (or `copy running-config startup-config`). Success is
   `[OK]` / `Building configuration...`.
4. Undo a single line: prefix it with `no` in the same section. There is no
   rollback on classic IOS; IOS-XE with `archive` configured has
   `configure replace` and `show archive config differences`.
5. Never `write erase` or `reload` without the user saying so in words.

## Everyday show commands (privileged mode)

| Question | Command |
| --- | --- |
| What is this box, what version | `show version`, `show inventory` |
| Is it healthy | `show environment all`, `show logging | last 50`, `show processes cpu sorted` |
| Interfaces up/down at a glance | `show ip interface brief`, `show interfaces status` (switches) |
| Interface descriptions | `show interfaces description` |
| One interface in detail | `show interfaces GigabitEthernet1/0/1` |
| VLANs and who is where | `show vlan brief`, `show mac address-table` |
| Neighbours | `show cdp neighbors`, `show lldp neighbors` |
| Routing | `show ip route`, `show ip route 10.0.0.0` |
| The running config | `show running-config` (`| section interface` for a part) |
| Saved vs running | `show startup-config`, `show archive config differences` (IOS-XE) |
| Who else is on the box | `show users` |

`do show ...` runs a show command from inside configuration mode.

## Paging and long output

`terminal length 0` once per session (it lasts for the session only). If
`--More--` appears anyway, pass `auto_reply={"--More--": " "}` or send a space;
`q` abandons the output.

## Interrupting and recovering

- A running `ping`, `traceroute` or `debug`: `send_keys(["ctrl-c"])` or the
  IOS escape `ctrl-shift-6` (send `ctrl-^`). `undebug all` stops all debugs.
- `% Invalid input detected at '^' marker`: the word under `^` is wrong; check
  the mode, IOS commands differ by mode.
- `% Ambiguous command`: type more of the word.
- `% Incomplete command`: more arguments needed; `?` lists them.
- Prompt is `>` but you need `#`: `enable`.

## Safety

`show ...` is safe. Anything typed in `(config)#` is live immediately;
`write memory`, `reload`, `write erase` change persistent state. Each needs
the user's explicit intent; `reload in 10` before path-affecting changes is
the default, not an option. Read-only mode (`SERIAL_CONSOLE_READ_ONLY`)
allows `show ...`, `enable`, `exit` and `terminal length 0`, and refuses the
rest.
