# BUILD — making the two installers

Goal: a single file the ham double-clicks. No Python, no terminal, no JSON.
Everything here is wired up; you mostly just run it.

## Why not a `.mcpb`?

A `.mcpb` extension runs in Claude Desktop's sandbox — no LAN, no `/dev/cu.*`, no
COM ports (the same wall as reaching the N3FJP VM). So it can't drive serial. The
install therefore registers a **host stdio subprocess** (the route that already
works for contest-mcp), but hides every manual step inside a clicker.

## One binary, two jobs

`ham_serial_mcp.py` is frozen into a single executable:
- launched with **no args** -> runs the MCP stdio server (what Claude calls);
- launched as **`ham-serial-mcp configure --command <self>`** -> writes the
  `claude_desktop_config.json` entry and exits (what the installer calls).

The configurator merges into existing servers (won't touch your contest-mcp
entry) and backs up the old file first.

## The one non-obvious trap: elevated installer vs. per-user config

Installer postinstall steps run **elevated** (root on macOS, admin on Windows),
but Claude's config is per-user. Writing it from the elevated context lands in the
wrong home and silently does nothing. Both installers correct for this:
- **Windows:** the `[Run]` entry has `Flags: runasoriginaluser`.
- **macOS:** `scripts/postinstall` finds the console user and runs the configurator
  as them via `launchctl asuser <uid> sudo -u <user> ... --config-home <home>`.

## Windows  ->  HamSerialMCP-Setup.exe

```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --name ham-serial-mcp --collect-all mcp ham_serial_mcp.py
```
Then compile `installer.iss` with Inno Setup (free). Output:
`Output\HamSerialMCP-Setup.exe`. Edit `AppPublisher` (your callsign) and, if you
have a code-signing cert, add a `SignTool` directive to avoid SmartScreen warnings
(optional; unsigned works, just warns).

## macOS  ->  HamSerialMCP-1.0.pkg

Unsigned (testing):
```
./build_macos.sh
```
Signed + notarized (on your Mac, with your Developer ID — you have the identities):
```
SIGN_IDENTITY_APP="Developer ID Application: Your Name (TEAMID)" \
SIGN_IDENTITY_INSTALLER="Developer ID Installer: Your Name (TEAMID)" \
NOTARY_PROFILE="AC" \
./build_macos.sh
```
`NOTARY_PROFILE` is a `notarytool store-credentials` keychain profile. Edit
`IDENTIFIER` (e.g. `com.<callsign>.hamserialmcp`) at the top of the script.
Uninstall: `./uninstall_macos.sh`.

## CI (both at once)

`.github/workflows/build.yml` runs a `windows-latest` + `macos-latest` matrix on
any `v*` tag (or manual dispatch): freezes, builds each installer, uploads
artifacts. PyInstaller isn't a cross-compiler, so this is the easiest way to get
both from one `git tag`. The Mac job is **unsigned** in CI — for a notarized pkg,
run `build_macos.sh` locally with your Developer ID, since exporting certs into CI
is extra ceremony you don't need for a hobby tool.

## Things to personalize before shipping

- `installer.iss`: `AppPublisher`, optional signing.
- `build_macos.sh` / `uninstall_macos.sh`: `IDENTIFIER` (your callsign).
- That's it. The server and configurator need no edits.

## The config entry produced

```json
{
  "mcpServers": {
    "ham-serial": {
      "command": "/Library/Application Support/HamSerialMCP/ham-serial-mcp",
      "args": []
    }
  }
}
```
