; Inno Setup script for the Windows installer.
;   iscc /DAppVersion=1.2.3 packaging\windows\tooltracer.iss
; Expects the PyInstaller output in dist\Tooltracer\.
;
; AppId must never change: it is how Windows recognises a new version as an upgrade of the
; installed one (same folder, same shortcuts, one entry in "Installed apps").

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8C5B0C55-6C1E-4B7E-9E0B-7A3D5B2F4C11}
AppName=Tooltracer
AppVersion={#AppVersion}
AppVerName=Tooltracer {#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\Tooltracer
DefaultGroupName=Tooltracer
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=Tooltracer
UninstallDisplayIcon={app}\Tooltracer.exe
OutputDir=..\..\dist
OutputBaseFilename=Tooltracer-{#AppVersion}-windows-setup
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
Source: "..\..\dist\Tooltracer\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Tooltracer"; Filename: "{app}\Tooltracer.exe"
Name: "{autodesktop}\Tooltracer"; Filename: "{app}\Tooltracer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Tooltracer.exe"; Description: "Launch Tooltracer"; Flags: nowait postinstall skipifsilent
