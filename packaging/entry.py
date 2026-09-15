"""PyInstaller entry script. The frozen installer binary starts here.

PyInstaller freezes a script, not a package, so this thin file imports the
package and calls the same main() the console script uses.
"""

from serial_console_mcp.server import main

main()
