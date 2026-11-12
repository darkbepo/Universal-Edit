; Universal Edit — Windows installer (Inno Setup 6). Built by .github/workflows/build-windows.yml:
;   iscc /DAppVersion=0.1.0 /DSourceDir=..\dist\UniversalEdit /DOutputDir=..\installer packaging\installer.iss
; Installs for the current user only (no administrator rights), into
; %LOCALAPPDATA%\Programs\Universal Edit. The right-click "Convert" entry and "Open with" are
; written by the app itself (UniversalEdit.exe --register …, tools/shell_integration.py), so the
; app's Settings window can switch them later; the uninstaller runs --unregister all.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\UniversalEdit"
#endif
#ifndef OutputDir
  #define OutputDir "..\installer"
#endif

[Setup]
; Never change AppId: Windows uses it to find the installed copy for updates / uninstalling.
AppId={{6F3C2B1E-8D4A-4E7B-9C55-2A1D7E0B9F31}
AppName=Universal Edit
AppVersion={#AppVersion}
AppVerName=Universal Edit {#AppVersion}
AppPublisher=darkbepo (open source)
AppPublisherURL=https://github.com/darkbepo/Universal-Edit
AppSupportURL=https://github.com/darkbepo/Universal-Edit/issues
DefaultDirName={localappdata}\Programs\Universal Edit
DefaultGroupName=Universal Edit
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=UniversalEdit-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\UniversalEdit.exe
UninstallDisplayName=Universal Edit
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "contextmenu"; Description: "Add ""Convert with Universal Edit"" to the right-click menu"; GroupDescription: "Windows:"
Name: "openwith"; Description: "Show Universal Edit in ""Open with"" for pictures, video, music, documents and 3D models"; GroupDescription: "Windows:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; files of an older version that a new one no longer has (the folder is ours alone)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Universal Edit"; Filename: "{app}\UniversalEdit.exe"
Name: "{autodesktop}\Universal Edit"; Filename: "{app}\UniversalEdit.exe"; Tasks: desktopicon

[Run]
; each switch is written as chosen (--register also clears what an older version registered)
Filename: "{app}\UniversalEdit.exe"; Parameters: "--register context-menu"; Tasks: contextmenu; Flags: runhidden waituntilterminated
Filename: "{app}\UniversalEdit.exe"; Parameters: "--unregister context-menu"; Tasks: not contextmenu; Flags: runhidden waituntilterminated
Filename: "{app}\UniversalEdit.exe"; Parameters: "--register open-with"; Tasks: openwith; Flags: runhidden waituntilterminated
Filename: "{app}\UniversalEdit.exe"; Parameters: "--unregister open-with"; Tasks: not openwith; Flags: runhidden waituntilterminated
Filename: "ms-settings:defaultapps"; Description: "Choose Universal Edit as the default app for pictures and videos (opens Windows settings)"; Tasks: openwith; Flags: postinstall shellexec skipifsilent unchecked
Filename: "{app}\UniversalEdit.exe"; Description: "Start Universal Edit"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\UniversalEdit.exe"; Parameters: "--unregister all"; Flags: runhidden waituntilterminated; RunOnceId: "UnregisterShell"

[Code]
{ On an upgrade the two Windows switches start as they are now — the app's Settings window may
  have changed them since the last install — instead of as ticked last time. Done once, so
  going Back to the page keeps what the user changed. }
var
  TasksPrepared: Boolean;

function AlreadyInstalled: Boolean;
begin
  Result := RegKeyExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F3C2B1E-8D4A-4E7B-9C55-2A1D7E0B9F31}_is1');
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpSelectTasks) and not TasksPrepared and AlreadyInstalled then
  begin
    TasksPrepared := True;
    if RegKeyExists(HKCU, 'Software\Classes\SystemFileAssociations\.png\shell\UniversalEdit.Convert') then
      WizardSelectTasks('contextmenu')
    else
      WizardSelectTasks('!contextmenu');
    if RegValueExists(HKCU, 'Software\RegisteredApplications', 'Universal Edit') then
      WizardSelectTasks('openwith')
    else
      WizardSelectTasks('!openwith');
  end;
end;
