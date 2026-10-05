#ifndef AppVersion
  #define AppVersion "1.2.1"
#endif
[Setup]
AppId={{50AEF5B8-BA58-4BC6-97E6-41834986854B}
AppName=Daglo TXT
AppVersion={#AppVersion}
AppPublisher=sopo9880
AppPublisherURL=https://github.com/sopo9880/Daglo_TXT
AppSupportURL=https://github.com/sopo9880/Daglo_TXT/issues
AppUpdatesURL=https://github.com/sopo9880/Daglo_TXT/releases
DefaultDirName={localappdata}\Programs\DagloTXT
DefaultGroupName=Daglo TXT
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=release
OutputBaseFilename=DagloTXT-Setup-{#AppVersion}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
CloseApplicationsFilter=DagloTXT.exe
RestartApplications=no
SetupLogging=yes
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\DagloTXT.exe

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "dist\DagloTXT\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Daglo TXT"; Filename: "{app}\DagloTXT.exe"
Name: "{autodesktop}\Daglo TXT"; Filename: "{app}\DagloTXT.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DagloTXT.exe"; Description: "Launch Daglo TXT"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var Installed: String;
    InstalledVersion, NewVersion: Int64;
begin
  Result := True;
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{50AEF5B8-BA58-4BC6-97E6-41834986854B}_is1', 'DisplayVersion', Installed) then
  begin
    if StrToVersion(Installed, InstalledVersion) and StrToVersion('{#AppVersion}', NewVersion) and (InstalledVersion > NewVersion) then
    begin
      MsgBox('A newer Daglo TXT version is already installed. Downgrade was blocked to protect your installation.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;
