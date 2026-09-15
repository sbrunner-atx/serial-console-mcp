; Inno Setup script for Ham Serial MCP (Windows)
; Build the binary first:  pyinstaller --onefile --name ham-serial-mcp --collect-all mcp ham_serial_mcp.py
; Then compile this with Inno Setup -> Output\HamSerialMCP-Setup.exe

#define MyAppName "Ham Serial MCP"
#define MyAppVersion "1.0"
#define MyAppPublisher "Your Callsign"

[Setup]
AppId={{B3F1A2C4-7E5D-4A19-9C2B-1D6E8F0A4C77}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\HamSerialMCP
DisableProgramGroupPage=yes
DisableDirPage=yes
OutputDir=Output
OutputBaseFilename=HamSerialMCP-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Files]
Source: "dist\ham-serial-mcp.exe"; DestDir: "{app}"; Flags: ignoreversion

[Run]
; Register with Claude Desktop AS THE REAL USER (runasoriginaluser), so %APPDATA%
; resolves to the logged-in user's profile rather than the elevated admin's.
Filename: "{app}\ham-serial-mcp.exe"; \
  Parameters: "configure --command ""{app}\ham-serial-mcp.exe"""; \
  Flags: runhidden runasoriginaluser; \
  StatusMsg: "Registering with Claude Desktop..."

[UninstallRun]
; Runs before files are removed, so the exe still exists here.
Filename: "{app}\ham-serial-mcp.exe"; \
  Parameters: "configure --remove"; \
  Flags: runhidden runasoriginaluser; \
  RunOnceId: "RemoveClaudeEntry"

[Messages]
FinishedLabel=Setup is done. Now FULLY QUIT Claude Desktop (right-click the tray icon, Quit) and reopen it. Then ask Claude: "What serial ports do you see?"
