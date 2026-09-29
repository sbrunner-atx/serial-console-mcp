# BUILD — the retired `.pkg` and `.exe` installers

**Status (September 2026):** releases now ship `serial-console-mcp.mcpb`, a Claude
Desktop extension built from `manifest.json` with `npx @anthropic-ai/mcpb pack`.
The installers below are no longer attached to releases. This file stays as a
record of how they were built.

## Why there were installers

The installers were written on the assumption that a `.mcpb` extension runs in
Claude Desktop's sandbox with no access to `/dev/cu.*` or COM ports. That turned
out not to hold for serial ports: on 28 September 2026 the 0.3.2 extension listed
the ports on a Mac and opened `/dev/cu.Bluetooth-Incoming-Port` without error.
Windows COM ports through the extension have not been tested yet.

## One binary, two jobs

The `serial_console_mcp` package (entry script `packaging/entry.py`) is frozen
into a single executable:
- launched with **no args** -> runs the MCP stdio server (what Claude calls);
- launched as **`serial-console-mcp configure --command <self>`** -> writes the
  `claude_desktop_config.json` entry and exits (what the installer calls).

The configurator merges into existing servers (won't touch your other MCP
entries) and backs up the old file first.

## The one non-obvious trap: elevated installer vs. per-user config

Installer postinstall steps run **elevated** (root on macOS, admin on Windows),
but Claude's config is per-user. Writing it from the elevated context lands in the
wrong home and silently does nothing. Both installers correct for this:
- **Windows:** the `[Run]` entry has `Flags: runasoriginaluser`. Inno does not allow
  that flag in `[UninstallRun]`, so the uninstall-time `configure --remove` runs
  elevated; it only reaches the right profile when the admin account is the user's
  own (the usual single-user PC). Otherwise the stale entry is harmless: Claude
  Desktop shows the server as unavailable until it is removed by hand.
- **macOS:** `scripts/postinstall` finds the console user and runs the configurator
  as them via `launchctl asuser <uid> sudo -u <user> ... --config-home <home>`.

## Windows  ->  SerialConsoleMCP-Setup.exe

```
python -m venv .venv && .venv\Scripts\activate
pip install ".[freeze]"
pyinstaller --onefile --name serial-console-mcp --collect-all mcp --collect-all serial_console_mcp --collect-all pyte packaging/entry.py
```
Then compile `installer.iss` with Inno Setup (free). Output:
`Output\SerialConsoleMCP-Setup.exe`. Edit `AppPublisher` (your name/callsign) and,
if you have a code-signing cert, add a `SignTool` directive to avoid SmartScreen
warnings (optional; unsigned works, just warns).

## macOS  ->  SerialConsoleMCP-0.3.2.pkg

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

## PyPI

`.github/workflows/release.yml` publishes the sdist and wheel to PyPI with
Trusted Publishing (OIDC, no API token) whenever a GitHub Release is published.
The tag must equal `v` + the version in `pyproject.toml`; the workflow checks.
Release steps:

```
# bump version in pyproject.toml and src/serial_console_mcp/__init__.py, update CHANGELOG.md
git tag vX.Y.Z && git push origin vX.Y.Z          # builds the installers
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <(sed -n '/^## \[X.Y.Z\]/,/^## \[/p' CHANGELOG.md)
```

One-time setup on pypi.org: Manage project → Publishing → add a trusted publisher
for `sbrunner-atx/serial-console-mcp`, workflow `release.yml`, environment `release`.

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
