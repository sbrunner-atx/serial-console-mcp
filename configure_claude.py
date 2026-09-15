#!/usr/bin/env python3
"""
configure_claude.py
===================
Registers (or removes) ham-serial-mcp in Claude Desktop by editing exactly one
entry in claude_desktop_config.json. Creates the file if missing, preserves any
servers already there, and backs up the old file first.

Usable three ways:
  * imported  -> write_config(command, args, remove=False, config_home=None)
  * as a CLI  -> python configure_claude.py --command "/path/to/ham-serial-mcp"
  * via the frozen server binary -> ham-serial-mcp configure --command "<binary>"

--config-home lets an installer that runs as root point writes at the real
user's home directory.
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

SERVER_KEY = "ham-serial"


def claude_config_path(config_home: Path | None = None) -> Path:
    home = Path(config_home) if config_home else Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if os.name == "nt":
        appdata = Path(config_home) / "AppData" / "Roaming" if config_home else Path(os.environ["APPDATA"])
        return appdata / "Claude" / "claude_desktop_config.json"
    base = (home / ".config") if config_home else Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return base / "Claude" / "claude_desktop_config.json"


def write_config(command: str, args: list[str], remove: bool = False,
                 config_home: Path | None = None) -> Path:
    cfg = claude_config_path(config_home)
    cfg.parent.mkdir(parents=True, exist_ok=True)

    data = {}
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text() or "{}")
        except json.JSONDecodeError:
            shutil.copy2(cfg, cfg.with_suffix(f".broken-{int(time.time())}.json"))
            data = {}
    data.setdefault("mcpServers", {})

    if cfg.exists():
        shutil.copy2(cfg, cfg.with_suffix(f".backup-{int(time.time())}.json"))

    if remove:
        data["mcpServers"].pop(SERVER_KEY, None)
    else:
        data["mcpServers"][SERVER_KEY] = {"command": command, "args": list(args)}

    tmp = cfg.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(cfg)
    return cfg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="configure")
    ap.add_argument("--command", help='Executable to launch (frozen binary path, or "python").')
    ap.add_argument("--arg", action="append", default=[],
                    help="An argument to pass (repeatable), e.g. the server .py path.")
    ap.add_argument("--remove", action="store_true", help="Remove the entry instead of adding it.")
    ap.add_argument("--config-home", default=None,
                    help="Override the user home dir (for root-run installers).")
    a = ap.parse_args(argv)

    if not a.remove and not a.command:
        ap.error("--command is required unless --remove is given")

    home = Path(a.config_home) if a.config_home else None
    cfg = write_config(a.command or "", a.arg, remove=a.remove, config_home=home)

    print(("Removed" if a.remove else "Registered") + f" ham-serial in {cfg}")
    if not a.remove:
        print("\nFully quit Claude Desktop and reopen it, then ask:")
        print('  "What serial ports do you see?"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
