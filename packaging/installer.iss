; Universal Edit — Windows installer (Inno Setup 6). Built by .github/workflows/build-windows.yml:
;   iscc /DAppVersion=1.0.0 /DSourceDir=..\dist\UniversalEdit /DOutputDir=..\installer packaging\installer.iss
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
#ifndef AddonsDir
  #define AddonsDir "..\dist\addons"
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

[Types]
Name: "full"; Description: "Converter and every editor"
Name: "converter"; Description: "Converter only"
Name: "custom"; Description: "Choose"; Flags: iscustom

[Components]
Name: "main"; Description: "Universal Edit (converter and viewer)"; Types: full converter custom; Flags: fixed
Name: "image"; Description: "Image editor (layers, retouching, background removal; about 24 MB)"; Types: full
Name: "model"; Description: "3D viewer"; Types: full
Name: "audio"; Description: "Audio editor"; Types: full
Name: "video"; Description: "Video editor"; Types: full
Name: "code"; Description: "Code editor"; Types: full

[Tasks]
Name: "contextmenu"; Description: "Add ""Quick Convert"" to the right-click menu (convert a file in one click)"; GroupDescription: "Windows:"
Name: "openwith"; Description: "Show Universal Edit in ""Open with"" for pictures, video, music, documents and 3D models"; GroupDescription: "Windows:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; editor add-ons: copied to a temporary folder, then installed (checked) by the app itself
Source: "{#AddonsDir}\UniversalEdit-addon-image-*.zip"; DestDir: "{tmp}\addons"; Components: image; Flags: deleteafterinstall
Source: "{#AddonsDir}\UniversalEdit-addon-model-*.zip"; DestDir: "{tmp}\addons"; Components: model; Flags: deleteafterinstall
Source: "{#AddonsDir}\UniversalEdit-addon-audio-*.zip"; DestDir: "{tmp}\addons"; Components: audio; Flags: deleteafterinstall
Source: "{#AddonsDir}\UniversalEdit-addon-video-*.zip"; DestDir: "{tmp}\addons"; Components: video; Flags: deleteafterinstall
Source: "{#AddonsDir}\UniversalEdit-addon-code-*.zip"; DestDir: "{tmp}\addons"; Components: code; Flags: deleteafterinstall

[InstallDelete]
; files of an older version that a new one no longer has (the folder is ours alone)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Universal Edit"; Filename: "{app}\UniversalEdit.exe"
Name: "{autodesktop}\Universal Edit"; Filename: "{app}\UniversalEdit.exe"; Tasks: desktopicon

[Run]
; the editors are installed by [Code] InstallEditors (it checks the result)
; each switch is written as chosen (--register also clears what an older version registered)
Filename: "{app}\UniversalEdit.exe"; Parameters: "--register context-menu"; Tasks: contextmenu; Flags: runhidden waituntilterminated
Filename: "{app}\UniversalEdit.exe"; Parameters: "--unregister context-menu"; Tasks: not contextmenu; Flags: runhidden waituntilterminated
Filename: "{app}\UniversalEdit.exe"; Parameters: "--register open-with"; Tasks: openwith; Flags: runhidden waituntilterminated
Filename: "{app}\UniversalEdit.exe"; Parameters: "--unregister open-with"; Tasks: not openwith; Flags: runhidden waituntilterminated
Filename: "ms-settings:defaultapps"; Description: "Choose Universal Edit as the default app for pictures and videos (opens Windows settings)"; Tasks: openwith; Flags: postinstall shellexec skipifsilent unchecked
Filename: "{app}\UniversalEdit.exe"; Description: "Start Universal Edit"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; the editors installed as add-ons (the app's settings and tools there are kept)
Type: filesandordirs; Name: "{localappdata}\UniversalEdit\addons"

[Code]
{ The chosen editors are installed from the add-on zips copied to the temporary setup
  folder. Unlike a [Run] entry this sees the result: a failure is logged and (unless silent)
  explained — setup still finishes, and the editors can be installed later from the app's
  Settings → Editors. (No braces inside these comments: one would end the comment.) }
procedure InstallEditors;
var
  ResultCode: Integer;
begin
  if not (WizardIsComponentSelected('image') or WizardIsComponentSelected('model') or
          WizardIsComponentSelected('audio') or WizardIsComponentSelected('video') or
          WizardIsComponentSelected('code')) then
    Exit;
  WizardForm.StatusLabel.Caption := 'Installing the editors…';
  if not Exec(ExpandConstant('{app}\UniversalEdit.exe'),
              '--install-addon all "' + ExpandConstant('{tmp}\addons') + '"', '', SW_HIDE,
              ewWaitUntilTerminated, ResultCode) then
    Log(Format('Installing the editors: the app didn''t start (Windows error %d).', [ResultCode]))
  else if ResultCode <> 0 then
    Log(Format('Installing the editors failed (exit code %d).', [ResultCode]))
  else
    Exit;
  SuppressibleMsgBox('Some editors couldn''t be installed. Universal Edit itself is ready: ' +
                     'add the editors later in the app (cog, top left > Editors).',
                     mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    InstallEditors;
end;

{ Uninstalling: the app takes out everything it registered — Quick Convert, Open with, the
  file-type icons and any "always open with Universal Edit" Windows remembers — so those files
  go back to Windows' own choice (exit code 3 = Windows protects some of them, e.g. .pdf on
  Windows 11: only its Settings can change those). Then, if asked (not by default), the app's
  settings and downloaded tools go too. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    if Exec(ExpandConstant('{app}\UniversalEdit.exe'), '--unregister all', '', SW_HIDE,
            ewWaitUntilTerminated, ResultCode) and (ResultCode = 3) and not UninstallSilent then
      MsgBox('Windows keeps a few file types (such as PDF) protected, so Universal Edit couldn''t ' +
             'give them back to their earlier app. Choose another app for them in Windows Settings ' +
             '> Apps > Default apps.', mbInformation, MB_OK);
  end;
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
  begin
    if MsgBox('Also remove Universal Edit''s settings and the tools it downloaded ' +
              '(LibreOffice, Pandoc, …)?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(ExpandConstant('{localappdata}\UniversalEdit'), True, True, True);
      RegDeleteKeyIncludingSubkeys(HKCU, 'Software\UniversalEdit');
    end;
  end;
end;


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
