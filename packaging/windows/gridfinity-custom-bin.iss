; Inno Setup script for the Windows installer.
;   iscc /DAppVersion=1.2.3 packaging\windows\gridfinity-custom-bin.iss
; Expects the PyInstaller output in dist\GridfinityCustomBin\.
;
; AppId must never change: it is how Windows recognises a new version as an upgrade of the
; installed one (same folder, same shortcuts, one entry in "Installed apps").

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8C5B0C55-6C1E-4B7E-9E0B-7A3D5B2F4C11}
AppName=Gridfinity Custom Bin
AppVersion={#AppVersion}
AppVerName=Gridfinity Custom Bin {#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\GridfinityCustomBin
DefaultGroupName=Gridfinity Custom Bin
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=Gridfinity Custom Bin
UninstallDisplayIcon={app}\GridfinityCustomBin.exe
OutputDir=..\..\dist
OutputBaseFilename=GridfinityCustomBin-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; Remove the previous version's program files so nothing stale is left behind after an upgrade.
; User data (library folder, settings) lives elsewhere and is never touched.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\GridfinityCustomBin\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Gridfinity Custom Bin"; Filename: "{app}\GridfinityCustomBin.exe"
Name: "{autodesktop}\Gridfinity Custom Bin"; Filename: "{app}\GridfinityCustomBin.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GridfinityCustomBin.exe"; Description: "Launch Gridfinity Custom Bin"; Flags: nowait postinstall skipifsilent
