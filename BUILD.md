# BUILD — making the two installers

Goal: a single file the user double-clicks. No Python, no terminal, no JSON.
Everything here is wired up; you mostly just run it.

## Why not a `.mcpb`?

A `.mcpb` extension runs in Claude Desktop's sandbox — no LAN, no `/dev/cu.*`, no
COM ports. So it can't drive serial. The install therefore registers a **host
stdio subprocess** (the route that works for reaching real hardware), but hides
every manual step inside a clicker.

## One binary, two jobs

`serial_console_mcp.py` is frozen into a single executable:
- launched with **no args** -> runs the MCP stdio server (what Claude calls);
- launched as **`serial-console-mcp configure --command <self>`** -> writes the
  `claude_desktop_config.json` entry and exits (what the installer calls).

The configurator merges into existing servers (won't touch your other MCP
entries) and backs up the old file first.

## The one non-obvious trap: elevated installer vs. per-user config

Installer postinstall steps run **elevated** (root on macOS, admin on Windows),
but Claude's config is per-user. Writing it from the elevated context lands in the
wrong home and silently does nothing. Both installers correct for this:
- **Windows:** the `[Run]` entry has `Flags: runasoriginaluser`.
- **macOS:** `scripts/postinstall` finds the console user and runs the configurator
  as them via `launchctl asuser <uid> sudo -u <user> ... --config-home <home>`.

## Windows  ->  SerialConsoleMCP-Setup.exe

```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --name serial-console-mcp --collect-all mcp serial_console_mcp.py
```
Then compile `installer.iss` with Inno Setup (free). Output:
`Output\SerialConsoleMCP-Setup.exe`. Edit `AppPublisher` (your name/callsign) and,
if you have a code-signing cert, add a `SignTool` directive to avoid SmartScreen
warnings (optional; unsigned works, just warns).

## macOS  ->  SerialConsoleMCP-0.1.0.pkg

Unsigned (testing):
```
./build_macos.sh
```
Signed + notarized (on your Mac, with your Developer ID):
```
SIGN_IDENTITY_APP="Developer ID Application: Your Name (TEAMID)" \
SIGN_IDENTITY_INSTALLER="Developer ID Installer: Your Name (TEAMID)" \
NOTARY_PROFILE="AC" \
./build_macos.sh
```
`NOTARY_PROFILE` is a `notarytool store-credentials` keychain profile. The
package identifier is `org.stefanbrunner.serialconsolemcp` (override with
`IDENTIFIER=...`). Uninstall: `./uninstall_macos.sh`.

## CI (both at once)

`.github/workflows/build.yml` runs the pytest suite on Linux/macOS/Windows on
every push and pull request. On a `v*` tag (or manual dispatch) it additionally
freezes the binary and builds each installer, uploading them as artifacts. PyInstaller isn't a cross-compiler, so this is the easiest way to get
both from one `git tag`. The Mac job is **unsigned** in CI — for a notarized pkg,
run `build_macos.sh` locally with your Developer ID, since exporting certs into CI
is extra ceremony you don't need for a hobby tool.

## Things to personalize if you fork this

- `installer.iss`: `AppPublisher`, `AppId` (generate a new GUID), optional signing.
- `build_macos.sh` / `uninstall_macos.sh`: `IDENTIFIER`.
- The server and configurator need no edits.

## Testing against real hardware

`tests/` is the unit suite (fake serial port, runs in CI). `_hosttest/run_live.py`
is a developer harness that drives the real tool functions against a real port
from a JSON list of steps, without the MCP transport in the way. It ships a stub
`mcp` package so it also runs on a host Python older than 3.10; it sends only
what the step file lists.

## The config entry produced

```json
{
  "mcpServers": {
    "serial-console": {
      "command": "/Library/Application Support/SerialConsoleMCP/serial-console-mcp",
      "args": []
    }
  }
}
```
