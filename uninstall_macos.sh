#!/bin/bash
# Run as your normal user (it will prompt for sudo for the file removal).
set -euo pipefail
INSTALL_DIR="/Library/Application Support/SerialConsoleMCP"
BIN="$INSTALL_DIR/serial-console-mcp"

# Remove the Claude entry as the current user (no sudo) so it hits your config.
if [ -x "$BIN" ]; then
  "$BIN" configure --remove || true
fi
sudo rm -rf "$INSTALL_DIR"
sudo pkgutil --forget org.stefanbrunner.serialconsolemcp 2>/dev/null || true
echo "Removed Serial Console MCP. Restart Claude Desktop."
