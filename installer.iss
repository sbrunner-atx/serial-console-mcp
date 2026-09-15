; Inno Setup script for Serial Console MCP (Windows)
; Build the binary first:  pip install . pyinstaller && pyinstaller --onefile --name serial-console-mcp --collect-all mcp --collect-all serial_console_mcp packaging/entry.py
; Then compile this with Inno Setup -> Output\SerialConsoleMCP-Setup.exe

#define MyAppName "Serial Console MCP"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Stefan Brunner (AE5VG)"

[Setup]
AppId={{B3F1A2C4-7E5D-4A19-9C2B-1D6E8F0A4C77}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\SerialConsoleMCP
DisableProgramGroupPage=yes
DisableDirPage=yes
OutputDir=Output
OutputBaseFilename=SerialConsoleMCP-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Files]
Source: "dist\serial-console-mcp.exe"; DestDir: "{app}"; Flags: ignoreversion

[Run]
; Register with Claude Desktop AS THE REAL USER (runasoriginaluser), so %APPDATA%
; resolves to the logged-in user's profile rather than the elevated admin's.
Filename: "{app}\serial-console-mcp.exe"; \
  Parameters: "configure --command ""{app}\serial-console-mcp.exe"""; \
  Flags: runhidden runasoriginaluser; \
  StatusMsg: "Registering with Claude Desktop..."

[UninstallRun]
; Runs before files are removed, so the exe still exists here.
Filename: "{app}\serial-console-mcp.exe"; \
  Parameters: "configure --remove"; \
  Flags: runhidden runasoriginaluser; \
  RunOnceId: "RemoveClaudeEntry"

[Messages]
FinishedLabel=Setup is done. Now FULLY QUIT Claude Desktop (right-click the tray icon, Quit) and reopen it. Then ask Claude: "What serial ports do you see?"
